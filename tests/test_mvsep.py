"""API contract, network failure and filesystem safety without paid requests."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from resolve_mvsep.core.secrets import Redactor
from resolve_mvsep.mvsep.client import MVSEPClient
from resolve_mvsep.mvsep.errors import APIError, DeadlineExceeded, MVSEPError, NetworkError
from resolve_mvsep.mvsep.models import Mode, RemoteFile, RemoteResult, SeparationSpec
from resolve_mvsep.mvsep.transport import HTTPTransport, MultipartBody, SafeRedirects, validate_url

TOKEN = "test/secret+token"


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class Response(io.BytesIO):
    def __init__(self, content=b"RIFF-test-stem", headers=None):
        super().__init__(content)
        self.headers = headers or {"Content-Length": str(len(content)), "Content-Type": "audio/wav"}


class Transport:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.redactor = Redactor(TOKEN)
        self.streams = []

    def json(self, *args):
        self.calls.append(args)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    def open(self, *args):
        self.calls.append(args)
        return self.streams.pop(0)


def status(value, files=None):
    data = {"message": value, "current_order": 3}
    if files is not None:
        data["files"] = files
    return {"success": True, "status": value, "data": data}


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "музыка.wav"
        self.source.write_bytes(b"source-bytes-untouched")
        self.clock = Clock()
        self.spec = SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40)

    def client(self, responses, **kwargs):
        self.transport = Transport(responses)
        return MVSEPClient(TOKEN, transport=self.transport, clock=self.clock, sleep=self.clock.sleep, **kwargs)

    def test_mp4_upload_preserves_video_bytes_and_mime_type(self):
        video = self.root / 'видео со звуком.MP4'
        original = b'\x00\x00\x00\x18ftypmp42\x00\xffembedded-audio-video'
        video.write_bytes(original)
        client = self.client([{'success': True, 'data': {'hash': 'mp4-task'}}])
        self.assertEqual(client.create_job(video, self.spec), 'mp4-task')
        payload = self.transport.calls[0][3]
        body = b''.join(payload)
        self.assertIn(b'filename="source.mp4"', body)
        self.assertIn(b'Content-Type: video/mp4', body)
        self.assertIn(original, body)
        self.assertEqual(len(body), payload.content_length)
        self.assertEqual(video.read_bytes(), original)

    def test_create_streams_documented_multipart_and_never_publishes_demo(self):
        client = self.client([{"success": True, "data": {"hash": "remote-1"}}])
        self.assertEqual(client.create_job(self.source, self.spec), "remote-1")
        method, url, timeout, body = self.transport.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/api/separation/create"))
        content = b"".join(body)
        self.assertEqual(len(content), body.content_length)
        self.assertEqual(content, b"".join(body))  # can replay a 307/308 redirect
        for value in (b'name="audiofile"', b'name="sep_type"\r\n\r\n40',
                      b'name="output_format"\r\n\r\n1', b'name="is_demo"\r\n\r\n0', TOKEN.encode()):
            self.assertIn(value, content)
        self.assertEqual(self.source.read_bytes(), b"source-bytes-untouched")

    def test_preset_does_not_send_ignored_algorithm_options_or_format(self):
        spec = SeparationSpec(Mode.SPEECH_MUSIC_SFX, None, preset_id=123)
        fields = spec.form_fields()
        self.assertEqual(fields, {"is_demo": "0", "preset_id": "123"})
        for kwargs in ({"sep_type": 40, "preset_id": 123},
                       {"sep_type": None, "preset_id": 123, "api_options": {"add_opt1": "0"}}):
            with self.assertRaises(ValueError):
                SeparationSpec(Mode.VOCALS_INSTRUMENTAL, **kwargs)

    def test_options_cannot_override_token_publication_or_file(self):
        for name in ("api_token", "is_demo", "audiofile", "url", "webhook_url"):
            with self.assertRaises(ValueError):
                SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40, api_options={name: "value"})

    def test_upload_transport_failure_is_not_retried(self):
        client = self.client([NetworkError("lost connection")])
        with self.assertRaisesRegex(NetworkError, "outcome is unknown"):
            client.create_job(self.source, self.spec)
        self.assertEqual(len(self.transport.calls), 1)

    def test_upload_server_error_is_not_retried(self):
        client = self.client([APIError("server", status_code=503)])
        with self.assertRaises(APIError):
            client.create_job(self.source, self.spec)
        self.assertEqual(len(self.transport.calls), 1)

    def test_queue_processing_distributing_merging_and_done(self):
        outputs = [{"download": "vocals.wav", "url": "https://de2.mvsep.com/results/voice.wav"}]
        client = self.client([status("waiting"), status("distributing"), status("processing"),
                              status("merging"), status("done", outputs)], poll_interval=2)
        observed = []
        result = client.wait_for_result("remote-1", timeout=20, on_status=lambda x: observed.append(x.status))
        self.assertEqual(observed, ["waiting", "distributing", "processing", "merging", "done"])
        self.assertEqual(result.files[0].filename, "vocals.wav")
        self.assertTrue(all("mirror=0" in call[1] for call in self.transport.calls))

    def test_polling_deadline_is_bounded(self):
        client = self.client([status("waiting")] * 20, poll_interval=2)
        with self.assertRaises(DeadlineExceeded):
            client.wait_for_result("remote-1", timeout=5)
        self.assertEqual(self.clock.value, 5)
        self.assertEqual(len(self.transport.calls), 3)

    def test_read_retries_transient_errors_and_honors_numeric_retry_after(self):
        client = self.client([APIError("busy", status_code=429, retry_after=3),
                              NetworkError("temporary"), status("processing")])
        result = client.get_result("remote-1", deadline=30)
        self.assertEqual(result.status, "processing")
        self.assertEqual(self.clock.value, 5)

    def test_authentication_errors_are_not_retried(self):
        client = self.client([APIError("invalid token", status_code=401)])
        with self.assertRaises(APIError):
            client.get_result("remote-1", deadline=30)
        self.assertEqual(len(self.transport.calls), 1)

    def test_failed_malformed_and_missing_output_responses(self):
        responses = [status("failed"), status("not_found"), status("cancelled"), status("error"), status("done", []),
                     status("done", [{"name": "wrong-key.wav", "url": "https://mvsep.com/file"}]),
                     {'success': True, 'status': 'converting', 'data': ['invalid fields']},
                     *[{'success': True, 'status': value} for value in (None, '', [], 5, '\nprocessing')],
                     {"success": False, "data": {"message": TOKEN}}]
        for response in responses:
            with self.subTest(response=response):
                client = self.client([response])
                with self.assertRaises(MVSEPError) as error:
                    client.get_result("remote-1", deadline=30)
                self.assertNotIn(TOKEN, str(error.exception))

    def test_video_preparation_and_new_phases_continue_until_valid_done(self):
        outputs = [{'download': 'voice.wav', 'url': 'https://mvsep.com/voice.wav'}]
        responses = [{'success': True, 'status': 'converting', 'data': []},
                     {'success': True, 'status': 'new_preparation_stage', 'data': None},
                     status('waiting'), status('processing'), status('done', outputs)]
        client = self.client(responses, poll_interval=2)
        observed = []
        result = client.wait_for_result('same-remote-task', timeout=20, on_status=observed.append)
        self.assertEqual([item.status for item in observed],
                         ['converting', 'new_preparation_stage', 'waiting', 'processing', 'done'])
        self.assertTrue(all(not item.files for item in observed[:-1]))
        self.assertEqual(result.files[0].filename, 'voice.wav')
        self.assertTrue(all(call[0] == 'GET' for call in self.transport.calls))

    def test_unknown_phase_remains_bounded_by_original_deadline(self):
        client = self.client([status('new_preparation_stage')] * 8, poll_interval=2)
        with self.assertRaises(DeadlineExceeded):
            client.wait_for_result('same-remote-task', timeout=5)
        self.assertEqual(self.clock.value, 5)
        self.assertEqual(len(self.transport.calls), 3)

    def test_new_status_diagnostics_redact_credentials_before_normalization(self):
        client = self.client([status('Stage' + TOKEN)])
        result = client.get_result('same-remote-task', deadline=30)
        self.assertNotIn(TOKEN, result.status)
        self.assertNotIn(TOKEN.lower(), result.status)

    def test_download_uses_absolute_regional_url_without_token_or_overwrite(self):
        client = self.client([])
        output = self.root / "output"
        output.mkdir()
        existing = output / "01_vocals.wav"
        existing.write_bytes(b"keep")
        self.transport.streams = [Response()]
        result = RemoteResult("done", (RemoteFile("vocals.wav", "https://sg.mvsep.com/data/stem.wav"),))
        paths = client.download_results(result, output, timeout=30)
        self.assertEqual(existing.read_bytes(), b"keep")
        self.assertEqual(paths[0].read_bytes(), b"RIFF-test-stem")
        self.assertEqual(self.transport.calls[0][1], "https://sg.mvsep.com/data/stem.wav")
        self.assertNotIn(TOKEN, str(self.transport.calls))

    def test_download_filename_cannot_escape_directory(self):
        client = self.client([])
        self.transport.streams = [Response(), Response()]
        result = RemoteResult("done", (RemoteFile("../../outside.wav", "https://mvsep.com/a"),
                                       RemoteFile(r"C:\CON:bad?.wav", "https://mvsep.com/b")))
        output = self.root / "output"
        for path in client.download_results(result, output, timeout=30):
            self.assertEqual(path.parent, output)
            self.assertTrue(path.is_file())
        self.assertFalse((self.root / "outside.wav").exists())

    def test_source_named_outputs_do_not_overwrite_existing_stems(self):
        client = self.client([])
        output = self.root / "output"
        output.mkdir()
        existing = output / "Песня_vocals.wav"
        existing.write_bytes(b"previous-result")
        self.transport.streams = [Response(), Response()]
        result = RemoteResult("done", (
            RemoteFile("source_bs_roformer_mt_81_vocals.wav", "https://mvsep.com/a"),
            RemoteFile("source_bs_roformer_mt_81_other.wav", "https://mvsep.com/b")))
        paths = client.download_results(result, output, timeout=30, source_stem="Песня")
        self.assertEqual([path.name for path in paths], ["Песня_vocals_1.wav", "Песня_other.wav"])
        self.assertEqual(existing.read_bytes(), b"previous-result")
        self.assertEqual(self.source.read_bytes(), b"source-bytes-untouched")

    def test_partial_download_is_removed_and_never_reported_as_success(self):
        client = self.client([], max_get_retries=0)
        self.transport.streams = [Response(b"short", {"Content-Length": "100"})]
        result = RemoteResult("done", (RemoteFile("vocals.wav", "https://mvsep.com/a"),))
        output = self.root / "output"
        with self.assertRaises(NetworkError):
            client.download_results(result, output, timeout=30)
        self.assertEqual(list(output.iterdir()), [])

    def test_html_download_is_not_saved_as_audio(self):
        client = self.client([])
        self.transport.streams = [Response(b"<html>error</html>", {"Content-Type": "text/html"})]
        result = RemoteResult("done", (RemoteFile("vocals.wav", "https://mvsep.com/a"),))
        with self.assertRaises(MVSEPError):
            client.download_results(result, self.root / "output", timeout=30)
        self.assertEqual(list((self.root / "output").iterdir()), [])


class TransportTests(unittest.TestCase):
    def test_untrusted_downloads_and_redirects_are_rejected(self):
        for url in ("file:///C:/secret", "http://mvsep.com/a", "https://mvsep.com.evil.test/a",
                    "https://evil.test/?mvsep.com", "https://name:password@mvsep.com/a"):
            with self.assertRaises(MVSEPError):
                validate_url(url)
        request = urllib.request.Request("https://mvsep.com/api/separation/get?api_token=" + TOKEN)
        with self.assertRaises(MVSEPError):
            SafeRedirects().redirect_request(request, None, 302, "redirect", {}, "https://evil.test")

    def test_transport_sanitizes_http_body_and_exception_url(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError(
            "https://mvsep.com/?api_token=" + TOKEN, 401, TOKEN, {},
            io.BytesIO(json.dumps({"data": {"message": TOKEN}}).encode()))
        transport = HTTPTransport(Redactor(TOKEN), opener)
        with self.assertRaises(APIError) as error:
            transport.json("GET", "https://mvsep.com/api/test", 1)
        self.assertNotIn(TOKEN, str(error.exception))
        self.assertEqual(error.exception.status_code, 401)
        self.assertTrue(error.exception.__suppress_context__)

    def test_invalid_json_hides_response_body(self):
        opener = Mock()
        opener.open.return_value = Response(("not json " + TOKEN).encode())
        with self.assertRaises(MVSEPError) as error:
            HTTPTransport(Redactor(TOKEN), opener).json("GET", "https://mvsep.com/api/test", 1)
        self.assertNotIn(TOKEN, str(error.exception))


if __name__ == "__main__":
    unittest.main()
