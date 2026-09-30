# scripts/migrate_context.py

"""
Миграция: get_context() → self.ctx.context
Безопасно — оба указывают на один объект (через set_instance).
"""

import os
import re

FILES = [
    "commands/activity_stats.py",
    "commands/apps.py",
    "commands/create_macro.py",
    "commands/habits.py",
    "commands/info.py",
    "commands/knowledge_cmd.py",
    "commands/macro.py",
    "commands/media.py",
    "commands/music.py",
    "commands/smart.py",
    "commands/telegram_cmd.py",
    "commands/vscode_cmd.py",
    "commands/web.py",
]

def migrate_file(path: str) -> bool:
    if not os.path.exists(path):
        print(f"  ⚠ Не найден: {path}")
        return False

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    original = content

    # 1. Убрать импорт
    content = re.sub(
        r'^from core\.context import get_context\s*\n',
        '',
        content,
        flags=re.MULTILINE,
    )

    # 2. Заменить вызовы get_context()
    # get_context().get() → self.ctx.context.get()
    content = content.replace('get_context().get()', 'self.ctx.context.get()')

    # get_context().update(...) → self.ctx.context.update(...)
    content = content.replace('get_context().update(', 'self.ctx.context.update(')

    # get_context().clear() → self.ctx.context.clear()
    content = content.replace('get_context().clear()', 'self.ctx.context.clear()')

    # Ловим оставшиеся get_context()
    content = content.replace('get_context()', 'self.ctx.context')

    if content == original:
        print(f"  ○ Без изменений: {path}")
        return False

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"  ✓ Мигрирован: {path}")
    return True


def main():
    print("Миграция get_context() → self.ctx.context")
    print("=" * 50)

    changed = 0
    for path in FILES:
        if migrate_file(path):
            changed += 1

    print(f"\nИтого: {changed} файлов обновлено")
    print("\nget_context() по-прежнему работает (обратная совместимость),")
    print("но новый код должен использовать self.ctx.context")


if __name__ == "__main__":
    main()