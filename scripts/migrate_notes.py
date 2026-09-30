#!/usr/bin/env python3
"""
Миграция заметок из notes.json в базу знаний.

Запуск: python scripts/migrate_notes.py

Безопасно запускать несколько раз — дубликаты не создаются.
"""

import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.knowledge.storage import KnowledgeStorage


def migrate():
    notes_file = "data/notes.json"

    if not os.path.exists(notes_file):
        print(f"Файл {notes_file} не найден. Нечего мигрировать.")
        return

    # Загружаем старые заметки
    with open(notes_file, "r", encoding="utf-8") as f:
        notes = json.load(f)

    if not notes:
        print("Заметок нет.")
        return

    print(f"Найдено {len(notes)} заметок в {notes_file}")

    # Открываем базу знаний
    storage = KnowledgeStorage("data/knowledge.db")

    # Проверяем существующие записи чтобы не дублировать
    existing = storage.get_recent(1000)
    existing_texts = {e.content.lower().strip() for e in existing}

    migrated = 0
    skipped = 0

    for note in notes:
        text = note.get("text", "").strip()
        if not text:
            continue

        # Проверяем дубликат
        if text.lower().strip() in existing_texts:
            skipped += 1
            continue

        # Определяем время создания
        time_str = note.get("time", "")
        if time_str:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(time_str)
                created_at = dt.timestamp()
            except Exception:
                created_at = time.time()
        else:
            created_at = time.time()

        # Добавляем в базу
        storage.add_entry(
            content=text,
            entry_type="note",
            source="import",
            tags="migrated",
        )
        migrated += 1

    storage.close()

    print(f"\nРезультат:")
    print(f"  Мигрировано: {migrated}")
    print(f"  Пропущено (дубликаты): {skipped}")

    if migrated > 0:
        # Переименовываем старый файл
        backup = notes_file + ".migrated.bak"
        os.rename(notes_file, backup)
        print(f"  Старый файл → {backup}")

    print("\nГотово!")


if __name__ == "__main__":
    migrate()