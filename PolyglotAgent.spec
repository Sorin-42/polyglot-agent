# PyInstaller recipe for a standalone, single-file PolyglotAgent.exe that needs
# no Python on the target PC. Build on Windows:  powershell -File tools\build_exe.ps1
# The app window uses WebView2 (part of Windows 10/11) through pywebview + pythonnet.
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hidden = (collect_submodules("uvicorn") + collect_submodules("websockets")
          + ["pyautogui", "mss", "mss.windows", "PIL.Image",
             "webview.platforms.winforms", "webview.platforms.edgechromium", "clr"])
datas = ([("app/static", "app/static")] + collect_data_files("webview")
         + collect_data_files("pythonnet") + collect_data_files("clr_loader"))

a = Analysis(
    ["polyglot_agent.py"],
    pathex=["."],
    datas=datas,
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
