"""Native notification-area icon; stdlib only, owned by the progress window.

Shell API contract: https://learn.microsoft.com/windows/win32/api/shellapi/nf-shellapi-shell_notifyiconw
"""
import ctypes
import time
from ctypes import wintypes as w

from .core.i18n import tr
from .app_icon import load_icon

TRAY_MESSAGE = 0x8000 + 73


class Guid(ctypes.Structure):
    _fields_ = [('data1', w.DWORD), ('data2', w.WORD), ('data3', w.WORD), ('data4', w.BYTE * 8)]


class NotifyData(ctypes.Structure):
    _fields_ = [('cbSize', w.DWORD), ('hWnd', w.HWND), ('uID', w.UINT), ('uFlags', w.UINT),
                ('uCallbackMessage', w.UINT), ('hIcon', w.HICON), ('szTip', w.WCHAR * 128),
                ('dwState', w.DWORD), ('dwStateMask', w.DWORD), ('szInfo', w.WCHAR * 256),
                ('uVersion', w.UINT), ('szInfoTitle', w.WCHAR * 64), ('dwInfoFlags', w.DWORD),
                ('guidItem', Guid), ('hBalloonIcon', w.HICON)]


class TrayIcon:
    def __init__(self, window):
        self.window = window
        self.present = False
        self.tip, self.tone = 'HVPC Resolve-MVSEP', 'working'
        self.sent = None
        self.next_attempt = 0.0
        self.icons = {}
        self.shell = ctypes.WinDLL('shell32', use_last_error=True)
        self.shell.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.POINTER(NotifyData)]
        self.shell.Shell_NotifyIconW.restype = w.BOOL
        u = window.user
        u.RegisterWindowMessageW.argtypes, u.RegisterWindowMessageW.restype = [w.LPCWSTR], w.UINT
        self.taskbar_created = u.RegisterWindowMessageW('TaskbarCreated')
        u.CreateIcon.argtypes = [w.HINSTANCE, ctypes.c_int, ctypes.c_int, w.BYTE, w.BYTE, w.LPVOID, w.LPVOID]
        u.CreateIcon.restype = w.HICON
        u.DestroyIcon.argtypes = [w.HICON]
        u.CreatePopupMenu.restype = w.HMENU
        u.AppendMenuW.argtypes = [w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR]
        u.TrackPopupMenu.argtypes = [w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND, w.LPVOID]
        u.TrackPopupMenu.restype = w.UINT
        u.DestroyMenu.argtypes = [w.HMENU]
        u.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
        u.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        u.IsWindowEnabled.argtypes, u.IsWindowEnabled.restype = [w.HWND], w.BOOL
        u.GetSystemMetrics.argtypes, u.GetSystemMetrics.restype = [ctypes.c_int], ctypes.c_int
        try:
            for name, rgb in [('working', (49, 95, 134)), ('done', (35, 143, 120)), ('error', (140, 65, 82))]:
                # Preserve the logo in every state; the tooltip and window carry status.
                self.icons[name] = (load_icon(u, u.GetSystemMetrics(49) or 16, u.GetSystemMetrics(50) or 16)
                                    or self.make_icon(rgb))
        except Exception:
            self.close()
            raise

    def make_icon(self, rgb):
        # A small H: simple solid geometry remains legible at taskbar icon sizes.
        pixels = bytearray()
        mask = bytearray([255] * (32 * 4))
        for y in range(32):
            for x in range(32):
                inside = 2 <= x <= 29 and 2 <= y <= 29
                white = inside and 7 <= y <= 24 and (7 <= x <= 11 or 20 <= x <= 24 or 14 <= y <= 17 and 7 <= x <= 24)
                r, g, b = (255, 255, 255) if white else rgb
                pixels.extend((b, g, r, 255) if inside else (0, 0, 0, 0))
                if inside:
                    mask[y * 4 + x // 8] &= ~(0x80 >> (x % 8))
        icon = self.window.user.CreateIcon(self.window.module, 32, 32, 1, 32,
                                           ctypes.create_string_buffer(bytes(mask)),
                                           ctypes.create_string_buffer(bytes(pixels)))
        if not icon:
            raise ctypes.WinError(ctypes.get_last_error())
        return icon

    def data(self, flags=0):
        data = NotifyData()
        data.cbSize, data.hWnd, data.uID = ctypes.sizeof(NotifyData), self.window.hwnd, 1
        data.uFlags, data.uCallbackMessage = flags, TRAY_MESSAGE
        data.hIcon, data.szTip = self.icons[self.tone], self.tip
        return data

    def ensure(self, *, force=False):
        if self.present:
            return True
        if not force and time.monotonic() < self.next_attempt:
            return False
        data = self.data(1 | 2 | 4 | 0x80)  # MESSAGE, ICON, TIP, SHOWTIP
        self.present = bool(self.shell.Shell_NotifyIconW(0, ctypes.byref(data)))
        self.next_attempt = time.monotonic() + 5
        if self.present:
            data.uVersion = 4
            self.shell.Shell_NotifyIconW(4, ctypes.byref(data))
            self.sent = (self.tip, self.tone)
        return self.present

    def update(self, tip, tone):
        self.tip, self.tone = tip, tone
        if self.ensure() and self.sent != (tip, tone):
            data = self.data(2 | 4 | 0x80)
            self.present = bool(self.shell.Shell_NotifyIconW(1, ctypes.byref(data)))
            if self.present:
                self.sent = (tip, tone)
        return self.present

    def notify(self, message):
        if self.ensure():
            data = self.data(0x10)  # INFO
            data.szInfoTitle = 'HVPC Resolve-MVSEP'
            data.szInfo = message.encode('utf-16-le')[:510].decode('utf-16-le', errors='ignore')
            data.dwInfoFlags = 1 | 0x10 | 0x80  # informational, silent, respect quiet time
            self.shell.Shell_NotifyIconW(1, ctypes.byref(data))

    def menu(self):
        win, u = self.window, self.window.user
        menu = u.CreatePopupMenu()
        if not menu:
            win.restore_window()
            return
        try:
            u.AppendMenuW(menu, 0, 101, tr('Показать состояние'))
            u.AppendMenuW(menu, 0 if u.IsWindowEnabled(win.controls['result']) else 1, 102, tr('Открыть результат'))
            if not win.background.busy:
                u.AppendMenuW(menu, 0x800, 0, None)
                u.AppendMenuW(menu, 0, 103, tr('Закрыть окно'))
            point = w.POINT()
            u.GetCursorPos(ctypes.byref(point))
            win.focus(win.hwnd)
            selected = u.TrackPopupMenu(menu, 0x100 | 2, point.x, point.y, 0, win.hwnd, None)
            u.PostMessageW(win.hwnd, 0, 0, 0)
            if selected == 101:
                win.restore_window()
            elif selected == 102:
                win.on_command(1, 0)
            elif selected == 103:
                win.request_close()
            else:
                data = self.data()
                self.shell.Shell_NotifyIconW(3, ctypes.byref(data))
        finally:
            u.DestroyMenu(menu)

    def message(self, message, wp, lp):
        if self.taskbar_created and message == self.taskbar_created:
            self.present = False
            if not self.ensure(force=True) and self.window.background.hidden:
                self.window.restore_window()
            return 0
        if message == TRAY_MESSAGE:
            event = lp & 0xFFFF  # NOTIFYICON_VERSION_4 packs the icon ID above this
            if event in {0x203, 0x400, 0x401, 0x405}:  # double click, SELECT, KEYSELECT, BALLOONUSERCLICK
                self.window.restore_window()
            elif event in {0x7B, 0x205}:  # CONTEXTMENU / legacy right-up
                self.menu()
            return 0
        return None

    def close(self):
        if self.present:
            data = self.data()
            self.shell.Shell_NotifyIconW(2, ctypes.byref(data))
            self.present = False
        for icon in self.icons.values():
            self.window.user.DestroyIcon(icon)
        self.icons.clear()
