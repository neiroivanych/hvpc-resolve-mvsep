"""Keep table relationships and treat HTML/RTF content as inert data."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from resolve_mvsep.core.description import Table, html_blocks, document_rtf, escape_rtf


class DescriptionTests(unittest.TestCase):
    def test_dnr_merged_headers_keep_metrics_attached_to_each_model(self):
        blocks = html_blocks('''<p>Сравнение</p><table><tbody>
          <tr><td rowspan="2"><strong>Название алгоритма</strong></td><td colspan="5"><strong>DnR v3</strong></td></tr>
          <tr><td>music (SDR)</td><td colspan="3">sfx (SDR)</td><td>speech (SDR)</td></tr>
          <tr><td>SCNet Large</td><td>9.94</td><td colspan="3">11.35</td><td>12.59</td></tr>
          <tr><td>Ensemble (Mel + SCNet)</td><td>10.15</td><td colspan="3">11.67</td><td>12.81</td></tr>
        </tbody></table><p>После таблицы</p>''')
        table = next(block for block in blocks if isinstance(block, Table))
        self.assertEqual(table.boundaries, [0, 1, 2, 5, 6])
        self.assertEqual([(c.start, c.end, c.text) for c in table.rows[2]],
                         [(0, 1, "SCNet Large"), (1, 2, "9.94"), (2, 5, "11.35"), (5, 6, "12.59")])
        self.assertEqual(table.rows[0][0].merge, "first")
        self.assertEqual(table.rows[1][0].merge, "continue")
        for width in (6000, 12000):
            result = document_rtf(blocks, width).decode("ascii")
            self.assertEqual(result.count(r"\trowd"), 4)
            self.assertIn(r"\clvmgf", result)
            self.assertIn(r"\clvmrg", result)
            self.assertIn("SCNet Large", result)
            self.assertIn(r"\cellx" + str(width), result)
        self.assertEqual(blocks[-1].text, "После таблицы")

    def test_html_scripts_styles_and_rtf_commands_cannot_execute(self):
        blocks = html_blocks(r'<script>upload()</script><style>bad()</style><p>Тест 😀 {\object x}</p><img src="https://external.invalid/a">')
        self.assertEqual(len(blocks), 1)
        rendered = document_rtf(blocks, 6000).decode("ascii")
        self.assertNotIn("upload()", rendered)
        self.assertNotIn("external.invalid", rendered)
        self.assertIn(r"\{\\object x\}", rendered)
        self.assertIn(r"\u-10179?\u-8704?", rendered)

    def test_paragraphs_inside_cells_do_not_split_table_rows(self):
        table = html_blocks('<table><tr><th>A</th><th>B</th></tr><tr><td><p>One</p><p>Two</p></td><td>3<br>4</td></tr></table>')[0]
        self.assertEqual(len(table.rows), 2)
        self.assertEqual(table.rows[1][0].text, "One\nTwo")
        self.assertEqual(table.rows[1][1].text, "3\n4")
