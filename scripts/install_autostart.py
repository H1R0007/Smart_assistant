# scripts/install_autostart.py

"""
Добавить ярлык автозапуска в shell:startup.
python scripts/install_autostart.py [--remove]
"""

import sys
import os
from pathlib import Path


def get_startup_folder() -> Path:
    return Path(os.environ["APPDATA"]) / (
        r"Microsoft\Windows\Start Menu\Programs\Startup"
    )


def create_shortcut():
    try:
        import winshell
    except ImportError:
        # Fallback: создаём .bat
        return create_bat()

    project_dir = Path(__file__).resolve().parent.parent
    python_exe = sys.executable
    target_script = project_dir / "start.py"

    shortcut_path = str(get_startup_folder() / "Пятница.lnk")

    winshell.CreateShortcut(
        Path=shortcut_path,
        Target=python_exe,
        Arguments=f'"{target_script}"',
        StartIn=str(project_dir),
        Description="Пятница — Голосовой ассистент",
    )
    print(f"✅ Ярлык создан: {shortcut_path}")
    return True


def create_bat():
    """Fallback: .bat файл вместо .lnk."""
    project_dir = Path(__file__).resolve().parent.parent
    python_exe = sys.executable

    bat_content = f'''@echo off
cd /d "{project_dir}"
start "" "{python_exe}" "{project_dir / 'start.py'}"
'''

    bat_path = get_startup_folder() / "Пятница.bat"
    bat_path.write_text(bat_content, encoding="utf-8")
    print(f"✅ Bat-файл создан: {bat_path}")
    return True


def remove():
    for name in ("Пятница.lnk", "Пятница.bat"):
        path = get_startup_folder() / name
        if path.exists():
            path.unlink()
            print(f"✅ Удалено: {path}")


if __name__ == "__main__":
    if "--remove" in sys.argv:
        remove()
    else:
        create_shortcut()