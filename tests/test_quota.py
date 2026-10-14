"""Daily quota uses live server counts and never turns missing data into zero."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from resolve_mvsep.core.i18n import using_language
from resolve_mvsep.core.quota import QuotaMonitor, QuotaResult, quota_text, read_quota
from resolve_mvsep.mvsep.client import MVSEPClient
from resolve_mvsep.mvsep.models import DailyQuota
from resolve_mvsep.mvsep.errors import MVSEPError, NetworkError
from resolve_mvsep.progress_layout import MIN_PROGRESS, MIN_SOURCE_HEIGHT, progress_layout


class QuotaTests(unittest.TestCase):
    def test_queue_counter_is_parsed_as_remaining_over_max_including_zero(self):
        transport = Mock()
        client = MVSEPClient('fixture-token', transport=transport, request_timeout=20)
        for left, maximum in ((48, 50), (0, 50), (0, 0), (123, 200)):
            transport.json.return_value = {'queue': {}, 'free_separations': {'left': left, 'max': maximum}}
            self.assertEqual(client.get_daily_quota(), DailyQuota(left, maximum))
        transport.json.assert_called_with('GET', 'https://mvsep.com/api/app/queue?api_token=fixture-token', 8)
        self.assertEqual(transport.json.call_count, 4)

    def test_invalid_missing_and_rejected_counts_are_not_invented(self):
        transport = Mock()
        client = MVSEPClient('fixture-token', transport=transport)
        invalid = [None, {}, {'free_separations': None},
                   {'success': False, 'free_separations': {'left': 4, 'max': 50}}]
        invalid += [{'free_separations': {'left': left, 'max': maximum}} for left, maximum in
                    ((None, 50), (1, None), (-1, 50), (51, 50), (True, 50), (1, False), ('48', 50), (1.5, 50))]
        for payload in invalid:
            transport.json.return_value = payload
            with self.subTest(payload=payload), self.assertRaises(MVSEPError):
                client.get_daily_quota()

    def test_read_only_bounded_call_without_identity_persistence(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch('resolve_mvsep.core.quota.load_mvsep_settings') as load, \
                patch('resolve_mvsep.core.quota.MVSEPClient') as factory:
            root = Path(folder)
            load.return_value = Mock(api_token='fixture-token')
            factory.return_value.get_daily_quota.return_value = DailyQuota(48, 50)
            self.assertEqual(read_quota(root), QuotaResult('ready', DailyQuota(48, 50)))
            factory.assert_called_once_with('fixture-token', request_timeout=8, max_get_retries=0)
            self.assertEqual([call[0] for call in factory.return_value.method_calls], ['get_daily_quota'])
            self.assertEqual(list(root.iterdir()), [])

    def test_missing_key_skips_network_and_failure_does_not_leak_details(self):
        with patch('resolve_mvsep.core.quota.load_mvsep_settings') as load, \
                patch('resolve_mvsep.core.quota.MVSEPClient') as factory:
            load.return_value = Mock(api_token='')
            self.assertEqual(read_quota(Path('.')), QuotaResult('no_token'))
            factory.assert_not_called()
            load.return_value = Mock(api_token='secret')
            factory.return_value.get_daily_quota.side_effect = NetworkError('private identity and key')
            self.assertEqual(read_quota(Path('.')), QuotaResult('unavailable'))

    def test_localized_loading_zero_unknown_and_missing_key_are_distinct(self):
        for locale in ('ru', 'en'):
            with using_language(locale):
                values = [quota_text(result) for result in (None, QuotaResult('no_token'), QuotaResult('unavailable'),
                          QuotaResult('ready', DailyQuota(0, 50)), QuotaResult('ready', DailyQuota(48, 50)))]
                self.assertEqual(len(set(values)), len(values))
                self.assertIn('0 / 50', values[3])
                self.assertIn('48 / 50', values[4])
                if locale == 'en':
                    self.assertNotRegex(' '.join(values), '[А-Яа-я]')

    def test_refresh_is_nonblocking_throttled_and_skips_hidden_viewer(self):
        now, tasks = [0], []
        loader = Mock(return_value=QuotaResult('ready', DailyQuota(48, 50)))
        monitor = QuotaMonitor(Path('.'), loader=loader, clock=lambda: now[0], launch=tasks.append)
        self.assertIsNone(monitor.poll('upload'))
        self.assertEqual(len(tasks), 1)
        loader.assert_not_called()
        monitor.poll('accepted')  # change while request is in flight must not be lost
        self.assertEqual(len(tasks), 1)
        tasks.pop(0)()
        self.assertEqual(monitor.poll('accepted'), loader.return_value)
        self.assertEqual(len(tasks), 1)
        tasks.pop(0)()
        for _ in range(10):
            monitor.poll('accepted')
        self.assertEqual(tasks, [])
        now[0] = 61
        monitor.poll('completed', visible=False)
        self.assertEqual(tasks, [])
        monitor.poll('completed')
        self.assertEqual(len(tasks), 1)
        loader.side_effect = RuntimeError('private details')
        tasks.pop(0)()
        self.assertEqual(monitor.poll('completed'), QuotaResult('unavailable'))
        self.assertEqual(tasks, [])
        now[0] = 122
        monitor.poll('completed')
        self.assertEqual(len(tasks), 1)

    def test_wrapped_filename_and_quota_have_separate_columns_and_clearance(self):
        for width in (MIN_PROGRESS[0], 744, 1100):
            for source_height in (MIN_SOURCE_HEIGHT, 92, 158):
                height = MIN_PROGRESS[1] + source_height - MIN_SOURCE_HEIGHT
                boxes = progress_layout(width, height, source_height=source_height)
                source, quota = boxes['source'], boxes['quota']
                self.assertGreaterEqual(quota[0] - (source[0] + source[2]), 24)
                self.assertLessEqual(quota[0] + quota[2], width - 22)
                self.assertGreaterEqual(boxes['card'][1] - max(source[1] + source[3], quota[1] + quota[3]), 6)
                self.assertGreaterEqual(boxes['detail'][3], 70)
                for name, (x, y, w, h) in boxes.items():
                    self.assertGreaterEqual(min(x, y, w, h), 0, name)
                    self.assertLessEqual(y + h, height, name)


if __name__ == '__main__':
    unittest.main()
