# PyInstaller recipe for "Polyglot Agent.app": a Mac app that needs no Python. It opens in
# its own window (WebKit, through pywebview + pyobjc). Build on a Mac: sh tools/build_mac.sh
# (GitHub Actions does this for Apple Silicon and Intel; see .github/workflows/build-exe.yml).
# -*- mode: python ; coding: utf-8 -*-
import re

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', open("app/__init__.py", encoding="utf-8").read()).group(1)

hidden = (collect_submodules("uvicorn") + collect_submodules("websockets")
          + ["PIL.Image", "webview.platforms.cocoa"])
datas = [("app/static", "app/static")] + collect_data_files("webview")

a = Analysis(
    ["polyglot_agent.py"],
    pathex=["."],
    datas=datas,
    hiddenimports=hidden,
    excludes=["pytest", "tkinter", "mss", "pyautogui", "clr", "pythonnet"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Polyglot Agent",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Polyglot Agent", upx=False)
app = BUNDLE(
    coll,
    name="Polyglot Agent.app",
    icon="app/static/icons/icon-512.png",      # PyInstaller turns it into an .icns (needs Pillow)
    bundle_identifier="io.github.sorin-42.polyglot-agent",
    version=VERSION,
    info_plist={
        "CFBundleDisplayName": "Polyglot Agent",
        "CFBundleShortVersionString": VERSION,
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.productivity",
    },
)
