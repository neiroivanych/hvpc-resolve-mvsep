"""Contract/safety tests using read-only doubles, not a live Resolve session."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from resolve_mvsep.core.config import Settings, load_settings
from resolve_mvsep.core.errors import InspectionError
from resolve_mvsep.main import run
from resolve_mvsep.resolve.api import connect
from resolve_mvsep.resolve.media import inspect_source
from resolve_mvsep.resolve.timeline import choose_clip, inspect_clip, timeline_frame_rate


class Media:
    def __init__(self, path):
        self.path = path
        self.uid = 'media-1'

    def GetName(self):
        return "Исходник.wav"

    def GetUniqueId(self):
        return self.uid

    def GetClipProperty(self, key=None):
        props = {"File Path": self.path, "Type": "Audio", "Sample Rate": "48000",
                 "Proxy Media Path": "proxy-must-not-be-used.wav"}
        return props if key is None else props.get(key)


class Clip:
    def __init__(self, path):
        self.media = Media(path)
        self.uid, self.kind, self.linked = 'clip-1', 'audio', []

    def GetName(self):
        return "Диалог — дубль 1"

    def GetUniqueId(self):
        return self.uid

    def GetLinkedItems(self):
        return self.linked

    def GetMediaPoolItem(self):
        return self.media

    def GetTrackTypeAndIndex(self):
        return [self.kind, 1]

    def GetStart(self, subframePrecision=False):
        return 90100.5 if subframePrecision else 90100

    def GetEnd(self, subframePrecision=False):
        return 90350.5 if subframePrecision else 90350

    def GetDuration(self, subframePrecision=False):
        return 250

    def GetLeftOffset(self, subframePrecision=False):
        return 12.5 if subframePrecision else 12

    def GetSourceStartTime(self):
        return .5

    def GetSourceEndTime(self):
        return 10.5

    def GetRightOffset(self, subframePrecision=False):
        return 50


class Timeline:
    def __init__(self, clip):
        self.selection = [clip]
        self.items = [clip]
        self.current_video = None
        self.selection_reads = 0
        self.current_video_reads = 0

    def GetName(self):
        return "Таймлайн"

    def GetUniqueId(self):
        return "timeline-1"

    def GetSelectedClips(self):
        self.selection_reads += 1
        return self.selection

    def GetCurrentVideoItem(self):
        self.current_video_reads += 1
        return self.current_video

    def GetTrackCount(self, track_type):
        return 1

    def GetItemListInTrack(self, track_type, track_index):
        return self.items

    def GetTrackName(self, track_type, index):
        return "Dialogue"

    def GetStartFrame(self):
        return 90000

    def GetStartTimecode(self):
        return "01:00:00:00"

    def GetCurrentTimecode(self):
        return "01:00:00:00"  # Deliberately outside the selected clip.

    def GetSettings(self):
        return {"timelineFrameRate": "25"}

    def GetSetting(self, name):
        return "25"


class Project:
    def __init__(self, timeline):
        self.timeline = timeline

    def GetName(self):
        return "M1 Test"

    def GetUniqueId(self):
        return 'project-1'

    def GetCurrentTimeline(self):
        return self.timeline


class Manager:
    def __init__(self, project):
        self.project = project

    def GetCurrentProject(self):
        return self.project


class Resolve:
    def __init__(self, timeline):
        self.manager = Manager(Project(timeline))
        self.version = [21, 1, 0, 17, ""]

    def GetProjectManager(self):
        return self.manager

    def GetVersion(self):
        return self.version

    def GetVersionString(self):
        return ".".join(str(value) for value in self.version[:4])


class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "исходник с пробелами.wav"
        self.source.write_bytes(b"unchanged source bytes; M1 does not decode media")
        self.clip = Clip(str(self.source))
        self.timeline = Timeline(self.clip)
        self.app = Resolve(self.timeline)
        self.logger = logging.getLogger("test.resolve_mvsep")
        self.logger.addHandler(logging.NullHandler())
        self.addCleanup(self.logger.handlers.clear)
        dialog_patch = patch("resolve_mvsep.main.show_message", return_value=True)
        self.dialog = dialog_patch.start()
        self.addCleanup(dialog_patch.stop)

    def context(self):
        return connect(self.logger, self.app)

    def test_full_read_only_inspection_and_repeated_launch(self):
        before = hashlib.sha256(self.source.read_bytes()).digest()
        with redirect_stdout(io.StringIO()), patch.dict(os.environ, {"MVSEP_API_TOKEN": "secret-test-token"}):
            self.assertEqual(run(self.root, self.app), 0)
            self.assertEqual(run(self.root, self.app), 0)
        reports = list((self.root / "logs").glob("*.json"))
        self.assertEqual(len(reports), 2)
        report = json.loads(reports[0].read_text(encoding="utf-8"))
        self.assertEqual(report["clip"]["name"], "Диалог — дубль 1")
        self.assertEqual(report["clip"]["timeline_start"], 90100.5)
        self.assertEqual(report["clip"]["offset_from_timeline_start"], 100.5)
        self.assertEqual(report["clip"]["selection_method"], "selected")
        self.assertEqual(report["source"]["file_path"], str(self.source))
        self.assertTrue(report["source"]["file_exists"])
        self.assertEqual(report["status"], "inspected")
        self.assertEqual(self.dialog.call_count, 2)
        summary = self.dialog.call_args.args[0]
        self.assertIn(str(self.source), summary)
        self.assertIn("Диалог — дубль 1", summary)
        self.assertIn(".json", summary)
        self.assertEqual(self.timeline.current_video_reads, 0)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(), before)
        for path in (self.root / "logs").iterdir():
            self.assertNotIn("secret-test-token", path.read_text(encoding="utf-8"))

    def test_empty_selection_does_not_fall_back_to_current_video(self):
        self.timeline.selection = []
        self.timeline.current_video = self.clip
        with self.assertRaisesRegex(InspectionError, "No timeline clip selected"):
            choose_clip(self.context(), Settings(), self.logger)
        self.assertEqual(self.timeline.current_video_reads, 0)

    def test_multiple_selections_are_not_silently_collapsed(self):
        self.timeline.selection = [self.clip, self.clip]
        with self.assertRaisesRegex(InspectionError, "Выбрано клипов: 2"):
            choose_clip(self.context(), Settings(), self.logger)

    def test_linked_video_audio_selection_uses_audio_without_changing_selection(self):
        video = Clip(str(self.source)); video.uid, video.kind = 'video-1', 'video'
        video.linked, self.clip.linked = [self.clip], [video]
        for selection in ([video, self.clip], [self.clip, video]):
            self.timeline.selection = selection
            self.assertIs(choose_clip(self.context(), Settings(), self.logger), self.clip)
            self.assertEqual(self.timeline.selection, selection)
        (self.root / 'settings.json').write_text('{"action": "separate"}')
        with patch('resolve_mvsep.separation.launch_separation') as launch, redirect_stdout(io.StringIO()):
            self.assertEqual(run(self.root, self.app), 0)
        self.assertEqual(launch.call_args.args[1].clip.unique_id, self.clip.uid)
        self.assertEqual(launch.call_args.args[1].clip.track_type, 'audio')

    def test_unrelated_or_ambiguous_pairs_are_rejected(self):
        video = Clip(str(self.source)); video.uid, video.kind = 'video-1', 'video'
        self.timeline.selection = [video, self.clip]
        with self.assertRaises(InspectionError):
            choose_clip(self.context(), Settings(), self.logger)
        video.linked, self.clip.linked = [self.clip], [video]
        for attribute, value in (('uid', 'external-media'), ('path', str(self.root / 'external.mp4'))):
            old = getattr(video.media, attribute)
            setattr(video.media, attribute, value)
            with self.assertRaises(InspectionError):
                choose_clip(self.context(), Settings(), self.logger)
            setattr(video.media, attribute, old)
        self.timeline.selection.append(Clip(str(self.source)))
        with self.assertRaises(InspectionError):
            choose_clip(self.context(), Settings(), self.logger)

    def test_old_version_never_calls_new_selection_api(self):
        self.app.version = [20, 3, 2, 0, ""]
        with self.assertRaisesRegex(InspectionError, "21.0.4"):
            choose_clip(self.context(), Settings(), self.logger)
        self.assertEqual(self.timeline.selection_reads, 0)

    def test_first_supported_version_can_read_selection_and_legacy_settings(self):
        self.app.version = [21, 0, 4, 0, ""]
        self.assertIs(choose_clip(self.context(), Settings(), self.logger), self.clip)
        with patch.object(self.timeline, "GetSettings", side_effect=AssertionError("new API")):
            self.assertEqual(timeline_frame_rate(self.context()), "25")

    def test_explicit_video_fallback_and_gap(self):
        settings = Settings(selection_mode="current_video")
        with self.assertRaisesRegex(InspectionError, "No current video"):
            choose_clip(self.context(), settings, self.logger)
        self.timeline.current_video = self.clip
        self.assertIs(choose_clip(self.context(), settings, self.logger), self.clip)
        self.assertEqual(self.timeline.selection_reads, 0)

    def test_explicit_audio_track_fallback_validates_indices(self):
        self.assertIs(choose_clip(self.context(), Settings(selection_mode="track_item"), self.logger), self.clip)
        for setting in (Settings(selection_mode="track_item", track_index=2),
                        Settings(selection_mode="track_item", item_index=2)):
            with self.assertRaises(InspectionError):
                choose_clip(self.context(), setting, self.logger)

    def test_no_project_or_timeline(self):
        self.app.manager.project.timeline = None
        with self.assertRaisesRegex(InspectionError, "No current timeline"):
            self.context()
        self.app.manager.project = None
        with self.assertRaisesRegex(InspectionError, "No current project"):
            self.context()

    def test_resolve_not_running(self):
        with patch("resolve_mvsep.resolve.api._load_script_module") as module:
            module.return_value.scriptapp.return_value = None
            with self.assertRaisesRegex(InspectionError, "Resolve is not available"):
                connect(self.logger)

    def test_missing_or_non_file_media(self):
        self.clip.media.path = str(self.root / "missing.wav")
        source, warnings = inspect_source(self.clip)
        self.assertFalse(source.file_exists)
        self.assertTrue(warnings)
        self.clip.media.path = ""
        with self.assertRaisesRegex(InspectionError, "no File Path"):
            inspect_source(self.clip)
        self.clip.media = None
        with self.assertRaisesRegex(InspectionError, "no MediaPoolItem"):
            inspect_source(self.clip)

    def test_failure_has_traceback_without_success_report(self):
        self.timeline.selection = []
        with redirect_stdout(io.StringIO()):
            self.assertEqual(run(self.root, self.app), 1)
        logs = list((self.root / "logs").glob("*.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("Traceback", logs[0].read_text(encoding="utf-8"))
        self.assertFalse(list((self.root / "logs").glob("*.json")))
        self.dialog.assert_called_once()
        self.assertTrue(self.dialog.call_args.kwargs["error"])
        self.assertIn("No timeline clip selected", self.dialog.call_args.args[0])
        self.assertIn(str(logs[0]), self.dialog.call_args.args[0])

    def test_console_unavailable_still_shows_result_and_writes_report(self):
        with patch.object(sys, "stdout", None):
            self.assertEqual(run(self.root, self.app), 0)
        self.dialog.assert_called_once()
        self.assertEqual(len(list((self.root / "logs").glob("*.json"))), 1)

    def test_dialog_failure_keeps_successful_report(self):
        self.dialog.return_value = False
        with redirect_stdout(io.StringIO()):
            self.assertEqual(run(self.root, self.app), 0)
        self.assertEqual(len(list((self.root / "logs").glob("*.json"))), 1)
        log = next((self.root / "logs").glob("*.log")).read_text(encoding="utf-8")
        self.assertIn("Could not display the result dialog", log)

    def test_dialog_can_be_disabled(self):
        (self.root / "settings.json").write_text('{"show_result_dialog": false}', encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(run(self.root, self.app), 0)
        self.dialog.assert_not_called()

    def test_terminal_invocation_does_not_open_dialog(self):
        with patch("resolve_mvsep.main.connect", return_value=self.context()), redirect_stdout(io.StringIO()):
            self.assertEqual(run(self.root), 0)
        self.dialog.assert_not_called()

    def test_separate_action_uses_current_clip_snapshot_and_background_launcher(self):
        (self.root / "settings.json").write_text('{"action": "separate"}', encoding="utf-8")
        with patch("resolve_mvsep.separation.launch_separation", return_value=self.root / "job.json") as launch, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(run(self.root, self.app), 0)
        self.assertEqual(launch.call_args.args[1].source.file_path, str(self.source))
        self.assertEqual(launch.call_args.args[1].clip.timeline_start, 90100.5)
        self.assertTrue(launch.call_args.kwargs["notify"])
        self.dialog.assert_not_called()  # live window replaces the modal startup message

    def test_active_separation_reopens_even_without_a_selected_clip(self):
        (self.root / "settings.json").write_text('{"action": "separate"}', encoding="utf-8")
        with patch("resolve_mvsep.separation.reopen_active", return_value=self.root / "job.json"), \
                patch("resolve_mvsep.main.connect") as connect, redirect_stdout(io.StringIO()):
            self.assertEqual(run(self.root, self.app), 0)
            connect.assert_not_called()
        self.dialog.assert_not_called()

    def test_config_rejects_invalid_values_and_accepts_bom(self):
        self.assertEqual(load_settings(self.root), Settings())
        path = self.root / "settings.json"
        for invalid in ({"track_index": True}, {"item_index": 0}, {"track_type": "subtitle"},
                        {"selection_mode": "auto"}, {"show_result_dialog": "false"},
                        {"unknown": "secret"}, []):
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaises(InspectionError):
                load_settings(self.root)
        path.write_text('{"selection_mode": "track_item"}', encoding="utf-8-sig")
        self.assertEqual(load_settings(self.root).selection_mode, "track_item")

    def test_launcher_uses_injected_object_and_ignores_host_argv(self):
        with patch.dict(os.environ, {"RESOLVE_MVSEP_HOME": str(ROOT)}), \
                patch("resolve_mvsep.main.run", return_value=0) as entry, \
                patch.object(sys, "argv", ["Resolve.exe", "--unrelated-host-option"]):
            # Native menu execution need not use __name__ == '__main__'.
            runpy.run_path(str(ROOT / "scripts" / "HVPC Resolve-MVSEP.py"),
                           init_globals={"resolve": self.app}, run_name="resolve_menu")
            entry.assert_called_once_with(ROOT, self.app)


if __name__ == "__main__":
    unittest.main()
