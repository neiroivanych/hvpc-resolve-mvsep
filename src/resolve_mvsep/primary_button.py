"""Muted blue native primary button, retaining native focus and click behavior."""
import ctypes
from ctypes import wintypes as w


class Header(ctypes.Structure):
    _fields_ = [("hwnd", w.HWND), ("identifier", ctypes.c_size_t), ("code", w.UINT)]


class CustomDraw(ctypes.Structure):
    _fields_ = [("header", Header), ("stage", w.DWORD), ("dc", w.HDC),
                ("rect", w.RECT), ("item", ctypes.c_size_t), ("state", w.UINT), ("data", w.LPARAM)]


class DrawItem(ctypes.Structure):
    _fields_ = [("kind", w.UINT), ("identifier", w.UINT), ("item", w.UINT),
                ("action", w.UINT), ("state", w.UINT), ("hwnd", w.HWND),
                ("dc", w.HDC), ("rect", w.RECT), ("data", ctypes.c_size_t)]


def draw_notification(window, message, pointer):
    if not pointer:
        return None
    window.user.IsWindowEnabled.argtypes = [w.HWND]
    window.user.IsWindowEnabled.restype = w.BOOL
    disabled = not window.user.IsWindowEnabled(window.controls["start"])
    if message == 0x4E:  # WM_NOTIFY / NM_CUSTOMDRAW keeps default-button semantics.
        header = ctypes.cast(pointer, ctypes.POINTER(Header)).contents
        if header.hwnd != window.controls["start"] or header.code != (-12 & 0xffffffff):
            return None
        info = ctypes.cast(pointer, ctypes.POINTER(CustomDraw)).contents
        if info.stage != 1:  # CDDS_PREPAINT
            return 0
        paint(window, info.dc, info.rect, disabled=disabled,
              pressed=bool(info.state & 1), hot=bool(info.state & 0x40), focused=bool(info.state & 0x10),
              hide_focus=bool(info.state & 0x100))
        return 4  # CDRF_SKIPDEFAULT
    if message == 0x2B:  # WM_DRAWITEM fallback if visual-style activation is unavailable.
        info = ctypes.cast(pointer, ctypes.POINTER(DrawItem)).contents
        if info.hwnd != window.controls["start"]:
            return None
        paint(window, info.dc, info.rect, disabled=disabled,
              pressed=bool(info.state & 1), hot=bool(info.state & 0x40), focused=bool(info.state & 0x10),
              hide_focus=bool(info.state & 0x200))
        return 1
    return None


def paint(window, dc, rect, *, disabled, pressed, hot, focused, hide_focus):
    g, u = window.gdi, window.user
    g.SaveDC.argtypes, g.SaveDC.restype = [w.HDC], ctypes.c_int
    g.RestoreDC.argtypes = [w.HDC, ctypes.c_int]
    g.RoundRect.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    g.SetBkMode.argtypes = [w.HDC, ctypes.c_int]
    u.DrawTextW.argtypes = [w.HDC, w.LPCWSTR, ctypes.c_int, ctypes.POINTER(w.RECT), w.UINT]
    u.DrawFocusRect.argtypes = [w.HDC, ctypes.POINTER(w.RECT)]
    color = 0xE1D7CD if disabled else 0x6B4B26 if pressed else 0x956C3A if hot else 0x865F31
    brush = g.CreateSolidBrush(color)
    saved = g.SaveDC(dc)
    try:
        u.FillRect(dc, ctypes.byref(rect), window.brushes["background"])
        g.SelectObject(dc, brush)
        g.SelectObject(dc, window.stock(8))  # NULL_PEN
        g.RoundRect(dc, rect.left, rect.top, rect.right, rect.bottom, 8, 8)
        g.SelectObject(dc, window.own_fonts[2] or window.font)
        g.SetBkMode(dc, 1)
        g.SetTextColor(dc, 0x71655B if disabled else 0xFFFFFF)
        label = window.value("start")
        text_rect = w.RECT(rect.left + 8, rect.top + int(pressed), rect.right - 8, rect.bottom + int(pressed))
        u.DrawTextW(dc, label, -1, ctypes.byref(text_rect), 0x825)  # centered, single line, no mnemonic
        if focused and not hide_focus and not disabled:
            focus = w.RECT(rect.left + 4, rect.top + 4, rect.right - 4, rect.bottom - 4)
            u.DrawFocusRect(dc, ctypes.byref(focus))
    finally:
        g.RestoreDC(dc, saved)
        window.delete_object(brush)
