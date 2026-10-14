"""Application-owned Windows icons, released after their window is destroyed."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
from pathlib import Path


def load_icon(user, width: int, height: int):
    user.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT]
    user.LoadImageW.restype = w.HANDLE
    user.DestroyIcon.argtypes, user.DestroyIcon.restype = [w.HICON], w.BOOL
    # IMAGE_ICON, LR_LOADFROMFILE. Never LR_SHARED: file icons need explicit disposal.
    return user.LoadImageW(None, str(Path(__file__).with_name('app.ico')), 1, width, height, 0x10)


class WindowIcons:
    def __init__(self, window):
        self.user = window.user
        self.handles = []
        self.user.GetSystemMetrics.argtypes, self.user.GetSystemMetrics.restype = [ctypes.c_int], ctypes.c_int
        for kind, xmetric, ymetric, fallback in ((0, 49, 50, 16), (1, 11, 12, 32)):
            icon = load_icon(self.user, self.user.GetSystemMetrics(xmetric) or fallback,
                             self.user.GetSystemMetrics(ymetric) or fallback)
            if icon:
                self.handles.append(icon)
                window.send(window.hwnd, 0x80, kind, icon)  # WM_SETICON

    def close(self):
        for handle in self.handles:
            self.user.DestroyIcon(handle)
        self.handles.clear()
