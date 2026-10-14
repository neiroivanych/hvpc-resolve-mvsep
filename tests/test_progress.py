"""Progress telemetry, repeat-launch protection and honest queue/start estimates."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from resolve_mvsep.core.active import active_job, process_identity, record_worker
from resolve_mvsep.core.job import Job, JobStatus
from resolve_mvsep.core.progress import describe
from resolve_mvsep.core.runner import job_path
from resolve_mvsep.mvsep.client import MVSEPClient
from resolve_mvsep.mvsep.errors import NetworkError
from resolve_mvsep.mvsep.models import Mode, SeparationSpec
from resolve_mvsep.separation import launch_separation, reopen_active


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.job = Job.create(self.root / "music.wav", SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40))
        self.path = job_path(self.root, self.job)
        self.job.save(self.path)

    def test_current_process_has_stable_identity_and_invalid_pid_is_not_alive(self):
        identity = process_identity(os.getpid())
        self.assertTrue(identity)
        self.assertEqual(identity, process_identity(os.getpid()))
        self.assertIsNone(process_identity(-1))

    def test_repeat_click_reuses_job_before_inspecting_clip_or_loading_token(self):
        record_worker(self.root, self.job, os.getpid())
        with patch("resolve_mvsep.separation.show_progress") as show, \
                patch("resolve_mvsep.separation._start_worker") as start:
            result = launch_separation(self.root, Mock(), notify=True)
            self.assertEqual(result, self.path)
            start.assert_not_called()
            show.assert_called_once_with(self.root, self.path)
            self.assertEqual(reopen_active(self.root, notify=False), self.path)
        self.assertEqual(len(list((self.root / "jobs").glob("*/job.json"))), 1)

    def test_completed_job_is_not_reused_as_an_active_job(self):
        record_worker(self.root, self.job, os.getpid())
        self.job.status = JobStatus.COMPLETED
        self.job.save(self.path)
        self.assertIsNone(active_job(self.root))

    def test_stale_pid_and_reused_pid_are_ignored(self):
        record_worker(self.root, self.job, os.getpid())
        for identity in (None, "different-creation-time"):
            with patch("resolve_mvsep.core.active.process_identity", return_value=identity):
                self.assertIsNone(active_job(self.root))

    def test_real_worker_record_is_discoverable_without_launcher(self):
        record_worker(self.root, self.job)
        self.assertEqual(active_job(self.root), self.path)

    def test_windows_state_replace_retries_a_brief_viewer_read_conflict(self):
        original = Path.replace
        attempts = []
        def replace_with_conflict(source, target):
            attempts.append(source)
            if len(attempts) == 1:
                raise PermissionError("reader temporarily holds the previous state")
            return original(source, target)
        with patch.object(Path, "replace", replace_with_conflict):
            self.job.status = JobStatus.QUEUED
            self.job.save(self.path)
        self.assertEqual(Job.load(self.path).status, JobStatus.QUEUED)
        self.assertEqual(len(attempts), 2)

    def test_old_job_json_loads_with_new_telemetry_defaults(self):
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for key in ("account_wait_seconds", "remote_message", "queue_count", "expected_files"):
            data.pop(key)
        self.path.write_text(json.dumps(data), encoding="utf-8")
        loaded = Job.load(self.path)
        self.assertIsNone(loaded.account_wait_seconds)
        self.assertEqual(loaded.remote_message, "")

    def test_queue_eta_is_account_start_time_never_completion_or_countdown(self):
        instant = datetime.now(timezone.utc)
        self.job.status = JobStatus.QUEUED
        self.job.queue_position = 3
        self.job.account_wait_seconds = 100
        self.job.queue_checked_at = (instant - timedelta(seconds=30)).isoformat()
        view = describe(self.job, instant=instant)
        self.assertIn("старейшей задачи аккаунта: ≈ 01:40", view.detail)
        self.assertIn("другой вашей задаче", view.detail)
        self.assertIn("полного завершения сервис не сообщает", view.detail)
        self.assertIn("очереди: 3", view.detail)
        self.job.account_wait_seconds = None
        self.assertIn("недоступна", describe(self.job, instant=instant).detail)
        self.job.account_wait_seconds = 100
        self.job.queue_checked_at = (instant - timedelta(seconds=65)).isoformat()
        self.assertIn("недоступна", describe(self.job, instant=instant).detail)

    def test_stopped_worker_never_shows_an_active_indicator(self):
        self.job.status = JobStatus.UPLOADING
        view = describe(self.job, running=False)
        self.assertFalse(view.active)
        self.assertIn("не работает", view.title)
        self.assertIn("Проверьте историю", view.detail)

    def test_remote_staleness_is_visible_and_elapsed_freezes_at_completion(self):
        instant = datetime.now(timezone.utc)
        self.job.created_at = (instant - timedelta(minutes=3)).isoformat()
        self.job.status = JobStatus.PROCESSING
        self.job.remote_checked_at = (instant - timedelta(seconds=90)).isoformat()
        self.assertIn("Свежего статуса пока нет", describe(self.job, instant=instant).detail)
        self.job.status = JobStatus.COMPLETED
        self.job.updated_at = (instant - timedelta(minutes=1)).isoformat()
        self.job.result_files = ["a.wav", "b.wav"]
        view = describe(self.job, instant=instant)
        self.assertEqual(view.elapsed, "Прошло: 02:00")
        self.assertIn("Скачано файлов: 2", view.detail)
        self.assertFalse(view.active)

    def test_optional_summary_failure_does_not_fail_or_delay_with_retries(self):
        transport = Mock()
        transport.json.side_effect = [
            {"success": True, "status": "waiting", "data": {}},
            NetworkError("estimate unavailable"),
            {"success": True, "status": "done", "data": {
                "files": [{"download": "stem.wav", "url": "https://mvsep.com/stem.wav"}]}},
        ]
        client = MVSEPClient("fake-token", transport=transport, sleep=lambda _: None)
        summaries = []
        result = client.wait_for_result("hash", timeout=30, on_queue=summaries.append)
        self.assertEqual(result.status, "done")
        self.assertEqual(summaries, [None])
        self.assertEqual(transport.json.call_count, 3)
        self.assertEqual(transport.json.call_args_list[1].args[2], 3)

    def test_optional_summary_validates_null_invalid_and_zero_without_false_now(self):
        transport = Mock()
        client = MVSEPClient("fake-token", transport=transport)
        for value in (None, True, -1, float("nan"), "soon"):
            transport.json.return_value = {"estimated_wait_seconds": value}
            self.assertIsNone(client.get_queue_summary().estimated_wait_seconds)
        transport.json.return_value = {"estimated_wait_seconds": 0, "ahead": 0}
        summary = client.get_queue_summary()
        self.assertEqual(summary.estimated_wait_seconds, 0)
        self.assertEqual(summary.ahead, 0)

    def test_live_queue_summary_envelope_and_missing_fields(self):
        transport = Mock()
        client = MVSEPClient("fake-token", transport=transport)
        transport.json.return_value = {"success": True, "data": {
            "queued": 1, "processing": 0, "ahead": 4,
            "estimated_wait_seconds": 73.5, "throughput_per_min": 2.1}}
        summary = client.get_queue_summary()
        self.assertEqual(summary.estimated_wait_seconds, 73.5)
        self.assertEqual(summary.ahead, 4)
        transport.json.return_value = {"success": True, "data": {}}
        from resolve_mvsep.mvsep.errors import MVSEPError
        with self.assertRaises(MVSEPError):
            client.get_queue_summary()

    def test_remote_details_parse_and_redact_without_trusting_invalid_counters(self):
        transport = Mock()
        transport.json.return_value = {"success": True, "status": "distributing", "data": {
            "message": "working fake-token", "current_order": True, "queue_count": -1,
            "finished_chunks": 2, "all_chunks": 8}}
        client = MVSEPClient("fake-token", transport=transport)
        result = client.get_result("hash", deadline=client.clock() + 30)
        self.assertNotIn("fake-token", result.message)
        self.assertEqual(result.finished_chunks, 2)
        self.assertEqual(result.all_chunks, 8)
        self.assertIsNone(result.queue_count)
        self.assertIsNone(result.queue_position)


if __name__ == "__main__":
    unittest.main()
