"""Replace the icon inside an existing Windows exe (no compiler needed).

    .venv\\Scripts\\python tools\\set_exe_icon.py PolyglotAgent.exe launcher\\icon.ico

launcher/build.sh embeds the icon when the launcher is compiled; this updates
an already built exe in place with the Win32 resource-update API, keeping its
other resources (version info) and their language.
"""
from __future__ import annotations

import ctypes
import struct
import sys
from ctypes import wintypes

RT_ICON, RT_GROUP_ICON = 3, 14
GROUP_ID = 1
LOAD_LIBRARY_AS_DATAFILE = 0x2

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.BeginUpdateResourceW.restype = wintypes.HANDLE
k32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
k32.UpdateResourceW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wintypes.WORD,
                                ctypes.c_void_p, wintypes.DWORD]
k32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
k32.LoadLibraryExW.restype = wintypes.HMODULE
k32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
k32.FreeLibrary.argtypes = [wintypes.HMODULE]
EnumLangProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p,
                                  wintypes.WORD, ctypes.c_void_p)
k32.EnumResourceLanguagesW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, EnumLangProc,
                                       ctypes.c_void_p]


def read_ico(path: str) -> list[tuple[bytes, bytes]]:
    """[(12-byte entry header without size/offset, image bytes)] from an .ico file."""
    data = open(path, "rb").read()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or kind != 1:
        raise ValueError(f"{path} is not an .ico file")
    out = []
    for i in range(count):
        head = data[6 + 16 * i: 6 + 16 * i + 8]        # w, h, colors, reserved, planes, bpp
        size, offset = struct.unpack_from("<II", data, 6 + 16 * i + 8)
        out.append((head, data[offset:offset + size]))
    return out


def group_language(exe: str) -> int:
    """Language of the exe's existing icon group, so we replace it instead of adding a second one."""
    found: list[int] = []
    mod = k32.LoadLibraryExW(exe, None, LOAD_LIBRARY_AS_DATAFILE)
    if not mod:
        return 0
    try:
        cb = EnumLangProc(lambda h, t, n, lang, p: found.append(lang) or True)
        k32.EnumResourceLanguagesW(mod, RT_GROUP_ICON, GROUP_ID, cb, None)
    finally:
        k32.FreeLibrary(mod)
    return found[0] if found else 0


def set_icon(exe: str, ico: str) -> int:
    images = read_ico(ico)
    lang = group_language(exe)
    group = struct.pack("<HHH", 0, 1, len(images))
    for n, (head, img) in enumerate(images, start=1):
        group += head + struct.pack("<IH", len(img), n)
    h = k32.BeginUpdateResourceW(exe, False)
    if not h:
        raise ctypes.WinError(ctypes.get_last_error())
    ok = True
    for n, (_, img) in enumerate(images, start=1):
        buf = ctypes.create_string_buffer(img, len(img))
        ok &= bool(k32.UpdateResourceW(h, RT_ICON, n, lang, buf, len(img)))
    buf = ctypes.create_string_buffer(group, len(group))
    ok &= bool(k32.UpdateResourceW(h, RT_GROUP_ICON, GROUP_ID, lang, buf, len(group)))
    if not k32.EndUpdateResourceW(h, not ok) or not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    return len(images)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(f"{sys.argv[1]}: replaced icon with {set_icon(sys.argv[1], sys.argv[2])} sizes")
