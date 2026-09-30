# gui_app.py

"""Точка входа для GUI-приложения."""

import sys
from PySide6.QtWidgets import QApplication
from core.gui.theme import apply_theme
from core.gui.app import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    apply_theme(app)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()