"""M2 configuration, durable state, exclusivity, and menu/worker integration."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from resolve_mvsep.core.errors import InspectionError
from resolve_mvsep.core.job import Job, JobStatus
from resolve_mvsep.core.lock import SingleJobLock
from resolve_mvsep.core.logging import close_logger, create_logger
from resolve_mvsep.core.mvsep_config import load_mvsep_settings
from resolve_mvsep.core.runner import execute_job, job_path, validate_source
from resolve_mvsep.core.secrets import Redactor
from resolve_mvsep.mvsep.errors import DeadlineExceeded, NetworkError
from resolve_mvsep.mvsep.models import Mode, RemoteFile, RemoteResult, SeparationSpec, TransferProgress
from resolve_mvsep.core.models import ClipMetadata, InspectionReport, SourceMedia
from resolve_mvsep.separation import launch_separation

TOKEN = "fake-private/key+value"


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        self.config = self.root / "config" / "mvsep.json"
        self.config.write_text(json.dumps({"api_token": TOKEN}), encoding="utf-8")
        self.source = self.root / "original.wav"
        self.source.write_bytes(b"original")
        self.settings = load_mvsep_settings(self.root, environ={})
        with redirect_stdout(io.StringIO()):
            self.logger, self.log_path = create_logger(self.root, prefix="m2", secrets=(TOKEN,))
        self.addCleanup(close_logger, self.logger)
        self.job = Job.create(self.source, self.settings.spec, {"clip": {"timeline_start": 86400.5}})
        self.client = Mock()
        self.client.redactor = Redactor(TOKEN)
        self.client.create_job.return_value = "remote-123"

        def wait(task_hash, *, timeout, on_status, on_queue):
            on_status(RemoteResult("waiting", queue_position=2))
            on_status(RemoteResult("processing"))
            result = RemoteResult("done", (RemoteFile("vocals.wav", "https://mvsep.com/a"),), output_format="1")
            on_status(result)
            return result

        def download(result, directory, *, timeout, on_file, on_progress, source_stem, preset_output, run_index, completed_files, legacy_paths):
            self.assertEqual(source_stem, "original")
            self.assertFalse(preset_output)
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / "stem.wav"
            target.write_bytes(b"downloaded")
            on_file(target)
            return [target]

        self.client.wait_for_result.side_effect = wait
        self.client.download_results.side_effect = download

    def test_environment_token_wins_and_secrets_are_not_in_repr(self):
        settings = load_mvsep_settings(self.root, environ={"MVSEP_API_TOKEN": "env-secret"})
        self.assertEqual(settings.api_token, "env-secret")
        self.assertNotIn("env-secret", repr(settings))
        self.assertNotIn(TOKEN, repr(self.settings))
        self.assertEqual(load_mvsep_settings(self.root, environ={"MVSEP_API_TOKEN": " "}).api_token, TOKEN)

    def test_missing_token_and_invalid_config_have_actionable_safe_errors(self):
        self.config.write_text('{"api_token": ""}', encoding="utf-8")
        with self.assertRaisesRegex(InspectionError, "MVSEP_API_TOKEN"):
            load_mvsep_settings(self.root, environ={})
        self.assertEqual(load_mvsep_settings(self.root, require_token=False, environ={}).api_token, "")
        self.config.write_text('{"api_token": "' + TOKEN, encoding="utf-8")
        with self.assertRaises(InspectionError) as error:
            load_mvsep_settings(self.root, environ={})
        self.assertNotIn(TOKEN, str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)

    def test_logical_modes_defaults_and_preset_are_independent(self):
        self.assertEqual(self.settings.spec.sep_type, 40)
        self.config.write_text(json.dumps({"api_token": TOKEN, "mode": "speech_music_sfx"}), encoding="utf-8")
        self.assertEqual(load_mvsep_settings(self.root, environ={}).spec.sep_type, 45)
        self.config.write_text(json.dumps({"api_token": TOKEN, "preset_id": 456}), encoding="utf-8")
        spec = load_mvsep_settings(self.root, environ={}).spec
        self.assertIsNone(spec.sep_type)
        self.assertEqual(spec.preset_id, 456)

    def test_timeout_and_option_validation(self):
        for value in ({"poll_interval_seconds": 0}, {"request_timeout_seconds": True},
                      {"job_timeout_seconds": float("nan")}, {"max_get_retries": 11},
                      {"sep_type": True}, {"api_options": {"api_token": TOKEN}}):
            self.config.write_text(json.dumps({"api_token": TOKEN, **value}), encoding="utf-8")
            with self.assertRaises(InspectionError):
                load_mvsep_settings(self.root, environ={})

    def test_job_roundtrip_download_completion_and_source_unchanged(self):
        completed = execute_job(self.root, self.job, self.settings, self.logger, self.client)
        saved = Job.load(job_path(self.root, completed))
        self.assertEqual(saved.status, JobStatus.COMPLETED)
        self.assertEqual(saved.remote_hash, "remote-123")
        self.assertEqual(saved.source_clip["clip"]["timeline_start"], 86400.5)
        self.assertEqual(saved.progress, 1.0)
        self.assertEqual(len(saved.result_files), 1)
        self.assertEqual(self.source.read_bytes(), b"original")
        self.assertNotIn(TOKEN, job_path(self.root, saved).read_text(encoding="utf-8"))
        self.assertNotIn(TOKEN, self.log_path.read_text(encoding="utf-8"))

    def test_selected_destination_is_used_for_downloads_and_sidecars(self):
        chosen = self.root / "Selected results"
        self.job.output_root = str(chosen)
        self.job.save(job_path(self.root, self.job))
        completed = execute_job(self.root, self.job, self.settings, self.logger, self.client)
        target = Path(completed.result_files[0])
        self.assertTrue(target.is_relative_to(chosen.resolve()))
        self.assertTrue(Path(str(target) + ".settings.json").is_file())
        self.assertEqual(self.source.read_bytes(), b"original")

    def test_unwritable_destination_fails_before_upload_or_attempt_reservation(self):
        self.job.output_root = str(self.source)  # existing file cannot be a folder
        with self.assertRaises(InspectionError):
            execute_job(self.root, self.job, self.settings, self.logger, self.client)
        self.client.create_job.assert_not_called()
        self.client.get_account_access.assert_not_called()
        self.assertIsNone(self.job.run_index)
        self.assertFalse((self.root / "config/source-runs.json").exists())

    def test_timeout_retains_hash_and_resume_does_not_upload_or_change_algorithm(self):
        real_wait = self.client.wait_for_result.side_effect
        self.client.wait_for_result.side_effect = DeadlineExceeded("timeout")
        with self.assertRaises(DeadlineExceeded):
            execute_job(self.root, self.job, self.settings, self.logger, self.client)
        saved = Job.load(job_path(self.root, self.job))
        self.assertEqual(saved.status, JobStatus.FAILED)
        self.assertEqual(saved.remote_hash, "remote-123")
        self.client.wait_for_result.side_effect = real_wait
        # New jobs may now choose another algorithm. This job retains its snapshot.
        self.config.write_text(json.dumps({"api_token": TOKEN, "sep_type": 45}), encoding="utf-8")
        current_settings = load_mvsep_settings(self.root, environ={})
        completed = execute_job(self.root, saved, current_settings, self.logger, self.client)
        self.assertEqual(completed.sep_type, 40)
        self.client.create_job.assert_called_once()

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows path limit')
    def test_long_result_path_is_rejected_before_upload(self):
        from resolve_mvsep.mvsep.errors import MVSEPError
        self.job.output_root = str(self.root / ('a' * 100) / ('b' * 60))
        with self.assertRaisesRegex(MVSEPError, 'Output path is too long'):
            execute_job(self.root, self.job, self.settings, self.logger, self.client)
        self.client.create_job.assert_not_called()
        saved = Job.load(job_path(self.root, self.job))
        self.assertEqual(saved.status, JobStatus.FAILED)
        self.assertIsNone(saved.remote_hash)

    def test_unknown_creation_outcome_is_not_resubmitted(self):
        self.client.create_job.side_effect = NetworkError("unknown outcome")
        with self.assertRaises(NetworkError):
            execute_job(self.root, self.job, self.settings, self.logger, self.client)
        with self.assertRaisesRegex(InspectionError, "no upload repeated"):
            execute_job(self.root, Job.load(job_path(self.root, self.job)), self.settings, self.logger, self.client)
        self.client.create_job.assert_called_once()

    def test_new_server_phase_is_saved_and_failed_wait_can_resume_same_job(self):
        normal_wait = self.client.wait_for_result.side_effect
        def wait(task_hash, *, timeout, on_status, on_queue):
            on_status(RemoteResult('converting', message='Preparing WAV'))
            snapshot = Job.load(job_path(self.root, self.job))
            self.assertEqual(snapshot.status, JobStatus.PROCESSING)
            self.assertEqual(snapshot.remote_status, 'converting')
            on_status(RemoteResult('new_preparation_stage', message='Still preparing'))
            raise DeadlineExceeded('local wait exhausted')
        self.client.wait_for_result.side_effect = wait
        with self.assertRaises(DeadlineExceeded):
            execute_job(self.root, self.job, self.settings, self.logger, self.client)
        saved = Job.load(job_path(self.root, self.job))
        self.assertEqual(saved.remote_status, 'new_preparation_stage')
        self.assertEqual(saved.remote_message, 'Still preparing')
        self.client.wait_for_result.side_effect = normal_wait
        completed = execute_job(self.root, saved, self.settings, self.logger, self.client)
        self.assertEqual(completed.status, JobStatus.COMPLETED)
        self.client.create_job.assert_called_once()

    def test_logs_redact_tokens_in_chained_tracebacks_and_saved_error(self):
        self.client.create_job.side_effect = RuntimeError("echo " + TOKEN + " fake-private%2Fkey%2Bvalue")
        with self.assertRaises(RuntimeError):
            execute_job(self.root, self.job, self.settings, self.logger, self.client)
        log = self.log_path.read_text(encoding="utf-8")
        saved = job_path(self.root, self.job).read_text(encoding="utf-8")
        for text in (log, saved):
            self.assertNotIn(TOKEN, text)
            self.assertNotIn("fake-private%2Fkey%2Bvalue", text)
            self.assertIn("[REDACTED]", text)
        self.assertIn("Traceback", log)

    def test_completed_job_does_not_upload_again(self):
        completed = execute_job(self.root, self.job, self.settings, self.logger, self.client)
        execute_job(self.root, completed, self.settings, self.logger, self.client)
        self.client.create_job.assert_called_once()
        self.client.download_results.assert_called_once()

    def test_runner_persists_progress_in_reloaded_job_before_marking_completed(self):
        self.job.save(job_path(self.root, self.job))  # runner reloads this object under lock
        samples = []
        def create(source, spec, *, on_progress):
            on_progress(TransferProgress("upload", 0, 100))
            on_progress(TransferProgress("upload", 100, 100))
            samples.append(Job.load(job_path(self.root, self.job)))
            return "remote-123"
        def download(result, directory, *, timeout, on_file, on_progress, source_stem, preset_output, run_index, completed_files, legacy_paths):
            on_progress(TransferProgress("download", 0, 50, 1, 1))
            on_progress(TransferProgress("download", 50, 50, 1, 1))
            samples.append(Job.load(job_path(self.root, self.job)))
            directory.mkdir(parents=True, exist_ok=True)
            file = directory / "stem.wav"
            file.write_bytes(b"audio")
            on_file(file)
            return [file]
        self.client.create_job.side_effect = create
        self.client.download_results.side_effect = download
        completed = execute_job(self.root, self.job, self.settings, self.logger, self.client)
        self.assertEqual(samples[0].status, JobStatus.UPLOADING)
        self.assertEqual(samples[0].transfer_bytes, 100)
        self.assertIsNone(samples[0].remote_hash)  # 100% bytes isn't acceptance by MVSEP
        self.assertEqual(samples[1].status, JobStatus.DOWNLOADING)
        self.assertEqual(samples[1].transfer_bytes, 50)
        self.assertEqual(completed.status, JobStatus.COMPLETED)

    def test_only_one_worker_can_hold_lock_and_completed_lock_releases(self):
        lock_path = self.root / "jobs" / ".active.lock"
        with SingleJobLock(lock_path):
            with self.assertRaisesRegex(InspectionError, "already running"):
                execute_job(self.root, self.job, self.settings, self.logger, self.client)
            self.client.create_job.assert_not_called()
        with SingleJobLock(lock_path):
            pass

    def test_mp4_goes_to_server_unchanged_and_can_resume_without_upload(self):
        video = self.root / "original.MP4"
        video.write_bytes(b"video with embedded audio; decoded by server")
        self.job.source_file = str(video)
        self.job.source_clip = {'source': {'properties': {'Audio Ch': '2'}}}
        completed = execute_job(self.root, self.job, self.settings, self.logger, self.client)
        self.assertEqual(completed.status, JobStatus.COMPLETED)
        self.assertEqual(self.client.create_job.call_args.args[0], video)
        self.assertEqual(video.read_bytes(), b"video with embedded audio; decoded by server")
        execute_job(self.root, completed, self.settings, self.logger, self.client)
        self.client.create_job.assert_called_once()

    def test_mp4_without_audio_and_unsupported_formats_are_rejected(self):
        video = self.root / 'video.mp4'; video.write_bytes(b'video')
        for channels in ('0', '', None, 'unknown'):
            with self.assertRaisesRegex(InspectionError, 'звуковую дорожку'):
                validate_source(video, {'Audio Ch': channels})
        unsupported = self.root / 'video.mov'; unsupported.write_bytes(b'video')
        with self.assertRaisesRegex(InspectionError, 'MP4'):
            validate_source(unsupported)

    def test_background_launch_hands_off_metadata_but_no_token_in_arguments_or_job(self):
        report = InspectionReport(
            "2026-10-08T00:00:00+00:00", "21.1.0.17", "Test", "Timeline", "tl-1", 86400,
            "01:00:00:00", "24", "01:00:00:00",
            ClipMetadata("Audio", "clip-1", "selected", "audio", 2, "Audio 2", 86400, 86640,
                         240, 0, 0, 0),
            SourceMedia(str(self.source), True, "Audio", "media-1", {}),
        )
        with patch("resolve_mvsep.separation.worker_python", return_value=Path(sys.executable)), \
                patch("resolve_mvsep.separation.subprocess.Popen") as popen, \
                patch("resolve_mvsep.core.active.process_identity", return_value="test-process"), \
                patch("resolve_mvsep.separation.load_mvsep_settings", return_value=self.settings):
            popen.return_value.pid = 1234
            path = launch_separation(self.root, report, notify=True)
        saved = Job.load(path)
        self.assertEqual(saved.source_clip["clip"]["track_index"], 2)
        self.assertEqual(saved.status, JobStatus.CONFIGURING)
        command = popen.call_args_list[0].args[0]
        self.assertIn("--run-job", command)
        self.assertIn("--notify", command)
        self.assertNotIn(TOKEN, str(command))
        self.assertNotIn(TOKEN, path.read_text(encoding="utf-8"))
        self.assertIn("--configure", command)
        self.assertEqual(popen.call_count, 1)  # progress viewer opens only after Start
        self.assertNotIn("shell", popen.call_args.kwargs)
        if sys.platform == "win32":
            self.assertNotEqual(popen.call_args.kwargs["creationflags"], 0)


if __name__ == "__main__":
    unittest.main()
