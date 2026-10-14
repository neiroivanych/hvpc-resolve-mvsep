"""Non-modal Windows progress viewer. Stdlib only; no Tk in ResolvePython.

This process reads local state; explicit buttons launch a separate Resolve importer.
During work, Close hides it in the notification area; the worker remains independent.
A per-job lock prevents duplicate UIs/icons.
"""

from __future__ import annotations

import ctypes
import os
import time
from pathlib import Path

from .core.active import worker_running
from .core.background import BackgroundState, background_hint, tray_tip
from .core.errors import InspectionError
from .core.job import Job
from .core.output import output_directory, output_base
from .core.profiles import job_spec, save_profile
from .core.lock import SingleJobLock
from .core.progress import describe
from .core.pipeline import PipelineView, pipeline_view
from .core.i18n import load_language, tr, using_language
from .core.quota import QuotaMonitor, quota_text
from .windows_style import visual_styles
from .app_icon import WindowIcons
from .selectable_text import READONLY_LABEL, enable_select_all
from .progress_panel import ProgressPanel
from .resolve_monitor import ResolveMonitor
from .core.resolve_import import (read_receipt, import_running, import_message, track_range,
                                 track_scroll_hint, track_details, timeline_confirmed)


def show_progress_window(path: Path, *, auto_close_ms: int = 0, on_ready=None) -> int:
    """auto_close_ms/on_ready support an offline native-window smoke test."""
    if os.name != "nt":
        raise OSError("The progress window currently supports Windows only.")
    path = path.resolve()
    saved = Job.load(path)
    if path != path.parents[2] / "jobs" / saved.id / "job.json":
        raise ValueError("Expected jobs/<id>/job.json in this project.")
    request = path.parent / "show-window"
    request.touch()
    try:
        with SingleJobLock(path.parent / ".window.lock"):
            with using_language(load_language(path.parents[2])), visual_styles() as animated_bar:
                return _Window(path, auto_close_ms, on_ready, animated_bar=animated_bar).run()
    except InspectionError:
        # Existing viewer notices the request and restores itself on its next tick.
        return 0


class _Window:
    window_style = 0x02CF0000
    window_title = "HVPC Resolve-MVSEP — состояние задачи"
    window_height = 920
    window_width = 760
    window_position = (200, 40)
    def __init__(self, path: Path, auto_close_ms: int, on_ready, *, animated_bar: bool):
        from ctypes import wintypes as w
        self.w = w
        self.path = path
        self.started = time.monotonic()
        self.auto_close_ms = auto_close_ms
        self.on_ready = on_ready
        self.copy_callbacks = {}
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        self.controls = {}
        self.control_kinds = {}
        self.cached = {}
        self.request_time = None
        self.hwnd = None
        self.callback_error = None
        self.animated_bar = animated_bar
        self.bar_mode = None
        self.bar_position = None
        self.profile_name_filled = False
        self.progress_panel = None
        self.quota_monitor = None
        self.import_process = None
        self.resume_process = None
        self.import_error = ''
        self.import_warning = False
        self.resolve_monitor = ResolveMonitor(path.parents[2], path)
        self.resolve_status = self.resolve_monitor.state
        self.background = BackgroundState()
        self.tray = None
        self.background_notified = False
        self.read_error_since = None

        def bind(dll, name, result, *args):
            function = getattr(dll, name)
            function.restype, function.argtypes = result, args
            return function

        self.proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

        class WindowClass(ctypes.Structure):
            _fields_ = [("style", w.UINT), ("lpfnWndProc", self.proc_type),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", w.HINSTANCE), ("hIcon", w.HICON), ("hCursor", w.HANDLE),
                        ("hbrBackground", w.HBRUSH), ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR)]

        self.WindowClass = WindowClass
        self.module = bind(self.kernel, "GetModuleHandleW", w.HMODULE, w.LPCWSTR)(None)
        self.register = bind(self.user, "RegisterClassW", w.ATOM, ctypes.POINTER(WindowClass))
        self.unregister = bind(self.user, "UnregisterClassW", w.BOOL, w.LPCWSTR, w.HINSTANCE)
        self.create = bind(self.user, "CreateWindowExW", w.HWND, w.DWORD, w.LPCWSTR, w.LPCWSTR,
                           w.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                           w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID)
        self.default = bind(self.user, "DefWindowProcW", ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        self.destroy = bind(self.user, "DestroyWindow", w.BOOL, w.HWND)
        self.show = bind(self.user, "ShowWindow", w.BOOL, w.HWND, ctypes.c_int)
        self.set_style = bind(self.user, "SetWindowLongW", w.LONG, w.HWND, ctypes.c_int, w.LONG)
        self.focus = bind(self.user, "SetForegroundWindow", w.BOOL, w.HWND)
        self.set_text = bind(self.user, "SetWindowTextW", w.BOOL, w.HWND, w.LPCWSTR)
        self.enable = bind(self.user, "EnableWindow", w.BOOL, w.HWND, w.BOOL)
        self.move = bind(self.user, "MoveWindow", w.BOOL, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.BOOL)
        self.client_rect = bind(self.user, "GetClientRect", w.BOOL, w.HWND, ctypes.POINTER(w.RECT))
        self.is_visible = bind(self.user, "IsWindowVisible", w.BOOL, w.HWND)
        self.invalidate = bind(self.user, "InvalidateRect", w.BOOL, w.HWND, ctypes.POINTER(w.RECT), w.BOOL)
        self.send = bind(self.user, "SendMessageW", ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        self.timer = bind(self.user, "SetTimer", ctypes.c_size_t, w.HWND, ctypes.c_size_t, w.UINT, w.LPVOID)
        self.kill_timer = bind(self.user, "KillTimer", w.BOOL, w.HWND, ctypes.c_size_t)
        self.quit = bind(self.user, "PostQuitMessage", None, ctypes.c_int)
        self.get_message = bind(self.user, "GetMessageW", w.BOOL, ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
        self.translate = bind(self.user, "TranslateMessage", w.BOOL, ctypes.POINTER(w.MSG))
        self.dispatch = bind(self.user, "DispatchMessageW", ctypes.c_ssize_t, ctypes.POINTER(w.MSG))
        self.dialog_message = bind(self.user, "IsDialogMessageW", w.BOOL, w.HWND, ctypes.POINTER(w.MSG))
        self.stock = bind(self.gdi, "GetStockObject", w.HANDLE, ctypes.c_int)
        make_font = bind(self.gdi, "CreateFontW", w.HANDLE, *([ctypes.c_int] * 5),
                         *([w.DWORD] * 8), w.LPCWSTR)
        self.delete_object = bind(self.gdi, "DeleteObject", w.BOOL, w.HANDLE)
        self.own_fonts = [make_font(-16, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI"),
                          make_font(-22, 0, 0, 0, 600, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI"),
                          make_font(-16, 0, 0, 0, 600, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI")]
        self.font = self.own_fonts[0] or self.stock(17)
        self.callback = self.proc_type(self._dispatch)
        self.common = ctypes.WinDLL("comctl32", use_last_error=True)
        class InitControls(ctypes.Structure):
            _fields_ = [("dwSize", w.DWORD), ("dwICC", w.DWORD)]
        initialize = bind(self.common, "InitCommonControlsEx", w.BOOL, ctypes.POINTER(InitControls))
        if not initialize(ctypes.byref(InitControls(ctypes.sizeof(InitControls), 0x20))):
            raise ctypes.WinError(ctypes.get_last_error())

    def text(self, name, value):
        if self.cached.get(name) != value:
            line = self.send(self.controls[name], 0xCE, 0, 0) if name == "detail" else 0
            self.set_text(self.controls[name], value)
            if line:
                self.send(self.controls[name], 0xB6, 0, line)  # keep scrolled details in view
            self.cached[name] = value
            if name == 'source' and self.progress_panel:
                self.progress_panel.reflow()

    def child(self, name, kind, text, x, y, width, height, style=0, identifier=0):
        selectable = kind == "STATIC"
        if selectable:
            kind, style = "EDIT", READONLY_LABEL
        hwnd = self.create(0, kind, text, 0x50000000 | style, x, y, width, height,
                           self.hwnd, identifier, self.module, None)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self.controls[name] = hwnd
        self.control_kinds[name] = kind
        self.send(hwnd, 0x30, self.font, 1)  # WM_SETFONT
        if selectable:
            self.copy_callbacks[name] = enable_select_all(self, hwnd)
        return hwnd

    def tick(self):
        try:
            job = Job.load(self.path)
        except (OSError, ValueError, TypeError, KeyError):
            self.text("stage", tr('Не удаётся прочитать состояние. Повторяем…'))
            if self.progress_panel:
                self.progress_panel.update(PipelineView(None, '', tr('Ожидаем данные'),
                    note=tr('Не удаётся прочитать состояние. Повторяем…'), tone='idle'))
            if self.read_error_since is None:
                self.read_error_since = time.monotonic()
            if time.monotonic() - self.read_error_since >= 5 and self.background.hidden:
                self.restore_window(attention=True)
            return  # atomic replacement or a transient file-sharing violation
        self.read_error_since = None
        running = worker_running(self.path) or time.monotonic() - self.started < 5
        connection = self.resolve_monitor.tick()
        if connection != self.resolve_status:
            self.resolve_status = connection
            self.invalidate(self.hwnd, None, True)
        connection_title, connection_note = connection.copy(job)
        self.text('connection_title', connection_title)
        self.text('hint', connection_note)
        self.enable(self.controls['connection_retry'], self.resolve_monitor.process is None)
        view = describe(job, running=running)
        panel_view, stage_title = pipeline_view(job, view), view.title
        self.text("elapsed", view.elapsed)
        if self.quota_monitor:
            quota = self.quota_monitor.poll((bool(job.remote_hash), job.status.value == 'completed'),
                                             visible=bool(self.is_visible(self.hwnd)))
            self.text('quota', quota_text(quota))
        variant = tr('  •  Вариант {v0:03d}', v0=job.run_index) if job.run_index is not None else ""
        self.text("source", Path(job.source_file).name + variant)
        self.enable(self.controls["settings"], bool(job.settings_snapshot or job.remote_hash or job.result_files))
        can_save = job.status.value == "completed"
        resumable = bool(job.remote_hash) and (job.status.value == 'failed' or (not view.active and not can_save and job.status.value not in {'cancelled', 'configuring'}))
        self.show(self.controls['resume'], 5 if resumable else 0)
        self.text('resume', tr('Продолжить скачивание') if job.remote_status == 'done' else tr('Продолжить задачу'))
        self.enable(self.controls['resume'], resumable and not worker_running(self.path)
                    and not (self.resume_process is not None and self.resume_process.poll() is None))
        receipt = read_receipt(self.path) if can_save else {}
        local_import = self.import_process is not None and self.import_process.poll() is None
        automatic = (can_save and job.post_import_state in {'pending', 'running'} and worker_running(self.path))
        importing = local_import or import_running(receipt) or automatic
        for key in ('import_pool', 'import_timeline'):
            self.show(self.controls[key], 0 if resumable else 5)
            allowed = connection.pool_allowed if key == 'import_pool' else connection.timeline_allowed
            self.enable(self.controls[key], can_save and bool(job.result_files) and not importing and allowed)
        self.enable(self.controls["save_profile"], can_save)
        self.enable(self.controls["profile_name"], can_save)
        if can_save and not self.profile_name_filled:
            suffix = f" — {job.run_index:03d}" if job.run_index is not None else tr(' — эталон')
            self.set_text(self.controls["profile_name"], Path(job.source_file).stem[:80 - len(suffix)] + suffix)
            self.profile_name_filled = True
        destination = (output_directory(self.path.parents[2], job) if job.output_relative or job.remote_hash or job.result_files
                       else output_base(self.path.parents[2], job))
        import_detail = import_message(receipt) if can_save else ''
        if can_save and self.import_process is not None and self.import_process.poll() not in (None, 0) and receipt.get('status') != 'failed':
            self.import_error = tr('Импорт не запустился или был прерван. Подробности: import-worker.log в папке задачи.')
        if (local_import or automatic) and receipt.get('status') != 'running':
            import_detail = tr('Добавляем результаты в Resolve…')
        if can_save and job.post_import_state in {'pending', 'running', 'failed'} and not importing and receipt.get('status') not in {'completed', 'failed'}:
            import_detail = tr('Автоматическое добавление не завершено. Скачанные файлы сохранены; можно добавить их кнопками ниже.')
        import_warning = bool(can_save and not importing and (self.import_error or receipt.get('status') == 'failed'))
        warning_changed = import_warning != self.import_warning
        if warning_changed:
            self.import_warning = import_warning
            self.invalidate(self.hwnd, None, True)
        if can_save and self.progress_panel:
            if importing:
                panel_view = PipelineView(5, tr('ИМПОРТ В RESOLVE'), tr('Добавляем результаты'),
                                          note=tr('Можно свернуть в фон; импорт продолжится.'), animate=True)
            elif receipt.get('status') == 'completed' and not self.import_error:
                if receipt.get('target') == 'timeline':
                    if timeline_confirmed(receipt):
                        stage_title = tr('Готово — результаты в Resolve')
                        tracks = track_range(receipt)
                        panel_view = PipelineView(5, tr('РЕЗУЛЬТАТ ИМПОРТА НА ТАЙМЛАЙН'), tracks,
                                                  secondary=tr('Таймлайн: {name}', name=receipt.get('timeline_name') or '—'),
                                                  note=track_scroll_hint(receipt), tone='done', numeric=len(tracks) <= 20)
                    else:
                        panel_view = PipelineView(5, tr('ИМПОРТ ЗАПИСАН РАНЕЕ'), tr('Проверьте расположение'),
                                                  note=tr('Нажмите «На новые дорожки», чтобы проверить расположение.'), tone='idle')
                else:
                    stage_title = tr('Готово — результаты в Resolve')
                    panel_view = PipelineView(5, tr('ДОБАВЛЕНО В RESOLVE'), tr('Файлы в Media Pool'),
                                              note=tr('Повторное нажатие проверит уже добавленные файлы.'), tone='done')
        self.text('stage', stage_title)
        if self.progress_panel:
            self.progress_panel.update(panel_view)
        locations = track_details(receipt) if can_save else ''
        import_text = self.import_error or import_detail
        self.text("detail", (import_text + '\r\n\r\n' if import_warning else '')
                  + (locations + '\r\n\r\n' if locations else '') + view.detail
                  + "\r\n\r\n" + tr("Папка результата: {path}", path=destination)
                  + ('\r\n\r\n' + import_text if can_save and not import_warning else ''))
        self.text("remaining", tr('Импорт не завершён. Причина указана ниже.') if import_warning else
                  import_text if can_save else view.remaining)
        if warning_changed and import_warning:
            # Reveal a new failure once; subsequent polls preserve manual scrolling.
            self.send(self.controls['detail'], 0xB1, 0, 0)  # EM_SETSEL
            self.send(self.controls['detail'], 0xB7, 0, 0)  # EM_SCROLLCARET
        self.text("activity", view.progress_label)
        mode = "percent" if view.percent is not None else "marquee" if view.active else "stopped"
        if mode != self.bar_mode:
            self.bar_mode = mode
            self.bar_position = None
            self.send(self.controls["bar"], 0x40A, 0, 0)  # PBM_SETMARQUEE off
            self.set_style(self.controls["bar"], -16, 0x50000001 | (8 if mode == "marquee" else 0))
            self.send(self.controls["bar"], 0x406, 0, 1000)  # PBM_SETRANGE32
            if mode == "marquee" and self.animated_bar:
                self.send(self.controls["bar"], 0x40A, 1, 30)
            self.show(self.controls["bar"], 0 if mode == "marquee" and not self.animated_bar else 5)
        if view.percent is not None:
            position = int(view.percent * 10)
            if self.bar_position != position:
                self.send(self.controls["bar"], 0x402, position, 0)  # PBM_SETPOS
                self.bar_position = position
        elif mode == "stopped":
            self.send(self.controls["bar"], 0x402, 0, 0)
        elif not self.animated_bar:
            block = int(time.monotonic() * 2) % 24
            self.text("activity", "·" * block + "●" + "·" * (24 - block) + tr('  Процент неизвестен'))
        output = output_directory(self.path.parents[2], job)
        self.enable(self.controls["result"], output.is_dir())
        busy = view.active or importing or (self.resume_process is not None and self.resume_process.poll() is None)
        finished = self.background.observe(busy)
        tone = 'working' if busy else 'done' if can_save and receipt.get('status') != 'failed' and not self.import_error else 'error'
        tray_title = (tr('Добавляем результаты в Resolve…') if importing else
                      tr('Файлы скачаны — импорт не завершён') if can_save and tone == 'error' else stage_title)
        tray_available = bool(self.tray and self.tray.update(tray_tip(job, tray_title), tone))
        self.text('close', tr('Свернуть в фон') if busy and tray_available else tr('Свернуть окно') if busy else tr('Закрыть окно'))
        self.text('background_hint', background_hint(busy, tray_available))
        self.show(self.controls['background_hint'], 5 if busy else 0)
        for name in ('profile_label', 'profile_name', 'save_profile', 'profile_notice'):
            self.show(self.controls[name], 0 if busy else 5)
        if self.background.hidden and not tray_available:
            self.restore_window(attention=True)
        if finished:
            was_hidden = self.background.hidden
            self.restore_window(attention=True)
            if was_hidden and self.tray:
                self.tray.notify(tray_title + '\n' + Path(job.source_file).name)
        request = self.path.parent / "show-window"
        try:
            stamp = request.stat().st_mtime_ns
            if stamp != self.request_time:
                self.request_time = stamp
                self.restore_window()
        except OSError:
            pass

    def _dispatch(self, hwnd, message, wp, lp):
        # ctypes must never swallow an exception escaping a native callback.
        try:
            custom = self.native_message(hwnd, message, wp, lp)
            if custom is not None:
                return custom
            if message == 0x113:  # WM_TIMER
                if wp == 2:
                    self.destroy(hwnd)
                elif wp == 3:
                    self.kill_timer(hwnd, 3)
                    if self.on_ready:
                        self.on_ready(self)
                elif wp == 4 and self.progress_panel:
                    if self.progress_panel.view and self.progress_panel.view.animate:
                        self.invalidate(self.controls['flow'], None, False)
                else:
                    self.tick()
                return 0
            if message == 0x111:  # WM_COMMAND
                if self.on_command(wp & 0xFFFF, wp >> 16):
                    return 0
            if message == 0x2:  # WM_DESTROY
                self.quit(0)
                return 0
            return self.default(hwnd, message, wp, lp)
        except Exception as exc:
            self.callback_error = exc
            self.quit(1)
            return 0

    def native_message(self, hwnd, message, wp, lp):
        if self.tray:
            handled = self.tray.message(message, wp, lp)
            if handled is not None:
                return handled
        if message == 0x10 and self.progress_panel:  # WM_CLOSE, including caption X / Alt+F4
            self.request_close()
            return 0
        if message == 0x2 and self.tray:  # remove before the HWND is destroyed
            self.tray.close()
        if self.progress_panel:
            return self.progress_panel.message(hwnd, message, wp, lp)
        return None

    def restore_window(self, *, attention=False):
        self.background.hidden = False
        self.show(self.hwnd, 9)
        focused = self.focus(self.hwnd)
        if attention and not focused:
            class Flash(ctypes.Structure):
                _fields_ = [('size', self.w.UINT), ('hwnd', self.w.HWND), ('flags', self.w.DWORD),
                            ('count', self.w.UINT), ('timeout', self.w.DWORD)]
            self.user.FlashWindowEx.argtypes = [ctypes.POINTER(Flash)]
            self.user.FlashWindowEx(ctypes.byref(Flash(ctypes.sizeof(Flash), self.hwnd, 2, 3, 0)))

    def request_close(self):
        self.tick()  # completion may have arrived between the timer and the click
        available = bool(self.tray and self.tray.ensure(force=True))
        action = self.background.close_action(available)
        if action == 'close':
            self.destroy(self.hwnd)
        elif action == 'hide':
            self.background.hidden = True
            self.show(self.hwnd, 0)
            if not self.background_notified:
                self.background_notified = True
                self.tray.notify(tr('Задача продолжает работу. Двойной щелчок по значку HVPC Resolve-MVSEP вернёт окно. После завершения оно откроется автоматически.'))
        else:
            self.background.hidden = False
            self.show(self.hwnd, 6)  # SW_MINIMIZE: retain an ordinary taskbar entry

    def cleanup(self):
        if self.tray:
            self.tray.close()
        self.resolve_monitor.close()
        if self.progress_panel:
            self.progress_panel.close()

    def on_command(self, identifier, notification):
        if identifier == 10:
            self.resolve_monitor.refresh()
            self.tick()
            return True
        if identifier == 9:
            try:
                if self.resume_process is not None and self.resume_process.poll() is None:
                    return True
                from .separation import resume_job
                self.resume_process = resume_job(self.path.parents[2], self.path)
            except (InspectionError, OSError, ValueError, TypeError) as exc:
                from .core.feedback import show_message
                show_message(str(exc), error=True, title=tr(self.window_title))
            self.tick()
            return True
        if identifier in (7, 8):
            try:
                connection = self.resolve_monitor.tick()
                if not (connection.pool_allowed if identifier == 7 else connection.timeline_allowed):
                    self.tick()
                    return True
                job = Job.load(self.path)
                automatic = job.post_import_state in {'pending', 'running'} and worker_running(self.path)
                if job.status.value != 'completed' or automatic or (self.import_process is not None and self.import_process.poll() is None) or import_running(read_receipt(self.path)):
                    return True
                from .import_results import launch_import
                self.import_error = ''
                self.import_process = launch_import(self.path.parents[2], self.path, 'media_pool' if identifier == 7 else 'timeline')
            except (OSError, ValueError, TypeError) as exc:
                self.import_error = str(exc)
            self.tick()
            return True
        if identifier == 5:
            try:
                job = Job.load(self.path)
                if job.status.value != "completed":
                    raise InspectionError(tr('Дождитесь завершения разделения.'))
                name = ctypes.create_unicode_buffer(81)
                self.user.GetWindowTextW.argtypes = [self.w.HWND, self.w.LPWSTR, ctypes.c_int]
                self.user.GetWindowTextW(self.controls["profile_name"], name, 81)
                save_profile(self.path.parents[2], name.value, job_spec(job))
                self.text("profile_notice", tr('Набор сохранён. Для другого исходника выберите его\r\nв списке «Мои наборы настроек» перед разделением.'))
            except (InspectionError, OSError, ValueError, TypeError, KeyError) as exc:
                self.text("profile_notice", str(exc))
            return True
        if identifier == 4:
            from .core.recipe import write_reports
            try:
                job = Job.load(self.path)
                report = write_reports(self.path.parents[2], job)
                os.startfile(str(report))
            except (OSError, ValueError, TypeError, KeyError):
                self.text("detail", tr('Не удалось открыть настройки. Параметры задачи сохранены в:\r\n') + str(self.path))
            return True
        if identifier in (1, 2):
            try:
                folder = (output_directory(self.path.parents[2], Job.load(self.path))
                          if identifier == 1 else self.path.parent)
                os.startfile(str(folder))
            except (InspectionError, OSError, ValueError, TypeError, KeyError):
                self.text("detail", tr('Не удалось открыть папку. Путь сохранён в:\r\n') + str(self.path))
            return True
        if identifier == 3:
            self.request_close()
            return True
        return False

    def build_controls(self):
        self.child("stage", "STATIC", tr('Запуск…'), 22, 20, 650, 28)
        self.send(self.controls["stage"], 0x30, self.own_fonts[1] or self.font, 1)
        self.child("source", "STATIC", "", 22, 55, 650, 24, 0x4000 | 0x80)
        self.child('quota', 'STATIC', quota_text(None), 460, 58, 244, 48)
        self.set_style(self.controls['quota'], -16, 0x50000846)  # right-aligned multiline label
        self.quota_monitor = QuotaMonitor(self.path.parents[2])
        self.child("bar", "msctls_progress32", "", 22, 88, 650, 18, 1)
        self.child("activity", "STATIC", "", 22, 115, 650, 24)
        self.child("elapsed", "STATIC", "", 22, 146, 650, 24)
        self.child("remaining", "STATIC", "", 22, 176, 650, 42)
        self.child("detail", "EDIT", "", 22, 224, 650, 181, 0x00200000 | 0x4 | 0x40 | 0x800 | 0x10000)
        self.child("hint", "STATIC", "", 22, 412, 650, 42)
        self.child('connection_title', 'STATIC', '', 22, 388, 500, 24)
        self.send(self.controls['connection_title'], 0x30, self.own_fonts[2] or self.font, 1)
        self.child('connection_retry', 'BUTTON', tr('Проверить связь'), 525, 388, 145, 26, 0x10000, 10)
        self.child('import_pool', 'BUTTON', tr('В Media Pool'), 22, 440, 300, 34, 0x10000, 7)
        self.child('import_timeline', 'BUTTON', tr('На новые дорожки'), 340, 440, 300, 34, 0x10000, 8)
        self.child('resume', 'BUTTON', tr('Продолжить скачивание'), 22, 440, 618, 34, 0x10000, 9)
        self.child("result", "BUTTON", tr('Открыть результат'), 22, 458, 155, 30, 0x10000, 1)
        self.child("settings", "BUTTON", tr('Настройки'), 190, 458, 148, 30, 0x10000, 4)
        self.child("logs", "BUTTON", tr('Папка задачи'), 351, 458, 148, 30, 0x10000, 2)
        self.child("close", "BUTTON", tr('Закрыть окно'), 512, 458, 160, 30, 0x10000, 3)
        self.child("profile_label", "STATIC", tr('Сохранить параметры этой попытки в «Мои наборы»:'), 22, 500, 650, 23)
        self.child("profile_name", "EDIT", "", 22, 528, 445, 28, 0x00810080, 6)
        self.send(self.controls["profile_name"], 0xC5, 80, 0)
        self.child("save_profile", "BUTTON", tr('Сохранить набор'), 482, 527, 190, 30, 0x10000, 5)
        self.child("profile_notice", "STATIC", "", 22, 569, 650, 40, 0x80)
        self.child('background_hint', 'STATIC', '', 22, 500, 650, 64)
        from .tray_icon import TrayIcon
        try:
            self.tray = TrayIcon(self)
        except OSError:
            self.tray = None  # do not hide a running task without a usable return path
        self.progress_panel = ProgressPanel(self)
        self.progress_panel.reflow()
        self.timer(self.hwnd, 4, 100, None)

    def run(self):
        icons = None
        name = "ResolveMVSEPProgress_" + str(os.getpid())
        cls = self.WindowClass()
        cls.lpfnWndProc, cls.hInstance, cls.hbrBackground, cls.lpszClassName = self.callback, self.module, 16, name
        if not self.register(ctypes.byref(cls)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            # Caption, system menu, minimize box; fixed content area with scrollable details.
            self.hwnd = self.create(0, name, tr(self.window_title), self.window_style,
                                    *self.window_position, self.window_width, self.window_height,
                                    None, None, self.module, None)
            if not self.hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            icons = WindowIcons(self)
            self.build_controls()
            self.tick()
            self.show(self.hwnd, 5)
            if not self.timer(self.hwnd, 1, 500, None):
                raise ctypes.WinError(ctypes.get_last_error())
            if self.auto_close_ms:
                self.timer(self.hwnd, 2, self.auto_close_ms, None)
            if self.on_ready:
                self.timer(self.hwnd, 3, 900, None)  # allow the native bar to finish its first animation
            message = self.w.MSG()
            while True:
                outcome = self.get_message(ctypes.byref(message), None, 0, 0)
                if outcome == -1:
                    raise ctypes.WinError(ctypes.get_last_error())
                if outcome == 0:
                    break
                if not self.dialog_message(self.hwnd, ctypes.byref(message)):
                    self.translate(ctypes.byref(message))
                    self.dispatch(ctypes.byref(message))
            if self.callback_error:
                raise self.callback_error
            return int(message.wParam)
        finally:
            if self.hwnd:
                self.destroy(self.hwnd)
            self.unregister(name, self.module)
            for font in self.own_fonts:
                if font:
                    self.delete_object(font)
            try:
                self.cleanup()
            finally:
                if icons:
                    icons.close()
