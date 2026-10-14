"""Borderless native read-only labels: mouse selection, Copy menu and Ctrl+A."""
import ctypes
from ctypes import wintypes as w

READONLY_LABEL = 0x844  # ES_READONLY | ES_MULTILINE | ES_AUTOVSCROLL, no border


def enable_select_all(window, hwnd):
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT,
                                      w.WPARAM, w.LPARAM, ctypes.c_size_t, ctypes.c_size_t)
    common = window.common
    common.SetWindowSubclass.argtypes = [w.HWND, callback_type, ctypes.c_size_t, ctypes.c_size_t]
    common.SetWindowSubclass.restype = w.BOOL
    common.RemoveWindowSubclass.argtypes = [w.HWND, callback_type, ctypes.c_size_t]
    common.DefSubclassProc.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    common.DefSubclassProc.restype = ctypes.c_ssize_t
    window.user.GetKeyState.argtypes, window.user.GetKeyState.restype = [ctypes.c_int], ctypes.c_short

    @callback_type
    def handler(handle, message, wp, lp, identifier, data):
        if message == 0x82:  # WM_NCDESTROY
            common.RemoveWindowSubclass(handle, handler, identifier)
        if message == 0x100 and wp == 0x41 and window.user.GetKeyState(0x11) < 0:
            window.send(handle, 0xB1, 0, -1)  # EM_SETSEL, Ctrl+A
            return 0
        return common.DefSubclassProc(handle, message, wp, lp)

    if not common.SetWindowSubclass(hwnd, handler, 2, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return handler
