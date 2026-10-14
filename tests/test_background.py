"""Background mode preserves a return path and never acts on the worker/Resolve."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from resolve_mvsep.cli import show_failed_job
from resolve_mvsep.core.background import BackgroundState, background_hint, tray_tip
from resolve_mvsep.core.i18n import using_language
from resolve_mvsep.core.job import Job, JobStatus
from resolve_mvsep.mvsep.models import Mode, SeparationSpec
from resolve_mvsep.separation import show_progress


class BackgroundTests(unittest.TestCase):
    def test_close_during_work_keeps_return_path_and_completion_is_one_shot(self):
        state = BackgroundState()
        self.assertFalse(state.observe(True))
        self.assertEqual(state.close_action(True), 'hide')
        state.hidden = True
        for _ in range(5):
            self.assertFalse(state.observe(True))  # closing Resolve does not finish the worker
        self.assertTrue(state.observe(False))
        self.assertFalse(state.observe(False))  # acknowledgement must not reopen every timer tick
        self.assertEqual(state.close_action(True), 'close')

    def test_resumed_or_import_work_can_finish_again_and_missing_tray_never_hides(self):
        state = BackgroundState()
        self.assertFalse(state.observe(False))  # initially opening an old result is not a new event
        for _ in range(2):
            self.assertFalse(state.observe(True))
            self.assertEqual(state.close_action(False), 'minimize')
            self.assertTrue(state.observe(False))  # success, failure or worker death all restore

    def test_tooltip_handles_unicode_filename_and_preserves_stage_within_shell_limit(self):
        job = Job.create(Path('Wave 🌊' * 90 + '.mp3'), SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40))
        for locale, title in [('ru', 'Скачивание готовых файлов…'), ('en', 'Downloading your results…')]:
            with using_language(locale):
                tip = tray_tip(job, title)
                self.assertIn(title, tip)
                self.assertLessEqual(len(tip.encode('utf-16-le')), 254)
                self.assertTrue(background_hint(True, True))
                self.assertTrue(background_hint(True, False))
                self.assertEqual(background_hint(False, True), '')

    def test_failure_uses_full_result_window_even_without_remote_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = Job.create(root / 'file.wav', SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40))
            job.status, job.error = JobStatus.FAILED, 'Upload failed before confirmation'
            path = root / 'jobs' / job.id / 'job.json'
            job.save(path)
            with patch('resolve_mvsep.separation.show_progress', return_value=True) as show:
                self.assertTrue(show_failed_job(root, job))
                show.assert_called_once_with(root, path, notify_failure=False)
            with patch('resolve_mvsep.separation.show_progress', return_value=False):
                self.assertFalse(show_failed_job(root, job))  # caller must retain its modal fallback
            job.status = JobStatus.COMPLETED
            job.save(path)
            with patch('resolve_mvsep.separation.show_progress') as show:
                self.assertFalse(show_failed_job(root, job))
                show.assert_not_called()

    def test_missing_result_window_retains_error_fallback_without_spawning_recursively(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(show_failed_job(root, None))
            with patch('resolve_mvsep.separation.show_message') as notice:
                self.assertFalse(show_progress(root, root / 'missing/job.json', notify_failure=False))
                notice.assert_not_called()
