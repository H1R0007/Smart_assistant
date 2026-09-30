# start.py

"""
Единая точка запуска: ядро + GUI.
Запускает main.py и gui_app.py как два процесса.
Завершение одного завершает другой.
"""

import subprocess
import sys
import os
import time
import signal

def main():
    python = sys.executable
    project_dir = os.path.dirname(os.path.abspath(__file__))

    # Запускаем ядро
    core_proc = subprocess.Popen(
        [python, os.path.join(project_dir, "main.py")],
        cwd=project_dir,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )

    # Ждём пока ядро поднимет IPC (WebSocket на 8765)
    time.sleep(3)

    # Запускаем GUI
    gui_proc = subprocess.Popen(
        [python, os.path.join(project_dir, "gui_app.py")],
        cwd=project_dir,
    )

    # Ждём завершения любого из процессов
    try:
        while True:
            # Проверяем оба процесса
            core_ret = core_proc.poll()
            gui_ret = gui_proc.poll()

            if core_ret is not None:
                # Ядро завершилось — останавливаем GUI
                print("Ядро завершено, останавливаем GUI...")
                gui_proc.terminate()
                break

            if gui_ret is not None:
                # GUI завершилось — ядро продолжает (трей)
                # Или останавливаем тоже:
                print("GUI завершено, останавливаем ядро...")
                core_proc.terminate()
                break

            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\nЗавершение...")
        core_proc.terminate()
        gui_proc.terminate()

    # Ждём завершения
    core_proc.wait(timeout=5)
    gui_proc.wait(timeout=5)


if __name__ == "__main__":
    main()