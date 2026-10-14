"""Counts and UI copy must not change catalog data, requests or access rules."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from resolve_mvsep.core.catalog import (availability_label, unsupported_reason, new_algorithm, premium_requirements,
                                       description_fragments, filter_algorithms)
from resolve_mvsep.core.catalog_copy import output_count_text, catalog_blocks
from resolve_mvsep.core.description import Paragraph, Table, html_blocks
from resolve_mvsep.core.i18n import using_language
from resolve_mvsep.mvsep.models import Mode, SeparationSpec


class CatalogCopyTests(unittest.TestCase):
    def test_demucs_translation_preserves_metric_table_structure_names_and_values(self):
        html = ('<p>Algorithm Demucs3 splits track into 4 stems (bass, drums, vocals, other). '
                'The winner of the&nbsp;Music Demuxing Challenge 2021.&nbsp;</p>'
                '<p>Link: <a href="https://github.com/facebookresearch/demucs/tree/v3">'
                'https://github.com/facebookresearch/demucs/tree/v3</a></p><p><strong>Quality table</strong></p>'
                '<table><tr><td rowspan="2">Algorithm name</td><td colspan="2">Multisong dataset</td></tr>'
                '<tr><td>SDR Bass</td><td>SDR Drums</td></tr><tr><td>Demucs3 (Model A)</td>'
                '<td>9.50</td><td>8.97</td></tr></table>'
                '<p><strong>Note</strong>: For version A only MUSDB18 training data was used for training, '
                'so quality is worse than Demucs3 Model B. Demucs3 Model A and Demucs3 Model B has the same '
                'architecture, but has different weights.</p>')
        original = html_blocks(html)
        with using_language('ru'):
            translated = catalog_blocks(html, 10)
            self.assertIn('на 4 компонента', translated[0].text)
            self.assertIn('2021', translated[0].text)
            self.assertEqual(translated[1].text, 'Ссылка: https://github.com/facebookresearch/demucs/tree/v3')
            self.assertEqual(translated[2], Paragraph('Таблица качества', bold=original[2].bold))
            self.assertEqual(translated[3], original[3])
            self.assertIn('одинаковая архитектура, но разные веса', translated[4].text)
            self.assertIn('MUSDB18', translated[4].text)
        with using_language('en'):
            self.assertEqual(catalog_blocks(html, 10), original)

    def test_mislabeled_ensemble_record_cannot_override_english_or_explicit_russian(self):
        en = {'lang': 'en', 'short_description': 'Ensemble summary',
              'long_description': '<p>This is an Ensemble model.</p>'}
        wrong = {'lang': 'en', 'short_description': 'Ensemble summary',
                 'long_description': '<p>Это Ensemble с дополнительными моделями.</p>'}
        ru = {'lang': 'ru', 'short_description': 'Обновлённый русский текст',
              'long_description': '<p>Актуальное русское описание.</p>'}
        for records in ([en, wrong], [wrong, en]):
            item = {'render_id': 30, 'algorithm_descriptions': records}
            saved = copy.deepcopy(item)
            with using_language('ru'):
                self.assertEqual(description_fragments(item)[1], wrong['long_description'])
            with using_language('en'):
                self.assertEqual(description_fragments(item)[1], en['long_description'])
            self.assertEqual(item, saved)
            for ordered in ([*records, ru], [ru, *records]):
                with using_language('ru'):
                    self.assertEqual(description_fragments({**item, 'algorithm_descriptions': ordered}),
                                     (ru['short_description'], ru['long_description']))

    def test_synth_translation_keeps_all_published_category_names(self):
        labels = ['Synth', 'Synthesizer', 'Synth Pad', 'Synth Bass', 'Synth Vocals', 'Synth Strings',
                  'Synth Percussion', 'Synth FX', 'Synth Keys', 'Synth Brass', 'Synth Guitar',
                  'Synth Flute', 'Synth Ambiant']
        html = '<p>Synth extraction model. Synth is included the following stems: ' + ', '.join(labels) + '</p>'
        with using_language('ru'):
            text = catalog_blocks(html, 88)[0].text
            self.assertIn('синтезаторных партий', text)
            self.assertIn('медные духовые', text)
            self.assertNotIn('extraction model', text)
            for label in labels:
                self.assertIn(label, text)
        with using_language('en'):
            self.assertEqual(catalog_blocks(html, 88), html_blocks(html))

    def test_russian_and_original_english_search_find_translated_model(self):
        item = {'render_id': 88, 'name': 'MVSep Synth', 'description': 'Synth extraction model'}
        before = copy.deepcopy(item)
        with using_language('ru'):
            self.assertEqual(filter_algorithms({88: item}, None, 'синтезаторных'), [88])
            self.assertEqual(filter_algorithms({88: item}, None, 'extraction model'), [88])
        with using_language('en'):
            self.assertEqual(filter_algorithms({88: item}, None, 'extraction model'), [88])
        self.assertEqual(item, before)

    def test_future_changed_paragraph_is_never_silently_replaced_with_old_translation(self):
        html = '<p>Synth extraction model has new limits. Version 2, 6 stems.</p>'
        with using_language('ru'):
            self.assertEqual(catalog_blocks(html, 88), html_blocks(html))
            self.assertEqual(catalog_blocks('<p>Новый русский текст от MVSEP.</p>', 88),
                             [Paragraph('Новый русский текст от MVSEP.')])

    def test_voice_recording_examples_keep_english_input_with_labelled_russian_explanation(self):
        sample = ('"Hey! I’m recording this clip to test how well the new technology works. The secret is '
                  'to relax and speak exactly like I would to a friend. Do you think the AI can really '
                  'copy my style and energy in just fifteen seconds?"')
        html = '<h3>Option 2: Conversational (Vlog &amp; Social Media)</h3><blockquote><p>' + sample + '</p></blockquote>'
        with using_language('ru'):
            blocks = catalog_blocks(html, 103)
            self.assertEqual(blocks[0], Paragraph('Вариант 2: разговорный (видеоблоги и соцсети)', bold=html_blocks(html)[0].bold))
            self.assertEqual(blocks[1].text, sample)
            self.assertTrue(blocks[2].text.startswith('Перевод примера (для понимания):'))
            self.assertIn('пятнадцать секунд', blocks[2].text)
        with using_language('en'):
            self.assertEqual(catalog_blocks(html, 103), html_blocks(html))

    def test_bandit_english_only_prose_has_reviewed_russian_copy_and_preserves_links(self):
        html = ('<p>Bandit v2 is a model for cinematic audio source separation in 3 stems: speech, music, effects/sfx. '
                'It was trained on DnR v3 dataset.</p><p>More information in official repository: '
                'https://github.com/kwatcharasupat/bandit-v2<br>Paper: https://arxiv.org/pdf/2407.07275</p>')
        with using_language('ru'):
            text = '\n'.join(block.text for block in catalog_blocks(html, 45))
            self.assertIn('три компонента: речь, музыку', text)
            self.assertNotIn('is a model', text)
            self.assertIn('https://github.com/kwatcharasupat/bandit-v2', text)
            self.assertIn('https://arxiv.org/pdf/2407.07275', text)
            self.assertEqual(catalog_blocks(html, 46), html_blocks(html))
        with using_language('en'):
            self.assertEqual(catalog_blocks(html, 45), html_blocks(html))

    def test_count_agreement_including_teens_and_compound_numerals(self):
        forms = {'выходной компонент': (1, 21, 101),
                 'выходных компонента': (2, 3, 4, 22, 24, 102),
                 'выходных компонентов': (0, 5, 10, 11, 12, 13, 14, 20, 25, 111, 112, 114)}
        with using_language('ru'):
            for form, counts in forms.items():
                for count in counts:
                    self.assertEqual(output_count_text(count), f'По каталогу: {count} {form} — ')
        with using_language('en'):
            self.assertEqual(output_count_text(1), 'Catalog output: 1 component — ')
            for count in (0, 2, 11, 21, 101):
                self.assertEqual(output_count_text(count), f'Catalog output: {count} components — ')

    def test_copy_edits_preserve_table_values_and_english(self):
        html = '<p>Quality table</p><p>Команда смогда проверить модель на несколких записях.</p><table><tr><td>SDR</td><td>11.23</td></tr></table>'
        with using_language('ru'):
            edited = catalog_blocks(html, 46)
            self.assertEqual(edited[0].text, 'Таблица качества')
            self.assertIn('смогла', edited[1].text)
            self.assertIn('нескольких', edited[1].text)
            self.assertEqual(edited[2], html_blocks(html)[2])
        with using_language('en'):
            self.assertEqual(catalog_blocks(html, 46), html_blocks(html))

    def test_whisper_name_correction_is_not_applied_to_whispering_vocals(self):
        html = '<p>модель шепота; перед использованием шепота сначала извлекается вокал.</p>'
        with using_language('ru'):
            self.assertNotIn('шепота', catalog_blocks(html, 39)[0].text)
            self.assertEqual(catalog_blocks(html, 99), html_blocks(html))

    def test_script_support_and_premium_have_distinct_labels_without_mutating_request(self):
        for lang in ('ru', 'en'):
            with using_language(lang):
                whisper = {'render_id': 39, 'name': 'Whisper', 'is_active': 1, 'orientation': 0}
                original = copy.deepcopy(whisper)
                self.assertTrue(availability_label(whisper))
                self.assertIn('Premium', unsupported_reason(whisper))
                self.assertEqual(premium_requirements(SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 39), {39: whisper}), [])
                paid = {'render_id': 26, 'name': 'Ensemble', 'is_active': 1, 'orientation': 2}
                spec = new_algorithm(SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40), paid)
                self.assertFalse(availability_label(paid))
                self.assertFalse(unsupported_reason(paid))
                self.assertTrue(premium_requirements(spec, {26: paid}))
                disabled = {**paid, 'is_active': 0}
                self.assertIn('MVSEP', availability_label(disabled))
                self.assertNotEqual(availability_label(disabled), availability_label(whisper))
                self.assertEqual(whisper, original)
