"""Comparable attempts and portable settings, without network or paid uploads."""

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from resolve_mvsep.core.errors import InspectionError
from resolve_mvsep.core.history import reserve_run
from resolve_mvsep.core.job import Job, JobStatus
from resolve_mvsep.core.logging import close_logger, create_logger
from resolve_mvsep.core.mvsep_config import load_mvsep_settings
from resolve_mvsep.core.recipe import capture_settings, report_data, report_text, write_reports
from resolve_mvsep.core.runner import execute_job, job_path
from resolve_mvsep.mvsep.client import MVSEPClient
from resolve_mvsep.mvsep.models import Mode, SeparationSpec
from test_mvsep import Response, Transport, status


class HistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "Песня.mp3"
        self.source.write_bytes(b"original audio")
        self.spec = SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40, api_options={"add_opt1": "81", "add_opt2": "8"})
        self.job = Job.create(self.source, self.spec, {"project_name": "Test", "clip": {"track_name": "Audio 1"}})
        (self.root / "logs").mkdir()
        self.catalog_path = self.root / "logs/mvsep_algorithms.json"
        self.catalog_path.write_text(json.dumps([{"render_id": 40, "name": "BS Roformer", "algorithm_fields": [
            {"name": "add_opt1", "text": "Model", "default_key": "81", "options": '{"81":"model v1"}'},
            {"name": "add_opt2", "text": "Overlap", "default_key": "2", "options": '{"8":"87.5%", "2":"50%"}'},
            {"name": "add_opt3", "text": "Stems", "default_key": "0", "conditions": [{"operator": "in"}]}
        ]}]), encoding="utf-8")

    def test_number_increases_for_same_source_with_identical_or_different_settings(self):
        settings = [self.spec, self.spec, SeparationSpec(Mode.SPEECH_MUSIC_SFX, 45, output_format=0)]
        jobs = [Job.create(self.source, spec) for spec in settings]
        for job in jobs:
            reserve_run(self.root, job)
        self.assertEqual([job.run_index for job in jobs], [1, 2, 3])
        self.assertEqual(len({job.source_sha256 for job in jobs}), 1)
        self.assertEqual(self.source.read_bytes(), b"original audio")

    def test_byte_identical_copy_shares_sequence_but_changed_content_starts_new_sequence(self):
        reserve_run(self.root, self.job)
        copy = self.root / "different folder" / "Copy.wav"
        copy.parent.mkdir()
        shutil.copyfile(self.source, copy)
        copied = Job.create(copy, self.spec)
        reserve_run(self.root, copied)
        self.assertEqual(copied.run_index, 2)
        self.source.write_bytes(b"different audio")
        changed = Job.create(self.source, self.spec)
        reserve_run(self.root, changed)
        self.assertEqual(changed.run_index, 1)
        self.assertNotEqual(changed.source_sha256, self.job.source_sha256)

    def test_reservation_recovers_after_crash_before_job_save_and_survives_job_removal(self):
        self.job.save(job_path(self.root, self.job))
        reserve_run(self.root, self.job)
        recovered = Job.load(job_path(self.root, self.job))  # number wasn't saved yet
        reserve_run(self.root, recovered)
        self.assertEqual(recovered.run_index, 1)
        job_path(self.root, self.job).unlink()
        another = Job.create(self.source, self.spec)
        reserve_run(self.root, another)
        self.assertEqual(another.run_index, 2)

    def test_corrupt_ledger_is_not_reset_or_overwritten(self):
        reserve_run(self.root, self.job)
        path = self.root / "config/source-runs.json"
        path.write_text("corrupt", encoding="utf-8")
        with self.assertRaises(InspectionError):
            reserve_run(self.root, Job.create(self.source, self.spec))
        self.assertEqual(path.read_text(), "corrupt")

    def test_snapshot_keeps_exact_values_and_labels_when_catalog_or_profile_changes(self):
        self.job.settings_profile = "My preset"
        self.job.settings_snapshot = capture_settings(self.root, self.job)
        self.job.save(job_path(self.root, self.job))
        self.catalog_path.write_text("[]", encoding="utf-8")
        saved = Job.load(job_path(self.root, self.job))
        text = report_text(report_data(saved))
        self.assertIn("BS Roformer", text)
        self.assertIn("87.5% (значение API: 8)", text)
        self.assertIn("Model [add_opt1]: model v1", text)
        self.assertIn("My preset", text)
        self.assertIn("не передавался; значение на сервере не зафиксировано", text)
        self.assertNotIn("50%", text)

    def test_legacy_and_server_preset_reports_do_not_invent_hidden_values(self):
        self.job.api_options = {}
        legacy = report_text(report_data(self.job))
        self.assertIn("Старый запуск", legacy)
        self.assertIn("не назначался", legacy)
        self.assertNotIn("model v1", legacy)
        preset = Job.create(self.source, SeparationSpec(Mode.VOCALS_INSTRUMENTAL, None, preset_id=123))
        preset.settings_snapshot = capture_settings(self.root, preset)
        text = report_text(report_data(preset))
        self.assertIn("Серверный пресет MVSEP: 123", text)
        self.assertIn("не раскрыты", text)
        self.assertNotIn("Запрошенный формат", text)

    def test_sidecars_travel_with_audio_and_never_write_next_to_external_manifest_paths(self):
        reserve_run(self.root, self.job)
        self.job.settings_snapshot = capture_settings(self.root, self.job)
        output = self.root / "output" / self.job.id
        output.mkdir(parents=True)
        audio = output / "Песня_vocals_001.wav"
        audio.write_bytes(b"stem")
        self.job.result_files = [str(audio), str(self.source)]
        report = write_reports(self.root, self.job)
        self.assertTrue(report.is_file())
        companion = audio.with_name(audio.name + ".settings.json")
        data = json.loads(companion.read_text(encoding="utf-8"))
        self.assertEqual(data["audio_file"], audio.name)
        self.assertEqual(data["job_id"], self.job.id)
        self.assertEqual(data["run_index"], 1)
        self.assertEqual(data["settings"]["specification"]["api_options"], self.spec.api_options)
        self.assertNotIn("api_token", companion.read_text(encoding="utf-8"))
        self.assertFalse(self.source.with_name(self.source.name + ".settings.json").exists())

    def prepare_runner(self):
        config = self.root / "config"
        config.mkdir(exist_ok=True)
        (config / "mvsep.json").write_text('{"api_token":"offline-test"}', encoding="utf-8")
        settings = load_mvsep_settings(self.root, environ={})
        with redirect_stdout(io.StringIO()):
            logger, _ = create_logger(self.root, prefix="test", secrets=("offline-test",))
        self.addCleanup(close_logger, logger)
        return settings, logger

    def fake_client(self):
        transport = Transport([{"success": True, "data": {"hash": "remote-hash"}}, status("done", [
            {"download": "source_bs_roformer_vocals.wav", "url": "https://mvsep.com/a"},
            {"download": "source_bs_roformer_other.wav", "url": "https://mvsep.com/b"}])])
        transport.streams = [Response(), Response()]
        return MVSEPClient("offline-test", transport=transport)

    def test_runner_numbers_all_stems_together_and_writes_complete_matching_settings(self):
        settings, logger = self.prepare_runner()
        first = execute_job(self.root, self.job, settings, logger, self.fake_client())
        second = execute_job(self.root, Job.create(self.source, self.spec), settings, logger, self.fake_client())
        self.assertEqual([Path(p).name for p in first.result_files], ["Песня_vocals_001.wav", "Песня_other_001.wav"])
        self.assertEqual([Path(p).name for p in second.result_files], ["Песня_vocals_002.wav", "Песня_other_002.wav"])
        for job in (first, second):
            for filename in job.result_files:
                data = json.loads(Path(filename + ".settings.json").read_text(encoding="utf-8"))
                self.assertEqual(data["run_index"], job.run_index)
                self.assertEqual(len(data["result_files"]), 2)
                self.assertEqual(data["settings"]["request_fields"]["add_opt2"], "8")

    def test_resume_does_not_allocate_new_number_or_relabel_snapshot(self):
        settings, logger = self.prepare_runner()
        self.job.settings_snapshot = capture_settings(self.root, self.job)
        reserve_run(self.root, self.job)
        self.job.remote_hash, self.job.status = "saved-hash", JobStatus.FAILED
        self.job.save(job_path(self.root, self.job))
        client = self.fake_client()
        next(client.transport.responses)  # skip create response: resume polls only
        original = self.job.settings_snapshot
        self.catalog_path.write_text("[]", encoding="utf-8")
        self.source.unlink()  # downloading already-created results needs no source
        completed = execute_job(self.root, self.job, settings, logger, client)
        self.assertEqual(completed.run_index, 1)
        self.assertEqual(completed.settings_snapshot, original)
        self.assertTrue(all(call[0] == "GET" for call in client.transport.calls))

    def test_unwritable_report_does_not_turn_completed_audio_back_into_a_failed_job(self):
        settings, logger = self.prepare_runner()
        self.job.status = JobStatus.COMPLETED
        self.job.remote_hash = "already-done"
        self.job.save(job_path(self.root, self.job))
        client = self.fake_client()
        with patch("resolve_mvsep.core.runner.write_reports", side_effect=PermissionError("report open")):
            result = execute_job(self.root, self.job, settings, logger, client)
        self.assertEqual(result.status, JobStatus.COMPLETED)
        self.assertEqual(Job.load(job_path(self.root, self.job)).status, JobStatus.COMPLETED)
        self.assertEqual(client.transport.calls, [])

if __name__ == "__main__":
    unittest.main()
