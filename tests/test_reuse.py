"""Portable recipes apply settings to a new source; output paths stay resumable."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from resolve_mvsep.core.errors import InspectionError
from resolve_mvsep.core.history import reserve_run
from resolve_mvsep.core.job import Job
from resolve_mvsep.core.output import assign_output_directory, output_directory, default_output_root
from resolve_mvsep.core.i18n import save_preferences, save_language
from resolve_mvsep.core.profiles import job_spec, load_profiles, read_result_settings, save_profile
from resolve_mvsep.core.recipe import capture_settings, write_reports
from resolve_mvsep.core.setup import apply_choices
from resolve_mvsep.mvsep.models import Mode, SeparationSpec
from resolve_mvsep.mvsep.names import output_name


class ReuseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.source = self.root / "Жизнь вечная.mp3"
        self.source.write_bytes(b"source one")
        self.spec = SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40, api_options={"add_opt1": "81", "add_opt2": "8"}, output_format=0)
        self.job = Job.create(self.source, self.spec)
        reserve_run(self.root, self.job)
        self.job.settings_snapshot = capture_settings(self.root, self.job)

    def test_new_source_accepts_whole_attempt_recipe_from_either_stem(self):
        output = assign_output_directory(self.root, self.job)
        output.mkdir(parents=True)
        for role in ("vocals", "other"):
            audio = output / f"Жизнь вечная_{role}_001.mp3"
            audio.write_bytes(b"result")
            self.job.result_files.append(str(audio))
        write_reports(self.root, self.job)
        second = self.root / "Музыка волн.mp3"
        second.write_bytes(b"source two")
        target = Job.create(second, SeparationSpec(Mode.SPEECH_MUSIC_SFX, 45))
        for audio in self.job.result_files:
            spec, label = read_result_settings(Path(audio + ".settings.json"))
            self.assertIn("001", label)
            self.assertEqual(spec, self.spec)
            apply_choices(target, spec, None)
            self.assertEqual(target.source_file, str(second))
            self.assertIsNone(target.remote_hash)
            self.assertEqual(target.result_files, [])
            self.assertEqual(target.specification(), self.spec)

    def test_saving_reference_uses_snapshot_not_changed_job_or_defaults(self):
        self.job.api_options["add_opt2"] = "2"
        save_profile(self.root, "Эталон 001", job_spec(self.job))
        chosen = load_profiles(self.root)["Эталон 001"]
        self.assertEqual(chosen.api_options["add_opt2"], "8")
        self.assertEqual(chosen.output_format, 0)

    def test_bad_report_or_credentials_as_options_are_rejected_without_store_changes(self):
        bad = self.root / "bad.json"
        for value in ([], {"version": 1}, {"version": 1, "settings": {"specification": {"api_token": "secret"}}}):
            bad.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(InspectionError) as error:
                read_result_settings(bad)
            self.assertNotIn("secret", str(error.exception))
        self.assertFalse((self.root / "config/profiles.json").exists())

    def test_new_paths_group_identical_sources_and_persist_for_resumes(self):
        path = assign_output_directory(self.root, self.job)
        self.assertIn("Жизнь вечная", path.parent.name)
        self.assertIn("вариант 001", path.name)
        self.job.save(self.root / "jobs" / self.job.id / "job.json")
        renamed = self.root / "Копия.mp3"
        renamed.write_bytes(self.source.read_bytes())
        second = Job.create(renamed, self.spec)
        reserve_run(self.root, second)
        next_path = assign_output_directory(self.root, second)
        self.assertEqual(path.parent, next_path.parent)
        self.assertIn("вариант 002", next_path.name)
        self.job.created_at = "2000-01-01T00:00:00+00:00"
        self.assertEqual(output_directory(self.root, self.job), path)
        self.assertEqual(assign_output_directory(self.root, self.job), path)

    def test_old_existing_output_and_saved_remote_job_keep_legacy_paths(self):
        old = self.root / "output" / self.job.id
        old.mkdir(parents=True)
        self.assertEqual(assign_output_directory(self.root, self.job), old)
        pending_remote = Job.create(self.source, self.spec)
        pending_remote.remote_hash = "already-submitted"
        self.assertEqual(assign_output_directory(self.root, pending_remote), self.root / "output" / pending_remote.id)

    def test_same_visible_name_with_different_audio_does_not_mix_source_folders(self):
        first = assign_output_directory(self.root, self.job)
        self.job.save(self.root / "jobs" / self.job.id / "job.json")
        self.source.write_bytes(b"different audio, same filename")
        replacement = Job.create(self.source, self.spec)
        reserve_run(self.root, replacement)
        second = assign_output_directory(self.root, replacement)
        self.assertNotEqual(first.parent, second.parent)
        self.assertEqual(first.parent.name, "Жизнь вечная")
        self.assertTrue(second.parent.name.startswith("Жизнь вечная ["))

    def test_saved_output_paths_cannot_escape_output_root(self):
        for value in ("../outside", "C:/outside", "/outside", "a/../../outside", "", "a\\..\\outside"):
            self.job.output_relative = value
            with self.subTest(value=value), self.assertRaises(InspectionError):
                output_directory(self.root, self.job)

    def test_long_filename_budget_preserves_role_number_and_extension(self):
        name = output_name("Очень длинное имя " * 20, "source_model_vocals.mp3", run_index=27, max_length=80)
        self.assertLessEqual(len(name), 80)
        self.assertTrue(name.endswith("_vocals_027.mp3"))

    def test_custom_root_is_frozen_and_language_changes_do_not_reset_default(self):
        chosen = self.root / "Мои результаты"
        self.job.output_root = str(chosen)
        destination = assign_output_directory(self.root, self.job)
        self.assertTrue(destination.is_relative_to(chosen))
        self.job.save(self.root / "jobs" / self.job.id / "job.json")
        alternate = self.root / "Другие результаты"
        save_preferences(self.root, output_root=str(alternate), split_ratio=.6)
        save_language(self.root, "en")
        self.assertEqual(default_output_root(self.root), alternate)
        saved = Job.load(self.root / "jobs" / self.job.id / "job.json")
        self.assertEqual(output_directory(self.root, saved), destination)
        self.assertEqual(assign_output_directory(self.root, saved), destination)
        old = Job.create(self.source, self.spec)
        old.remote_hash = "legacy"
        self.assertEqual(output_directory(self.root, old), self.root / "output" / old.id)

    def test_custom_root_retains_sidecars_and_rejects_relative_path_escape(self):
        chosen = self.root / "Мои результаты"
        self.job.output_root = str(chosen)
        destination = assign_output_directory(self.root, self.job)
        destination.mkdir(parents=True)
        audio = destination / "source_vocals_001.wav"
        audio.write_bytes(b"downloaded")
        self.job.result_files = [str(audio)]
        write_reports(self.root, self.job)
        sidecar = Path(str(audio) + ".settings.json")
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(data["result_directory"], str(destination))
        self.assertEqual(read_result_settings(sidecar)[0], self.spec)
        self.job.output_relative = "../outside"
        with self.assertRaises(InspectionError):
            output_directory(self.root, self.job)


if __name__ == "__main__":
    unittest.main()
