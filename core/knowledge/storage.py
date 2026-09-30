# core/knowledge/storage.py

"""
Хранилище персональной базы знаний.

Архитектура:
- SQLite с FTS5 для полнотекстового поиска (мгновенный)
- Отдельная таблица для embeddings (семантический поиск)
- Таблица активности (автоматический дневник)
- WAL mode для параллельного чтения/записи
- Миграции схемы для обновлений без потери данных
- Полный бэкап перед миграциями

Почему SQLite, а не ChromaDB/FAISS:
- Ноль зависимостей (sqlite3 в стандартной библиотеке)
- FTS5 покрывает 80% запросов без embeddings
- Один файл = простой бэкап
- WAL mode = чтение не блокирует запись
- Embeddings храним как BLOB (numpy → bytes, быстрее чем pickle)
"""

import sqlite3
import time
import shutil
import logging
import threading
import struct
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
#  Схема БД
# ═══════════════════════════════════════════

SCHEMA_VERSION = 2

SCHEMA_SQL = """
-- Версия схемы
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

-- ═══════════════════════════════════════
--  Заметки и факты (активный ввод)
-- ═══════════════════════════════════════

CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    entry_type TEXT NOT NULL DEFAULT 'note',
    -- Типы: note, fact, snippet, bookmark, thought
    source TEXT DEFAULT 'voice',
    -- Источники: voice, clipboard, auto, import
    tags TEXT DEFAULT '',
    -- Теги через запятую: "работа,python,важно"
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    is_archived INTEGER DEFAULT 0,
    -- Мягкое удаление
    importance INTEGER DEFAULT 0
    -- 0=обычная, 1=важная, 2=критическая
);

CREATE INDEX IF NOT EXISTS idx_entries_type
    ON entries(entry_type);
CREATE INDEX IF NOT EXISTS idx_entries_created
    ON entries(created_at);
CREATE INDEX IF NOT EXISTS idx_entries_importance
    ON entries(importance);

-- Полнотекстовый поиск (FTS5)
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    content,
    tags,
    content='entries',
    content_rowid='id',
    tokenize='unicode61'
);

-- Триггеры для синхронизации FTS
CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
    INSERT INTO entries_fts(rowid, content, tags)
    VALUES (new.id, new.content, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, content, tags)
    VALUES ('delete', old.id, old.content, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, content, tags)
    VALUES ('delete', old.id, old.content, old.tags);
    INSERT INTO entries_fts(rowid, content, tags)
    VALUES (new.id, new.content, new.tags);
END;

-- ═══════════════════════════════════════
--  Embeddings (семантический поиск)
-- ═══════════════════════════════════════

CREATE TABLE IF NOT EXISTS embeddings (
    entry_id INTEGER PRIMARY KEY,
    vector BLOB NOT NULL,
    -- numpy float32 array as bytes
    model_name TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
);

-- ═══════════════════════════════════════
--  Лог активности (пассивный сбор)
-- ═══════════════════════════════════════

CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    -- Типы: app_switch, command, search, file_open, clipboard
    target TEXT DEFAULT '',
    -- Что: имя приложения, команда, поисковый запрос
    details TEXT DEFAULT '',
    -- JSON с доп. данными
    window_title TEXT DEFAULT '',
    timestamp REAL NOT NULL,
    duration_sec REAL DEFAULT 0
    -- Сколько времени провёл (для app_switch)
);

CREATE INDEX IF NOT EXISTS idx_activity_timestamp
    ON activity_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_activity_action
    ON activity_log(action);

-- ═══════════════════════════════════════
--  Связи между записями
-- ═══════════════════════════════════════

CREATE TABLE IF NOT EXISTS entry_links (
    from_id INTEGER NOT NULL,
    to_id INTEGER NOT NULL,
    link_type TEXT DEFAULT 'related',
    -- Типы: related, followup, contradicts, supersedes
    created_at REAL NOT NULL,
    PRIMARY KEY (from_id, to_id),
    FOREIGN KEY (from_id) REFERENCES entries(id) ON DELETE CASCADE,
    FOREIGN KEY (to_id) REFERENCES entries(id) ON DELETE CASCADE
);

-- ═══════════════════════════════════════
--  История чата (для персистентности GUI)
-- ═══════════════════════════════════════

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT DEFAULT '',
    timestamp REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_timestamp
    ON chat_messages(timestamp);
"""


# ═══════════════════════════════════════════
#  Data classes
# ═══════════════════════════════════════════

@dataclass
class Entry:
    """Запись в базе знаний."""
    id: int = 0
    content: str = ""
    entry_type: str = "note"
    source: str = "voice"
    tags: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    is_archived: int = 0
    importance: int = 0

    @property
    def tag_list(self) -> list[str]:
        return [t.strip() for t in self.tags.split(",") if t.strip()]

    @property
    def age_hours(self) -> float:
        return (time.time() - self.created_at) / 3600

    @property
    def age_display(self) -> str:
        hours = self.age_hours
        if hours < 1:
            return f"{int(hours * 60)} минут назад"
        elif hours < 24:
            return f"{int(hours)} часов назад"
        elif hours < 48:
            return "вчера"
        else:
            return f"{int(hours / 24)} дней назад"


@dataclass
class ActivityRecord:
    """Запись в логе активности."""
    id: int = 0
    action: str = ""
    target: str = ""
    details: str = ""
    window_title: str = ""
    timestamp: float = 0.0
    duration_sec: float = 0.0


@dataclass
class SearchResult:
    """Результат поиска."""
    entry: Entry
    score: float = 0.0
    match_type: str = "fts"
    # "fts" — полнотекстовый, "semantic" — по embeddings, "hybrid" — оба


# ═══════════════════════════════════════════
#  Сериализация embeddings
# ═══════════════════════════════════════════

def vector_to_bytes(vec: np.ndarray) -> bytes:
    """
    numpy float32 → bytes.

    Формат: [dim:uint32][float32 * dim]
    Быстрее pickle в ~10 раз, компактнее в ~3 раза.
    """
    dim = vec.shape[0]
    header = struct.pack('I', dim)
    return header + vec.astype(np.float32).tobytes()


def bytes_to_vector(data: bytes) -> np.ndarray:
    """bytes → numpy float32."""
    dim = struct.unpack('I', data[:4])[0]
    return np.frombuffer(data[4:], dtype=np.float32, count=dim)


# ═══════════════════════════════════════════
#  Хранилище
# ═══════════════════════════════════════════

class KnowledgeStorage:
    """
    Персистентное хранилище базы знаний.

    Потокобезопасное. Использует connection-per-thread
    и WAL mode для параллельных операций.

    Жизненный цикл:
    1. __init__: создание/открытие БД, миграции
    2. add_entry / add_activity / search / ...
    3. close: закрытие всех соединений
    """

    def __init__(self, db_path: str = "data/knowledge.db"):
        self._db_path = db_path
        self._local = threading.local()
        self._write_lock = threading.Lock()

        # Создаём директорию
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

        # Инициализация схемы
        conn = self._get_connection()
        self._migrate(conn)

        logger.info(
            "KnowledgeStorage: %s (%d записей, %d логов)",
            db_path, self.entry_count, self.activity_count
        )

    def _get_connection(self) -> sqlite3.Connection:
        """Получить соединение для текущего потока."""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                timeout=10.0
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA cache_size=-8000")  # 8MB cache
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn
    
    # ══════════════════════════════════════════════
    #  Чат (персистентность GUI)
    # ══════════════════════════════════════════════

    def add_chat_message(
        self,
        role: str,
        content: str,
        source: str = "",
        timestamp: float = 0,
    ) -> int:
        """Сохранить сообщение чата."""
        now = timestamp if timestamp > 0 else time.time()
        with self._write_lock:
            conn = self._get_connection()
            cursor = conn.execute(
                """
                INSERT INTO chat_messages (role, content, source, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (role, content, source, now),
            )
            conn.commit()
            return cursor.lastrowid

    def get_chat_messages(
        self,
        limit: int = 100,
        since: float = 0,
    ) -> list[dict]:
        """Получить последние сообщения чата."""
        conn = self._get_connection()
        if since > 0:
            rows = conn.execute(
                """
                SELECT role, content, source, timestamp
                FROM chat_messages
                WHERE timestamp > ?
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                (since, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT role, content, source, timestamp
                FROM chat_messages
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            rows = list(reversed(rows))

        return [
            {
                "role": r["role"],
                "content": r["content"],
                "source": r["source"],
                "timestamp": r["timestamp"],
            }
            for r in rows
        ]

    def clear_chat_messages(self) -> int:
        """Очистить историю чата. Возвращает количество удалённых."""
        with self._write_lock:
            conn = self._get_connection()
            cursor = conn.execute("DELETE FROM chat_messages")
            conn.commit()
            return cursor.rowcount

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Миграция схемы БД."""
        # Проверяем текущую версию
        try:
            row = conn.execute(
                "SELECT version FROM schema_version LIMIT 1"
            ).fetchone()
            current_version = row[0] if row else 0
        except sqlite3.OperationalError:
            current_version = 0

        if current_version >= SCHEMA_VERSION:
            return

        # Бэкап перед миграцией
        if os.path.exists(self._db_path) and current_version > 0:
            backup_path = f"{self._db_path}.v{current_version}.bak"
            try:
                shutil.copy2(self._db_path, backup_path)
                logger.info("Бэкап БД: %s", backup_path)
            except Exception as e:
                logger.warning("Не удалось создать бэкап: %s", e)

        # Применяем схему
        conn.executescript(SCHEMA_SQL)

        # Обновляем версию
        conn.execute("DELETE FROM schema_version")
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (SCHEMA_VERSION,)
        )
        conn.commit()

        logger.info(
            "Схема БД обновлена: v%d → v%d",
            current_version, SCHEMA_VERSION
        )

    # ══════════════════════════════════════════════
    #  Записи (entries)
    # ══════════════════════════════════════════════

    def add_entry(
        self,
        content: str,
        entry_type: str = "note",
        source: str = "voice",
        tags: str = "",
        importance: int = 0,
    ) -> int:
        """
        Добавить запись.

        Returns: ID новой записи
        """
        now = time.time()
        with self._write_lock:
            conn = self._get_connection()
            cursor = conn.execute(
                """
                INSERT INTO entries
                    (content, entry_type, source, tags,
                     created_at, updated_at, importance)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (content, entry_type, source, tags,
                 now, now, importance)
            )
            conn.commit()
            entry_id = cursor.lastrowid

        logger.debug("Добавлена запись #%d: '%s'", entry_id, content[:50])
        return entry_id

    def get_tracker_entries(
        self,
        hours: float = 24,
        entry_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[Entry]:
        """
        Получить записи трекера из entries за последние N часов.

        Это НЕ сырой activity_log, а человекочитаемые записи,
        созданные _save_to_knowledge().

        Для сырых данных используйте get_activity() / get_activity_summary().

        Args:
            hours:      Временное окно (от текущего момента назад)
            entry_type: Фильтр по типу ('app_switch', 'command', etc.)
            limit:      Максимум записей
        """
        conn = self._get_connection()
        since = time.time() - (hours * 3600)

        if entry_type:
            rows = conn.execute(
                """
                SELECT * FROM entries
                WHERE source = 'tracker'
                    AND is_archived = 0
                    AND created_at > ?
                    AND entry_type = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (since, entry_type, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM entries
                WHERE source = 'tracker'
                    AND is_archived = 0
                    AND created_at > ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (since, limit)
            ).fetchall()

        return [self._row_to_entry(r) for r in rows]

    def update_entry(self, entry_id: int, content: str) -> None:
        """Обновить текст записи."""
        with self._write_lock:
            conn = self._get_connection()
            conn.execute(
                "UPDATE entries SET content=?, updated_at=? WHERE id=?",
                (content, time.time(), entry_id)
            )
            conn.commit()

    def update_tags(self, entry_id: int, tags: str) -> None:
        """Обновить теги записи."""
        with self._write_lock:
            conn = self._get_connection()
            conn.execute(
                "UPDATE entries SET tags=?, updated_at=? WHERE id=?",
                (tags, time.time(), entry_id)
            )
            conn.commit()

    def archive_entry(self, entry_id: int) -> None:
        """Архивировать (мягкое удаление)."""
        with self._write_lock:
            conn = self._get_connection()
            conn.execute(
                "UPDATE entries SET is_archived=1, updated_at=? WHERE id=?",
                (time.time(), entry_id)
            )
            conn.commit()

    def get_entry(self, entry_id: int) -> Optional[Entry]:
        """Получить запись по ID."""
        conn = self._get_connection()
        row = conn.execute(
            "SELECT * FROM entries WHERE id=? AND is_archived=0",
            (entry_id,)
        ).fetchone()
        return self._row_to_entry(row) if row else None

    def get_recent(
        self,
        count: int = 10,
        entry_type: Optional[str] = None,
    ) -> list[Entry]:
        """Последние записи."""
        conn = self._get_connection()
        if entry_type:
            rows = conn.execute(
                """
                SELECT * FROM entries
                WHERE is_archived=0 AND entry_type=?
                ORDER BY created_at DESC LIMIT ?
                """,
                (entry_type, count)
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM entries
                WHERE is_archived=0
                ORDER BY created_at DESC LIMIT ?
                """,
                (count,)
            ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def get_by_tag(self, tag: str, limit: int = 20) -> list[Entry]:
        """Записи с определённым тегом."""
        conn = self._get_connection()
        rows = conn.execute(
            """
            SELECT * FROM entries
            WHERE is_archived=0 AND tags LIKE ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (f"%{tag}%", limit)
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    # ══════════════════════════════════════════════
    #  Полнотекстовый поиск (FTS5)
    # ══════════════════════════════════════════════

    def search_fts(
        self,
        query: str,
        limit: int = 10,
        source: Optional[str] = None,
        since: Optional[float] = None,
    ) -> list[SearchResult]:
        """
        Полнотекстовый поиск через FTS5.

        Стратегия: AND → OR → LIKE.
        AND точнее, но может не найти при опечатках.
        OR как fallback, LIKE как последний шанс.

        Args:
            query:  Поисковый запрос
            limit:  Максимум результатов
            source: Фильтр по источнику ('voice', 'tracker', etc.).
                    None = искать по всем источникам.
            since:  Unix timestamp. Искать только записи новее.
                    None = без ограничения по времени.
        """
        conn = self._get_connection()
        fts_query = self._prepare_fts_query(query)

        # ── Динамические фильтры ──
        extra_clauses = []
        extra_params: list = []

        if source:
            extra_clauses.append("AND e.source = ?")
            extra_params.append(source)
        if since is not None:
            extra_clauses.append("AND e.created_at > ?")
            extra_params.append(since)

        extra_sql = " ".join(extra_clauses)

        # ── Попытка 1: AND (точный) ──
        try:
            rows = conn.execute(
                f"""
                SELECT e.*, rank
                FROM entries_fts
                JOIN entries e ON e.id = entries_fts.rowid
                WHERE entries_fts MATCH ?
                    AND e.is_archived = 0
                    {extra_sql}
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, *extra_params, limit)
            ).fetchall()

            if rows:
                return [
                    SearchResult(
                        entry=self._row_to_entry(row),
                        score=-float(row['rank']),
                        match_type="fts",
                    )
                    for row in rows
                ]
        except sqlite3.OperationalError:
            pass

        # ── Попытка 2: OR (если AND пуст и слов > 1) ──
        words = query.strip().split()
        if len(words) > 1:
            parts = []
            for w in words:
                clean = ''.join(c for c in w if c.isalnum() or c in '-_')
                if clean:
                    parts.append(f'"{clean}"')
            if parts:
                or_query = " OR ".join(parts)
                try:
                    rows = conn.execute(
                        f"""
                        SELECT e.*, rank
                        FROM entries_fts
                        JOIN entries e ON e.id = entries_fts.rowid
                        WHERE entries_fts MATCH ?
                            AND e.is_archived = 0
                            {extra_sql}
                        ORDER BY rank
                        LIMIT ?
                        """,
                        (or_query, *extra_params, limit)
                    ).fetchall()
                    if rows:
                        return [
                            SearchResult(
                                entry=self._row_to_entry(row),
                                score=-float(row['rank']),
                                match_type="fts",
                            )
                            for row in rows
                        ]
                except sqlite3.OperationalError:
                    pass

        # ── Попытка 3: LIKE ──
        return self._search_like(query, limit, source, since)

    def _search_like(
        self,
        query: str,
        limit: int = 10,
        source: Optional[str] = None,
        since: Optional[float] = None,
    ) -> list[SearchResult]:
        """Fallback поиск через LIKE."""
        conn = self._get_connection()

        extra_clauses = []
        extra_params: list = []

        if source:
            extra_clauses.append("AND source = ?")
            extra_params.append(source)
        if since is not None:
            extra_clauses.append("AND created_at > ?")
            extra_params.append(since)

        extra_sql = " ".join(extra_clauses)

        rows = conn.execute(
            f"""
            SELECT * FROM entries
            WHERE is_archived=0 AND content LIKE ?
                {extra_sql}
            ORDER BY created_at DESC LIMIT ?
            """,
            (f"%{query}%", *extra_params, limit)
        ).fetchall()
        return [
            SearchResult(
                entry=self._row_to_entry(r), score=1.0, match_type="like"
            )
            for r in rows
        ]

    @staticmethod
    def _prepare_fts_query(query: str) -> str:
        """
        Подготовить запрос для FTS5.

        Одно слово:  "молоко" → '"молоко"'
        Несколько:   "купить молоко" → '"купить" AND "молоко"'
        """
        words = query.strip().split()
        if not words:
            return '""'

        parts = []
        for w in words:
            clean = ''.join(
                c for c in w if c.isalnum() or c in '-_'
            )
            if clean:
                parts.append(f'"{clean}"')

        if not parts:
            return '""'

        return " AND ".join(parts)

    # ══════════════════════════════════════════════
    #  Embeddings
    # ══════════════════════════════════════════════

    def save_embedding(
        self,
        entry_id: int,
        vector: np.ndarray,
        model_name: str,
    ) -> None:
        """Сохранить embedding для записи."""
        blob = vector_to_bytes(vector)
        now = time.time()
        with self._write_lock:
            conn = self._get_connection()
            conn.execute(
                """
                INSERT OR REPLACE INTO embeddings
                    (entry_id, vector, model_name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (entry_id, blob, model_name, now)
            )
            conn.commit()

    def get_all_embeddings(self) -> list[tuple[int, np.ndarray]]:
        """
        Загрузить все embeddings в память.

        Для cosine similarity поиска нужны все векторы.
        При 10000 записей × 384 dims = ~15MB RAM — приемлемо.
        """
        conn = self._get_connection()
        rows = conn.execute(
            """
            SELECT e.entry_id, e.vector
            FROM embeddings e
            JOIN entries en ON en.id = e.entry_id
            WHERE en.is_archived = 0
            """
        ).fetchall()

        results = []
        for row in rows:
            try:
                vec = bytes_to_vector(row['vector'])
                results.append((row['entry_id'], vec))
            except Exception as ex:
                logger.warning("Broken embedding #%d: %s", row['entry_id'], ex)

        return results

    def get_entries_without_embeddings(
        self,
        limit: int = 100
    ) -> list[Entry]:
        """Записи у которых ещё нет embedding."""
        conn = self._get_connection()
        rows = conn.execute(
            """
            SELECT e.* FROM entries e
            LEFT JOIN embeddings emb ON emb.entry_id = e.id
            WHERE e.is_archived = 0 AND emb.entry_id IS NULL
            ORDER BY e.created_at DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    # ══════════════════════════════════════════════
    #  Лог активности
    # ══════════════════════════════════════════════

    def add_activity(
        self,
        action: str,
        target: str = "",
        details: str = "",
        window_title: str = "",
        duration_sec: float = 0,
        timestamp: float = 0,
    ) -> int:
        """Добавить запись в лог активности."""
        now = timestamp if timestamp > 0 else time.time()
        with self._write_lock:
            conn = self._get_connection()
            cursor = conn.execute(
                """
                INSERT INTO activity_log
                    (action, target, details, window_title,
                     timestamp, duration_sec)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (action, target, details, window_title,
                 now, duration_sec)
            )
            conn.commit()
            return cursor.lastrowid

    def get_activity(
        self,
        hours: float = 24,
        action: Optional[str] = None,
        limit: int = 50,
    ) -> list[ActivityRecord]:
        """Получить лог активности за последние N часов."""
        conn = self._get_connection()
        since = time.time() - (hours * 3600)

        if action:
            rows = conn.execute(
                """
                SELECT * FROM activity_log
                WHERE timestamp > ? AND action = ?
                ORDER BY timestamp DESC LIMIT ?
                """,
                (since, action, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM activity_log
                WHERE timestamp > ?
                ORDER BY timestamp DESC LIMIT ?
                """,
                (since, limit)
            ).fetchall()

        return [self._row_to_activity(r) for r in rows]

    def get_activity_summary(self, hours: float = 8) -> dict:
        """
        Сводка активности за период.

        Возвращает: {
            "total_commands": int,
            "top_apps": [(name, minutes), ...],
            "top_actions": [(action, count), ...],
        }
        """
        conn = self._get_connection()
        since = time.time() - (hours * 3600)

        # Топ приложений по времени
        top_apps = conn.execute(
            """
            SELECT target, SUM(duration_sec) as total_sec
            FROM activity_log
            WHERE timestamp > ? AND action = 'app_switch'
                AND target != ''
            GROUP BY target
            ORDER BY total_sec DESC
            LIMIT 10
            """,
            (since,)
        ).fetchall()

        # Топ действий
        top_actions = conn.execute(
            """
            SELECT action, COUNT(*) as cnt
            FROM activity_log
            WHERE timestamp > ?
            GROUP BY action
            ORDER BY cnt DESC
            LIMIT 10
            """,
            (since,)
        ).fetchall()

        # Общее количество команд
        total = conn.execute(
            """
            SELECT COUNT(*) FROM activity_log
            WHERE timestamp > ? AND action = 'command'
            """,
            (since,)
        ).fetchone()[0]

        return {
            "total_commands": total,
            "top_apps": [
                (r['target'], round(r['total_sec'] / 60, 1))
                for r in top_apps
            ],
            "top_actions": [
                (r['action'], r['cnt'])
                for r in top_actions
            ],
        }
    
    def get_app_duration_stats(
        self, hours: float = 168,
    ) -> dict[str, dict]:
        """
        Статистика длительности по приложениям.

        Возвращает среднюю, суммарную длительность и число
        сессий для каждого приложения.

        Используется для голосовых ответов:
        "В среднем ты проводишь в VS Code 45 минут
        за сессию. Сегодня — уже 60 минут."

        Args:
            hours: период в часах (по умолчанию неделя)

        Returns: {
            "VS Code": {
                "avg_min": 45.2,
                "total_hours": 22.5,
                "sessions": 30,
            },
            ...
        }
        """
        conn = self._get_connection()
        since = time.time() - hours * 3600

        rows = conn.execute(
            """
            SELECT target,
                   AVG(duration_sec) as avg_sec,
                   SUM(duration_sec) as total_sec,
                   COUNT(*) as cnt
            FROM activity_log
            WHERE timestamp > ?
                AND action = 'app_switch'
                AND target != ''
                AND duration_sec > 5
            GROUP BY target
            HAVING cnt >= 3
            ORDER BY total_sec DESC
            """,
            (since,),
        ).fetchall()

        stats = {}
        for r in rows:
            stats[r["target"]] = {
                "avg_min": round(r["avg_sec"] / 60, 1),
                "total_hours": round(
                    r["total_sec"] / 3600, 1,
                ),
                "sessions": r["cnt"],
            }

        return stats

    # ══════════════════════════════════════════════
    #  Связи
    # ══════════════════════════════════════════════

    def link_entries(
        self,
        from_id: int,
        to_id: int,
        link_type: str = "related",
    ) -> None:
        """Связать две записи."""
        with self._write_lock:
            conn = self._get_connection()
            conn.execute(
                """
                INSERT OR IGNORE INTO entry_links
                    (from_id, to_id, link_type, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (from_id, to_id, link_type, time.time())
            )
            conn.commit()

    def get_related(self, entry_id: int) -> list[Entry]:
        """Получить связанные записи."""
        conn = self._get_connection()
        rows = conn.execute(
            """
            SELECT e.* FROM entries e
            JOIN entry_links l ON (l.to_id = e.id OR l.from_id = e.id)
            WHERE (l.from_id = ? OR l.to_id = ?)
                AND e.id != ?
                AND e.is_archived = 0
            """,
            (entry_id, entry_id, entry_id)
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    # ══════════════════════════════════════════════
    #  Утилиты
    # ══════════════════════════════════════════════

    @property
    def entry_count(self) -> int:
        conn = self._get_connection()
        return conn.execute(
            "SELECT COUNT(*) FROM entries WHERE is_archived=0"
        ).fetchone()[0]

    @property
    def activity_count(self) -> int:
        conn = self._get_connection()
        return conn.execute(
            "SELECT COUNT(*) FROM activity_log"
        ).fetchone()[0]

    def vacuum(self) -> None:
        """Сжать БД (вызывать изредка)."""
        with self._write_lock:
            conn = self._get_connection()
            conn.execute("VACUUM")
            logger.info("VACUUM выполнен")

    def backup(self, backup_path: Optional[str] = None) -> str:
        """Создать бэкап БД."""
        if not backup_path:
            ts = time.strftime("%Y%m%d_%H%M%S")
            backup_path = f"{self._db_path}.{ts}.bak"

        with self._write_lock:
            conn = self._get_connection()
            backup_conn = sqlite3.connect(backup_path)
            conn.backup(backup_conn)
            backup_conn.close()

        logger.info("Бэкап: %s", backup_path)
        return backup_path

    def close(self) -> None:
        """Закрыть соединение текущего потока."""
        if hasattr(self._local, 'conn') and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

    @staticmethod
    def _row_to_entry(row) -> Entry:
        return Entry(
            id=row['id'],
            content=row['content'],
            entry_type=row['entry_type'],
            source=row['source'],
            tags=row['tags'],
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            is_archived=row['is_archived'],
            importance=row['importance'],
        )

    @staticmethod
    def _row_to_activity(row) -> ActivityRecord:
        return ActivityRecord(
            id=row['id'],
            action=row['action'],
            target=row['target'],
            details=row['details'],
            window_title=row['window_title'],
            timestamp=row['timestamp'],
            duration_sec=row['duration_sec'],
        )

    # ══════════════════════════════════════════════
    #  Паттерны и предложения
    # ══════════════════════════════════════════════

    def ensure_pattern_tables(self) -> None:
        """
        Создать таблицы для системы паттернов.

        v2: добавлены колонки context и shown_at.
        ALTER TABLE с try/except — идемпотентно,
        безопасно вызывать повторно на старых БД.
        """
        with self._write_lock:
            conn = self._get_connection()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS suggestion_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    suggestion_id TEXT NOT NULL,
                    suggestion_type TEXT NOT NULL DEFAULT '',
                    pattern_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    confidence REAL DEFAULT 0,
                    context TEXT DEFAULT '',
                    shown_at REAL DEFAULT 0,
                    created_at REAL NOT NULL,
                    resolved_at REAL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_feedback_pattern
                    ON suggestion_feedback(pattern_id);
                CREATE INDEX IF NOT EXISTS idx_feedback_created
                    ON suggestion_feedback(created_at);
                CREATE INDEX IF NOT EXISTS idx_feedback_status
                    ON suggestion_feedback(status);
            """)

            # Миграция существующих таблиц:
            # добавляем новые колонки, если их ещё нет
            for col, col_type in [
                ("context", "TEXT DEFAULT ''"),
                ("shown_at", "REAL DEFAULT 0"),
            ]:
                try:
                    conn.execute(
                        f"ALTER TABLE suggestion_feedback "
                        f"ADD COLUMN {col} {col_type}"
                    )
                    logger.info(
                        "Migration: added suggestion_feedback.%s", col,
                    )
                except sqlite3.OperationalError:
                    pass  # Колонка уже существует

            conn.commit()

    def add_suggestion_feedback(
        self,
        suggestion_id: str,
        suggestion_type: str,
        pattern_id: str,
        status: str,
        confidence: float = 0,
        context: str = "",
        shown_at: float = 0,
    ) -> None:
        """
        Записать реакцию пользователя на предложение.

        Args:
            context:  JSON-строка с context_snapshot
            shown_at: Время показа предложения (unix timestamp)
        """
        now = time.time()
        with self._write_lock:
            conn = self._get_connection()
            conn.execute(
                """
                INSERT INTO suggestion_feedback
                    (suggestion_id, suggestion_type, pattern_id,
                     status, confidence, context, shown_at,
                     created_at, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (suggestion_id, suggestion_type, pattern_id,
                 status, confidence, context, shown_at,
                 now, now),
            )
            conn.commit()

    def get_rejected_patterns(
        self, hours: float = 24,
    ) -> list[tuple[str, float]]:
        """
        Получить pattern_id'ы, отвергнутые за последние N часов.

        Returns: [(pattern_id, timestamp), ...]
        """
        conn = self._get_connection()
        since = time.time() - (hours * 3600)
        rows = conn.execute(
            """
            SELECT pattern_id, MAX(created_at) as last_rejected
            FROM suggestion_feedback
            WHERE status = 'rejected' AND created_at > ?
            GROUP BY pattern_id
            """,
            (since,),
        ).fetchall()
        return [(r["pattern_id"], r["last_rejected"]) for r in rows]

    def get_contextual_rejections(
        self, pattern_id: str, hours: float = 24,
    ) -> list[str]:
        """
        Получить JSON-контексты отклонённых предложений для паттерна.

        Используется SuggestionManager для анализа:
        "отказывался ли пользователь от этого паттерна
        в похожем контексте?"

        Returns:
            Список JSON-строк с context_snapshot'ами отказов.
            Пустые контексты (от старых записей до миграции)
            отфильтрованы.
        """
        conn = self._get_connection()
        since = time.time() - (hours * 3600)
        rows = conn.execute(
            """
            SELECT context FROM suggestion_feedback
            WHERE pattern_id = ?
                AND status = 'rejected'
                AND created_at > ?
                AND context != ''
            ORDER BY created_at DESC
            LIMIT 10
            """,
            (pattern_id, since),
        ).fetchall()
        return [r["context"] for r in rows]

    def get_feedback_stats(self, hours: float = 168) -> dict:
        """
        Статистика предложений за период (по умолчанию 7 дней).

        Returns: {accepted, rejected, ignored, total, acceptance_rate}
        """
        conn = self._get_connection()
        since = time.time() - (hours * 3600)
        rows = conn.execute(
            """
            SELECT status, COUNT(*) as cnt
            FROM suggestion_feedback
            WHERE created_at > ?
            GROUP BY status
            """,
            (since,),
        ).fetchall()

        stats = {r["status"]: r["cnt"] for r in rows}
        total = sum(stats.values())
        accepted = stats.get("accepted", 0)

        return {
            "accepted": accepted,
            "rejected": stats.get("rejected", 0),
            "ignored": stats.get("ignored", 0),
            "total": total,
            "acceptance_rate": accepted / total if total else 0,
        }