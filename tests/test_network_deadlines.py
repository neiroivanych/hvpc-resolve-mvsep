"""Slow peers must not reset the total download/JSON budget for every byte."""
import io
import json
import http.client
import socket
import sys
import unittest
import time
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from test_mvsep import Clock, TOKEN
from resolve_mvsep.core.secrets import Redactor
from resolve_mvsep.mvsep.client import MVSEPClient
from resolve_mvsep.mvsep.downloads import download_file
from resolve_mvsep.mvsep.errors import APIError, DeadlineExceeded, NetworkError
from resolve_mvsep.mvsep.models import Mode, RemoteFile, SeparationSpec
from resolve_mvsep.mvsep.streaming import read_block
from resolve_mvsep.mvsep.transport import HTTPTransport


class Trickle(io.BytesIO):
    def __init__(self, clock, content=b'{"success": true}', delay=1):
        super().__init__(content)
        self.clock, self.delay = clock, delay
        self.socket = Mock()
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=self.socket))

    def read(self, size=-1):
        raise AssertionError('read(size) would wait for a whole block, defeating the deadline')

    def read1(self, size=-1):
        self.clock.sleep(self.delay)
        return super().read(min(1, size))


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.opener = Mock()
        self.transport = HTTPTransport(Redactor(TOKEN), self.opener, clock=self.clock)

    def test_json_trickle_expires_and_closes_response(self):
        response = Trickle(self.clock)
        self.opener.open.return_value = response
        with self.assertRaises(DeadlineExceeded):
            self.transport.json('GET', 'https://mvsep.com/api/test', 3)
        self.assertEqual(self.clock.value, 3)
        self.assertTrue(response.closed)
        self.assertEqual([call.args[0] for call in response.socket.settimeout.call_args_list], [3, 2, 1])

    def test_json_small_blocks_are_reassembled(self):
        self.opener.open.return_value = Trickle(self.clock, delay=.01)
        self.assertEqual(self.transport.json('GET', 'https://mvsep.com/api/test', 3), {'success': True})

    def test_connection_time_counts_toward_get_response_budget(self):
        response = Trickle(self.clock)
        def connect(*args, **kwargs):
            self.clock.sleep(2)
            return response
        self.opener.open.side_effect = connect
        with self.assertRaises(DeadlineExceeded):
            self.transport.json('GET', 'https://mvsep.com/api/test', 3)
        self.assertEqual(self.clock.value, 3)
        response.socket.settimeout.assert_called_once_with(1)

    def test_http_error_body_cannot_hang_or_hide_status(self):
        body = Trickle(self.clock, b'private error body')
        self.opener.open.side_effect = urllib.error.HTTPError(
            'https://mvsep.com/?api_token=' + TOKEN, 503, 'unavailable', {}, body)
        with self.assertRaises(APIError) as error:
            self.transport.json('GET', 'https://mvsep.com/api/test', 3)
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(self.clock.value, 3)
        self.assertTrue(body.closed)
        self.assertNotIn(TOKEN, str(error.exception))

    def test_expired_budget_does_not_read_or_touch_socket(self):
        response = Trickle(self.clock)
        with self.assertRaises(DeadlineExceeded):
            read_block(response, 65536, deadline=0, timeout=60, clock=self.clock)
        self.assertEqual(self.clock.value, 0)
        response.socket.settimeout.assert_not_called()

    def test_budget_limited_socket_timeout_before_clock_deadline_keeps_reason(self):
        response = Trickle(self.clock)
        def early_timeout(size):
            self.clock.sleep(.249)
            raise TimeoutError('timed out')
        response.read1 = early_timeout
        with self.assertRaisesRegex(DeadlineExceeded, 'Download time limit'):
            read_block(response, 65536, deadline=.25, timeout=5, clock=self.clock,
                       message='Download time limit')
        self.assertLess(self.clock.value, .25)
        response.socket.settimeout.assert_called_once_with(.25)

    def test_shorter_request_socket_timeout_is_not_a_total_budget_timeout(self):
        response = Trickle(self.clock)
        def early_timeout(size):
            self.clock.sleep(.249)
            raise TimeoutError('timed out')
        response.read1 = early_timeout
        with self.assertRaises(TimeoutError):
            read_block(response, 65536, deadline=5, timeout=.25, clock=self.clock)
        response.socket.settimeout.assert_called_once_with(.25)

    def test_post_response_timeout_never_retries_or_claims_a_saved_hash(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.wav'
            source.write_bytes(b'audio')
            transport = Mock()
            transport.json.side_effect = DeadlineExceeded('response time limit')
            client = MVSEPClient(TOKEN, transport=transport)
            with self.assertRaises(NetworkError) as error:
                client.create_job(source, SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40))
            transport.json.assert_called_once()
            self.assertIn('outcome is unknown', str(error.exception))
            self.assertIn('Check MVSEP history', str(error.exception))

    def test_real_http_response_stalling_mid_file_obeys_remaining_budget(self):
        import tempfile
        reader, peer = socket.socketpair()  # local only, no MVSEP or Internet
        with reader, peer, tempfile.TemporaryDirectory() as directory:
            reader.settimeout(5)
            peer.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nx')
            response = http.client.HTTPResponse(reader)
            response.begin()
            transport = Mock(redactor=Redactor(TOKEN))
            transport.open.return_value = response
            start = time.monotonic()
            with self.assertRaisesRegex(DeadlineExceeded, 'Download time limit'):
                download_file(transport, RemoteFile('vocals.wav', 'https://mvsep.com/a'),
                              Path(directory), 1, request_timeout=5, deadline=start + .25)
            self.assertLess(time.monotonic() - start, 3)
            self.assertTrue(response.closed)
            self.assertEqual(list(Path(directory).iterdir()), [])
