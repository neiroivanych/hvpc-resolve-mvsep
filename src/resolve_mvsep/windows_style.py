"""Use the OS-provided v6 progress control, including indeterminate animation."""

from __future__ import annotations

import ctypes
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def visual_styles():
    from ctypes import wintypes as w
    class ActCtx(ctypes.Structure):
        _fields_ = [("cbSize", w.ULONG), ("dwFlags", w.DWORD), ("lpSource", w.LPCWSTR),
                    ("wProcessorArchitecture", w.WORD), ("wLangId", w.WORD),
                    ("lpAssemblyDirectory", w.LPCWSTR), ("lpResourceName", w.LPCWSTR),
                    ("lpApplicationName", w.LPCWSTR), ("hModule", w.HMODULE)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateActCtxW.argtypes, kernel.CreateActCtxW.restype = [ctypes.POINTER(ActCtx)], w.HANDLE
    kernel.ActivateActCtx.argtypes = [w.HANDLE, ctypes.POINTER(ctypes.c_size_t)]
    kernel.DeactivateActCtx.argtypes = [w.DWORD, ctypes.c_size_t]
    kernel.ReleaseActCtx.argtypes = [w.HANDLE]
    context = ActCtx()
    context.cbSize = ctypes.sizeof(context)
    context.lpSource = str(Path(__file__).with_name("progress.manifest"))
    handle = kernel.CreateActCtxW(ctypes.byref(context))
    cookie = ctypes.c_size_t()
    valid = bool(handle) and handle != ctypes.c_void_p(-1).value
    activated = valid and bool(kernel.ActivateActCtx(handle, ctypes.byref(cookie)))
    try:
        yield activated  # if unavailable, determinate bar + text animation still work
    finally:
        if activated:
            kernel.DeactivateActCtx(0, cookie)
        if valid:
            kernel.ReleaseActCtx(handle)
