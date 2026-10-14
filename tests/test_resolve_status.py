"""Connection probes must be read-only, bounded and independent of downloads."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from resolve_mvsep.core.i18n import using_language
from resolve_mvsep.core.job import Job, JobStatus
from resolve_mvsep.core.resolve_status import ResolveStatus
from resolve_mvsep.mvsep.models import Mode, SeparationSpec
from resolve_mvsep.resolve.status import inspect_connection
from resolve_mvsep.resolve_monitor import ResolveMonitor, STATUS_PREFIX


class ResolveStatusTests(unittest.TestCase):
    def setUp(self):
        self.job = Job.create(Path('source.wav'), SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40),
                              {'project_id': 'p', 'project_name': 'Project', 'timeline_id': 't', 'timeline_name': 'Timeline'})
        self.app = Mock()
        self.project = self.app.GetProjectManager.return_value.GetCurrentProject.return_value
        self.project.GetUniqueId.return_value = 'p'
        self.project.GetCurrentTimeline.return_value.GetUniqueId.return_value = 't'
        self.app.GetVersion.return_value = [21, 1, 0]

    def test_closed_loading_ready_and_different_project(self):
        with patch('resolve_mvsep.resolve.status._load_script_module') as sdk:
            sdk.return_value.scriptapp.return_value = None
            self.assertEqual(inspect_connection(self.job).code, 'unavailable')
        self.assertEqual(inspect_connection(self.job, self.app).code, 'ready')
        self.project.GetUniqueId.return_value = 'other'
        status = inspect_connection(self.job, self.app)
        self.assertEqual(status.code, 'wrong_project')
        self.assertFalse(status.pool_allowed or status.timeline_allowed)
        self.app.GetProjectManager.return_value.GetCurrentProject.return_value = None
        self.assertEqual(inspect_connection(self.job, self.app).code, 'no_project')

    def test_timeline_and_version_allow_media_pool_only_when_supported(self):
        self.project.GetCurrentTimeline.return_value.GetUniqueId.return_value = 'other'
        status = inspect_connection(self.job, self.app)
        self.assertTrue(status.pool_allowed)
        self.assertFalse(status.timeline_allowed)
        self.assertEqual(status.code, 'wrong_timeline')
        self.project.GetCurrentTimeline.return_value.GetUniqueId.return_value = 't'
        self.app.GetVersion.return_value = [20, 3, 0]
        self.assertEqual(inspect_connection(self.job, self.app), ResolveStatus('old_version'))
        self.project.GetCurrentTimeline.return_value = None
        self.assertEqual(inspect_connection(self.job, self.app).code, 'no_timeline')

    def test_legacy_jobs_require_both_project_name_and_saved_timeline(self):
        self.job.source_clip.pop('project_id')
        self.project.GetName.return_value = 'Project'
        self.project.GetTimelineCount.return_value = 1
        self.project.GetTimelineByIndex.return_value.GetUniqueId.return_value = 'other'
        self.assertEqual(inspect_connection(self.job, self.app).code, 'wrong_project')
        self.project.GetTimelineByIndex.return_value.GetUniqueId.return_value = 't'
        self.assertEqual(inspect_connection(self.job, self.app).code, 'ready')
        self.job.source_clip = {}
        self.app.reset_mock()
        self.assertEqual(inspect_connection(self.job, self.app).code, 'no_context')
        self.assertFalse(self.app.mock_calls)

    def test_shutdown_mid_probe_cannot_change_download_or_import_state(self):
        self.job.status = JobStatus.COMPLETED
        self.job.result_files = ['vocals.wav', 'other.wav']
        self.job.post_import_state = 'pending'
        before = dict(vars(self.job))
        self.project.GetCurrentTimeline.side_effect = OSError('bridge closed')
        self.assertEqual(inspect_connection(self.job, self.app).code, 'unavailable')
        self.assertEqual(vars(self.job), before)
        # Only getters were called; this check must never create a bin/track or import.
        self.assertTrue(all(call[0].split('.')[-1].startswith('Get') for call in self.app.mock_calls))

    def test_bilingual_warning_and_resume_are_separate_from_connection(self):
        for lang, phrase in [('ru', 'Скачивание не зависит'), ('en', 'Download works independently')]:
            with using_language(lang):
                self.assertIn(phrase, ResolveStatus('unavailable').copy(self.job)[1])
        for code in ('checking', 'unavailable', 'wrong_project', 'no_context'):
            state = ResolveStatus(code)
            self.assertFalse(state.pool_allowed or state.timeline_allowed)

    def test_cli_probe_does_not_load_mvsep_config_or_import(self):
        from contextlib import redirect_stdout
        from resolve_mvsep.cli import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'jobs' / self.job.id / 'job.json'
            self.job.save(path)
            with patch('resolve_mvsep.resolve.status.inspect_connection', return_value=ResolveStatus('unavailable')), \
                 patch('resolve_mvsep.cli.load_mvsep_settings') as config, \
                 patch('resolve_mvsep.import_results.run_import') as importer, redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(root, ['--resolve-status', str(path)]), 0)
                self.assertIn(STATUS_PREFIX + '{"code": "unavailable"}', output.getvalue())
                config.assert_not_called()
                importer.assert_not_called()

    def test_monitor_times_out_without_blocking_then_recovers(self):
        root = Path.cwd()
        process = Mock()
        process.poll.return_value = None
        process.returncode = 0
        clock = [100.0]
        with patch('resolve_mvsep.resolve_monitor.subprocess.Popen', return_value=process) as spawn, \
             patch('resolve_mvsep.resolve_monitor.worker_python', return_value=Path('python')), \
             patch('resolve_mvsep.resolve_monitor.time.monotonic', side_effect=lambda: clock[0]):
            monitor = ResolveMonitor(root, root / 'jobs/test/job.json')
            self.addCleanup(monitor.close)
            self.assertEqual(monitor.tick().code, 'checking')
            self.assertEqual(spawn.call_args.args[0][-2:], ['--resolve-status', str(root / 'jobs/test/job.json')])
            clock[0] += 9
            self.assertEqual(monitor.tick().code, 'unavailable')
            process.kill.assert_called_once()
            process.wait.assert_not_called()  # UI timer cannot block on an SDK process
            monitor.tick()
            self.assertEqual(spawn.call_count, 1)  # no duplicate probe while old process exits
            process.poll.return_value = -1
            monitor.tick()
            clock[0] += 6
            process.poll.return_value = None
            monitor.tick()
            monitor.output.write((STATUS_PREFIX + json.dumps({'code': 'ready'}) + '\n').encode())
            process.poll.return_value = 0
            self.assertEqual(monitor.tick().code, 'ready')
            self.assertEqual(spawn.call_count, 2)
            monitor.refresh()
            self.assertEqual(monitor.state.code, 'checking')
            monitor.close()

    def test_monitor_crash_or_malformed_response_never_enables_import(self):
        process = Mock(returncode=1)
        process.poll.return_value = 1
        with patch('resolve_mvsep.resolve_monitor.subprocess.Popen', return_value=process), \
             patch('resolve_mvsep.resolve_monitor.worker_python', return_value=Path('python')):
            monitor = ResolveMonitor(Path.cwd(), Path('job.json'))
            self.addCleanup(monitor.close)
            monitor.tick()
            monitor.output.write((STATUS_PREFIX + '{broken}\n').encode())
            self.assertFalse(monitor.tick().pool_allowed)
            monitor.close()
