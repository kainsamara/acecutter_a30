"""Точка входа GUI."""
from __future__ import annotations

import sys


def main() -> int:
    from pathlib import Path

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    # Иконка окна/таскбара. Путь от __file__ пакета: в venv — исходник,
    # в PyInstaller — распакованный bundle (--add-data кладёт туда же).
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "icon.png")))
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
