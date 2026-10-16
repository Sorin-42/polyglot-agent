"""Native Windows "Select folder" dialog (IFileOpenDialog with FOS_PICKFOLDERS).

The UI is a web page, which can't learn real folder paths, so the server opens the
dialog for it. Called from a worker thread; the dialog is modal to the window in
front (the app window, since the user just clicked Browse) so it opens on top.
"""
from __future__ import annotations

import ctypes
import os
import sys
import uuid
from typing import Optional

IS_WINDOWS = sys.platform == "win32"


class PickerUnavailable(Exception):
    """No native folder dialog on this system."""


if IS_WINDOWS:
    from ctypes import wintypes

    class _GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

        def __init__(self, text: str):
            u = uuid.UUID(text)
            super().__init__(u.time_low, u.time_mid, u.time_hi_version,
                             (ctypes.c_ubyte * 8)(*u.bytes[8:]))

    CLSID_FileOpenDialog = _GUID("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")
    IID_IFileOpenDialog = _GUID("D57C7288-D4AD-4768-BE02-9D969532D960")
    IID_IShellItem = _GUID("43826D1E-E718-42EE-BC55-A1E261C37BFE")

    # vtable slots (IUnknown 0-2, IModalWindow 3, then IFileDialog)
    _RELEASE, _SHOW, _SET_OPTIONS, _GET_OPTIONS, _SET_FOLDER, _SET_TITLE, _GET_RESULT = 2, 3, 9, 10, 12, 17, 20
    _GET_DISPLAY_NAME = 5            # IShellItem
    FOS_PICKFOLDERS, FOS_FORCEFILESYSTEM, FOS_PATHMUSTEXIST = 0x20, 0x40, 0x800
    SIGDN_FILESYSPATH = ctypes.c_int(0x80058000 - (1 << 32)).value
    ERROR_CANCELLED = ctypes.c_long(0x800704C7 - (1 << 32)).value
    COINIT_APARTMENTTHREADED, COINIT_DISABLE_OLE1DDE, CLSCTX_INPROC_SERVER = 0x2, 0x4, 0x1

    _ole32 = ctypes.windll.ole32
    _shell32 = ctypes.windll.shell32
    _u32 = ctypes.windll.user32
    _ole32.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
    _ole32.CoCreateInstance.restype = ctypes.c_long
    _ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    _shell32.SHCreateItemFromParsingName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p,
                                                     ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
    _shell32.SHCreateItemFromParsingName.restype = ctypes.c_long
    _u32.GetForegroundWindow.restype = wintypes.HWND

    def _call(obj: ctypes.c_void_p, slot: int, *args, argtypes=()):
        """Call COM method `slot` of the interface pointer `obj`; returns the HRESULT."""
        vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[slot])
        return fn(obj, *args)

    def _check(hr: int, what: str) -> None:
        if hr < 0:
            raise OSError(f"{what} failed (0x{hr & 0xFFFFFFFF:08X})")


def pick_folder(title: str = "", initial: Optional[str] = None) -> Optional[str]:
    """Show the dialog and return the chosen folder, or None if the user cancelled."""
    if not IS_WINDOWS:
        raise PickerUnavailable("Folder browsing is only available on Windows.")
    co = _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE)
    dlg, item = ctypes.c_void_p(), ctypes.c_void_p()
    try:
        _check(_ole32.CoCreateInstance(ctypes.byref(CLSID_FileOpenDialog), None, CLSCTX_INPROC_SERVER,
                                       ctypes.byref(IID_IFileOpenDialog), ctypes.byref(dlg)),
               "CoCreateInstance")
        opts = wintypes.DWORD()
        _check(_call(dlg, _GET_OPTIONS, ctypes.byref(opts), argtypes=(ctypes.POINTER(wintypes.DWORD),)),
               "GetOptions")
        _check(_call(dlg, _SET_OPTIONS, opts.value | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST,
                     argtypes=(wintypes.DWORD,)), "SetOptions")
        if title:
            _call(dlg, _SET_TITLE, title, argtypes=(wintypes.LPCWSTR,))
        start = initial
        while start and not os.path.isdir(start):      # open at the nearest folder that exists
            parent = os.path.dirname(start.rstrip("\\/"))
            start = parent if parent and parent != start else None
        if start:
            folder = ctypes.c_void_p()
            if _shell32.SHCreateItemFromParsingName(start, None, ctypes.byref(IID_IShellItem),
                                                    ctypes.byref(folder)) >= 0:
                _call(dlg, _SET_FOLDER, folder, argtypes=(ctypes.c_void_p,))
                _call(folder, _RELEASE)
        hr = _call(dlg, _SHOW, _u32.GetForegroundWindow(), argtypes=(wintypes.HWND,))
        if hr == ERROR_CANCELLED:
            return None
        _check(hr, "Show")
        _check(_call(dlg, _GET_RESULT, ctypes.byref(item), argtypes=(ctypes.POINTER(ctypes.c_void_p),)),
               "GetResult")
        name = ctypes.c_void_p()
        _check(_call(item, _GET_DISPLAY_NAME, SIGDN_FILESYSPATH, ctypes.byref(name),
                     argtypes=(ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))), "GetDisplayName")
        try:
            return ctypes.wstring_at(name.value)
        finally:
            _ole32.CoTaskMemFree(name)
    finally:
        for obj in (item, dlg):
            if obj.value:
                _call(obj, _RELEASE)
        if co >= 0:
            _ole32.CoUninitialize()
