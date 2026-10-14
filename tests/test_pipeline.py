"""The visual stage panel must not turn telemetry gaps into progress promises."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from resolve_mvsep.core.i18n import using_language
from resolve_mvsep.core.job import Job, JobStatus
from resolve_mvsep.core.pipeline import pipeline_view, stage_labels
from resolve_mvsep.core.progress import describe
from resolve_mvsep.mvsep.models import Mode, SeparationSpec
from resolve_mvsep.progress_layout import progress_layout, MIN_PROGRESS


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.job = Job.create(Path('Example.wav'), SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40))
        self.job.remote_checked_at = self.job.queue_checked_at = self.now.isoformat()

    def panel(self, running=True, instant=None):
        instant = instant or self.now
        return pipeline_view(self.job, describe(self.job, running=running, instant=instant), instant=instant)

    def test_queue_uses_server_estimate_without_ticking_it_down_and_keeps_zero_position(self):
        self.job.status = JobStatus.QUEUED
        self.job.account_wait_seconds, self.job.queue_position, self.job.queue_count = 120, 16, 41
        panel = self.panel()
        self.assertEqual((panel.step, panel.value, panel.secondary), (1, '2:00', 'Позиция в очереди: 16 из 41'))
        self.assertIn('аккаунта', panel.note)
        self.assertEqual(self.panel(instant=self.now + timedelta(seconds=10)).value, '2:00')
        self.job.account_wait_seconds, self.job.queue_position, self.job.queue_count = 73, 0, 38
        self.assertEqual(self.panel().value, '1:13')
        self.assertEqual(self.panel().secondary, 'Позиция в очереди: 0 из 38')
        self.job.account_wait_seconds = 0
        self.assertEqual(self.panel().value, '0:00')

    def test_video_preparation_and_unknown_phases_never_claim_separation_or_completion(self):
        self.job.status = JobStatus.PROCESSING
        for locale in ('ru', 'en'):
            with using_language(locale):
                for state in ('preparing', 'converting', 'new_preparation_stage'):
                    self.job.remote_status = state
                    panel = self.panel()
                    view = describe(self.job, instant=self.now)
                    self.assertEqual(panel.step, None if state == 'new_preparation_stage' else 0)
                    self.assertFalse(panel.numeric)
                    self.assertTrue(view.active)
                    self.assertFalse(view.completed)
                    self.assertIn(state, view.detail)

    def test_missing_invalid_and_stale_eta_is_not_zero(self):
        self.job.status = JobStatus.QUEUED
        for value in (None, -1, True, float('nan'), float('inf'), '120'):
            self.job.account_wait_seconds = value
            self.assertEqual(self.panel().value, '—')
        self.job.account_wait_seconds, self.job.queue_position, self.job.queue_count = 60, 3, 41
        self.job.queue_checked_at = (self.now - timedelta(seconds=61)).isoformat()
        self.assertEqual(self.panel().value, '—')
        self.assertIn('3 из 41', self.panel().secondary)
        self.job.remote_checked_at = (self.now - timedelta(seconds=46)).isoformat()
        panel = self.panel()
        self.assertNotIn('41', panel.secondary)
        self.assertIn('свежие данные', panel.note)
        self.assertFalse(panel.animate)

    def test_stopped_or_failed_worker_does_not_animate_or_show_old_queue_eta(self):
        self.job.status = JobStatus.QUEUED
        self.job.account_wait_seconds = 120
        panel = self.panel(running=False)
        self.assertIsNone(panel.step)
        self.assertFalse(panel.animate)
        self.assertNotEqual(panel.value, '2:00')
        self.job.status = JobStatus.FAILED
        self.assertEqual(self.panel().tone, 'error')
        self.assertIsNone(self.panel().step)

    def test_processing_and_assembly_have_distinct_stages_without_fake_percent(self):
        self.job.status = JobStatus.PROCESSING
        self.job.remote_status = 'processing'
        self.assertEqual(self.panel().step, 2)
        self.assertNotIn('%', self.panel().value)
        self.job.remote_status = 'merging'
        self.assertEqual(self.panel().step, 3)
        self.assertIn('собирает', describe(self.job).title)
        self.job.remote_status = 'distributing'
        self.job.all_chunks, self.job.finished_chunks = 4, 3
        self.assertEqual(self.panel().value, '75%')
        self.job.remote_checked_at = None
        self.assertNotIn('%', self.panel().value)
        self.assertFalse(self.panel().animate)

    def test_upload_100_is_not_finished_or_advanced_until_server_reports_it(self):
        self.job.status = JobStatus.UPLOADING
        self.job.transfer_direction, self.job.transfer_bytes, self.job.transfer_total = 'upload', 100, 100
        panel = self.panel()
        self.assertEqual((panel.step, panel.value), (0, '100%'))
        self.job.status = JobStatus.DOWNLOADING
        self.job.transfer_direction = 'download'
        self.assertEqual(self.panel().step, 4)
        self.job.status = JobStatus.COMPLETED
        self.job.result_files = ['vocals.wav', 'other.wav']
        self.assertEqual(self.panel().step, 5)
        self.assertFalse(self.panel().animate)
        self.assertIn('2', self.panel().secondary)

    def test_locales_preserve_phase_and_every_state_has_translated_copy(self):
        for locale in ('ru', 'en'):
            with using_language(locale):
                self.assertEqual(len(stage_labels()), 5)
                for state in JobStatus:
                    self.job.status = state
                    panel = self.panel()
                    if locale == 'en':
                        self.assertNotRegex(' '.join((panel.caption, panel.value, panel.secondary, panel.note, *stage_labels())), '[А-Яа-я]')

    def test_compact_and_large_layouts_keep_all_actions_and_fields_inside_window(self):
        for width, height in (MIN_PROGRESS, (744, 821), (1000, 1000)):
            boxes = progress_layout(width, height)
            for name, (x, y, w, h) in boxes.items():
                self.assertGreaterEqual(w, 0, name)
                self.assertGreaterEqual(h, 0, name)
                self.assertLessEqual(x + w, width, name)
                self.assertLessEqual(y + h, height, name)
            self.assertLessEqual(boxes['detail'][1] + boxes['detail'][3], boxes['hint'][1])
            self.assertLessEqual(boxes['profile_name'][1] + boxes['profile_name'][3], boxes['profile_notice'][1])

    def test_details_divider_reserves_text_and_footer_at_both_limits(self):
        for width, height in (MIN_PROGRESS, (744, 881), (1100, 1040)):
            for preferred in (-1000, 174, 246, 2000):
                boxes = progress_layout(width, height, preferred)
                for name, (x, y, w, h) in boxes.items():
                    self.assertGreaterEqual(min(x, y, w, h), 0, name)
                    self.assertLessEqual(x + w, width, name)
                    self.assertLessEqual(y + h, height, name)
                self.assertGreaterEqual(boxes['detail'][3], 70)
                for upper, lower in (('status_card', 'splitter'), ('splitter', 'detail_card'),
                                     ('detail_card', 'detail_bottom'), ('detail_bottom', 'connection_card'),
                                     ('connection_card', 'import_pool')):
                    self.assertLessEqual(boxes[upper][1] + boxes[upper][3], boxes[lower][1])
                for name in ('hero_note', 'hero_value', 'hero_secondary', 'step_0'):
                    self.assertGreaterEqual(boxes[name][1], boxes['card'][1])
                    self.assertLessEqual(boxes[name][1] + boxes[name][3], boxes['card'][1] + boxes['card'][3])

    def test_expanding_details_takes_space_from_indicator_only(self):
        regular = progress_layout(744, 881, 246)
        expanded = progress_layout(744, 881, 174)
        self.assertGreater(expanded['detail'][3], regular['detail'][3])
        self.assertEqual(expanded['detail'][1] + expanded['detail'][3],
                         regular['detail'][1] + regular['detail'][3])
        for name in ('connection_card', 'hint', 'import_pool', 'import_timeline',
                     'result', 'settings', 'logs', 'close', 'profile_name', 'save_profile'):
            self.assertEqual(expanded[name], regular[name], name)


if __name__ == '__main__':
    unittest.main()
