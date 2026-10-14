"""Premium access is checked without uploading; API validation errors stay visible."""
import io
import json
import logging
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from resolve_mvsep.core.access import check_access
from resolve_mvsep.core.catalog import premium_option, premium_requirements
from resolve_mvsep.core.errors import InspectionError
from resolve_mvsep.core.job import Job, JobStatus
from resolve_mvsep.core.mvsep_config import MVSEPSettings
from resolve_mvsep.core.progress import describe
from resolve_mvsep.core.runner import execute_job, job_path
from resolve_mvsep.core.secrets import Redactor
from resolve_mvsep.mvsep.client import MVSEPClient
from resolve_mvsep.mvsep.error_details import error_details
from resolve_mvsep.mvsep.errors import APIError, NetworkError
from resolve_mvsep.mvsep.models import AccountAccess, Mode, SeparationSpec
from resolve_mvsep.mvsep.transport import HTTPTransport

TOKEN = 'private/token+secret'


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.algorithm = {'render_id': 40, 'name': 'BS Roformer', 'orientation': 0,
            'algorithm_fields': [
                {'id': 25, 'name': 'add_opt1', 'text': 'Model', 'options': '{"81":"2025.07","171":"124 bands"}',
                 'default_key': '81', 'price_rules': [{'option_key': '171', 'mode': 'multiplier', 'value': '1.0000'}]},
                {'id': 127, 'name': 'add_opt2', 'text': 'Overlap', 'options': '{"2":"50%","8":"87.5%"}',
                 'default_key': '2', 'price_rules': [{'option_key': '8', 'mode': 'multiplier', 'value': '2.0000'}]}]}
        self.catalog = {40: self.algorithm}
        self.paid = SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40, api_options={'add_opt1': '171', 'add_opt2': '2'})
        self.free = SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40, api_options={'add_opt1': '81', 'add_opt2': '2'})

    def test_multiplier_one_is_still_premium_and_normal_model_is_not(self):
        self.assertTrue(premium_option(self.algorithm['algorithm_fields'][0], '171'))
        self.assertTrue(premium_requirements(self.paid, self.catalog))
        self.assertFalse(premium_requirements(self.free, self.catalog))
        self.algorithm['orientation'] = 2
        self.assertTrue(premium_requirements(self.free, self.catalog))

    def test_hidden_paid_parameter_does_not_require_premium(self):
        self.algorithm['algorithm_fields'][1]['conditions'] = [
            {'depends_on_field_id': 25, 'operator': 'in', 'value_json': ['81'], 'action': 'hide'}]
        spec = SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40, api_options={'add_opt1': '81', 'add_opt2': '8'})
        self.assertFalse(premium_requirements(spec, self.catalog))

    def test_free_path_makes_no_account_request(self):
        client = Mock()
        check_access(self.free, self.catalog, client)
        self.assertEqual(client.method_calls, [])

    def test_negative_empty_disabled_and_unknown_access_block_before_upload(self):
        for access in (AccountAccess(True, -4), AccountAccess(True, 0), AccountAccess(False, 100),
                       AccountAccess(None, 100), AccountAccess(True, None)):
            client = Mock()
            client.get_account_access.return_value = access
            with self.subTest(access=access), self.assertRaisesRegex(InspectionError, 'Файл не отправлен'):
                check_access(self.paid, self.catalog, client)
            self.assertEqual([call[0] for call in client.method_calls], ['get_account_access'])

    def test_positive_access_allows_request_and_check_failure_is_explicit(self):
        client = Mock()
        client.get_account_access.return_value = AccountAccess(True, 100)
        check_access(self.paid, self.catalog, client)
        client.get_account_access.side_effect = NetworkError('offline')
        with self.assertRaisesRegex(InspectionError, 'Не удалось проверить'):
            check_access(self.paid, self.catalog, client)

    def test_runner_does_not_upload_or_reserve_variant_when_balance_is_negative(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'logs').mkdir()
            (root / 'logs/mvsep_algorithms.json').write_text(json.dumps([self.algorithm]), encoding='utf-8')
            source = root / 'new.mp3'
            source.write_bytes(b'local fake audio')
            job = Job.create(source, self.paid)
            client = Mock()
            client.redactor = Redactor(TOKEN)
            client.get_account_access.return_value = AccountAccess(True, -4)
            logger = logging.getLogger('test_access_silent')
            logger.handlers = [logging.NullHandler()]
            logger.propagate = False
            with self.assertRaisesRegex(InspectionError, 'Баланс кредитов в MVSEP: -4'):
                execute_job(root, job, MVSEPSettings(TOKEN, self.paid), logger, client)
            client.create_job.assert_not_called()
            self.assertFalse((root / 'config/source-runs.json').exists())
            saved = Job.load(job_path(root, job))
            self.assertIsNone(saved.run_index)
            self.assertIsNone(saved.remote_hash)
            self.assertIsNotNone(saved.processing_started_at)
            self.assertEqual(saved.status, JobStatus.FAILED)

    def test_account_api_reads_only_and_retains_no_account_identity_or_token(self):
        transport = Mock()
        transport.json.return_value = {'success': True, 'data': {'premium_enabled': 1,
            'premium_minutes': -4, 'email': 'private@example.test', 'api_token': TOKEN}}
        account = MVSEPClient(TOKEN, transport=transport).get_account_access()
        self.assertEqual(account, AccountAccess(True, -4))
        self.assertNotIn(TOKEN, repr(account))
        self.assertNotIn('private@example', repr(account))
        args = transport.json.call_args.args
        self.assertEqual(args[0], 'GET')
        self.assertIn('/app/user?', args[1])
        self.assertLessEqual(args[2], 10)

    def test_account_unknown_numbers_are_not_assumed_zero_or_available(self):
        transport = Mock()
        client = MVSEPClient(TOKEN, transport=transport)
        for value in (None, True, 'unknown', float('nan'), float('inf')):
            transport.json.return_value = {'success': True, 'data': {'premium_enabled': '1', 'premium_minutes': value}}
            self.assertIsNone(client.get_account_access().credits)

    def test_http_400_retains_root_validation_errors_and_redacts_tokens(self):
        payload = {'success': False, 'message': 'Validation failed',
                   'errors': {'add_opt1': ['Premium is required'], 'details': {'message': TOKEN}},
                   'data': {'email': 'private@example.test', 'api_token': TOKEN}}
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError('https://mvsep.com/api/separation/create', 400, 'bad', {},
            io.BytesIO(json.dumps(payload).encode()))
        with self.assertRaises(APIError) as error:
            HTTPTransport(Redactor(TOKEN), opener).json('GET', 'https://mvsep.com/api/test', 1)
        self.assertIn('Premium is required', str(error.exception))
        self.assertIn('Validation failed', str(error.exception))
        self.assertNotIn(TOKEN, str(error.exception))
        self.assertNotIn('private@example', str(error.exception))

    def test_success_false_nested_errors_are_also_visible(self):
        client = MVSEPClient(TOKEN)
        with self.assertRaisesRegex(APIError, 'Balance exhausted'):
            client._checked({'success': False, 'data': {'errors': ['Balance exhausted']}})
        self.assertEqual(error_details({'data': {'api_token': TOKEN, 'email': 'secret'}}, Redactor(TOKEN)), '')
        self.assertEqual(error_details('<html>private</html>', Redactor(TOKEN)), '')

    def test_elapsed_excludes_time_in_settings_and_old_jobs_keep_fallback(self):
        instant = datetime.now(timezone.utc)
        job = Job.create(Path('fake.mp3'), self.free)
        job.created_at = (instant - timedelta(minutes=5)).isoformat()
        job.processing_started_at = (instant - timedelta(seconds=6)).isoformat()
        job.status = JobStatus.FAILED
        job.updated_at = instant.isoformat()
        self.assertEqual(describe(job).elapsed, 'Прошло: 00:06')
        job.processing_started_at = None
        self.assertEqual(describe(job).elapsed, 'Прошло: 05:00')


if __name__ == '__main__':
    unittest.main()
