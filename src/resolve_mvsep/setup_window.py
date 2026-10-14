"""Native per-job algorithm, parameter, format and reusable-profile chooser."""
from __future__ import annotations

import ctypes
import os
import queue
import threading
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from .core.catalog import (availability_label, choices, description_fragments, expected_outputs, field_label, filter_algorithms, group_name,
                           new_algorithm, normalized_options, parameter_help, refresh_catalog,
                           matching_options, option_label, premium_label, premium_requirements,
                           unsupported_reason, validate_catalog, validate_selection, visible_fields)
from .core.errors import InspectionError
from .core.account_status import BalanceResult, balance_text, read_balance
from .core.quota import QuotaMonitor, quota_text
from .core.resolve_import import latest_completed_for_project
from .core.recent_settings import policy, remembered_spec, remember_choice
from .core.job import Job
from .core.i18n import language, load_language, save_language, load_preferences, save_preferences, tr, using_language, LANGUAGES
from .core.description import Paragraph, document_rtf
from .core.catalog_copy import catalog_blocks, output_count_text
from .core.output import default_output_root, prepare_output_root
from .core.profiles import load_profiles, save_profile, read_result_settings
from .core.recipe import capture_settings
from .core.setup import FORMAT_NAMES, cached_algorithms, freeze_defaults, with_format
from .file_dialog import choose_result_settings, choose_output_folder
from .rich_description import stream_rtf, repaint_on_scroll
from .selectable_text import READONLY_LABEL, enable_select_all
from .primary_button import draw_notification
from .mvsep.transport import validate_url
from .mvsep.errors import MVSEPError
from .progress_window import _Window
from .windows_style import visual_styles
from .setup_layout import layout, column_width, MIN_CLIENT


def choose_settings(root: Path, job: Job, *, auto_close_ms=0, on_ready=None, balance_loader=None, quota_loader=None):
    with using_language(load_language(root)), visual_styles() as styled:
        window = SetupWindow(root, job, auto_close_ms, on_ready, animated_bar=styled,
                             balance_loader=balance_loader, quota_loader=quota_loader)
        window.run()
        return window.choice


class SetupWindow(_Window):
    window_title = "HVPC Resolve-MVSEP — новое разделение"
    window_width, window_height = 1400, 1000
    window_style = 0x02CF0000  # resizable, maximize, clip child controls while repainting
    window_position = (80, 35)
    default_notice = "Результаты каждого разделения сохраняются отдельно.\r\nИсходный файл не изменяется. Обрабатывается весь файл.\r\nВнутри выбранной папки: исходник → дата и номер попытки."

    def __init__(self, root, job, auto_close_ms=0, on_ready=None, *, animated_bar=True,
                 balance_loader=None, quota_loader=None):
        super().__init__(root / "jobs" / job.id / "job.json", auto_close_ms, on_ready, animated_bar=animated_bar)
        self.root, self.job = root, job
        if Path(job.source_file).suffix.lower() == '.mp4':
            self.default_notice = ('MP4 отправится на MVSEP: сервер извлечёт звук и выполнит разделение.\r\n'
                                   'Исходный файл не изменяется. Обрабатывается весь файл.\r\n'
                                   'Внутри выбранной папки: исходник → дата и номер попытки.')
        self.destination = Path(job.output_root) if job.output_root else default_output_root(root)
        preference = load_preferences(root).get("split_ratio", .42)
        self.split_ratio = preference if isinstance(preference, (float, int)) and .1 <= preference <= .9 else .42
        self.dragging = False
        self.detail_blocks, self.detail_rtf = [], None
        self.reset_description_scroll = True
        self.rich = ctypes.WinDLL("Msftedit.dll")
        try:
            self.catalog = validate_catalog(list(cached_algorithms(root).values()))
        except InspectionError:
            self.catalog = {}
        self.remember_enabled, self.remember_mode = policy(root)
        remembered = remembered_spec(root)
        self.initial_group_pending = remembered is not None
        self.initial = freeze_defaults(remembered or job.specification(), self.catalog)
        if self.initial.preset_id is None and self.initial.sep_type in self.catalog:
            try:
                defaults = normalized_options(self.catalog[self.initial.sep_type], self.initial.api_options)
                self.initial = replace(self.initial, api_options={**defaults, **self.initial.api_options})
            except InspectionError:
                pass  # refresh shows the unsupported dependency before enabling Start
        self.spec = self.initial
        self.profiles = load_profiles(root)
        self.profile_names, self.groups, self.algorithm_ids = [], [], []
        self.formats = list(FORMAT_NAMES)
        self.field_controls = {}
        self.help_links = {}
        self.choice = None
        self.ready = self.updating = self.fetching = False
        self.catalog_messages = queue.Queue()
        self.balance_messages = queue.Queue()
        self.balance_loader = balance_loader or read_balance
        self.balance_result = None
        self.balance_fetching = False
        self.quota_monitor = QuotaMonitor(root, loader=quota_loader)
        self.source_height, self.context_height = 24, 22
        self.reflowing = False
        self.last_result = latest_completed_for_project(root, job.source_clip)
        self.premium_notice = self.has_premium = False
        self.selectable_callbacks = {}
        self.user.GetWindowTextW.argtypes = [self.w.HWND, self.w.LPWSTR, ctypes.c_int]
        self.user.SetFocus.argtypes, self.user.SetFocus.restype = [self.w.HWND], self.w.HWND
        self.setup_theme()

    def setup_theme(self):
        w = self.w
        self.gdi.CreateSolidBrush.argtypes, self.gdi.CreateSolidBrush.restype = [w.DWORD], w.HBRUSH
        self.gdi.SetBkColor.argtypes = self.gdi.SetTextColor.argtypes = [w.HDC, w.DWORD]
        self.user.FillRect.argtypes = [w.HDC, ctypes.POINTER(w.RECT), w.HBRUSH]
        self.user.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT]
        self.user.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
        self.user.RedrawWindow.argtypes = [w.HWND, ctypes.POINTER(w.RECT), w.HANDLE, w.UINT]
        self.user.SetCapture.argtypes, self.user.SetCapture.restype = [w.HWND], w.HWND
        self.user.ReleaseCapture.argtypes = []
        self.user.LoadCursorW.argtypes, self.user.LoadCursorW.restype = [w.HINSTANCE, w.LPVOID], w.HANDLE
        self.user.SetCursor.argtypes, self.user.SetCursor.restype = [w.HANDLE], w.HANDLE
        self.user.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
        self.user.ScreenToClient.argtypes = [w.HWND, ctypes.POINTER(w.POINT)]
        self.arrow = self.user.LoadCursorW(None, ctypes.c_void_p(32512))
        self.resize_cursor = self.user.LoadCursorW(None, ctypes.c_void_p(32644))
        self.user.GetDC.argtypes, self.user.GetDC.restype = [w.HWND], w.HDC
        self.user.ReleaseDC.argtypes = [w.HWND, w.HDC]
        self.gdi.SelectObject.argtypes, self.gdi.SelectObject.restype = [w.HDC, w.HANDLE], w.HANDLE
        self.gdi.GetTextExtentPoint32W.argtypes = [w.HDC, w.LPCWSTR, ctypes.c_int, ctypes.POINTER(w.SIZE)]
        self.colors = {"background": 0xF8F5F2, "card": 0xFFFFFF, "description": 0xF9EFE4,
                       "text": 0x403025, "muted": 0x756659, "accent": 0x976026,
                       "premium": 0x52418C}  # muted burgundy #8C4152
        self.brushes = {name: self.gdi.CreateSolidBrush(color) for name, color in self.colors.items()
                        if name in {"background", "card", "description", "accent"}}
        # Clamp the initial size to the usable desktop, including a visible footer.
        self.user.SystemParametersInfoW.argtypes = [w.UINT, w.UINT, w.LPVOID, w.UINT]
        area = w.RECT()
        if self.user.SystemParametersInfoW(0x30, 0, ctypes.byref(area), 0):
            self.window_width = min(self.window_width, area.right - area.left)
            self.window_height = min(self.window_height, area.bottom - area.top)
            self.window_position = (area.left + max(0, (area.right - area.left - self.window_width) // 2),
                                    area.top + max(0, (area.bottom - area.top - self.window_height) // 2))

    def native_message(self, hwnd, message, wp, lp):
        if message == 0x10 and getattr(self, "ready", False):  # WM_CLOSE, before controls are destroyed
            self.persist_last_selection()
        if message in {0x4E, 0x2B} and self.controls.get("start"):
            result = draw_notification(self, message, lp)
            if result is not None:
                return result
        if message in {0x200, 0x201, 0x202, 0x20} and getattr(self, "ready", False):
            point = self.w.POINT()
            if message == 0x20:
                self.user.GetCursorPos(ctypes.byref(point))
                self.user.ScreenToClient(hwnd, ctypes.byref(point))
            else:
                point.x, point.y = ctypes.c_short(lp & 0xffff).value, ctypes.c_short(lp >> 16).value
            rect = self.w.RECT()
            self.client_rect(hwnd, ctypes.byref(rect))
            sx, sy, sw, sh = self.boxes(rect.right, rect.bottom)["splitter"]
            over = sx <= point.x < sx + sw and sy <= point.y < sy + sh
            if message == 0x201 and over:
                self.dragging = True
                self.user.SetCapture(hwnd)
                return 0
            if message == 0x200 and self.dragging:
                available = max(1, rect.right - 56)
                self.split_ratio = column_width(rect.right, (point.x - 28) / available) / available
                self.reflow()
                return 0
            if message == 0x202 and self.dragging:
                self.dragging = False
                self.user.ReleaseCapture()
                try:
                    save_preferences(self.root, split_ratio=self.split_ratio)
                except OSError:
                    pass  # Current layout still works; a read-only preference file must not close it.
                return 0
            if message == 0x20 and ((wp == hwnd and lp & 0xffff == 1) or self.dragging):
                self.user.SetCursor(self.resize_cursor if over or self.dragging else self.arrow)
                return 1
        if message == 0x215:  # WM_CAPTURECHANGED
            self.dragging = False
        if message == 0x24:  # WM_GETMINMAXINFO
            class MinMax(ctypes.Structure):
                _fields_ = [(key, self.w.POINT) for key in ("reserved", "maximum", "position", "minimum", "track")]
            info = ctypes.cast(lp, ctypes.POINTER(MinMax)).contents
            info.minimum.x = MIN_CLIENT[0] + 16
            info.minimum.y = MIN_CLIENT[1] + self.source_height - 24 + self.context_height - 22 + 39
            return 0
        if message == 5 and wp != 1 and getattr(self, "ready", False):
            self.reflow()
            return 0
        if message == 0x14 and hasattr(self, "brushes"):  # WM_ERASEBKGND
            rect = self.w.RECT()
            self.client_rect(hwnd, ctypes.byref(rect))
            self.user.FillRect(wp, ctypes.byref(rect), self.brushes["background"])
            for name, (x, y, width, height) in self.boxes(rect.right, rect.bottom).items():
                if name.startswith("card_"):
                    card = self.w.RECT(x, y, x + width, y + height)
                    self.user.FillRect(wp, ctypes.byref(card), self.brushes["card"])
                elif name == "detail":
                    panel = self.w.RECT(x, y, x + width, y + height)
                    self.user.FillRect(wp, ctypes.byref(panel), self.brushes["description"])
                elif name == "splitter":
                    middle = y + height // 2
                    for offset in (5, 9):
                        grip = self.w.RECT(x + offset, middle - 22, x + offset + 2, middle + 22)
                        self.user.FillRect(wp, ctypes.byref(grip), self.brushes["accent"])
            return 1
        if message in {0x138, 0x133, 0x134, 0x135} and hasattr(self, "brushes"):
            # Read-only EDIT sends WM_CTLCOLORSTATIC, editable fields use CTLCOLOREDIT.
            name = next((key for key, handle in self.controls.items() if handle == lp), "")
            background = "description" if name == "detail" else "background" if name in {
                "stage", "source", "context", "notice", "language_label", "balance", "quota"} else "card"
            color = "accent" if name in {"catalog_heading", "algorithm", "parameters_label"} else "text"
            if name in {"context", "found", "catalog_date", "language_label"}:
                color = "muted"
            if name == "premium_badge" or (name == "notice" and self.premium_notice):
                color = "premium"
            if name == "balance" and self.balance_result and self.balance_result.credits is not None and self.balance_result.credits <= 0:
                color = "premium"
            self.gdi.SetTextColor(wp, 0x000000 if name == 'quota' else self.colors[color])
            self.gdi.SetBkColor(wp, self.colors[background])
            return self.brushes[background]
        return None

    def cleanup(self):
        for brush in self.brushes.values():
            self.delete_object(brush)

    def boxes(self, width, height):
        return layout(width, height, self.split_ratio, len(self.field_controls), self.has_premium,
                      source_height=self.source_height, context_height=self.context_height)

    def paint_all(self):
        self.user.RedrawWindow(self.hwnd, None, None, 0x585)  # erase/repaint parent AND every child

    def reflow(self):
        if self.reflowing:
            return
        self.reflowing = True
        try:
            self.measure_header()
            self.reflow_controls()
        finally:
            self.reflowing = False

    def measure_header(self):
        rect = self.w.RECT()
        self.client_rect(self.hwnd, ctypes.byref(rect))
        dc = self.user.GetDC(self.hwnd)
        previous = self.gdi.SelectObject(dc, self.font)
        try:
            size = self.w.SIZE()
            self.gdi.GetTextExtentPoint32W(dc, 'Ag', 2, ctypes.byref(size))
            line_height = max(1, size.cy)
        finally:
            self.gdi.SelectObject(dc, previous)
            self.user.ReleaseDC(self.hwnd, dc)
        for name, minimum in (('source', 24), ('context', 22)):
            self.user.SetWindowPos(self.controls[name], None, *self.boxes(rect.right, rect.bottom)[name], 0x11C)
            lines = self.send(self.controls[name], 0xBA, 0, 0)  # native wrapping, including unbroken names
            setattr(self, name + '_height', max(minimum, lines * line_height + 4))
        minimum = MIN_CLIENT[1] + self.source_height - 24 + self.context_height - 22
        if rect.bottom < minimum:
            outer = self.w.RECT()
            self.user.GetWindowRect(self.hwnd, ctypes.byref(outer))
            self.user.SetWindowPos(self.hwnd, None, 0, 0, outer.right - outer.left,
                                   outer.bottom - outer.top + minimum - rect.bottom, 0x16)

    def reflow_controls(self):
        rect = self.w.RECT()
        self.client_rect(self.hwnd, ctypes.byref(rect))
        boxes = self.boxes(rect.right, rect.bottom)
        for name, handle in self.controls.items():
            box = boxes.get(name)
            if name == "detail":
                x, y, width, height = box
                box = (x, y + 10, width, height - 20)
            if name.startswith("param_"):
                for identifier, (_, control, _) in self.field_controls.items():
                    if name == control or name in {control + "_label", control + "_help"}:
                        box = boxes["field_" + str(identifier - 100) + name[len(control):]]
                        if name == control and self.control_kinds[name] == "EDIT":
                            box = (*box[:3], 28)
                        break
            if box:
                # Batch geometry with no copied pixels or intermediate repaint.
                self.user.SetWindowPos(handle, None, *box, 0x11C)
                if self.control_kinds[name] == "COMBOBOX":
                    self.send(handle, 0x160, box[2], 0)  # popup width follows the actual field
        # Inner padding for the scrollable description, independent of its background.
        _, _, width, height = boxes["detail"]
        # Keep vertical margins outside the scrolling surface: RichEdit otherwise
        # copies old table pixels into the bottom padding during native scrolling.
        inset = self.w.RECT(12, 0, width - 24, height - 20)
        self.send(self.controls["detail"], 0xB3, 0, ctypes.addressof(inset))
        self.render_description(width - 42)
        # A shorter viewport must not hide the selected model below its last row.
        listing = self.controls["algorithms"]
        index, top = self.send(listing, 0x188, 0, 0), self.send(listing, 0x18E, 0, 0)
        row_height = max(1, self.send(listing, 0x1A1, 0, 0))
        visible = max(1, (boxes["algorithms"][3] - 20) // row_height)
        if index >= 0 and not top <= index < top + visible:
            self.send(listing, 0x197, max(0, index - visible // 2), 0)
        self.paint_all()

    def render_description(self, width):
        payload = document_rtf(self.detail_blocks, width * 15)
        if payload == self.detail_rtf:
            return
        scroll = self.w.POINT()
        if not self.reset_description_scroll:
            self.send(self.controls["detail"], 0x4DD, 0, ctypes.addressof(scroll))
        stream_rtf(self.send, self.controls["detail"], payload)
        self.send(self.controls["detail"], 0x4DE, 0, ctypes.addressof(scroll))
        self.reset_description_scroll = False
        self.detail_rtf = payload

    def switch_language(self, value):
        if value not in LANGUAGES:
            return
        # Keep form values and filter identity; translating must never reapply a preset.
        spec = self.selected_spec()
        selected = self.profile_names[max(0, self.send(self.controls["profiles"], 0x147, 0, 0))]
        group_index = max(0, self.send(self.controls["group"], 0x147, 0, 0))
        save_language(self.root, value)
        language.set(value)
        self.spec = spec
        self.updating = True
        try:
            self.send(self.controls["language"], 0x14E, LANGUAGES.index(value), 0)
            self.relabel()
            self.refill_profiles(selected)
            self.refill_groups()
            self.send(self.controls["group"], 0x14E, group_index, 0)
            self.filter_list()
            self.refresh()
            self.catalog_date()
        finally:
            self.updating = False
        self.reflow()

    def value(self, control, length=4097):
        buffer = ctypes.create_unicode_buffer(length)
        self.user.GetWindowTextW(self.controls[control], buffer, length)
        return buffer.value

    def combo_add(self, control, value):
        buffer = ctypes.create_unicode_buffer(str(value))
        self.send(self.controls[control], 0x143, 0, ctypes.cast(buffer, ctypes.c_void_p).value)

    def refill_profiles(self, selected=None):
        self.profile_names = [None] + sorted(self.profiles, key=str.casefold)
        self.send(self.controls["profiles"], 0x14B, 0, 0)
        for name in self.profile_names:
            self.combo_add("profiles", name or tr('Текущие настройки'))
        self.send(self.controls["profiles"], 0x14E, self.profile_names.index(selected), 0)

    def selected_spec(self):
        if self.spec.preset_id is not None:
            return self.spec
        index = self.send(self.controls["format"], 0x147, 0, 0)
        return with_format(self.spec, self.formats[index])

    def persist_last_selection(self):
        try:
            remember_choice(self.root, self.selected_spec())
        except (InspectionError, OSError, ValueError, TypeError, KeyError):
            # Optional preference persistence must not prevent closing a window.
            from .core.feedback import show_message
            show_message(tr('Не удалось сохранить последние настройки. Проверьте доступ к папке config.'), error=True)

    def update_remember_policy(self):
        enabled = bool(self.send(self.controls['remember_settings'], 0xF0, 0, 0))
        index = max(0, self.send(self.controls['remember_mode'], 0x147, 0, 0))
        mode = ('successful', 'all')[index]
        save_preferences(self.root, remember_last_settings=enabled, remember_settings_mode=mode)
        self.remember_enabled, self.remember_mode = enabled, mode
        self.enable(self.controls['remember_mode'], enabled)

    def refill_groups(self):
        index = self.send(self.controls["group"], 0x147, 0, 0)
        selected = self.groups[index] if 0 <= index < len(self.groups) else None
        if self.initial_group_pending and self.catalog:
            # Group membership follows the saved algorithm, including old
            # snapshots and catalogs fetched after the window has opened.
            algorithm = self.catalog.get(self.spec.sep_type) if self.spec.preset_id is None else None
            selected = group_name(algorithm) if algorithm else None
            self.initial_group_pending = False
        self.groups = [None] + list(dict.fromkeys(group_name(item) for item in self.catalog.values()))
        self.send(self.controls["group"], 0x14B, 0, 0)
        for name in self.groups:
            self.combo_add("group", name or tr('Все группы'))
        self.send(self.controls["group"], 0x14E, self.groups.index(selected) if selected in self.groups else 0, 0)

    def filter_list(self):
        index = self.send(self.controls["group"], 0x147, 0, 0)
        self.algorithm_ids = filter_algorithms(self.catalog, self.groups[max(0, index)], self.value("search"))
        self.send(self.controls["algorithms"], 0x184, 0, 0)
        labels = []
        for key in self.algorithm_ids:
            algorithm = self.catalog[key]
            label = f"{key} · {algorithm['name']}" + premium_label(algorithm)
            label += availability_label(algorithm)
            labels.append(label)
            buffer = ctypes.create_unicode_buffer(label)
            self.send(self.controls["algorithms"], 0x180, 0, ctypes.cast(buffer, ctypes.c_void_p).value)
        dc = self.user.GetDC(self.controls["algorithms"])
        if dc:
            previous = self.gdi.SelectObject(dc, self.font)
            try:
                extent = 0
                for label in labels:
                    size = self.w.SIZE()
                    self.gdi.GetTextExtentPoint32W(dc, label, len(label.encode("utf-16-le")) // 2, ctypes.byref(size))
                    extent = max(extent, size.cx)
                self.send(self.controls["algorithms"], 0x194, extent + 12, 0)
            finally:
                self.gdi.SelectObject(dc, previous)
                self.user.ReleaseDC(self.controls["algorithms"], dc)
        index = self.algorithm_ids.index(self.spec.sep_type) if self.spec.sep_type in self.algorithm_ids else -1
        self.send(self.controls["algorithms"], 0x186, index, 0)
        if index >= 0:
            self.send(self.controls["algorithms"], 0x197, max(0, index - 5), 0)  # LB_SETTOPINDEX
        self.text("found", tr('Алгоритмов: {v0}', v0=len(self.algorithm_ids)))

    def show_loaded(self):
        self.updating = True
        self.set_text(self.controls["search"], "")
        self.send(self.controls["group"], 0x14E, 0, 0)
        self.updating = False
        self.filter_list()
        self.refresh()

    def refresh(self, focus_field=None):
        self.updating = True
        if self.ready:
            self.send(self.hwnd, 0xB, 0, 0)  # WM_SETREDRAW: do not expose transient parameter geometry
        try:
            preset = self.spec.preset_id is not None
            self.send(self.controls["format"], 0x14E, -1 if preset else self.formats.index(self.spec.output_format), 0)
            self.enable(self.controls["format"], not preset)
            self.text("format_label", tr('Формат задаёт серверный пресет') if preset else tr('Формат результата'))
            algorithm = self.catalog.get(self.spec.sep_type, {})
            title = tr('Пресет MVSEP №{v0}', v0=self.spec.preset_id) if preset else algorithm.get("name", tr('Алгоритм №{v0}', v0=self.spec.sep_type))
            self.text("algorithm", title)
            badge = premium_label(algorithm).strip().strip("[]") if not preset else ""
            self.has_premium = bool(badge)
            self.text("premium_badge", badge[:1].upper() + badge[1:])
            for name in list(self.controls):
                if name.startswith("param_"):
                    self.destroy(self.controls.pop(name))
                    self.selectable_callbacks.pop(name, None)
                    self.cached.pop(name, None)
                    self.control_kinds.pop(name, None)
            self.field_controls.clear()
            self.help_links.clear()
            reason = ""
            if preset:
                detail = tr('Алгоритмы, параметры и формат определяет серверный пресет MVSEP.\r\nЧтобы настроить отдельный алгоритм, выберите его слева.')
                blocks = [Paragraph(detail)]
                active_fields = []
            else:
                reason = unsupported_reason(algorithm)
                try:
                    active_fields = visible_fields(algorithm, self.spec.api_options)[:4] if not reason else []
                    help_text = parameter_help(algorithm, self.spec.api_options) if not reason else ""
                    short, full = description_fragments(algorithm)
                    outputs = expected_outputs(algorithm, self.spec.api_options) if not reason else {}
                    output_text = (output_count_text(len(outputs)) + ", ".join(outputs.values()) +
                                   tr('. Это результаты одного исходного файла; фактический список вернёт MVSEP.')) if outputs else ""
                    matches = matching_options(algorithm, self.value("search"))
                    blocks = [Paragraph(reason)] if reason else []
                    if matches:
                        blocks += [Paragraph(tr('Найдено в настройках — выберите нужное значение в соответствующем поле ниже:'))]
                        blocks += [Paragraph(match) for match in matches]
                    short_blocks = catalog_blocks(short, self.spec.sep_type)
                    blocks += short_blocks
                    blocks += [Paragraph(text) for text in (output_text, help_text) if text]
                    if full and full != short:
                        full_blocks = catalog_blocks(full, self.spec.sep_type)
                        if short_blocks and full_blocks[:len(short_blocks)] == short_blocks:
                            full_blocks = full_blocks[len(short_blocks):]
                        blocks += full_blocks
                except InspectionError as exc:
                    reason, detail, active_fields = str(exc), str(exc), []
                    blocks = [Paragraph(detail)]
            self.detail_blocks = blocks or [Paragraph(tr('MVSEP не предоставил описание.'))]
            self.detail_rtf = None
            self.reset_description_scroll = True
            self.text("parameters_label", tr('Параметры алгоритма') if active_fields else tr('Дополнительных параметров нет') if not reason and not preset else "")
            self.enable(self.controls["reset"], bool(active_fields))
            for row, field in enumerate(active_fields):
                key, y = field["name"], 368 + row * 54
                name = f"param_{key}"
                label = field_label(field)
                if label != "Model type" and field.get("required"):
                    label += " *"
                self.selectable(name + "_label", label, 340, y, 562, 21)
                if field.get("help_link"):
                    self.child(name + "_help", "BUTTON", tr('Справка'), 918, y - 1, 84, 22, 0x10000, 200 + row)
                    self.help_links[200 + row] = field["help_link"]
                if field.get("input_type", "select") == "select":
                    values = choices(field)
                    current = self.spec.api_options.get(key)
                    keys = list(values)
                    self.child(name, "COMBOBOX", "", 340, y + 23, 662, 220, 0x00210003, 100 + row)
                    if current not in values:
                        keys.insert(0, current)
                        self.combo_add(name, tr('— Выберите значение —') if current in (None, "") else tr('Недоступное сохранённое значение: {v0}', v0=current))
                    for value in values:
                        self.combo_add(name, option_label(algorithm, field, value))
                    self.send(self.controls[name], 0x14E, keys.index(current), 0)
                    self.field_controls[100 + row] = (key, name, keys)
                else:
                    self.child(name, "EDIT", self.spec.api_options.get(key, ""), 340, y + 23, 662, 27, 0x00810080, 100 + row)
                    self.send(self.controls[name], 0xC5, 4096, 0)
                    self.field_controls[100 + row] = (key, name, None)
            try:
                validate_selection(self.spec, self.catalog)
            except InspectionError as exc:
                reason = str(exc)
            self.enable(self.controls["start"], not reason)
            self.enable(self.controls["save"], not reason)
            paid = premium_requirements(self.spec, self.catalog) if not reason else []
            self.premium_notice = bool(paid)
            self.send(self.controls["notice"], 0x30, (self.own_fonts[2] or self.font) if paid else self.font, 1)
            self.text("notice", reason or (tr('Выбраны параметры Premium. Перед отправкой проверьте доступ и баланс MVSEP.') if paid else tr(self.default_notice)))
            if focus_field and f"param_{focus_field}" in self.controls:
                self.user.SetFocus(self.controls[f"param_{focus_field}"])
        finally:
            self.updating = False
            if self.ready:
                self.send(self.hwnd, 0xB, 1, 0)
        self.reflow()

    LABELS = {
        "stage": "Новое разделение", "profiles_label": "Мои наборы настроек",
        "import": "Из результата…", "catalog_heading": "Каталог алгоритмов",
        "group_label": "Группа алгоритмов", "search_label": "Поиск: алгоритмы, модели, настройки",
        "reload": "Обновить каталог", "reset": "Сбросить параметры",
        "format_label": "Формат результата", "name_label": "Сохранить эти настройки как набор",
        "save": "Сохранить набор", "cancel": "Отмена", "start": "Начать разделение",
        "folder_label": "Папка для результатов",
        "folder_choose": "Выбрать папку…", "folder_default": "Сбросить путь",
        "folder_remember": "Использовать для следующих разделений",
        "balance_refresh": "Обновить данные",
        "last_result": "Последний результат",
        "remember_settings": "Сохранять последние настройки",
        "after_label": "После разделения:",
        "auto_pool": "Добавить в медиа проекта",
        "auto_timeline": "Добавить на дорожки (и в медиа)",
    }

    def relabel(self):
        self.set_text(self.hwnd, tr(self.window_title))
        for key, message in self.LABELS.items():
            self.text(key, tr(message))
        self.text("balance", balance_text(self.balance_result, self.balance_fetching))
        self.text('quota', quota_text(self.quota_monitor.result).replace('\r\n', ' '))
        metadata = self.job.source_clip
        self.text("context", tr("Проект: {project}  •  Дорожка: {track}",
                  project=metadata.get("project_name", "—"), track=metadata.get("clip", {}).get("track_name", "—")))
        self.send(self.controls["format"], 0x14B, 0, 0)
        for code in self.formats:
            self.combo_add("format", tr(FORMAT_NAMES[code]))
        self.send(self.controls['remember_mode'], 0x14B, 0, 0)
        for label in (tr('Только успешные'), tr('Все')):
            self.combo_add('remember_mode', label)
        self.send(self.controls['remember_mode'], 0x14E, ('successful', 'all').index(self.remember_mode), 0)
        self.enable(self.controls['remember_mode'], self.remember_enabled)

    def selectable(self, name, text="", x=0, y=0, width=100, height=28):
        handle = self.child(name, "EDIT", text, x, y, width, height, READONLY_LABEL)
        self.selectable_callbacks[name] = enable_select_all(self, handle)
        return handle

    def build_controls(self):
        def add(name, kind="STATIC", text="", style=0, identifier=0):
            if kind == "STATIC":
                return self.selectable(name, text)
            return self.child(name, kind, text, 0, 0, 100, 28, style, identifier)
        for key in ("stage", "source", "context", "profiles_label", "catalog_heading", "group_label",
                    "search_label", "found", "catalog_date", "algorithm", "parameters_label",
                    "format_label", "name_label", "notice", "premium_badge", "balance", "quota", "after_label"):
            add(key, style=0x4080 if key in {"source", "context"} else 0)
        self.send(self.controls["stage"], 0x30, self.own_fonts[1] or self.font, 1)
        quota_font = self.gdi.CreateFontW(-16, 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 5, 0, 'Segoe UI')
        if quota_font:
            self.own_fonts.append(quota_font)
        self.send(self.controls['quota'], 0x30, quota_font or self.own_fonts[2] or self.font, 1)
        for key in ("catalog_heading", "algorithm", "premium_badge", "profiles_label", "format_label", "name_label", "parameters_label"):
            self.send(self.controls[key], 0x30, self.own_fonts[2] or self.font, 1)
        self.text("source", Path(self.job.source_file).name)
        add("language_label", text="Язык / Language")
        add("language", "COMBOBOX", style=0x00210003, identifier=30)
        for label in ("RU — Русский", "EN — English"):
            self.combo_add("language", label)
        self.send(self.controls["language"], 0x14E, LANGUAGES.index(language.get()), 0)
        for key, identifier in (("profiles", 10), ("group", 20), ("format", 11)):
            add(key, "COMBOBOX", style=0x00210003, identifier=identifier)
        for key, identifier, limit in (("search", 21, 120), ("name", 12, 80)):
            add(key, "EDIT", style=0x00810080, identifier=identifier)
            self.send(self.controls[key], 0xC5, limit, 0)
        add("folder_label")
        add("folder_path", "EDIT", str(self.destination), style=0x00810880, identifier=33)
        add("folder_remember", "BUTTON", style=0x10003, identifier=34)
        add('remember_settings', 'BUTTON', style=0x10003, identifier=37)
        self.send(self.controls['remember_settings'], 0xF1, int(self.remember_enabled), 0)
        add('remember_mode', 'COMBOBOX', style=0x00210003, identifier=38)
        add('auto_pool', 'BUTTON', style=0x10003, identifier=39)
        add('auto_timeline', 'BUTTON', style=0x10003, identifier=40)
        for key, identifier in (("import", 14), ("reload", 23), ("reset", 24), ("save", 13),
                                ("folder_choose", 31), ("folder_default", 32),
                                ("cancel", 2), ("start", 1), ("balance_refresh", 35), ("last_result", 36)):
            style = (0x10001 if self.animated_bar else 0x1000B) if key == "start" else 0x10000
            add(key, "BUTTON", style=style, identifier=identifier)
        self.enable(self.controls['last_result'], self.last_result is not None)
        add("algorithms", "LISTBOX", style=0x00B10101, identifier=22)
        self.send(self.controls["algorithms"], 0x194, 980, 0)
        add("detail", "RICHEDIT50W", style=0x04210844)
        self.send(self.controls["detail"], 0x443, 0, self.colors["description"])
        self.detail_scroll_callback = repaint_on_scroll(self, self.controls["detail"])
        self.relabel()
        self.refill_profiles()
        self.refill_groups()
        self.filter_list()
        self.refresh()
        self.catalog_date()
        self.ready = True
        self.reflow()
        self.start_balance_refresh()
        if not self.catalog:
            self.start_refresh()

    def catalog_date(self):
        try:
            stamp = (self.root / "logs/mvsep_algorithms.json").stat().st_mtime
            self.text("catalog_date", datetime.fromtimestamp(stamp).strftime("%d.%m.%Y"))
        except OSError:
            self.text("catalog_date", tr('Нет копии'))

    def start_refresh(self):
        if self.fetching:
            return
        self.fetching = True
        self.enable(self.controls["reload"], False)
        self.text("catalog_date", tr('Загрузка…'))
        def fetch():
            try:
                self.catalog_messages.put((refresh_catalog(self.root), None))
            except Exception:
                self.catalog_messages.put((None, True))  # Localize on the UI thread, in its current language.
        threading.Thread(target=fetch, daemon=True).start()

    def start_balance_refresh(self):
        if self.balance_fetching:
            return
        self.balance_fetching = True
        self.quota_monitor.refresh_requested = True
        self.balance_result = None
        self.enable(self.controls["balance_refresh"], False)
        self.text("balance", balance_text(None, True))
        def fetch():
            try:
                result = self.balance_loader(self.root)
            except Exception:
                result = BalanceResult("unavailable")
            self.balance_messages.put(result)
        threading.Thread(target=fetch, daemon=True).start()

    def tick(self):
        quota = self.quota_monitor.poll('setup', visible=bool(self.is_visible(self.hwnd)))
        self.text('quota', quota_text(quota).replace('\r\n', ' '))
        try:
            self.balance_result = self.balance_messages.get_nowait()
        except queue.Empty:
            pass
        else:
            self.balance_fetching = False
            self.enable(self.controls["balance_refresh"], True)
            self.text("balance", balance_text(self.balance_result))
        try:
            catalog, error = self.catalog_messages.get_nowait()
        except queue.Empty:
            pass
        else:
            self.fetching = False
            self.enable(self.controls["reload"], True)
            self.catalog_date()
            if catalog is not None:
                self.catalog = catalog
                self.refill_groups()
                self.filter_list()
                self.refresh()
            if error:
                message = tr('Не удалось обновить каталог. ')
                message += tr('Используется сохранённая копия; попробуйте позже.') if self.catalog else tr('Проверьте соединение и нажмите «Обновить каталог».')
                self.text("notice", message)
        request = self.path.parent / "show-window"
        try:
            stamp = request.stat().st_mtime_ns
            if stamp != self.request_time:
                self.request_time = stamp
                self.show(self.hwnd, 9)
                self.focus(self.hwnd)
        except OSError:
            pass

    def on_command(self, identifier, notification):
        if not self.ready or self.updating:
            return False
        try:
            if identifier == 37 or (identifier == 38 and notification == 1):
                self.update_remember_policy()
                return True
            if identifier == 40:
                timeline = bool(self.send(self.controls['auto_timeline'], 0xF0, 0, 0))
                if timeline:
                    self.send(self.controls['auto_pool'], 0xF1, 1, 0)
                self.enable(self.controls['auto_pool'], not timeline)
                return True
            if identifier == 36 and self.last_result:
                from .separation import show_progress
                show_progress(self.root, self.last_result)
                self.persist_last_selection()
                self.destroy(self.hwnd)  # cancel this unsubmitted setup; show the saved result instead
                return True
            if identifier == 35:
                self.start_balance_refresh()
                return True
            if identifier in (31, 32):
                selected = choose_output_folder(self.hwnd, self.destination) if identifier == 31 else (self.root / "output").resolve()
                if selected is not None:
                    self.destination = selected
                    self.text("folder_path", str(selected))
                return True
            if identifier == 30 and notification == 1:
                index = self.send(self.controls["language"], 0x147, 0, 0)
                self.switch_language(LANGUAGES[index])
                return True
            if identifier == 14:
                path = choose_result_settings(self.hwnd, self.root / "output")
                if path is not None:
                    spec, label = read_result_settings(path)
                    self.initial = self.spec = spec
                    self.refill_profiles()
                    self.show_loaded()
                    self.set_text(self.controls["name"], label)
                return True
            if identifier == 10 and notification == 1:
                index = self.send(self.controls["profiles"], 0x147, 0, 0)
                name = self.profile_names[index]
                self.spec = self.profiles[name] if name else self.initial
                self.show_loaded()
                return True
            if (identifier == 20 and notification == 1) or (identifier == 21 and notification == 0x300):
                self.filter_list()
                if identifier == 21:
                    self.refresh()
                return True
            if identifier == 22 and notification == 1:
                index = self.send(self.controls["algorithms"], 0x188, 0, 0)
                if 0 <= index < len(self.algorithm_ids):
                    self.spec = new_algorithm(self.selected_spec(), self.catalog[self.algorithm_ids[index]])
                    self.refresh()
                return True
            if identifier == 23:
                self.start_refresh()
                return True
            if identifier == 24:
                if self.spec.preset_id is None and self.spec.sep_type in self.catalog:
                    self.spec = new_algorithm(self.selected_spec(), self.catalog[self.spec.sep_type])
                    self.refresh()
                return True
            if identifier == 11 and notification == 1:
                self.spec = self.selected_spec()
                return True
            if identifier in self.help_links:
                os.startfile(validate_url(self.help_links[identifier]))
                return True
            if identifier in self.field_controls:
                key, name, values = self.field_controls[identifier]
                if (values is not None and notification == 1) or (values is None and notification == 0x300):
                    options = dict(self.spec.api_options)
                    value = values[self.send(self.controls[name], 0x147, 0, 0)] if values is not None else self.value(name)
                    if value is None:
                        options.pop(key, None)
                    else:
                        options[key] = value
                    options = normalized_options(self.catalog[self.spec.sep_type], options)
                    self.spec = replace(self.selected_spec(), api_options=options)
                    if values is not None:
                        self.refresh(focus_field=key)
                    else:
                        try:
                            validate_selection(self.spec, self.catalog)
                            valid = True
                        except InspectionError:
                            valid = False
                        self.enable(self.controls["start"], valid)
                        self.enable(self.controls["save"], valid)
                    return True
            if identifier == 13:
                spec = self.selected_spec()
                validate_selection(spec, self.catalog)
                name = self.value("name", 81)
                save_profile(self.root, name, spec)
                self.profiles = load_profiles(self.root)
                self.spec = spec
                self.refill_profiles(name.strip())
                self.refresh()
                self.text("notice", tr('Набор сохранён. Для отправки файла нажмите «Начать разделение».'))
                return True
            if identifier == 1:
                spec = self.selected_spec()
                validate_selection(spec, self.catalog)
                prepare_output_root(self.destination)
                if self.send(self.controls["folder_remember"], 0xF0, 0, 0):
                    save_preferences(self.root, output_root=str(self.destination))
                self.job.output_root = str(self.destination.resolve())
                self.job.remember_settings = self.remember_enabled
                self.job.post_import_target = ('timeline' if self.send(self.controls['auto_timeline'], 0xF0, 0, 0)
                                               else 'media_pool' if self.send(self.controls['auto_pool'], 0xF0, 0, 0)
                                               else 'none')
                self.job.post_import_state = 'pending' if self.job.post_import_target != 'none' else None
                index = self.send(self.controls["profiles"], 0x147, 0, 0)
                name = self.profile_names[index]
                if name and self.profiles[name] != spec:
                    name += tr(' (изменён для этой задачи)')
                self.choice = (spec, name)
                # Freeze labels from the catalog actually shown, even if a background
                # catalog refresh finishes after this window is closed.
                snapshot_job = replace(self.job, mode=spec.mode, sep_type=spec.sep_type, preset_id=spec.preset_id,
                                       api_options=dict(spec.api_options), output_format=spec.output_format,
                                       settings_profile=name)
                self.job.settings_snapshot = capture_settings(self.root, snapshot_job, catalog=self.catalog)
                self.persist_last_selection()
                self.destroy(self.hwnd)
                return True
            if identifier == 2:
                self.persist_last_selection()
                self.destroy(self.hwnd)
                return True
        except (InspectionError, MVSEPError, OSError, ValueError) as exc:
            if identifier == 30:
                self.send(self.controls["language"], 0x14E, LANGUAGES.index(language.get()), 0)
            self.text("notice", str(exc))
            return True
        return False
