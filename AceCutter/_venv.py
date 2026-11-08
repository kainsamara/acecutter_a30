"""Самоподъём .venv для скриптов корня (launch.py, build_exe.py).

Юзер дёргает `py launch.py` / `py build_exe.py` ЛЮБЫМ питоном — скрипт
сам находит/создаёт/лечит .venv и перезапускает себя им. Заодно лечит
переезд папки: протухший editable-.pth ловится health-check'ом импорта
и чинится переустановкой `pip install -e`.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_BIN = "Scripts" if sys.platform == "win32" else "bin"
VENV_PY = ROOT / ".venv" / _BIN / ("python.exe" if sys.platform == "win32" else "python")


def ensure_venv(extras: str, check_imports: str) -> None:
    """Не в .venv — поднять/полечить его и перезапуститься уже им."""
    if getattr(sys, "frozen", False):
        return  # собранный exe: venv не нужен и невозможен
    if Path(sys.executable).resolve() == VENV_PY.resolve():
        return  # уже в правильном питоне — работаем

    need_install = True
    if VENV_PY.exists():
        r = subprocess.run([str(VENV_PY), "-c", check_imports],
                           capture_output=True, cwd=ROOT)
        need_install = r.returncode != 0  # протух (переезд?) — переустановим
    else:
        print(">> .venv нет — создаю (разово, ~минута)...", flush=True)
        r = subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")])
        if r.returncode != 0 or not VENV_PY.exists():
            sys.exit("!! не смог создать .venv — смотри ошибки выше")

    if need_install:
        print(f">> ставлю зависимости в .venv: .[gui,{extras}] ...", flush=True)
        r = subprocess.run(
            [str(VENV_PY), "-m", "pip", "install", "-e", f".[gui,{extras}]"],
            cwd=ROOT)
        if r.returncode != 0:
            sys.exit("!! pip install упал — смотри вывод выше")

    print(f">> перезапускаюсь venv-питоном: {' '.join(sys.argv)}", flush=True)
    raise SystemExit(subprocess.run([str(VENV_PY), *sys.argv], cwd=ROOT).returncode)
