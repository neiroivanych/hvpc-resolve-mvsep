"""Settings must be per job; opening/cancelling setup must never submit media."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from resolve_mvsep import cli
from resolve_mvsep.core.active import active_job, record_worker
from resolve_mvsep.core.errors import InspectionError
from resolve_mvsep.core.job import Job, JobStatus
from resolve_mvsep.core.lock import SingleJobLock
from resolve_mvsep.core.profiles import load_profiles, save_profile
from resolve_mvsep.core.secrets import Redactor
from resolve_mvsep.core.setup import apply_choices, freeze_defaults, with_format
from resolve_mvsep.mvsep.models import Mode, SeparationSpec
from resolve_mvsep.mvsep.names import output_name
from resolve_mvsep.separation import reopen_active


class SetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "config").mkdir()
        self.config = self.root / "config/mvsep.json"
        self.config.write_text(json.dumps({"api_token": "fake-token"}), encoding="utf-8")
        self.spec = SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40, api_options={"add_opt1": "81", "add_opt2": "8"})
        self.job = Job.create(self.root / "Песня.mp3", self.spec)
        self.job.status = JobStatus.CONFIGURING
        self.path = self.root / "jobs" / self.job.id / "job.json"
        self.job.save(self.path)

    def test_profile_roundtrip_snapshots_settings_without_credentials_or_paths(self):
        save_profile(self.root, "Вокал MP3", with_format(self.spec, 0))
        loaded = load_profiles(self.root)["Вокал MP3"]
        self.assertEqual(loaded.output_format, 0)
        self.assertEqual(loaded.api_options, self.spec.api_options)
        self.assertEqual(loaded.sep_type, 40)
        content = (self.root / "config/profiles.json").read_text(encoding="utf-8")
        for unwanted in ("api_token", "fake-token", "source_file", "Песня"):
            self.assertNotIn(unwanted, content)
        self.assertEqual(json.loads(self.config.read_text()), {"api_token": "fake-token"})

    def test_per_job_format_override_never_mutates_saved_profile_or_other_jobs(self):
        save_profile(self.root, "Вокал", self.spec)
        profile = load_profiles(self.root)["Вокал"]
        apply_choices(self.job, with_format(profile, 0), "Вокал")
        self.job.save(self.path)
        self.job.api_options["add_opt2"] = "2"
        saved = Job.load(self.path)
        self.assertEqual(saved.output_format, 0)
        self.assertEqual(saved.api_options["add_opt2"], "8")
        self.assertEqual(saved.settings_profile, "Вокал")
        self.assertEqual(profile.output_format, 1)
        self.assertEqual(profile.api_options["add_opt2"], "8")
        self.assertEqual(load_profiles(self.root)["Вокал"], self.spec)

    def test_duplicate_profile_names_and_corrupt_store_are_not_overwritten(self):
        save_profile(self.root, "Music", self.spec)
        path = self.root / "config/profiles.json"
        before = path.read_bytes()
        with self.assertRaises(InspectionError):
            save_profile(self.root, " music ", with_format(self.spec, 0))
        self.assertEqual(path.read_bytes(), before)
        path.write_text('{"bad": "fake-token"}', encoding="utf-8")
        with self.assertRaises(InspectionError) as error:
            save_profile(self.root, "New", self.spec)
        self.assertNotIn("fake-token", str(error.exception))
        self.assertEqual(path.read_text(), '{"bad": "fake-token"}')

    def test_algorithm_defaults_are_pinned_without_overriding_explicit_or_hidden_options(self):
        catalog = {40: {"algorithm_fields": [
            {"name": "add_opt1", "default_key": "99"},
            {"name": "add_opt2", "default_key": "2"},
            {"name": "add_opt3", "default_key": "0", "conditions": {"model": "different"}},
            {"name": "api_token", "default_key": "untrusted"},
        ]}}
        original = SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40, api_options={"add_opt1": "81"})
        frozen = freeze_defaults(original, catalog)
        self.assertEqual(frozen.api_options, {"add_opt1": "81", "add_opt2": "2"})
        self.assertEqual(original.api_options, {"add_opt1": "81"})

    def test_remote_preset_keeps_its_own_format_and_options(self):
        preset = SeparationSpec(Mode.VOCALS_INSTRUMENTAL, None, preset_id=123)
        with self.assertRaises(InspectionError):
            with_format(preset, 0)
        save_profile(self.root, "Server preset", preset)
        self.assertEqual(load_profiles(self.root)["Server preset"].form_fields(),
                         {"is_demo": "0", "preset_id": "123"})

    def test_submitted_job_cannot_be_reconfigured(self):
        for state, task_hash in ((JobStatus.PROCESSING, None), (JobStatus.FAILED, "known-hash")):
            self.job.status, self.job.remote_hash = state, task_hash
            with self.assertRaises(InspectionError):
                apply_choices(self.job, with_format(self.spec, 0), None)
            self.assertEqual(self.job.output_format, 1)

    def run_configure(self, choice):
        with patch("resolve_mvsep.setup_window.choose_settings", return_value=choice), \
                patch("resolve_mvsep.cli.execute_job") as execute, \
                patch("resolve_mvsep.cli.MVSEPClient") as client, \
                patch("resolve_mvsep.separation.show_progress") as viewer, \
                redirect_stdout(io.StringIO()):
            client.return_value.redactor = Redactor("fake-token")
            execute.side_effect = lambda root, job, *args: job
            result = cli.main(self.root, ["--run-job", str(self.path), "--configure"])
        return result, execute, client, viewer

    def test_cancel_and_window_close_make_no_api_call_or_worker_execution(self):
        result, execute, client, viewer = self.run_configure(None)
        self.assertEqual(result, 0)
        self.assertEqual(Job.load(self.path).status, JobStatus.CANCELLED)
        self.assertFalse((self.root / "config/source-runs.json").exists())
        execute.assert_not_called()
        self.assertEqual(client.return_value.method_calls, [])
        viewer.assert_not_called()
        self.assertIsNone(active_job(self.root))

    def test_start_hands_off_chosen_snapshot_and_opens_progress(self):
        result, execute, client, viewer = self.run_configure((with_format(self.spec, 0), "MP3"))
        self.assertEqual(result, 0)
        saved = Job.load(self.path)
        self.assertEqual(saved.status, JobStatus.PENDING)
        self.assertEqual(saved.output_format, 0)
        self.assertEqual(saved.api_options, self.spec.api_options)
        self.assertEqual(execute.call_args.args[1].specification(), saved.specification())
        viewer.assert_called_once_with(self.root, self.path)
        self.assertEqual(client.return_value.method_calls, [])

    def test_repeat_launch_restores_configuring_window_without_another_process(self):
        with patch("resolve_mvsep.core.active.process_identity", return_value="worker-id"), \
                patch("resolve_mvsep.separation.subprocess.Popen") as process:
            record_worker(self.root, self.job, 1234)
            self.assertEqual(reopen_active(self.root, notify=True), self.path)
            process.assert_not_called()
        self.assertTrue((self.path.parent / "show-window").is_file())

    def test_competing_worker_prevents_configuration_without_rewriting_manifest(self):
        before = self.path.read_bytes()
        with SingleJobLock(self.root / "jobs" / ".active.lock"):
            result, execute, client, viewer = self.run_configure((with_format(self.spec, 0), None))
        self.assertEqual(result, 1)
        self.assertEqual(self.path.read_bytes(), before)
        execute.assert_not_called()
        viewer.assert_not_called()
        self.assertEqual(client.return_value.method_calls, [])

    def test_setup_itself_holds_lock_and_is_discoverable_before_submission(self):
        def choose(root, job):
            self.assertEqual(active_job(root), self.path)
            with self.assertRaises(InspectionError):
                with SingleJobLock(root / "jobs" / ".active.lock"):
                    self.fail("setup must reserve the worker lock")
            return None
        with patch("resolve_mvsep.setup_window.choose_settings", side_effect=choose), \
                patch("resolve_mvsep.cli.execute_job") as execute, redirect_stdout(io.StringIO()):
            result = cli.main(self.root, ["--run-job", str(self.path), "--configure"])
        self.assertEqual(result, 0)
        execute.assert_not_called()


class NamingTests(unittest.TestCase):
    def test_original_unicode_name_and_server_stem_label_for_wav_or_mp3(self):
        for role in ("vocals", "other", "others", "drums", "bass", "no_drums", "backing_vocals"):
            for extension in ("wav", "mp3"):
                with self.subTest(role=role, extension=extension):
                    self.assertEqual(output_name("Жизнь вечная", f"source_bs_roformer_mt_81_{role}.{extension}"),
                                     f"Жизнь вечная_{role}.{extension}")

    def test_unknown_and_preset_output_labels_remain_distinct(self):
        self.assertEqual(output_name("Song", "source_new_model_custom-part.wav"), "Song_new_model_custom-part.wav")
        self.assertEqual(output_name("Song", "my_clean_vocals.wav", preset_output=True), "Song_my_clean_vocals.wav")
        self.assertEqual(output_name("Song", "source_model_vocals-fullness-level-1.wav"),
                         "Song_vocals-fullness-level-1.wav")

    def test_untrusted_labels_and_long_names_are_valid_bounded_local_filenames(self):
        for remote in ("../../escape.wav", r"C:\evil:bad?.mp3", "CON.wav"):
            name = output_name("Песня" * 100, remote)
            self.assertEqual(Path(name).name, name)
            self.assertNotRegex(name, r'[<>:"/\\|?*]')
            self.assertLessEqual(len(name), 150)
            self.assertEqual(Path(name).suffix, Path(remote).suffix)


if __name__ == "__main__":
    unittest.main()
