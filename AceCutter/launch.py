"""Лаунчер GUI: `py launch.py` любым питоном — venv поднимет себя сам.

PyInstaller-у отдаётся тоже этот файл: НЕ src/acecutter/app.py — тот
станет топ-левел скриптом `app`, и relative import
(`from .ui...`) умрёт с «attempted relative import with no known
parent package». Здесь пакет импортируется честно.
"""
import _venv

_venv.ensure_venv(extras="gui", check_imports="import acecutter, PySide6")

from acecutter.app import main  # noqa: E402 — строго после bootstrap'а

if __name__ == "__main__":
    raise SystemExit(main())
