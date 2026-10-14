"""Read-only RichEdit adapter available in Windows, without browser dependencies."""
import ctypes
from ctypes import wintypes as w


def repaint_on_scroll(window, hwnd):
    """RichEdit scrolls cached pixels, including stale table cells in its margins.

    Invalidate the entire control after a scroll, rather than only the newly
    exposed strip. Keep the callback alive until the child is destroyed.
    """
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT,
                                      w.WPARAM, w.LPARAM, ctypes.c_size_t, ctypes.c_size_t)
    common = window.common
    common.SetWindowSubclass.argtypes = [w.HWND, callback_type, ctypes.c_size_t, ctypes.c_size_t]
    common.SetWindowSubclass.restype = w.BOOL
    common.RemoveWindowSubclass.argtypes = [w.HWND, callback_type, ctypes.c_size_t]
    common.DefSubclassProc.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    common.DefSubclassProc.restype = ctypes.c_ssize_t

    @callback_type
    def handler(handle, message, wp, lp, identifier, data):
        if message == 0x82:  # WM_NCDESTROY
            common.RemoveWindowSubclass(handle, handler, identifier)
        result = common.DefSubclassProc(handle, message, wp, lp)
        if message in {0x115, 0x20A, 0x20E, 0xB5, 0xB6, 0x4DE, 0x100}:
            # Scrollbar, wheel, programmatic scroll and keyboard navigation.
            window.user.RedrawWindow(handle, None, None, 0x105)
        return result

    if not common.SetWindowSubclass(hwnd, handler, 1, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return handler


def stream_rtf(send, hwnd, data: bytes):
    callback_type = ctypes.WINFUNCTYPE(w.DWORD, ctypes.c_size_t, w.LPVOID, w.LONG, ctypes.POINTER(w.LONG))

    class EditStream(ctypes.Structure):
        _pack_ = 4  # richedit.h uses four-byte packing, including on x64
        _fields_ = [("cookie", ctypes.c_size_t), ("error", w.DWORD), ("callback", callback_type)]

    position = 0

    @callback_type
    def read(cookie, buffer, size, written):
        nonlocal position
        chunk = data[position:position + max(0, size)]
        if chunk:
            ctypes.memmove(buffer, chunk, len(chunk))
        written[0] = len(chunk)
        position += len(chunk)
        return 0

    stream = EditStream(0, 0, read)
    send(hwnd, 0x449, 2, ctypes.addressof(stream))  # EM_STREAMIN / SF_RTF
    if stream.error:
        raise OSError(f"RichEdit could not load description ({stream.error}).")
