"""Windows file chooser, using only ctypes available in ResolvePython."""

from __future__ import annotations

from .core.i18n import tr

import ctypes
from ctypes import wintypes as w
from pathlib import Path
from uuid import UUID


class OpenFileName(ctypes.Structure):
    # Desktop OPENFILENAMEW (the SDK's _MAC-only fields are not present).
    _fields_ = [("size", w.DWORD), ("owner", w.HWND), ("instance", w.HINSTANCE),
                ("filter", w.LPCWSTR), ("custom_filter", w.LPWSTR), ("max_custom", w.DWORD),
                ("filter_index", w.DWORD), ("file", w.LPWSTR), ("max_file", w.DWORD),
                ("file_title", w.LPWSTR), ("max_title", w.DWORD), ("directory", w.LPCWSTR),
                ("title", w.LPCWSTR), ("flags", w.DWORD), ("file_offset", w.WORD),
                ("extension_offset", w.WORD), ("default_extension", w.LPCWSTR),
                ("data", w.LPARAM), ("hook", w.LPVOID), ("template", w.LPCWSTR),
                ("reserved", w.LPVOID), ("reserved_value", w.DWORD), ("flags_ex", w.DWORD)]


def choose_result_settings(owner, directory: Path) -> Path | None:
    dialog = ctypes.WinDLL("comdlg32", use_last_error=True)
    choose = dialog.GetOpenFileNameW
    choose.argtypes, choose.restype = [ctypes.POINTER(OpenFileName)], w.BOOL
    error = dialog.CommDlgExtendedError
    error.argtypes, error.restype = [], w.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    options = OpenFileName()
    options.size, options.owner = ctypes.sizeof(options), owner
    options.filter = tr('Настройки HVPC Resolve-MVSEP (*.json)\x00*.json\x00\x00')
    options.filter_index = 1
    options.file, options.max_file = ctypes.cast(buffer, w.LPWSTR), len(buffer)
    options.directory = str(directory)
    options.title = tr('Выберите .settings.json нужного варианта разделения')
    options.flags = 0x80000 | 0x1000 | 0x800 | 0x8 | 0x4  # Explorer, existing file/path, no cwd change
    if choose(ctypes.byref(options)):
        return Path(buffer.value)
    code = error()
    if code:
        raise OSError(tr('Не удалось открыть выбор файла (код {v0}).', v0=code))
    return None


def choose_output_folder(owner, directory: Path) -> Path | None:
    """Windows Common Item Dialog in folder mode, with owned COM lifetimes."""
    class GUID(ctypes.Structure):
        _fields_ = [("bytes", ctypes.c_ubyte * 16)]

    def guid(value):
        return GUID.from_buffer_copy(UUID(value).bytes_le)

    def call(pointer, index, *types):
        address = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents[index]
        function = ctypes.WINFUNCTYPE(w.LONG, ctypes.c_void_p, *types)(address)
        return lambda *args: function(pointer, *args)

    def check(code):
        if code < 0:
            raise OSError(tr("Не удалось открыть выбор папки (код {code}).", code=f"0x{code & 0xffffffff:08X}"))

    shell = ctypes.WinDLL("shell32")
    # WinDLL avoids OleDLL's automatic HRESULT exception conversion on Cancel.
    ole = ctypes.WinDLL("ole32")
    ole.CoInitializeEx.argtypes, ole.CoInitializeEx.restype = [w.LPVOID, w.DWORD], w.LONG
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), w.LPVOID, w.DWORD, ctypes.POINTER(GUID), ctypes.POINTER(w.LPVOID)]
    ole.CoCreateInstance.restype = w.LONG
    ole.CoTaskMemFree.argtypes = [w.LPVOID]
    ole.CoUninitialize.argtypes = []
    shell.SHCreateItemFromParsingName.argtypes = [w.LPCWSTR, w.LPVOID, ctypes.POINTER(GUID), ctypes.POINTER(w.LPVOID)]
    shell.SHCreateItemFromParsingName.restype = w.LONG
    initialized = ole.CoInitializeEx(None, 2)
    check(initialized)
    dialog, item, selected, name = w.LPVOID(), w.LPVOID(), w.LPVOID(), w.LPVOID()
    try:
        cls = guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")
        iid = guid("42F85136-DB7E-439C-85F1-E4075D135FC8")
        check(ole.CoCreateInstance(ctypes.byref(cls), None, 1, ctypes.byref(iid), ctypes.byref(dialog)))
        flags = w.DWORD()
        check(call(dialog, 10, ctypes.POINTER(w.DWORD))(ctypes.byref(flags)))
        check(call(dialog, 9, w.DWORD)(flags.value | 0x20 | 0x40 | 0x800 | 0x8))
        check(call(dialog, 17, w.LPCWSTR)(tr("Выберите папку для результатов разделения")))
        check(call(dialog, 18, w.LPCWSTR)(tr("Выбрать папку")))
        initial = directory
        while not initial.is_dir() and initial != initial.parent:
            initial = initial.parent
        item_iid = guid("43826D1E-E718-42EE-BC55-A1E261C37BFE")
        if shell.SHCreateItemFromParsingName(str(initial), None, ctypes.byref(item_iid), ctypes.byref(item)) >= 0:
            check(call(dialog, 12, w.LPVOID)(item))
        code = call(dialog, 3, w.HWND)(owner)
        if code & 0xffffffff == 0x800704C7:
            return None
        check(code)
        check(call(dialog, 20, ctypes.POINTER(w.LPVOID))(ctypes.byref(selected)))
        check(call(selected, 5, w.DWORD, ctypes.POINTER(w.LPVOID))(0x80058000, ctypes.byref(name)))
        return Path(ctypes.wstring_at(name)).resolve()
    finally:
        if name:
            ole.CoTaskMemFree(name)
        for pointer in (selected, item, dialog):
            if pointer:
                call(pointer, 2)()
        ole.CoUninitialize()
