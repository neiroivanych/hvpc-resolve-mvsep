"""Slow completed downloads can be resumed without another separation or duplicate stems."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from test_mvsep import Clock, Response, Transport
from resolve_mvsep.mvsep.client import MVSEPClient
from resolve_mvsep.mvsep.downloads import file_record
from resolve_mvsep.mvsep.names import output_name, safe_name
from resolve_mvsep.mvsep.errors import APIError, DeadlineExceeded
from resolve_mvsep.mvsep.models import RemoteFile, RemoteResult
from resolve_mvsep.mvsep.models import Mode, SeparationSpec
from resolve_mvsep.core.job import Job, JobStatus
from resolve_mvsep.core.errors import InspectionError
from resolve_mvsep.core.progress import describe
from resolve_mvsep.core.pipeline import pipeline_view
from resolve_mvsep.core.i18n import using_language
from resolve_mvsep.separation import resume_job


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.clock = Clock()
        self.transport = Transport([])
        self.client = MVSEPClient('secret', transport=self.transport, clock=self.clock, sleep=self.clock.sleep)
        self.result = RemoteResult('done', (RemoteFile('vocals.wav', 'https://mvsep.com/a'),
                                           RemoteFile('other.wav', 'https://mvsep.com/b')))

    def slow_response(self, delay):
        clock = self.clock
        class Slow(Response):
            def read(self, size):
                data = super().read(size)
                if data:
                    clock.sleep(delay)
                return data
            read1 = read
        return Slow()

    def test_each_file_gets_full_timeout_even_when_batch_takes_longer(self):
        self.transport.streams = [self.slow_response(20), self.slow_response(20)]
        paths = self.client.download_results(self.result, self.root, timeout=30)
        self.assertEqual(self.clock.value, 40)
        self.assertEqual(len(paths), 2)
        self.assertTrue(all(path.is_file() for path in paths))

    def test_single_file_deadline_remains_bounded_and_partial_file_is_removed(self):
        self.transport.streams = [self.slow_response(31)]
        with self.assertRaises(DeadlineExceeded):
            self.client.download_results(self.result, self.root, timeout=30)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_retry_delay_exhausting_download_budget_keeps_actionable_download_error(self):
        self.transport.open = Mock(side_effect=APIError('busy', status_code=429, retry_after=60))
        with self.assertRaisesRegex(DeadlineExceeded, '^Download time limit reached'):
            self.client.download_results(self.result, self.root, timeout=3)
        self.assertEqual(self.clock.value, 3)
        self.transport.open.assert_called_once()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_long_unicode_names_fit_windows_units_and_keep_stem_role(self):
        name = output_name('🎵' * 150, 'source_vocals.wav', run_index=1, max_length=60)
        self.assertLessEqual(len(name.encode('utf-16-le')) // 2, 60)
        self.assertTrue(name.endswith('_vocals_001.wav'))
        name = safe_name('🎵' * 150 + '.wav', 60)
        self.assertLessEqual(len(name.encode('utf-16-le')) // 2, 60)
        self.assertTrue(name.endswith('.wav'))

    def test_resume_uses_fingerprints_without_network_and_downloads_only_missing_stem(self):
        existing = self.root / '01_vocals.wav'
        existing.write_bytes(b'original-finished-stem')
        records = {'vocals.wav': file_record(existing)}
        self.transport.streams = [Response(b'new-stem')]
        saved, progress = [], []
        paths = self.client.download_results(self.result, self.root, timeout=30, completed_files=records,
                                             on_file=saved.append, on_progress=progress.append)
        self.assertEqual(paths[0], existing.resolve())
        self.assertEqual(existing.read_bytes(), b'original-finished-stem')
        self.assertEqual(paths[1].read_bytes(), b'new-stem')
        self.assertEqual([call[1] for call in self.transport.calls], ['https://mvsep.com/b'])
        self.assertEqual(len(saved), 2)
        self.assertEqual(progress[0].file_index, 1)
        self.assertEqual(progress[0].transferred, len(b'original-finished-stem'))
        self.assertEqual(records['other.wav'], file_record(paths[1]))

    def test_changed_completed_file_is_preserved_but_not_reused_even_at_same_size(self):
        existing = self.root / '01_vocals.wav'
        existing.write_bytes(b'good')
        records = {'vocals.wav': file_record(existing)}
        existing.write_bytes(b'edit')
        self.transport.streams = [Response(b'good'), Response(b'other')]
        paths = self.client.download_results(self.result, self.root, timeout=30, completed_files=records,
                                             legacy_paths=(existing,))
        self.assertNotEqual(paths[0], existing)
        self.assertEqual(existing.read_bytes(), b'edit')
        self.assertEqual(paths[0].read_bytes(), b'good')

    def test_legacy_completed_file_is_adopted_only_with_matching_server_size(self):
        existing = self.root / '01_vocals.wav'
        existing.write_bytes(b'finished')
        records = {}
        header_only = Response(b'finished')
        self.transport.streams = [header_only, Response(b'new')]
        paths = self.client.download_results(self.result, self.root, timeout=30, completed_files=records,
                                             legacy_paths=(existing,))
        self.assertEqual(paths[0], existing.resolve())
        self.assertEqual(len(list(self.root.iterdir())), 2)
        self.assertEqual(records['vocals.wav'], file_record(existing))
        self.assertTrue(header_only.closed)

    def test_unrecorded_or_wrong_size_file_never_counts_as_downloaded(self):
        existing = self.root / '01_vocals.wav'
        existing.write_bytes(b'short')
        self.transport.streams = [Response(b'full-length'), Response(b'full-length'), Response(b'other')]
        paths = self.client.download_results(self.result, self.root, timeout=30, legacy_paths=(existing,))
        self.assertNotEqual(paths[0], existing)
        self.assertEqual(existing.read_bytes(), b'short')
        self.assertEqual(paths[0].read_bytes(), b'full-length')

    def saved_job(self):
        job = Job.create(self.root / 'song.mp3', SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40))
        job.status, job.remote_hash, job.remote_status = JobStatus.FAILED, 'known-hash', 'done'
        job.result_files = [str(self.root / 'vocals.wav')]
        job.error = 'Download time limit reached. The remote job can be resumed.'
        path = self.root / 'jobs' / job.id / 'job.json'
        job.save(path)
        return job, path

    def test_resume_launcher_only_passes_saved_job_not_file_or_configuration(self):
        job, path = self.saved_job()
        with patch('resolve_mvsep.separation.active_job', return_value=None), \
             patch('resolve_mvsep.separation.worker_running', return_value=False), \
             patch('resolve_mvsep.separation.worker_python', return_value=Path('python.exe')), \
             patch('resolve_mvsep.separation.record_worker') as record, \
             patch('resolve_mvsep.separation.show_progress') as show, \
             patch('resolve_mvsep.separation.subprocess.Popen') as launch:
            process = resume_job(self.root, path)
            self.assertEqual(launch.call_args.args[0][-3:], ['--resume', str(path.resolve()), '--notify'])
            self.assertNotIn('--file', launch.call_args.args[0])
            self.assertNotIn('--configure', launch.call_args.args[0])
            record.assert_called_once_with(self.root, job, process.pid)
            show.assert_called_once_with(self.root, path)

    def test_resume_rejects_no_hash_completed_and_duplicate_worker(self):
        job, path = self.saved_job()
        with patch('resolve_mvsep.separation.active_job', return_value=None), \
             patch('resolve_mvsep.separation.worker_running', return_value=True), \
             patch('resolve_mvsep.separation.show_progress'), \
             patch('resolve_mvsep.separation.subprocess.Popen') as launch:
            self.assertIsNone(resume_job(self.root, path))
            launch.assert_not_called()
        for state, task_hash in ((JobStatus.COMPLETED, 'known'), (JobStatus.FAILED, None)):
            job.status, job.remote_hash = state, task_hash
            job.save(path)
            with self.assertRaises(InspectionError):
                resume_job(self.root, path)

    def test_failed_download_ui_distinguishes_remote_completion_and_local_failure(self):
        job, _ = self.saved_job()
        for lang in ('ru', 'en'):
            with using_language(lang):
                view = describe(job, running=False)
                panel = pipeline_view(job, view)
                self.assertFalse(view.completed)
                self.assertIn('MVSEP', panel.caption)
                self.assertIn('1', panel.secondary)
                if lang == 'ru':
                    self.assertEqual(view.title, 'Скачивание не завершено')
                    self.assertIn('Продолжить скачивание', view.detail)
                    self.assertNotIn('Download time limit', view.detail)
                else:
                    self.assertNotRegex(view.detail + panel.note, '[А-Яа-яЁё]')

