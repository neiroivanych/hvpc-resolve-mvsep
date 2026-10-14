"""Offline checks: downloads, local import and remembered settings are independent."""
import io
import json
import logging
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from resolve_mvsep.completion import finish_download
from resolve_mvsep.core.config import Settings
from resolve_mvsep.core.i18n import save_preferences
from resolve_mvsep.core.job import Job, JobStatus
from resolve_mvsep.core.progress import describe
from resolve_mvsep.core.profiles import spec_data
from resolve_mvsep.core.recent_settings import policy, remember_choice, remembered_spec, remember_success
from resolve_mvsep.core.resolve_import import latest_completed_for_project
from resolve_mvsep.main import run
from resolve_mvsep.mvsep.models import Mode, SeparationSpec
from resolve_mvsep.resolve.api import ResolveContext


class CompletionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.spec = SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40,
                                  api_options={'add_opt1': '81', 'add_opt2': '8'})
        self.job = Job.create(self.root / 'Source.wav', self.spec,
                              {'project_id': 'p1', 'project_name': 'Project', 'timeline_id': 't1'})
        self.job.status, self.job.result_files = JobStatus.COMPLETED, ['vocals.wav', 'other.wav']
        self.job.separation_completed_at = '2026-10-09T10:00:00+00:00'
        self.path = self.root / 'jobs' / self.job.id / 'job.json'
        self.job.save(self.path)
        self.logger = Mock(spec=logging.Logger)

    def test_modes_are_independent_and_disabling_restores_default_behavior(self):
        self.assertEqual(policy(self.root), (False, 'successful'))
        remember_choice(self.root, self.spec)
        self.assertFalse((self.root / 'config/recent_settings.json').exists())
        save_preferences(self.root, remember_last_settings=True, remember_settings_mode='all')
        edited = replace(self.spec, output_format=0, api_options={'add_opt1': '82', 'add_opt2': '2'})
        remember_choice(self.root, edited)  # chosen then cancelled, no upload
        self.assertEqual(remembered_spec(self.root), edited)
        self.job.remember_settings = True
        remember_success(self.root, self.job)
        self.assertEqual(remembered_spec(self.root), edited)
        save_preferences(self.root, remember_settings_mode='successful')
        self.assertEqual(remembered_spec(self.root), self.spec)
        save_preferences(self.root, remember_last_settings=False)
        self.assertIsNone(remembered_spec(self.root))

    def test_failed_cancelled_and_older_jobs_do_not_replace_successful_recipe(self):
        save_preferences(self.root, remember_last_settings=True)
        self.job.remember_settings = True
        remember_success(self.root, self.job)
        changed = replace(self.job, output_format=0)
        for status in (JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.DOWNLOADING):
            changed.status = status
            remember_success(self.root, changed)
        changed.status = JobStatus.COMPLETED
        changed.separation_completed_at = '2026-10-08T10:00:00+00:00'
        remember_success(self.root, changed)
        self.assertEqual(remembered_spec(self.root), self.spec)
        changed.separation_completed_at = '2026-10-10T10:00:00+00:00'
        changed.result_files = []
        remember_success(self.root, changed)
        self.assertEqual(remembered_spec(self.root), self.spec)

    def test_recent_store_has_only_recipe_no_audio_paths_token_or_import_choices(self):
        save_preferences(self.root, remember_last_settings=True)
        self.job.remember_settings = True
        self.job.post_import_target = 'timeline'
        self.job.settings_snapshot = {'specification': spec_data(replace(self.spec, output_format=0))}
        remember_success(self.root, self.job)
        self.assertEqual(remembered_spec(self.root).output_format, 0)
        data = json.loads((self.root / 'config/recent_settings.json').read_text(encoding='utf-8'))
        self.assertEqual(set(data['successful']), {'specification', 'saved_at', 'job_id'})
        self.assertEqual(set(data['successful']['specification']), set(spec_data(self.spec)))
        for token in ('Source.wav', 'api_token', 'post_import', 'project_id'):
            self.assertNotIn(token, json.dumps(data))

    def test_corrupt_optional_memory_falls_back_without_overwriting_the_file(self):
        save_preferences(self.root, remember_last_settings=True)
        path = self.root / 'config/recent_settings.json'
        for content in ('[]', '{broken', '{"version":1,"successful":{"specification":{}}}'):
            path.write_text(content)
            self.assertIsNone(remembered_spec(self.root))
            self.assertEqual(path.read_text(), content)

    def test_completion_reopens_results_without_import_when_unchecked(self):
        with patch('resolve_mvsep.completion.show_progress') as view, patch('resolve_mvsep.completion.run_import') as importer:
            finish_download(self.root, self.job, self.logger, show_window=True)
            view.assert_called_once_with(self.root, self.path)
            importer.assert_not_called()

    def test_both_automatic_targets_claim_once_and_never_repeat_on_reopen(self):
        for target in ('media_pool', 'timeline'):
            with self.subTest(target=target):
                self.job.post_import_target, self.job.post_import_state = target, 'pending'
                def imported(root, path, selected):
                    self.assertEqual(Job.load(path).post_import_state, 'running')
                    self.assertEqual(selected, target)
                    return 0
                with patch('resolve_mvsep.completion.show_progress') as view, patch('resolve_mvsep.completion.run_import', side_effect=imported) as importer:
                    finish_download(self.root, self.job, self.logger, show_window=True)
                    saved = Job.load(self.path)
                    self.assertEqual(saved.post_import_state, 'completed')
                    self.assertEqual(saved.status, JobStatus.COMPLETED)
                    finish_download(self.root, saved, self.logger, show_window=True)
                    importer.assert_called_once_with(self.root, self.path, target)
                    self.assertEqual(view.call_count, 3)

    def test_import_failure_preserves_download_and_successful_settings(self):
        for failure in (1, RuntimeError('Resolve project changed')):
            self.job.post_import_target, self.job.post_import_state = 'timeline', 'pending'
            self.job.remember_settings = True
            save_preferences(self.root, remember_last_settings=True)
            options = {'side_effect': failure} if isinstance(failure, Exception) else {'return_value': failure}
            with patch('resolve_mvsep.completion.show_progress'), patch('resolve_mvsep.completion.run_import', **options) as importer:
                finish_download(self.root, self.job, self.logger, show_window=True)
                saved = Job.load(self.path)
                self.assertEqual(saved.status, JobStatus.COMPLETED)
                self.assertEqual(saved.post_import_state, 'failed')
                self.assertEqual(saved.result_files, self.job.result_files)
                self.assertEqual(remembered_spec(self.root), self.spec)
                finish_download(self.root, saved, self.logger, show_window=True)
                self.assertEqual(importer.call_count, 1)

    def test_pending_or_failed_separation_never_imports_or_records_success(self):
        for status in (JobStatus.PENDING, JobStatus.FAILED, JobStatus.CANCELLED):
            self.job.status, self.job.post_import_target, self.job.post_import_state = status, 'timeline', 'pending'
            with patch('resolve_mvsep.completion.show_progress') as view, patch('resolve_mvsep.completion.run_import') as importer:
                finish_download(self.root, self.job, self.logger, show_window=True)
                view.assert_not_called()
                importer.assert_not_called()

    def test_import_status_saves_do_not_extend_separation_elapsed_time(self):
        self.job.processing_started_at = '2026-10-09T09:58:00+00:00'
        self.assertIn('02:00', describe(self.job).elapsed)
        self.job.save(self.path)
        self.assertIn('02:00', describe(Job.load(self.path)).elapsed)

    def test_legacy_job_defaults_do_not_trigger_automatic_actions(self):
        data = json.loads(self.path.read_text(encoding='utf-8'))
        for key in ('remember_settings', 'post_import_target', 'post_import_state', 'separation_completed_at'):
            data.pop(key)
        self.path.write_text(json.dumps(data), encoding='utf-8')
        old = Job.load(self.path)
        self.assertFalse(old.remember_settings)
        self.assertEqual(old.post_import_target, 'none')
        self.assertIsNone(old.separation_completed_at)

    def test_latest_result_is_project_scoped_including_different_source_and_timeline(self):
        context = {'project_id': 'p1', 'project_name': 'Project', 'timeline_id': 'different'}
        self.assertEqual(latest_completed_for_project(self.root, context), self.path)
        self.assertIsNone(latest_completed_for_project(self.root, {**context, 'project_id': 'p2'}))
        legacy = {'project_name': 'Project', 'timeline_id': 't1'}
        self.assertEqual(latest_completed_for_project(self.root, legacy), self.path)
        self.assertIsNone(latest_completed_for_project(self.root, {**legacy, 'timeline_id': 't2'}))
        newer = Job.create(self.root / 'Another.mp3', self.spec, self.job.source_clip)
        newer.status, newer.result_files = JobStatus.FAILED, ['partial.wav']
        newer.save(self.root / 'jobs' / newer.id / 'job.json')
        self.assertEqual(latest_completed_for_project(self.root, context), self.path)

    def test_menu_without_selection_reopens_completed_result_without_upload(self):
        project, timeline = Mock(), Mock()
        project.GetUniqueId.return_value, project.GetName.return_value = 'p1', 'Project'
        timeline.GetUniqueId.return_value, timeline.GetSelectedClips.return_value = 't1', []
        context = ResolveContext(Mock(), Mock(), project, timeline, (21, 1, 0))
        with patch('resolve_mvsep.main.connect', return_value=context), \
             patch('resolve_mvsep.main.load_settings', return_value=Settings(action='separate')), \
             patch('resolve_mvsep.separation.reopen_active', return_value=None), \
             patch('resolve_mvsep.separation.launch_separation') as launch, \
             patch('resolve_mvsep.separation.show_progress') as view, \
             patch('resolve_mvsep.main.show_message'), redirect_stdout(io.StringIO()):
            self.assertEqual(run(self.root, injected_resolve=Mock()), 0)
            view.assert_called_once_with(self.root, self.path)
            launch.assert_not_called()
            timeline.GetSelectedClips.return_value = [Mock(), Mock()]
            self.assertEqual(run(self.root, injected_resolve=Mock()), 1)
            self.assertEqual(view.call_count, 1)  # ambiguous selection still reports its real error

