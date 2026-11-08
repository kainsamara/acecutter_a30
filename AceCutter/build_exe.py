"""Компактная сборка: один dist/AceCutter.exe, запуск с одного файла.

    py build_exe.py        # любым питоном — venv поднимет себя сам

--onefile: всё (включая Qt) пакуется в exe, при старте распаковывается
во temp — первый запуск ~2-4 с, это цена одного файла, не баг.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import _venv

ROOT = Path(__file__).resolve().parent

# В venv стоит метапакет PySide6 целиком (Addons/WebEngine/Pdf...).
# Код использует только QtWidgets/QtGui/QtCore — остальное отсекаем
# явно: иначе анализ тащит сотни МБ мусора в exe.
EXCLUDES = [
    "tkinter",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQml",
    "PySide6.QtPositioning",
    "PySide6.QtSensors",
    "PySide6.QtTest",
]

ENTRY = ROOT / "launch.py"  # НЕ app.py: relative imports требуют пакет (см. launch.py)
ICON_PNG = ROOT / "src" / "acecutter" / "icon.png"


def make_ico() -> Path:
    """icon.png -> build/acecutter.ico: иконка самого exe в проводнике.

    Нужна Pillow (extra `build`); PyInstaller на Windows просит .ico,
    png не принимает.
    """
    from PIL import Image

    ico = ROOT / "build" / "acecutter.ico"
    ico.parent.mkdir(exist_ok=True)
    with Image.open(ICON_PNG) as im:
        im.save(ico, format="ICO",
                sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                       (64, 64), (128, 128), (256, 256)])
    return ico


def main() -> int:
    _venv.ensure_venv(extras="build",
                      check_imports="import acecutter, PySide6, PIL, PyInstaller")
    ico = make_ico()
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile", "--windowed",
        "--name", "AceCutter",
        "--icon", str(ico),
        # иконка окна/таскбара: в bundle кладём рядом с пакетом,
        # путь Path(__file__).parent/"icon.png" совпадает и в venv, и в exe
        "--add-data", f"{ICON_PNG};acecutter",
        "--paths", str(ROOT / "src"),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
    ]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd.append(str(ENTRY))

    print(">> PyInstaller:", " ".join(cmd[2:]), flush=True)
    # PyInstaller пишет прогресс в stdout — не глотаем его
    proc = subprocess.run(cmd, cwd=ROOT)
    if proc.returncode != 0:
        print("!! сборка упала", file=sys.stderr)
        return 1

    exe = ROOT / "dist" / "AceCutter.exe"
    if not exe.exists():
        print(f"!! {exe} не появился", file=sys.stderr)
        return 1
    print(f">> OK: {exe} ({exe.stat().st_size / 1_048_576:.1f} МиБ)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
