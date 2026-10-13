# PyInstaller recipe for a standalone, single-file PolyglotAgent.exe that needs
# no Python on the target PC. Build on Windows:  powershell -File tools\build_exe.ps1
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hidden = (collect_submodules("uvicorn") + collect_submodules("websockets")
          + ["pyautogui", "mss", "mss.windows", "PIL.Image"])

a = Analysis(
    ["polyglot_agent.py"],
    pathex=["."],
    datas=[("app/static", "app/static")],
    hiddenimports=hidden,
    excludes=["pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="PolyglotAgent",
    console=False,            # no console window
    icon="launcher/icon.ico",
    upx=False,
)
