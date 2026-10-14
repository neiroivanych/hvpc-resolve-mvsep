"""The balance display uses a bounded read-only call, with no raw-account persistence."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from resolve_mvsep.core.account_status import BalanceResult, balance_text, read_balance
from resolve_mvsep.core.errors import InspectionError
from resolve_mvsep.core.i18n import using_language
from resolve_mvsep.mvsep.errors import NetworkError
from resolve_mvsep.mvsep.models import AccountAccess
from resolve_mvsep.setup_layout import MIN_CLIENT, layout


class AccountIndicatorTests(unittest.TestCase):
    def test_bounded_read_only_call_and_no_files_or_identity_retained(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch('resolve_mvsep.core.account_status.load_mvsep_settings') as load, \
                patch('resolve_mvsep.core.account_status.MVSEPClient') as factory:
            load.return_value = Mock(api_token='secret')
            client = factory.return_value
            client.get_account_access.return_value = AccountAccess(True, 12.5)
            root = Path(directory)
            self.assertEqual(read_balance(root), BalanceResult('ready', 12.5))
            factory.assert_called_once_with('secret', request_timeout=8, max_get_retries=0)
            self.assertEqual([call[0] for call in client.method_calls], ['get_account_access'])
            self.assertEqual(list(root.iterdir()), [])

    def test_missing_token_skips_network(self):
        with patch('resolve_mvsep.core.account_status.load_mvsep_settings') as load, \
                patch('resolve_mvsep.core.account_status.MVSEPClient') as factory:
            load.return_value = Mock(api_token='')
            self.assertEqual(read_balance(Path('.')), BalanceResult('no_token'))
            factory.assert_not_called()

    def test_network_and_config_errors_do_not_expose_raw_details_or_report_zero(self):
        with patch('resolve_mvsep.core.account_status.load_mvsep_settings') as load, \
                patch('resolve_mvsep.core.account_status.MVSEPClient') as factory:
            load.return_value = Mock(api_token='secret')
            factory.return_value.get_account_access.side_effect = NetworkError('secret and private identity')
            result = read_balance(Path('.'))
            self.assertEqual(result, BalanceResult('unavailable'))
            self.assertNotIn('secret', repr(result))
            load.side_effect = InspectionError('private config contents')
            self.assertEqual(read_balance(Path('.')), BalanceResult('unavailable'))

    def test_zero_negative_fractional_missing_and_pending_are_distinct_in_both_languages(self):
        for locale in ('ru', 'en'):
            with using_language(locale):
                labels = [balance_text(BalanceResult('ready', n)) for n in (0, -4, 1, 2, 12.5, None)]
                self.assertEqual(len(set(labels)), 6)
                self.assertIn('-4', labels[1])
                self.assertIn('12.5', labels[4])
                self.assertNotEqual(balance_text(BalanceResult('ready', 7), True), labels[0])
                self.assertNotEqual(balance_text(BalanceResult('no_token')), balance_text(None))
                if locale == 'en':
                    self.assertNotRegex(' '.join(labels), '[А-Яа-я]')

    def test_balance_row_and_badge_fit_minimum_and_resized_windows(self):
        for size in (MIN_CLIENT, (1384, 921), (1904, 1000)):
            for premium in (False, True):
                boxes = layout(*size, premium=premium)
                for name in ('stage', 'balance', 'balance_refresh', 'language', 'algorithm', 'premium_badge', 'detail'):
                    x, y, width, height = boxes[name]
                    self.assertGreaterEqual(width, 0)
                    self.assertGreaterEqual(height, 0)
                    self.assertLessEqual(x + width, size[0])
                for first, second in (('stage', 'balance'), ('balance', 'balance_refresh'), ('balance_refresh', 'language')):
                    self.assertLessEqual(boxes[first][0] + boxes[first][2], boxes[second][0])
                self.assertLessEqual(boxes['premium_badge'][1] + boxes['premium_badge'][3], boxes['detail'][1])
                self.assertLessEqual(boxes['detail'][1] + boxes['detail'][3], boxes['parameters_label'][1])

    def test_setup_quota_and_wrapped_source_keep_separate_header_columns(self):
        for width in (MIN_CLIENT[0], 1384, 1904):
            for source_height, context_height in ((24, 22), (100, 22), (160, 42)):
                height = MIN_CLIENT[1] + source_height - 24 + context_height - 22
                boxes = layout(width, height, source_height=source_height, context_height=context_height)
                source, quota, balance = boxes['source'], boxes['quota'], boxes['balance']
                self.assertGreaterEqual(quota[0] - (source[0] + source[2]), 24)
                self.assertEqual(quota[0], balance[0])
                self.assertGreaterEqual(quota[1] - (balance[1] + balance[3]), 8)
                self.assertGreaterEqual(boxes['language'][0] - (quota[0] + quota[2]), 16)
                self.assertLess(source[1] + source[3], boxes['context'][1])
                self.assertLess(boxes['context'][1] + boxes['context'][3], boxes['card_profiles'][1])
                self.assertGreaterEqual(boxes['algorithms'][3], 140)
                self.assertLessEqual(boxes['start'][1] + boxes['start'][3], height)


if __name__ == '__main__':
    unittest.main()
