"""Visible Windows feedback independent of Resolve's console output routing."""

from __future__ import annotations

import ctypes
import sys


def show_message(message: str, *, error: bool = False, title: str = "HVPC Resolve-MVSEP — M1") -> bool:
    """Show a Unicode result/error dialog. Failure must not discard an inspection."""
    if sys.platform != "win32":
        return False
    try:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        message_box = user32.MessageBoxW
        message_box.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
        message_box.restype = ctypes.c_int
        # MB_OK | MB_SETFOREGROUND | MB_ICONERROR/MB_ICONINFORMATION.
        flags = 0x00010000 | (0x00000010 if error else 0x00000040)
        return message_box(None, message, title, flags) != 0
    except (OSError, AttributeError):
        return False
