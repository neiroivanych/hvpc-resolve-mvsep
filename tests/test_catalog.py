"""The catalog must not silently substitute a reference or send inactive fields."""
import copy
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from resolve_mvsep.core.catalog import (description, description_fragments, expected_outputs, filter_algorithms, new_algorithm,
    normalized_options, plain_text, refresh_catalog, unsupported_reason, validate_catalog,
    validate_selection, visible_fields)
from resolve_mvsep.core.errors import InspectionError
from resolve_mvsep.core.i18n import using_language
from resolve_mvsep.core.job import Job
from resolve_mvsep.core.profiles import load_profiles, save_profile, read_result_settings
from resolve_mvsep.core.recipe import capture_settings, write_reports
from resolve_mvsep.core.setup import apply_choices
from resolve_mvsep.mvsep.models import Mode, SeparationSpec


def example_catalog():
    return [{"render_id": 40, "name": "BS Roformer", "algorithm_group": {"name": "Vocals / Instrumental"},
             "algorithm_descriptions": [{"lang": "ru", "short_description": "Разделяет вокал и музыку.",
                                         "long_description": "<p>Подробное описание.</p>"}],
             "algorithm_fields": [
                 {"id": 25, "name": "add_opt1", "text": "Vocal model type", "input_type": "select",
                  "options": '{"81":"Версия 2025.07", "171":"124 bands"}', "default_key": "81"},
                 {"id": 127, "name": "add_opt2", "text": "Overlap", "input_type": "select", "required": 1,
                  "options": '{"2":"50%", "8":"87.5%"}', "default_key": "2"},
                 {"id": 132, "name": "add_opt3", "text": "Set of stems", "input_type": "select",
                  "options": '{"0":"Standard", "1":"Fullness"}', "default_key": "0",
                  "conditions": [{"depends_on_field_id": 25, "operator": "in", "value_json": ["171"], "action": "show"}]}
             ]},
            {"render_id": 45, "name": "BandIt v2", "algorithm_group": {"name": "Effects"},
             "description": "Speech, music and effects", "algorithm_fields": []}]


class CatalogTests(unittest.TestCase):
    def test_missing_catalog_description_is_explicit_in_each_language(self):
        for lang, expected in (("ru", "MVSEP пока не предоставил описание этого алгоритма."),
                               ("en", "MVSEP has not provided a description for this algorithm yet.")):
            with using_language(lang):
                for value in ("<i>No data found</i>", "<p> </p>", None):
                    self.assertEqual(description_fragments({"description": value}), (expected, ""))

    def test_placeholder_does_not_hide_real_fallback_description_or_table(self):
        item = {"description": "No data found", "algorithm_descriptions": [
            {"lang": "ru", "short_description": "<i>No data found</i>", "long_description": ""},
            {"lang": "en", "short_description": "Actual description", "long_description": "<table><tr><td>1</td></tr></table>"}]}
        with using_language("ru"):
            short, full = description_fragments(item)
            self.assertEqual(short, "Actual description")
            self.assertIn("<table>", full)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = example_catalog()
        self.catalog = validate_catalog(self.data)
        self.spec = SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40, output_format=0)

    def test_group_and_russian_description_search(self):
        self.assertEqual(filter_algorithms(self.catalog, "Вокал и инструментал", "вокал"), [40])
        self.assertEqual(filter_algorithms(self.catalog, "Речь, музыка и эффекты", "вокал"), [])
        self.assertEqual(filter_algorithms(self.catalog, None, "45 bandit"), [45])
        self.assertIn("Подробное описание.", description(self.catalog[40]))
        self.assertNotIn("<p>", description(self.catalog[40]))
        self.assertEqual(plain_text("<p>A &amp; B</p><script>bad</script><p>C</p>"), "A & B\r\nC")

    def test_defaults_and_edit_reveal_and_remove_conditional_field(self):
        first = new_algorithm(self.spec, self.catalog[40])
        self.assertEqual(first.api_options, {"add_opt1": "81", "add_opt2": "2"})
        expanded = normalized_options(self.catalog[40], {"add_opt1": "171", "add_opt2": "8"})
        self.assertEqual(expanded, {"add_opt1": "171", "add_opt2": "8", "add_opt3": "0"})
        reduced = normalized_options(self.catalog[40], {**expanded, "add_opt1": "81", "add_opt3": "1"})
        self.assertEqual(reduced, {"add_opt1": "81", "add_opt2": "8"})
        self.assertEqual(first.output_format, 0)
        self.assertEqual(self.spec.api_options, {})

    def test_hide_rule_excludes_explicit_hidden_value(self):
        algorithm = copy.deepcopy(self.catalog[40])
        algorithm["algorithm_fields"][2]["conditions"][0]["action"] = "hide"
        opts = normalized_options(algorithm, {"add_opt1": "171", "add_opt3": "1"})
        self.assertNotIn("add_opt3", opts)
        self.assertIn("add_opt3", normalized_options(algorithm, {"add_opt1": "81"}))

    def test_changed_algorithm_does_not_carry_foreign_parameters_or_server_preset(self):
        old = replace(self.spec, api_options={"add_opt1": "171", "add_opt3": "1"})
        new = new_algorithm(old, self.catalog[45])
        self.assertEqual(new.mode, Mode.SPEECH_MUSIC_SFX)
        self.assertEqual(new.api_options, {})
        self.assertEqual(new.output_format, 0)
        preset = SeparationSpec(Mode.VOCALS_INSTRUMENTAL, None, preset_id=4)
        self.assertIsNone(new_algorithm(preset, self.catalog[40]).preset_id)

    def test_reference_values_missing_from_current_catalog_are_not_replaced(self):
        old = replace(self.spec, api_options={"add_opt1": "removed", "add_opt2": "8"})
        with self.assertRaisesRegex(InspectionError, "отсутствует"):
            validate_selection(old, self.catalog)
        self.assertEqual(old.api_options["add_opt1"], "removed")

    def test_missing_or_hidden_or_unknown_fields_block_before_submit(self):
        variants = [{"add_opt1": "81"}, {"add_opt2": "8"},
                    {"add_opt1": "81", "add_opt2": "8", "add_opt3": "1"},
                    {"add_opt1": "81", "add_opt2": "8", "add_opt99": "1"}]
        for options in variants:
            with self.subTest(options=options), self.assertRaises(InspectionError):
                validate_selection(replace(self.spec, api_options=options), self.catalog)

    def test_unknown_rule_dependency_and_cycle_cannot_be_silently_ignored(self):
        for rule in [{"depends_on_field_id": 25, "operator": "future", "action": "show"},
                     {"depends_on_field_id": 999, "operator": "in", "action": "show"},
                     {"depends_on_field_id": 132, "operator": "in", "action": "show"}]:
            algorithm = copy.deepcopy(self.catalog[40])
            algorithm["algorithm_fields"][2]["conditions"] = [rule]
            with self.subTest(rule=rule), self.assertRaises(InspectionError):
                visible_fields(algorithm, {})

    def test_required_text_and_select_zero_are_validated(self):
        algorithm = {"render_id": 222, "name": "Future", "algorithm_fields": [
            {"id": 1, "name": "add_opt1", "input_type": "text", "required": 1}]}
        spec = SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 222, api_options={"add_opt1": " "})
        with self.assertRaises(InspectionError):
            validate_selection(spec, {222: algorithm})
        validate_selection(replace(spec, api_options={"add_opt1": "0"}), {222: algorithm})

    def test_unsupported_and_inactive_models_do_not_enable_start(self):
        for patch in ({"is_active": 0}, {"audio_upload_disabled": 1}, {"audio_widget": "multi_upload"},
                      {"render_id": 127}, {"algorithm_group": {"name": "ASR and TTS"}}):
            with self.subTest(patch=patch):
                self.assertTrue(unsupported_reason({**self.catalog[40], **patch}))
        preset = SeparationSpec(Mode.VOCALS_INSTRUMENTAL, None, preset_id=9)
        validate_selection(preset, {})

    def test_invalid_or_failed_catalog_refresh_preserves_cache_bytes(self):
        path = self.root / "logs/mvsep_algorithms.json"
        path.parent.mkdir()
        path.write_text(json.dumps(self.data), encoding="utf-8")
        before = path.read_bytes()
        for data in (None, [], [self.data[0], self.data[0]], [{"render_id": 1, "name": "Invalid", "algorithm_fields": [{"name": "api_token"}]}]):
            client = Mock()
            client.get_algorithms.return_value = data
            with self.subTest(data=data), self.assertRaises(InspectionError):
                refresh_catalog(self.root, client)
            self.assertEqual(path.read_bytes(), before)
        client.get_algorithms.side_effect = OSError("offline")
        with self.assertRaises(OSError):
            refresh_catalog(self.root, client)
        self.assertEqual(path.read_bytes(), before)

    def test_public_catalog_refresh_does_not_read_credentials_or_submit(self):
        client = Mock()
        client.get_algorithms.return_value = self.data
        self.assertEqual(refresh_catalog(self.root, client), self.catalog)
        self.assertEqual([call[0] for call in client.method_calls], ["get_algorithms"])
        self.assertFalse((self.root / "config").exists())
        self.assertEqual(list((self.root / "logs").glob("*.tmp")), [])

    def test_chosen_conditional_values_survive_profile_report_and_new_source(self):
        client = Mock()
        client.get_algorithms.return_value = self.data
        refresh_catalog(self.root, client)
        selected = replace(self.spec, api_options={"add_opt1": "171", "add_opt2": "8", "add_opt3": "1"})
        validate_selection(selected, self.catalog)
        save_profile(self.root, "Эталон", selected)
        chosen = load_profiles(self.root)["Эталон"]
        job = Job.create(self.root / "Новый исходник.wav", self.spec)
        apply_choices(job, chosen, "Эталон")
        job.settings_snapshot = capture_settings(self.root, job)
        report = write_reports(self.root, job).with_suffix(".json")
        recovered, _ = read_result_settings(report)
        self.assertEqual(recovered.form_fields(), selected.form_fields())
        self.assertTrue(job.source_file.endswith("Новый исходник.wav"))
        self.assertEqual(job.settings_snapshot["parameters"][2]["label"], "Fullness")
        self.data[0]["algorithm_fields"][2]["options"] = '{"1":"Changed"}'
        refresh_catalog(self.root, client)
        self.assertEqual(job.settings_snapshot["parameters"][2]["label"], "Fullness")

    def test_output_prediction_uses_model_variant_and_only_visible_overrides(self):
        algorithm = copy.deepcopy(self.catalog[40])
        algorithm['outputs'] = '{"vocals":"Vocals","other":"Other"}'
        algorithm['algorithm_fields'][0]['server_key'] = 'model_type'
        algorithm['output_variants'] = {"variants": [{"match": {"model_type": "171"},
                                                    "outputs": {"vocals": "Vocals", "instrum": "Instrumental"}}]}
        algorithm['algorithm_fields'][2]['output_overrides'] = {"1": {"mode": "add", "outputs": {"fullness": "Fullness"}}}
        self.assertEqual(list(expected_outputs(algorithm, {"add_opt1": "171", "add_opt3": "1"})),
                         ['vocals', 'instrum', 'fullness'])
        self.assertEqual(list(expected_outputs(algorithm, {"add_opt1": "81", "add_opt3": "1"})), ['vocals', 'other'])

    def test_snapshot_uses_displayed_catalog_even_if_cached_copy_changes(self):
        spec = new_algorithm(self.spec, self.catalog[40])
        job = Job.create(self.root / 'new.wav', spec)
        other = copy.deepcopy(self.data)
        other[0]['algorithm_fields'][0]['options'] = '{"81":"New server label"}'
        client = Mock()
        client.get_algorithms.return_value = other
        refresh_catalog(self.root, client)
        snapshot = capture_settings(self.root, job, catalog=self.catalog)
        self.assertEqual(snapshot['parameters'][0]['label'], 'Версия 2025.07')

    def test_missing_catalog_default_requires_an_explicit_choice(self):
        algorithm = copy.deepcopy(self.catalog[40])
        algorithm['algorithm_fields'][0]['default_key'] = ''
        spec = new_algorithm(self.spec, algorithm)
        self.assertNotIn('add_opt1', spec.api_options)
        with self.assertRaises(InspectionError):
            validate_selection(spec, {40: algorithm})


if __name__ == "__main__":
    unittest.main()
