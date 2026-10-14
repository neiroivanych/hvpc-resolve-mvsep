"""Premium discovery and labels must agree with access checks, without selecting paid options."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from resolve_mvsep.core.catalog import (choices, filter_algorithms, matching_options, new_algorithm,
    option_label, premium_label, premium_requirements, validate_catalog, visible_fields)
from resolve_mvsep.core.i18n import using_language
from resolve_mvsep.mvsep.models import Mode, SeparationSpec
from test_catalog import example_catalog


class PremiumCatalogTests(unittest.TestCase):
    def setUp(self):
        data = example_catalog()
        self.algorithm = data[0]
        self.algorithm["orientation"] = 0
        self.model, self.overlap, self.conditional = self.algorithm["algorithm_fields"]
        self.model["price_rules"] = [{"option_key": "171", "mode": "multiplier", "value": "1.0000"}]
        self.overlap["price_rules"] = [{"option_key": "8", "mode": "multiplier", "value": "2.0000"}]
        self.conditional["price_rules"] = [{"option_key": "1", "mode": "multiplier", "value": "1.5000"}]
        self.paid_algorithm = {"render_id": 26, "name": "Ensemble", "orientation": 2,
            "algorithm_fields": [{"name": "add_opt1", "text": "Output files",
                                  "options": {"0": "Standard", "1": "Detailed"}, "default_key": "0"}]}
        self.catalog = validate_catalog([*data, self.paid_algorithm])
        self.spec = new_algorithm(SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40), self.algorithm)

    def test_search_finds_model_options_absent_from_description_and_does_not_select_them(self):
        before_data, before_spec = copy.deepcopy(self.catalog), copy.deepcopy(self.spec)
        for locale in ("ru", "en"):
            with using_language(locale):
                self.assertEqual(filter_algorithms(self.catalog, None, "124 bands"), [40])
                self.assertEqual(filter_algorithms(self.catalog, None, "87.5%"), [40])
                self.assertEqual(matching_options(self.algorithm, "124 bands"), ["Model type: 124 bands [Premium]"])
                self.assertEqual(matching_options(self.algorithm, "BS Roformer 124 bands"), ["Model type: 124 bands [Premium]"])
                self.assertEqual(matching_options(self.algorithm, "BS Roformer"), [])
                self.assertEqual(matching_options(self.algorithm, ""), [])
        self.assertEqual(self.catalog, before_data)
        self.assertEqual(self.spec, before_spec)
        self.assertEqual(self.spec.api_options, {"add_opt1": "81", "add_opt2": "2"})
        self.assertEqual(premium_requirements(self.spec, self.catalog), [])

    def test_search_includes_hidden_paid_options_but_access_uses_only_visible_choices(self):
        self.assertNotIn(self.conditional, visible_fields(self.algorithm, self.spec.api_options))
        self.assertEqual(filter_algorithms(self.catalog, None, "Fullness Premium"), [40])
        self.assertEqual(premium_requirements(self.spec, self.catalog), [])

    def test_whole_premium_and_mixed_algorithms_stay_in_catalog_and_search(self):
        self.assertEqual(filter_algorithms(self.catalog, None, ""), [40, 45, 26])
        self.assertEqual(filter_algorithms(self.catalog, None, "Premium"), [40, 26])
        self.assertEqual(premium_label(self.paid_algorithm), " [Premium]")
        self.assertEqual(premium_label(self.algorithm), " [есть настройки Premium]")
        self.assertEqual(premium_label(self.catalog[45]), "")
        with using_language("en"):
            self.assertEqual(premium_label(self.algorithm), " [Premium options available]")

    def test_suffix_after_value_including_paid_default_and_multiplier_one(self):
        self.assertEqual(option_label(self.algorithm, self.model, "171"), "124 bands [Premium]")
        self.assertEqual(option_label(self.algorithm, self.overlap, "8"), "87.5% [Premium]")
        self.assertEqual(option_label(self.algorithm, self.overlap, "2"), "50% · по умолчанию")
        self.overlap["default_key"] = "8"
        self.assertEqual(option_label(self.algorithm, self.overlap, "8"), "87.5% [Premium] · по умолчанию")
        with using_language("en"):
            self.assertEqual(option_label(self.algorithm, self.overlap, "8"), "87.5% [Premium] · default")

    def test_whole_premium_algorithm_marks_every_value_without_field_price_rules(self):
        field = self.paid_algorithm["algorithm_fields"][0]
        for value in choices(field):
            self.assertIn(" [Premium]", option_label(self.paid_algorithm, field, value))
            spec = new_algorithm(self.spec, self.paid_algorithm)
            self.assertTrue(premium_requirements(spec, self.catalog))

    def test_registration_requirement_is_not_a_premium_price_rule(self):
        self.model["registered_only_keys"] = ["81"]
        self.assertNotIn("Premium", option_label(self.algorithm, self.model, "81"))


if __name__ == "__main__":
    unittest.main()
