"""Native stage strip and a small audio-to-stems graphic. No web view or network."""
import ctypes
import time
from ctypes import wintypes as w

from .core.pipeline import stage_labels
from .core.i18n import load_preferences, save_preferences
from .primary_button import DrawItem
from .progress_layout import (progress_layout, card_height, MIN_PROGRESS, MIN_CARD_HEIGHT,
                              DEFAULT_CARD_HEIGHT, MIN_SOURCE_HEIGHT)


class ProgressPanel:
    def __init__(self, window):
        self.window, self.view = window, None
        preferred = load_preferences(window.path.parents[2]).get('progress_card_height')
        self.preferred_card = (preferred if type(preferred) is int and MIN_CARD_HEIGHT <= preferred <= 4000
                               else DEFAULT_CARD_HEIGHT)
        self.drag_origin = None
        self.source_height = MIN_SOURCE_HEIGHT
        self.reflowing = False
        self.colors = {'background': 0xF8F5F2, 'card': 0xFFFFFF, 'text': 0x403025,
                       'muted': 0x8A7767, 'line': 0xE9E1D9, 'queue': 0xAB6F43,
                       'working': 0x788F23, 'done': 0x788F23, 'error': 0x52418C, 'idle': 0x8A7767,
                       'warning': 0x245F83, 'warning_bg': 0xDDF4FF,
                       'details_bg': 0xF8EEE5, 'details_border': 0xDAC3AB, 'details_accent': 0x986A3B,
                       'error_bg': 0xEFE8F7,
                       'connected': 0x59724A, 'connection_bg': 0xEFEEE8}
        u, g = window.user, window.gdi
        g.CreateSolidBrush.argtypes, g.CreateSolidBrush.restype = [w.DWORD], w.HBRUSH
        g.SetTextColor.argtypes = g.SetBkColor.argtypes = [w.HDC, w.DWORD]
        u.FillRect.argtypes = [w.HDC, ctypes.POINTER(w.RECT), w.HBRUSH]
        u.FrameRect.argtypes = [w.HDC, ctypes.POINTER(w.RECT), w.HBRUSH]
        u.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT]
        u.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
        u.MonitorFromWindow.argtypes, u.MonitorFromWindow.restype = [w.HWND, w.DWORD], w.HANDLE
        u.GetMonitorInfoW.argtypes = [w.HANDLE, w.LPVOID]
        u.IsZoomed.argtypes, u.IsZoomed.restype = [w.HWND], w.BOOL
        u.RedrawWindow.argtypes = [w.HWND, ctypes.POINTER(w.RECT), w.HANDLE, w.UINT]
        u.SetCapture.argtypes, u.SetCapture.restype = [w.HWND], w.HWND
        u.ReleaseCapture.argtypes = []
        u.LoadCursorW.argtypes, u.LoadCursorW.restype = [w.HINSTANCE, w.LPVOID], w.HANDLE
        u.SetCursor.argtypes, u.SetCursor.restype = [w.HANDLE], w.HANDLE
        u.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
        u.ScreenToClient.argtypes = [w.HWND, ctypes.POINTER(w.POINT)]
        u.DrawTextW.argtypes = [w.HDC, w.LPCWSTR, ctypes.c_int, ctypes.POINTER(w.RECT), w.UINT]
        self.arrow = u.LoadCursorW(None, ctypes.c_void_p(32512))
        self.resize_cursor = u.LoadCursorW(None, ctypes.c_void_p(32645))
        g.SelectObject.argtypes, g.SelectObject.restype = [w.HDC, w.HANDLE], w.HANDLE
        g.CreatePen.argtypes, g.CreatePen.restype = [ctypes.c_int, ctypes.c_int, w.DWORD], w.HANDLE
        g.Polyline.argtypes = g.PolyBezier.argtypes = [w.HDC, ctypes.POINTER(w.POINT), w.DWORD]
        g.Ellipse.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        g.CreateCompatibleDC.argtypes, g.CreateCompatibleDC.restype = [w.HDC], w.HDC
        g.CreateCompatibleBitmap.argtypes, g.CreateCompatibleBitmap.restype = [w.HDC, ctypes.c_int, ctypes.c_int], w.HBITMAP
        g.DeleteDC.argtypes = [w.HDC]
        g.BitBlt.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HDC, ctypes.c_int, ctypes.c_int, w.DWORD]
        self.brushes = {key: g.CreateSolidBrush(color) for key, color in self.colors.items()}
        self.fonts = [g.CreateFontW(-size, 0, 0, 0, weight, 0, 0, 0, 1, 0, 0, 5, 0, 'Segoe UI')
                      for size, weight in ((13, 400), (13, 600), (40, 600), (24, 600))]
        # Separate artwork from selectable text; owner-drawn STATIC cannot steal focus.
        handle = window.create(0, 'STATIC', '', 0x5000000D, 0, 0, 100, 60, window.hwnd, 50, window.module, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        window.controls['flow'], window.control_kinds['flow'] = handle, 'ART'
        for name in ('hero_caption', 'hero_value', 'hero_secondary', 'hero_note'):
            window.child(name, 'STATIC', '', 0, 0, 100, 25)
            window.set_style(window.controls[name], -16, 0x50000845)  # centered read-only multiline text
        for index, label in enumerate(stage_labels()):
            name = f'step_{index}'
            window.child(name, 'STATIC', label, 0, 0, 100, 23)
            window.set_style(window.controls[name], -16, 0x50000845)
            window.send(window.controls[name], 0x30, self.fonts[0], 1)
        for name in ('hero_caption', 'hero_note'):
            window.send(window.controls[name], 0x30, self.fonts[0], 1)

    def boxes(self):
        rect = w.RECT()
        self.window.client_rect(self.window.hwnd, ctypes.byref(rect))
        return progress_layout(rect.right, rect.bottom, self.preferred_card, self.source_height)

    def repaint(self):
        # WS_CLIPCHILDREN excludes controls from the parent's paint region.
        # Repaint child backgrounds, text and non-client scrollbars as well.
        self.window.user.RedrawWindow(self.window.hwnd, None, None, 0x585)

    def reflow(self):
        if self.reflowing:
            return
        self.reflowing = True
        win = self.window
        try:
            # Let the native multiline EDIT wrap at its new width before counting
            # lines; this also handles long names without spaces and Unicode.
            win.user.SetWindowPos(win.controls['source'], None, *self.boxes()['source'], 0x11C)
            lines = win.send(win.controls['source'], 0xBA, 0, 0)  # EM_GETLINECOUNT
            self.source_height = max(MIN_SOURCE_HEIGHT, lines * 22 + 4)
            rect, outer = w.RECT(), w.RECT()
            win.client_rect(win.hwnd, ctypes.byref(rect))
            minimum = MIN_PROGRESS[1] + self.source_height - MIN_SOURCE_HEIGHT
            if rect.bottom < minimum:
                win.user.GetWindowRect(win.hwnd, ctypes.byref(outer))
                win.user.SetWindowPos(win.hwnd, None, 0, 0, outer.right - outer.left,
                                      outer.bottom - outer.top + minimum - rect.bottom, 0x16)
            for name, box in self.boxes().items():
                if name in win.controls:
                    # No stale pixel copies while controls move across one another.
                    win.user.SetWindowPos(win.controls[name], None, *box, 0x11C)
            self.repaint()
        finally:
            self.reflowing = False

    def step_color(self, index):
        if self.view is None or self.view.step is None:
            return 'muted'
        if index < self.view.step:
            return 'done'
        return self.view.tone if index == self.view.step else 'muted'

    def update(self, view):
        win = self.window
        changed = self.view != view
        self.view = view
        for name, value in (('hero_caption', view.caption), ('hero_value', view.value),
                            ('hero_secondary', view.secondary), ('hero_note', view.note)):
            win.text(name, value)
        if changed:
            win.send(win.controls['hero_value'], 0x30, self.fonts[2 if view.numeric else 3], 1)
            for index in range(5):
                win.send(win.controls[f'step_{index}'], 0x30, self.fonts[1 if view.step == index else 0], 1)
            win.invalidate(win.hwnd, None, True)
        win.invalidate(win.controls['flow'], None, False)

    def message(self, hwnd, message, wp, lp):
        win, u, g = self.window, self.window.user, self.window.gdi
        if message in {0x200, 0x201, 0x202, 0x20}:
            point = w.POINT()
            if message == 0x20:
                u.GetCursorPos(ctypes.byref(point))
                u.ScreenToClient(hwnd, ctypes.byref(point))
            else:
                point.x, point.y = ctypes.c_short(lp & 0xffff).value, ctypes.c_short(lp >> 16).value
            boxes = self.boxes()
            over = next((name for name in ('splitter', 'detail_bottom')
                         if (boxes[name][0] <= point.x < boxes[name][0] + boxes[name][2]
                             and boxes[name][1] <= point.y < boxes[name][1] + boxes[name][3])), None)
            if message == 0x201 and over:
                outer, client = w.RECT(), w.RECT()
                u.GetWindowRect(hwnd, ctypes.byref(outer))
                win.client_rect(hwnd, ctypes.byref(client))
                self.drag_origin = (over, point.y, boxes['card'][3], outer, client.bottom,
                                    boxes['detail'][3])
                u.SetCapture(hwnd)
                u.SetCursor(self.resize_cursor)
                return 0
            if message == 0x200 and self.drag_origin:
                rect = w.RECT()
                win.client_rect(hwnd, ctypes.byref(rect))
                edge, origin_y, origin_height, outer, client_height, details_height = self.drag_origin
                if edge == 'splitter':
                    self.preferred_card = card_height(rect.bottom - (self.source_height - 24),
                                                       origin_height + point.y - origin_y)
                    self.reflow()
                else:
                    # The footer follows the lower edge; keep the upper panel and
                    # text start still. Do not grow beyond the monitor work area.
                    class MonitorInfo(ctypes.Structure):
                        _fields_ = [('size', w.DWORD), ('monitor', w.RECT),
                                    ('work', w.RECT), ('flags', w.DWORD)]
                    info = MonitorInfo()
                    info.size = ctypes.sizeof(info)
                    outer_height = outer.bottom - outer.top
                    minimum = max(MIN_PROGRESS[1] + self.source_height - MIN_SOURCE_HEIGHT,
                                  client_height - details_height + 70)
                    minimum += outer_height - client_height
                    maximum = outer_height
                    if u.GetMonitorInfoW(u.MonitorFromWindow(hwnd, 2), ctypes.byref(info)):
                        maximum = max(outer_height, info.work.bottom - outer.top)
                    new_height = max(minimum, min(maximum, outer_height + point.y - origin_y))
                    if new_height != rect.bottom + outer_height - client_height:
                        self.preferred_card = origin_height
                        if u.IsZoomed(hwnd):
                            u.ShowWindow(hwnd, 9)  # Restore before changing a maximized window's size.
                        u.SetWindowPos(hwnd, None, outer.left, outer.top, outer.right - outer.left,
                                       new_height, 0x14)
                return 0
            if message == 0x202 and self.drag_origin:
                self.drag_origin = None
                u.ReleaseCapture()
                try:
                    save_preferences(win.path.parents[2], progress_card_height=self.preferred_card)
                except OSError:
                    pass  # An unwritable preference file must not close the viewer.
                return 0
            if message == 0x20 and ((wp == hwnd and lp & 0xffff == 1) or self.drag_origin):
                u.SetCursor(self.resize_cursor if over or self.drag_origin else self.arrow)
                return 1
        if message == 0x215:  # Capture lost: stop resizing even if mouse-up went elsewhere.
            self.drag_origin = None
        if message == 0x24:
            class MinMax(ctypes.Structure):
                _fields_ = [(name, w.POINT) for name in ('reserved', 'maximum', 'position', 'minimum', 'track')]
            info = ctypes.cast(lp, ctypes.POINTER(MinMax)).contents
            info.minimum.x = MIN_PROGRESS[0] + 16
            info.minimum.y = MIN_PROGRESS[1] + self.source_height - MIN_SOURCE_HEIGHT + 39
            return 0
        if message == 5 and wp != 1:
            self.reflow()
            return 0
        if message == 0x14:
            rect = w.RECT()
            win.client_rect(hwnd, ctypes.byref(rect))
            u.FillRect(wp, ctypes.byref(rect), self.brushes['background'])
            boxes = self.boxes()
            x, y, width, height = boxes['card']
            card = w.RECT(x, y, x + width, y + height)
            u.FillRect(wp, ctypes.byref(card), self.brushes['card'])
            x, y, width, height = boxes['detail_card']
            details = w.RECT(x, y, x + width, y + height)
            u.FillRect(wp, ctypes.byref(details), self.brushes['details_bg'])
            u.FrameRect(wp, ctypes.byref(details), self.brushes['details_border'])
            stripe = w.RECT(x, y, x + 4, y + height)
            u.FillRect(wp, ctypes.byref(stripe), self.brushes['details_accent'])
            for name in ('splitter', 'detail_bottom'):
                x, y, width, height = boxes[name]
                center_x, center_y = x + width // 2, y + height // 2
                for offset in (-3, 2):
                    grip = w.RECT(center_x - 18, center_y + offset,
                                  center_x + 18, center_y + offset + 2)
                    u.FillRect(wp, ctypes.byref(grip), self.brushes['details_accent'])
            if win.import_warning:
                x, y, width, height = boxes['status_card']
                status = w.RECT(x, y, x + width, y + height)
                u.FillRect(wp, ctypes.byref(status), self.brushes['error_bg'])
            x, y, width, height = boxes['connection_card']
            banner = w.RECT(x, y, x + width, y + height)
            banner_color = 'warning_bg' if win.resolve_status.tone == 'warning' else 'connection_bg'
            u.FillRect(wp, ctypes.byref(banner), self.brushes[banner_color])
            for index in range(5):
                x, y, width, height = boxes[f'line_{index}']
                line = w.RECT(x, y, x + width, y + height)
                color = self.step_color(index)
                u.FillRect(wp, ctypes.byref(line), self.brushes['line' if color == 'muted' else color])
            return 1
        if message in {0x138, 0x133}:
            name = next((name for name, handle in win.controls.items() if handle == lp), '')
            in_card = name.startswith(('hero_', 'step_'))
            background = 'card' if in_card else 'background' if name != 'profile_name' else 'card'
            color = 'muted' if name in {'hero_caption', 'hero_note', 'source', 'elapsed'} else 'text'
            if name.startswith('step_'):
                color = self.step_color(int(name[-1]))
            if name == 'hero_value' and self.view:
                color = 'text' if self.view.tone == 'queue' else self.view.tone
            if name == 'detail':
                background = 'details_bg'
            if name == 'quota':
                result = win.quota_monitor.result if win.quota_monitor else None
                color = ('error' if result and result.quota and result.quota.remaining == 0
                         else 'details_accent')
            if name == 'remaining' and win.import_warning:
                background, color = 'error_bg', 'error'
            if name in {'connection_title', 'hint'}:
                background = 'warning_bg' if win.resolve_status.tone == 'warning' else 'connection_bg'
                color = win.resolve_status.tone if name == 'connection_title' else 'text'
            g.SetTextColor(wp, self.colors[color])
            g.SetBkColor(wp, self.colors[background])
            return self.brushes[background]
        if message == 0x2B and lp:
            info = ctypes.cast(lp, ctypes.POINTER(DrawItem)).contents
            if info.hwnd == win.controls['flow']:
                self.paint_flow(info)
                return 1
        return None

    def paint_flow(self, info):
        win, g, u = self.window, self.window.gdi, self.window.user
        width, height = info.rect.right - info.rect.left, info.rect.bottom - info.rect.top
        if width <= 0 or height <= 0:
            return
        dc = g.CreateCompatibleDC(info.dc)
        bitmap = g.CreateCompatibleBitmap(info.dc, width, height)
        if not dc or not bitmap:
            if bitmap:
                win.delete_object(bitmap)
            if dc:
                g.DeleteDC(dc)
            return
        original_bitmap = g.SelectObject(dc, bitmap)
        tone = self.view.tone if self.view else 'idle'
        color = self.colors[tone]
        pen = g.CreatePen(0, 2, color)
        original_pen = g.SelectObject(dc, pen)
        original_brush = g.SelectObject(dc, self.brushes[tone])
        try:
            u.FillRect(dc, ctypes.byref(w.RECT(0, 0, width, height)), self.brushes['card'])
            cx, cy = width // 2 - 20, height // 2
            left, right = cx - 130, cx + 160
            for x in range(left, cx - 8, 15):
                points = (w.POINT * 2)(w.POINT(x, cy), w.POINT(x + 8, cy))
                g.Polyline(dc, points, 2)
            for offset in (-24, -12, 0, 12, 24):
                offset = round(offset * min(1, height / 60))
                points = (w.POINT * 4)(w.POINT(cx, cy), w.POINT(cx + 50, cy),
                                        w.POINT(cx + 40, cy + offset), w.POINT(right, cy + offset))
                g.PolyBezier(dc, points, 4)
            g.Ellipse(dc, cx - 4, cy - 4, cx + 5, cy + 5)
            if self.view and self.view.animate:
                x = left + int(time.monotonic() * 42) % (cx - left)
                g.Ellipse(dc, x - 3, cy - 3, x + 4, cy + 4)
            g.BitBlt(info.dc, info.rect.left, info.rect.top, width, height, dc, 0, 0, 0x00CC0020)
        finally:
            g.SelectObject(dc, original_brush)
            g.SelectObject(dc, original_pen)
            g.SelectObject(dc, original_bitmap)
            win.delete_object(pen)
            win.delete_object(bitmap)
            g.DeleteDC(dc)

    def close(self):
        for handle in (*self.fonts, *self.brushes.values()):
            if handle:
                self.window.delete_object(handle)
