"""Byte counters, ETA lifecycle and the distinction between transfer and job completion."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from resolve_mvsep.core.job import Job, JobStatus
from resolve_mvsep.core.progress import describe
from resolve_mvsep.core.secrets import Redactor
from resolve_mvsep.core.transfer import TransferReporter
from resolve_mvsep.mvsep.client import MVSEPClient
from resolve_mvsep.mvsep.errors import NetworkError
from resolve_mvsep.mvsep.models import Mode, RemoteFile, RemoteResult, SeparationSpec, TransferProgress
from resolve_mvsep.mvsep.transport import MultipartBody


class Response(io.BytesIO):
    def __init__(self, content, headers):
        super().__init__(content)
        self.headers = headers


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "audio.wav"
        self.source.write_bytes(b"a" * (600 * 1024))
        self.job = Job.create(self.source, SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40))
        self.path = self.root / "jobs" / self.job.id / "job.json"
        self.tick = 0
        self.clock = lambda: self.tick

    def test_upload_reports_consumed_audio_not_headers_or_read_ahead(self):
        events = []
        body = MultipartBody({"api_token": "fake-secret"}, self.source,
                             lambda sent, total: events.append((sent, total)))
        iterator = iter(body)
        prefix = next(iterator)
        self.assertEqual(events, [(0, 600 * 1024)])
        first = next(iterator)
        self.assertEqual(len(first), 256 * 1024)
        self.assertEqual(events[-1][0], 0)  # yielded for sending, not yet consumed
        second = next(iterator)
        self.assertEqual(events[-1][0], len(first))
        rest = list(iterator)
        self.assertEqual(events[-1], (600 * 1024, 600 * 1024))
        self.assertEqual(sum(map(len, [prefix, first, second, *rest])), body.content_length)
        count = len(events)
        self.assertEqual(len(b"".join(body)), body.content_length)
        self.assertEqual(events[count][0], 0)  # redirect starts a new transfer attempt
        self.assertEqual(events[-1][0], 600 * 1024)  # never counts two passes together

    def test_create_job_forwards_upload_events_without_changing_payload(self):
        events = []
        transport = Mock()
        def request(method, url, timeout, body):
            self.assertEqual(method, "POST")
            self.assertEqual(len(b"".join(body)), body.content_length)
            return {"success": True, "data": {"hash": "hash-1"}}
        transport.json.side_effect = request
        client = MVSEPClient("fake-secret", transport=transport)
        self.assertEqual(client.create_job(self.source, self.job.specification(), on_progress=events.append), "hash-1")
        self.assertEqual(events[0], TransferProgress("upload", 0, 600 * 1024))
        self.assertEqual(events[-1], TransferProgress("upload", 600 * 1024, 600 * 1024))

    def test_source_growing_during_upload_never_exceeds_announced_length(self):
        body = MultipartBody({}, self.source)
        chunks = iter(body)
        prefix, first = next(chunks), next(chunks)
        with self.source.open('ab') as stream:
            stream.write(b'changed source')
        sent = len(prefix) + len(first)
        with self.assertRaises(NetworkError):
            for chunk in chunks:
                sent += len(chunk)
        self.assertLess(sent, body.content_length)  # no suffix/extra bytes or successful request

    def test_source_truncated_before_upload_fails_without_emitting_body(self):
        body = MultipartBody({}, self.source)
        self.source.write_bytes(b'shorter')
        with self.assertRaises(NetworkError):
            next(iter(body))

    def test_download_reports_each_file_and_unknown_length_without_fake_total(self):
        events = []
        transport = Mock()
        transport.redactor = Redactor("fake-secret")
        content = b"RIFF" + b"a" * 300000
        transport.open.side_effect = [Response(content, {"Content-Length": str(len(content))}),
                                      Response(b"stem-two", {"Content-Type": "audio/wav"})]
        result = RemoteResult("done", (RemoteFile("a.wav", "https://mvsep.com/a"),
                                       RemoteFile("b.wav", "https://mvsep.com/b")))
        client = MVSEPClient("fake-secret", transport=transport)
        files = client.download_results(result, self.root / "out", timeout=30, on_progress=events.append)
        first = [event for event in events if event.file_index == 1]
        second = [event for event in events if event.file_index == 2]
        self.assertEqual(first[-1], TransferProgress("download", len(content), len(content), 1, 2))
        self.assertEqual(second[0].transferred, 0)
        self.assertIsNone(second[-1].total)
        self.assertEqual(second[-1].transferred, len(b"stem-two"))
        self.assertEqual(files[0].read_bytes(), content)

    def test_failed_download_resets_counters_before_retry_and_never_adds_attempts(self):
        events = []
        transport = Mock()
        transport.redactor = Redactor("fake-secret")
        transport.open.side_effect = [Response(b"short", {"Content-Length": "10"}),
                                      Response(b"0123456789", {"Content-Length": "10"})]
        client = MVSEPClient("fake-secret", transport=transport, sleep=lambda seconds: None)
        result = RemoteResult("done", (RemoteFile("a.wav", "https://mvsep.com/a"),))
        files = client.download_results(result, self.root / "out", timeout=30, on_progress=events.append)
        self.assertEqual([e.transferred for e in events], [0, 0, 5, 0, 0, 10])
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), b"0123456789")

    def test_eta_requires_samples_and_resets_for_retry_or_next_file(self):
        reporter = TransferReporter(self.job, self.path, clock=self.clock)
        reporter(TransferProgress("upload", 0, 1000))
        self.assertIsNone(self.job.transfer_eta)
        self.tick = 4
        reporter(TransferProgress("upload", 250, 1000))
        saved = Job.load(self.path)
        self.assertEqual(saved.transfer_rate, 62.5)
        self.assertEqual(saved.transfer_eta, 12)
        self.tick = 5
        reporter(TransferProgress("upload", 0, 1000))
        self.assertIsNone(self.job.transfer_rate)
        self.assertIsNone(self.job.transfer_eta)
        self.tick = 9
        reporter(TransferProgress("upload", 1000, 1000))
        self.assertIsNone(self.job.transfer_eta)  # finished bytes, server response pending
        reporter(TransferProgress("download", 0, None, 2, 2))
        self.assertEqual(self.job.transfer_bytes, 0)
        self.assertIsNone(self.job.transfer_rate)

    def test_progress_writes_are_throttled_but_final_byte_count_is_saved(self):
        self.job.save = Mock()
        reporter = TransferReporter(self.job, self.path, clock=self.clock)
        reporter(TransferProgress("upload", 0, 1000))
        for i in range(1, 5):
            self.tick = i / 10
            reporter(TransferProgress("upload", i * 100, 1000))
        self.assertEqual(self.job.save.call_count, 1)
        self.tick = 0.45
        reporter(TransferProgress("upload", 1000, 1000))
        self.assertEqual(self.job.save.call_count, 2)
        self.assertEqual(self.job.transfer_bytes, 1000)

    def upload_state(self):
        instant = datetime.now(timezone.utc)
        self.job.status = JobStatus.UPLOADING
        self.job.transfer_direction = "upload"
        self.job.transfer_bytes, self.job.transfer_total = 250000, 1000000
        self.job.transfer_eta, self.job.transfer_rate = 12, 62500
        self.job.transfer_updated_at = instant.isoformat()
        return instant

    def test_view_shows_transfer_percentage_eta_and_stale_estimate_disappears(self):
        instant = self.upload_state()
        view = describe(self.job, instant=instant)
        self.assertEqual(view.percent, 25)
        self.assertIn("25%", view.progress_label)
        self.assertIn("≈ 00:12", view.remaining)
        later = describe(self.job, instant=instant + timedelta(seconds=11))
        self.assertEqual(later.percent, 25)  # last measured bytes remain useful
        self.assertNotIn("00:12", later.remaining)
        self.assertIn("Нет новых данных", later.remaining)

    def test_full_upload_means_waiting_for_response_not_task_done_or_zero_eta(self):
        instant = self.upload_state()
        self.job.transfer_bytes = 1000000
        view = describe(self.job, instant=instant)
        self.assertEqual(view.percent, 100)
        self.assertFalse(view.completed)
        self.assertIn("ждём ответ", view.title)
        self.assertIn("Ожидаем подтверждения", view.remaining)
        self.assertNotIn("00:00", view.remaining)
        self.job.status = JobStatus.QUEUED
        self.assertIsNone(describe(self.job, instant=instant).percent)

    def test_no_premature_rounded_100_percent_or_percentage_for_unknown_size(self):
        instant = self.upload_state()
        self.job.transfer_bytes = 999999
        self.assertIn("99%", describe(self.job, instant=instant).progress_label)
        self.job.transfer_total = None
        self.job.transfer_eta = None
        view = describe(self.job, instant=instant)
        self.assertIsNone(view.percent)
        self.assertIn("размер неизвестен", view.progress_label)
        self.assertNotIn("%", view.progress_label)

    def test_chunk_percentage_is_only_for_valid_distributed_processing(self):
        self.job.status = JobStatus.PROCESSING
        self.job.remote_status = "distributing"
        self.job.finished_chunks, self.job.all_chunks = 3, 4
        view = describe(self.job)
        self.assertEqual(view.percent, 75)
        self.assertIn("Готово частей", view.progress_label)
        self.assertIn("скачивание ещё впереди", view.remaining)
        self.job.remote_status = "merging"
        self.assertIsNone(describe(self.job).percent)
        self.job.remote_status = "distributing"
        self.job.finished_chunks = 5
        self.assertIsNone(describe(self.job).percent)


if __name__ == "__main__":
    unittest.main()
