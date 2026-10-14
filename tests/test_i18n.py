"""Language choice must never change separation settings or source identity."""
import ast
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from string import Formatter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from resolve_mvsep.core.catalog import description, field_label, group_name, new_algorithm
from resolve_mvsep.core.i18n import language, load_language, save_language, tr, using_language
from resolve_mvsep.core.job import Job, JobStatus
from resolve_mvsep.core.profiles import load_profiles, save_profile
from resolve_mvsep.core.progress import describe
from resolve_mvsep.core.translations import EN
from resolve_mvsep.mvsep.models import Mode, SeparationSpec
from resolve_mvsep.setup_layout import layout, MIN_CLIENT


class LanguageTests(unittest.TestCase):
    def test_language_is_separate_from_presets_and_corrupt_preference_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40, api_options={"add_opt1": "81"}, output_format=0)
            save_profile(root, "Эталон {vocals}", spec)
            original = (root / "config/profiles.json").read_bytes()
            self.assertEqual(load_language(root), "ru")
            save_language(root, "en")
            self.assertEqual(load_language(root), "en")
            self.assertEqual(load_profiles(root)["Эталон {vocals}"], spec)
            self.assertEqual((root / "config/profiles.json").read_bytes(), original)
            self.assertFalse((root / "jobs").exists())
            (root / "config/ui.json").write_text("[]")
            self.assertEqual(load_language(root), "ru")
            with self.assertRaises(ValueError):
                save_language(root, "../en")

    def test_translation_formats_templates_not_user_values_and_restores_context(self):
        before = language.get()
        with using_language("en"):
            self.assertEqual(tr("Проект: {project}  •  Дорожка: {track}", project="Отмена {v0}", track="Audio 2"),
                             "Project: Отмена {v0}  •  Track: Audio 2")
            with using_language("ru"):
                self.assertEqual(tr("Отмена"), "Отмена")
            self.assertEqual(tr("Отмена"), "Cancel")
        self.assertEqual(language.get(), before)

    def test_catalog_language_changes_presentation_but_not_request(self):
        item = {"render_id": 40, "name": "BS Roformer", "algorithm_group": {"name": "Vocals / Instrumental"},
                "algorithm_fields": [{"name": "add_opt1", "text": "Vocal model type", "default_key": "81", "options": '{"81":"v2025"}'}],
                "algorithm_descriptions": [{"lang": "ru", "short_description": "Вокал"},
                                           {"lang": "en", "short_description": "Vocals"}]}
        initial = SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40, output_format=0)
        requests = []
        for locale, heading, detail in (("ru", "Вокал и инструментал", "Вокал"), ("en", "Vocals / Instrumental", "Vocals")):
            with using_language(locale):
                self.assertEqual(group_name(item), heading)
                self.assertEqual(description(item), detail)
                self.assertEqual(field_label(item["algorithm_fields"][0]), "Model type")
                requests.append(new_algorithm(initial, item).form_fields())
        self.assertEqual(requests[0], requests[1])

    def test_progress_localizes_every_stage_and_preserves_real_percent(self):
        job = Job.create(Path("audio.wav"), SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40))
        with using_language("en"):
            for state in JobStatus:
                job.status = state
                view = describe(job)
                for text in (view.title, view.detail, view.elapsed, view.remaining, view.progress_label):
                    self.assertNotRegex(text, r"[А-Яа-яЁё]")
            job.status, job.remote_status = JobStatus.PROCESSING, "distributing"
            job.all_chunks, job.finished_chunks = 4, 3
            view = describe(job)
            self.assertEqual(view.percent, 75)
            self.assertIn("3 of 4 (75%)", view.progress_label)
            job.status, job.transfer_direction = JobStatus.UPLOADING, "upload"
            job.transfer_bytes, job.transfer_total = 1_500_000, 3_000_000
            job.transfer_updated_at = datetime.now(timezone.utc).isoformat()
            view = describe(job)
            self.assertIn("1.5 MB / 3.0 MB", view.progress_label)
            self.assertEqual(view.percent, 50)

    def test_translation_placeholders_match_and_all_explicit_keys_exist(self):
        formatter = Formatter()
        for source, target in EN.items():
            tokens = lambda text: {(key, spec, conversion) for _, key, spec, conversion in formatter.parse(text) if key is not None}
            self.assertEqual(tokens(source), tokens(target), source)
        source_root = Path(__file__).resolve().parents[1] / "src/resolve_mvsep"
        for path in source_root.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr"
                        and node.args and isinstance(node.args[0], ast.Constant)):
                    key = node.args[0].value
                    if isinstance(key, str) and re.search("[А-Яа-яЁё]", key):
                        self.assertIn(key, EN, f"{path.name}: {key}")


class LayoutTests(unittest.TestCase):
    def test_splitter_expands_catalog_but_protects_parameter_and_folder_controls(self):
        for width, height in (MIN_CLIENT, (1384, 921), (1904, 1080)):
            normal = layout(width, height)
            expanded = layout(width, height, .8)
            self.assertGreater(expanded["algorithms"][2], normal["algorithms"][2])
            for ratio in (.1, .42, .8):
                boxes = layout(width, height, ratio)
                self.assertGreaterEqual(boxes["field_0"][2], 430)
                for name in ("folder_path", "folder_choose", "folder_default", "folder_remember", "start"):
                    x, y, w, h = boxes[name]
                    self.assertGreaterEqual(x, 0)
                    self.assertLessEqual(x + w, width)
                    self.assertLessEqual(y + h, height)

    def test_four_parameters_fit_minimum_default_and_maximized_sizes(self):
        for width, height in (MIN_CLIENT, (1304, 901), (1904, 1000)):
            boxes = layout(width, height)
            self.assertGreaterEqual(boxes["algorithms"][2], 368)
            self.assertGreaterEqual(boxes["field_0"][2], 500)
            self.assertGreaterEqual(boxes["algorithms"][3], 120)
            # Combo height describes the popup; its closed control is 28px tall.
            _, bottom, _, _ = boxes["card_output"]
            for row in range(4):
                x, y, w, _ = boxes[f"field_{row}"]
                self.assertLessEqual(y + 28, bottom - 16)
                self.assertLessEqual(x + w, width - 20)
            nx, ny, nw, nh = boxes["notice"]
            sx, sy, sw, sh = boxes["cancel"]
            self.assertLess(nx + nw, sx)
            self.assertLessEqual(ny + nh, height)
            self.assertLessEqual(sy + sh, height)
