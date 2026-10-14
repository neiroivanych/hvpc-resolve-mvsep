"""Import contracts: placement, isolation, retries and failures without Resolve."""
import hashlib
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from resolve_mvsep.core.errors import InspectionError
from resolve_mvsep.core.i18n import using_language
from resolve_mvsep.core.job import Job, JobStatus
from resolve_mvsep.core.resolve_import import (latest_completed, read_receipt, import_message,
                                             track_range, track_details, timeline_confirmed)
from resolve_mvsep.mvsep.models import Mode, SeparationSpec
from resolve_mvsep.resolve.importer import import_results, path_key
from resolve_mvsep.resolve.timeline import inspect_clip


class Media:
    def __init__(self, path, channels='2', fps=24):
        self.path, self.channels, self.fps = str(path), channels, fps
        self.full_duration = 12.0
    def GetUniqueId(self): return self.path
    def GetClipProperty(self, key=None):
        props = {'File Path':self.path, 'Type':'Audio', 'Audio Ch':self.channels, 'FPS':self.fps}
        return props if key is None else props.get(key, '')


class Item:
    def __init__(self, media, start=86421.5, source_start=1.25, seconds=4.5, track=1, uid='source-item'):
        self.media, self.start, self.source_start, self.seconds = media, start, source_start, seconds
        self.track, self.uid, self.speed, self.kind = track, uid, 100, 'audio'
    def GetUniqueId(self): return self.uid
    def GetName(self): return 'source'
    def GetTrackTypeAndIndex(self): return [self.kind,self.track]
    def GetStart(self, precise=False): return self.start
    def GetEnd(self, precise=False): return self.start + self.GetDuration()
    def GetDuration(self, precise=False): return self.seconds * 24
    def GetLeftOffset(self, precise=False): return self.source_start * 24
    def GetRightOffset(self, precise=False): return (self.media.full_duration - self.GetSourceEndTime()) * 24
    def GetMediaPoolItem(self): return self.media
    def GetSourceStartTime(self): return self.source_start
    def GetSourceEndTime(self): return self.source_start + self.seconds
    def GetSpeed(self): return {'Percentage':self.speed}


class Folder:
    def __init__(self, name): self.name, self.clips, self.folders = name, [], []
    def GetName(self): return self.name
    def GetClipList(self): return self.clips
    def GetSubFolderList(self): return self.folders


class Timeline:
    def __init__(self, source):
        self.uid, self.tracks, self.names, self.enabled = 'timeline', [[source]], ['Original'], [True]
        self.video_tracks = []
    def GetUniqueId(self): return self.uid
    def GetName(self): return 'Timeline'
    def GetTrackCount(self, kind): return len(self.video_tracks if kind == 'video' else self.tracks)
    def GetItemListInTrack(self, kind, n): return (self.video_tracks if kind == 'video' else self.tracks)[n-1]
    def GetTrackName(self, kind, n): return self.names[n-1]
    def GetStartFrame(self): return 86400
    def GetSettings(self): return {'timelineFrameRate':24}
    def AddTrack(self, kind, subtype):
        self.tracks.append([]); self.names.append(''); self.enabled.append(True)
        return True
    def SetTrackName(self, kind, n, name): self.names[n-1] = name; return True
    def SetTrackEnable(self, kind, n, enabled): self.enabled[n-1] = enabled; return True


class Pool:
    def __init__(self, timeline, source):
        self.root = Folder('Master'); self.folder = self.root; self.root.clips.append(source)
        self.timeline, self.imports, self.appends = timeline, 0, 0
        self.fail_append, self.lost_reply, self.wrong_duration = None, False, False
        self.legacy_only, self.source_fps = False, 24
        self.ghost_reply, self.remove_first = False, False
        self.import_infos, self.append_infos = [], []
    def GetRootFolder(self): return self.root
    def GetCurrentFolder(self): return self.folder
    def SetCurrentFolder(self, folder): self.folder = folder; return True
    def AddSubFolder(self, parent, name):
        folder = Folder(name); parent.folders.append(folder); return folder
    def ImportMedia(self, infos):
        self.import_infos.extend(infos)
        if self.legacy_only and isinstance(infos[0], dict): return None
        result = [Media(info['FilePath'] if isinstance(info, dict) else info, fps=self.source_fps) for info in infos]
        self.folder.clips.extend(result); self.imports += len(result); return result
    def AppendToTimeline(self, infos):
        self.append_infos.extend(infos)
        self.appends += 1
        if self.appends == self.fail_append: return None
        result = []
        for info in infos:
            media = info['mediaPoolItem']; fps = media.fps
            item = Item(media, info['recordFrame'], info['startFrame']/fps,
                        (info['endFrame'] - info['startFrame'])/fps, info['trackIndex'], f'added-{self.appends}')
            if self.wrong_duration: item.seconds -= 1/24
            if not self.ghost_reply:
                self.timeline.tracks[info['trackIndex']-1].append(item)
            result.append(item)
        if self.remove_first and self.appends == 2:
            self.timeline.tracks[1].clear()
        return None if self.lost_reply else result


class Project:
    def __init__(self, timeline, pool): self.timeline, self.pool, self.uid = timeline, pool, 'project'
    def GetName(self): return 'Project'
    def GetUniqueId(self): return self.uid
    def GetCurrentTimeline(self): return self.timeline
    def GetTimelineCount(self): return 1
    def GetTimelineByIndex(self, i): return self.timeline
    def GetMediaPool(self): return self.pool


class App:
    def __init__(self, project): self.project = project
    def GetVersion(self): return [21,1,0]
    def GetVersionString(self): return '21.1.0'
    def GetProjectManager(self): return self
    def GetCurrentProject(self): return self.project


class ResolveImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source.wav'; self.source.write_bytes(b'original source')
        self.files = [self.root/'source_vocals_001.wav', self.root/'source_other_001.wav']
        for file in self.files: file.write_bytes(b'fake decoded by Resolve double')
        media = Media(self.source); self.clip = Item(media)
        self.timeline = Timeline(self.clip); self.pool = Pool(self.timeline, media)
        self.project = Project(self.timeline, self.pool); self.app = App(self.project)
        report = {'project_name':'Project', 'project_id':'project', 'timeline_id':'timeline', 'timeline_frame_rate':'24',
                  'clip':asdict(inspect_clip(self.clip, self.timeline, 'selected', source_times=True)),
                  'source':{'media_pool_id':media.GetUniqueId()}}
        self.job = Job.create(self.source, SeparationSpec(Mode.VOCALS_INSTRUMENTAL, 40), report)
        self.job.status = JobStatus.COMPLETED; self.job.result_files = [str(file) for file in self.files]
        self.job.source_sha256 = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.path = self.root/'jobs'/self.job.id/'job.json'; self.job.save(self.path)

    def run_import(self, target='timeline'):
        before = self.path.read_bytes()
        try: return import_results(self.root, self.path, target, application=self.app)
        finally: self.assertEqual(self.path.read_bytes(), before)

    def test_media_pool_then_tracks_and_retry_preserve_source(self):
        before = asdict(inspect_clip(self.clip,self.timeline,'selected',source_times=True))
        receipt = self.run_import('media_pool')
        self.assertEqual(len(receipt['media_items']),2)
        self.assertEqual(len(self.timeline.tracks),1)
        self.assertIs(self.pool.folder,self.pool.root)
        self.run_import(); self.run_import(); self.run_import('media_pool')
        self.assertEqual((self.pool.imports,self.pool.appends),(2,2))
        self.assertEqual(self.timeline.enabled,[True,False,False])
        for track in self.timeline.tracks[1:]:
            self.assertEqual(len(track),1)
            self.assertEqual((track[0].start,track[0].seconds,track[0].source_start),(86421.5,4.5,1.25))
        self.assertEqual(asdict(inspect_clip(self.clip,self.timeline,'selected',source_times=True)),before)

    def test_source_fps_is_converted_through_seconds(self):
        self.pool.source_fps = 25
        self.run_import()
        self.assertEqual(self.timeline.tracks[1][0].source_start,1.25)
        self.assertEqual(self.timeline.tracks[1][0].seconds,4.5)

    def assert_current_trim_import(self, left, right, move=0):
        """An edge edit advances source in and record in together, at 100%."""
        self.clip.source_start += left
        self.clip.start += left * 24 + move
        self.clip.seconds -= left + right
        before = asdict(inspect_clip(self.clip, self.timeline, 'selected', source_times=True))
        file_bytes = {file: file.read_bytes() for file in [self.source, *self.files]}
        self.run_import(); self.run_import()
        self.assertEqual((self.pool.imports, self.pool.appends), (2, 2))
        # Import the complete files, without limiting Media Pool ranges.
        self.assertEqual(self.pool.import_infos, [{'FilePath': str(file.resolve())} for file in self.files])
        for track, spec in zip(self.timeline.tracks[1:], self.pool.append_infos):
            item = track[0]
            self.assertIs(item.GetMediaPoolItem(), spec['mediaPoolItem'])
            self.assertEqual(item.GetStart(True), self.clip.GetStart(True))
            self.assertAlmostEqual(item.GetEnd(True), self.clip.GetEnd(True))
            self.assertAlmostEqual(item.GetSourceStartTime(), self.clip.GetSourceStartTime())
            self.assertAlmostEqual(item.GetSourceEndTime(), self.clip.GetSourceEndTime())
            self.assertGreater(item.GetLeftOffset(True), 0)
            self.assertGreater(item.GetRightOffset(True), 0)
            self.assertAlmostEqual(item.GetLeftOffset(True) + item.GetDuration(True)
                                   + item.GetRightOffset(True), item.media.full_duration * 24)
        self.assertEqual(asdict(inspect_clip(self.clip, self.timeline, 'selected', source_times=True)), before)
        self.assertEqual({file: file.read_bytes() for file in file_bytes}, file_bytes)

    def test_left_trim_after_separation_keeps_full_result_media_and_handles(self):
        self.assert_current_trim_import(1.5, 0)

    def test_right_trim_after_separation_keeps_full_result_media_and_handles(self):
        self.assert_current_trim_import(0, .75)

    def test_both_edge_trims_after_separation_with_different_media_fps(self):
        self.pool.source_fps = 25
        self.assert_current_trim_import(.375, .75)

    def test_extended_edges_after_separation_keep_current_range(self):
        self.assert_current_trim_import(-.5, -2)

    def test_moved_source_uses_current_position_without_shortening_media(self):
        self.assert_current_trim_import(0, 0, move=250.5)

    def test_moved_source_with_both_edge_trims_uses_current_position_and_range(self):
        self.assert_current_trim_import(.5, .75, move=181.25)

    def test_source_moved_to_another_audio_track_is_found_by_id(self):
        self.timeline.AddTrack('audio', 'stereo')
        self.timeline.tracks[0].clear()
        self.timeline.tracks[1].append(self.clip)
        self.clip.track = 2
        self.clip.start += 120
        receipt = self.run_import()
        self.assertEqual(track_range(receipt), 'A3–A4')
        for track in self.timeline.tracks[2:]:
            self.assertEqual(track[0].start, self.clip.start)

    def test_mp4_video_anchor_trim_after_separation(self):
        video = self.root / 'source.mp4'; video.write_bytes(b'original source')
        self.clip.media.path = str(video)
        self.clip.kind = 'video'
        self.timeline.video_tracks = [[self.clip]]
        self.timeline.tracks[0].clear()
        self.job.source_file = str(video)
        self.job.source_clip['source']['media_pool_id'] = self.clip.media.GetUniqueId()
        self.job.source_clip['clip'] = asdict(inspect_clip(self.clip, self.timeline, 'selected', source_times=True))
        self.job.save(self.path)
        self.assert_current_trim_import(.5, 1)
        self.assertEqual(self.timeline.video_tracks, [[self.clip]])

    def test_trim_during_import_stops_before_adding_stems_at_different_positions(self):
        original_append = self.pool.AppendToTimeline
        def trim_after_append(infos):
            result = original_append(infos)
            self.clip.start += 12
            self.clip.source_start += .5
            self.clip.seconds -= .5
            return result
        with patch.object(self.pool, 'AppendToTimeline', side_effect=trim_after_append):
            with self.assertRaises(InspectionError): self.run_import()
        self.assertEqual(self.pool.appends, 1)
        self.assertEqual(read_receipt(self.path)['status'], 'failed')
        with self.assertRaises(InspectionError): self.run_import()
        self.assertEqual(self.pool.appends, 1)
        self.clip.start -= 12
        self.clip.source_start -= .5
        self.clip.seconds += .5
        self.run_import()
        self.assertEqual(self.pool.appends, 2)

    def test_mp4_video_anchor_adds_only_audio_at_moved_position(self):
        video = self.root / 'source.mp4'; video.write_bytes(b'original source')
        self.clip.media.path = str(video)
        self.clip.kind = 'video'
        self.timeline.video_tracks = [[self.clip]]
        self.timeline.tracks[0].clear()
        self.job.source_file = str(video)
        self.job.source_clip['source']['media_pool_id'] = self.clip.media.GetUniqueId()
        self.job.source_clip['clip'] = asdict(inspect_clip(self.clip, self.timeline, 'selected', source_times=True))
        self.job.save(self.path)
        original_start = self.clip.start
        self.clip.start += 24
        self.run_import(); self.run_import()
        self.assertEqual(self.pool.appends, 2)
        self.assertEqual(self.timeline.video_tracks, [[self.clip]])
        self.assertEqual(self.clip.start, original_start + 24)
        self.assertEqual([len(track) for track in self.timeline.tracks], [0, 1, 1])
        for track in self.timeline.tracks[1:]:
            self.assertEqual((track[0].start, track[0].source_start, track[0].seconds),
                             (86445.5, 1.25, 4.5))

    def test_partial_failure_reuses_empty_reserved_track_and_added_item(self):
        self.pool.fail_append = 2
        with self.assertRaises(InspectionError): self.run_import()
        self.assertEqual(read_receipt(self.path)['status'],'failed')
        self.pool.fail_append = None
        self.run_import()
        self.assertEqual(len(self.timeline.tracks),3)
        self.assertEqual([len(t) for t in self.timeline.tracks],[1,1,1])
        self.assertEqual(self.pool.imports,2)

    def test_lost_append_reply_is_reconciled_not_duplicated(self):
        self.pool.lost_reply = True
        with self.assertRaises(InspectionError): self.run_import()
        self.pool.lost_reply = False
        self.run_import()
        self.assertEqual(self.pool.appends,2)
        self.assertEqual(len(self.timeline.tracks),3)

    def test_wrong_native_placement_remains_failed_on_retry(self):
        self.pool.wrong_duration = True
        with self.assertRaises(InspectionError): self.run_import()
        with self.assertRaises(InspectionError): self.run_import()
        self.assertEqual(self.pool.appends,1)
        self.assertEqual(read_receipt(self.path)['status'],'failed')
        self.run_import('media_pool')  # downloaded files remain usable

    def test_legacy_import_signature_and_legacy_job_metadata(self):
        self.pool.legacy_only = True
        self.job.source_clip.pop('project_id')
        self.job.source_clip['clip'].pop('source_start_seconds')
        self.job.source_clip['clip'].pop('source_end_seconds')
        self.job.save(self.path)
        self.run_import()
        self.assertEqual(self.pool.imports,2)

    def test_wrong_project_or_timeline_does_not_mutate(self):
        self.project.uid = 'other'
        with self.assertRaises(InspectionError): self.run_import()
        self.assertEqual(self.pool.imports,0)
        self.project.uid = 'project'; self.timeline.uid = 'other'
        with self.assertRaises(InspectionError): self.run_import()
        self.assertEqual(self.pool.imports,0)

    def test_retimed_or_changed_source_has_media_pool_fallback(self):
        self.clip.speed = 50
        self.assert_import_reason('50%')
        self.clip.speed = 100
        self.source.write_bytes(b'changed')
        self.assert_import_reason('Содержимое исходного файла изменилось')
        self.run_import('media_pool')
        self.assertEqual(self.pool.appends,0)

    def test_legacy_job_reads_current_source_times_for_movement_and_trim(self):
        self.job.source_clip['clip'].pop('source_start_seconds')
        self.job.source_clip['clip'].pop('source_end_seconds')
        self.job.save(self.path)
        self.assert_current_trim_import(.25, 1, move=48)

    def test_relinked_clip_is_not_accepted_even_at_the_same_trim(self):
        other = self.root / 'other.wav'; other.write_bytes(self.source.read_bytes())
        self.clip.media = Media(other)
        self.assert_import_reason('заменён или перепривязан')
        self.assertEqual((self.pool.imports, self.pool.appends), (0, 0))

    def assert_import_reason(self, text, error=InspectionError):
        with self.assertRaises(error): self.run_import()
        receipt = read_receipt(self.path)
        self.assertEqual(receipt['status'], 'failed')
        self.assertIn(text, import_message(receipt))
        self.assertFalse(timeline_confirmed(receipt))
        self.assertEqual(self.pool.appends, 0)

    def test_deleted_clip_is_distinguished_from_missing_source_file(self):
        self.timeline.tracks[0].clear()
        self.assert_import_reason('клип не найден на исходном таймлайне')
        self.timeline.tracks[0].append(self.clip)
        self.source.unlink()
        self.assert_import_reason('файл недоступен на диске')
        self.assertIn(str(self.source.resolve()), read_receipt(self.path)['error'])
        self.run_import('media_pool')

    def test_missing_and_replaced_media_pool_source_are_diagnosed(self):
        with patch.object(self.clip, 'GetMediaPoolItem', return_value=None):
            self.assert_import_reason('потерял связь')
        with patch.object(self.clip.media, 'GetUniqueId', return_value='replaced-media'):
            self.assert_import_reason('заменён или перепривязан')

    def test_unknown_speed_and_invalid_ranges_have_distinct_diagnostics(self):
        with patch.object(self.clip, 'GetSpeed', return_value={}):
            self.assert_import_reason('не сообщил скорость')
        with patch.object(self.clip, 'GetSourceStartTime', return_value=None):
            self.assert_import_reason('не сообщил корректные границы')
        with patch.object(self.clip, 'GetSourceEndTime', return_value=9):
            self.assert_import_reason('Длительность исходного клипа не соответствует')
        with patch.object(self.clip, 'GetEnd', return_value=99999):
            self.assert_import_reason('Границы исходного клипа изменились во время проверки')

    def test_source_read_failure_is_not_reported_as_source_replacement(self):
        with patch('resolve_mvsep.resolve.importer.source_digest', side_effect=PermissionError()):
            self.assert_import_reason('Нет доступа к исходному файлу')
        with patch('resolve_mvsep.resolve.importer.source_digest', side_effect=OSError('device unavailable')):
            self.assert_import_reason('device unavailable')

    def test_timeline_fps_failure_and_changed_fps_are_distinguished(self):
        with patch.object(self.timeline, 'GetSettings', return_value={}):
            self.assert_import_reason('не сообщил корректную частоту кадров')
        with patch.object(self.timeline, 'GetSettings', return_value={'timelineFrameRate': 25}):
            self.assert_import_reason('Частота кадров таймлайна изменилась')

    def test_missing_result_diagnostic_names_the_file(self):
        self.files[1].unlink()
        self.assert_import_reason('Файл результата отсутствует')
        self.assertIn(self.files[1].name, import_message(read_receipt(self.path)))

    def test_native_exception_reports_the_failed_action_in_the_receipt(self):
        with patch.object(self.pool, 'ImportMedia', side_effect=RuntimeError('bridge lost')):
            self.assert_import_reason('Импорт файла', RuntimeError)
        self.assertIn('bridge lost', import_message(read_receipt(self.path)))
        self.assertIn(self.files[0].name, import_message(read_receipt(self.path)))

    def test_cleanup_exception_does_not_hide_primary_import_failure(self):
        with patch.object(self.pool, 'AppendToTimeline', return_value=[]), \
                patch.object(self.pool, 'SetCurrentFolder', side_effect=[True, True, RuntimeError('cleanup lost')]), \
                self.assertLogs('resolve_mvsep.resolve.importer', level='ERROR'):
            with self.assertRaisesRegex(InspectionError, 'не разместил файл'): self.run_import()
        self.assertIn('не разместил файл', import_message(read_receipt(self.path)))

    def test_connection_failures_are_localized_and_saved(self):
        messages = [
            ('No current project. Open a project in Resolve.', 'не открыт проект'),
            ('No current timeline. Open a timeline in the Edit page.', 'не открыт таймлайн'),
            ('Resolve is not available.', 'Нет подключения'),
        ]
        for native_message, expected in messages:
            with self.subTest(native_message=native_message), \
                    patch('resolve_mvsep.resolve.importer.connect', side_effect=InspectionError(native_message)):
                self.assert_import_reason(expected)

    def test_speed_error_is_localized_in_english(self):
        self.clip.speed = 50
        with using_language('en'):
            self.assert_import_reason('source clip speed is not 100%: 50%')

    def test_worker_preflight_failure_is_visible_without_overwriting_active_import(self):
        from resolve_mvsep.import_results import run_import
        from resolve_mvsep.core.active import process_identity
        from resolve_mvsep.core.resolve_import import save_receipt
        import os
        lock_error = InspectionError('Another MVSEP job is already running. Wait for it to finish.')
        with patch('resolve_mvsep.resolve.importer.import_results', side_effect=lock_error), \
                patch('resolve_mvsep.import_results.create_logger', return_value=(Mock(), None)), \
                patch('resolve_mvsep.import_results.close_logger'):
            self.assertEqual(run_import(self.root, self.path, 'timeline'), 1)
            self.assertIn('Другой импорт в Resolve', import_message(read_receipt(self.path)))
            save_receipt(self.path, {'status': 'running', 'pid': os.getpid(), 'identity': process_identity(os.getpid())})
            before = self.path.with_name('resolve-import.json').read_bytes()
            self.assertEqual(run_import(self.root, self.path, 'timeline'), 1)
            self.assertEqual(before, self.path.with_name('resolve-import.json').read_bytes())

    def test_missing_result_or_incomplete_download_cannot_import(self):
        self.job.status = JobStatus.PROCESSING; self.job.save(self.path)
        with self.assertRaises(InspectionError): self.run_import()
        self.job.status = JobStatus.COMPLETED; self.job.save(self.path)
        self.files[1].unlink()
        with self.assertRaises(InspectionError): self.run_import()
        self.assertEqual(self.pool.imports,0)

    def test_user_populated_reserved_track_is_never_overwritten(self):
        self.pool.fail_append = 1
        with self.assertRaises(InspectionError): self.run_import()
        self.timeline.tracks[1].append(Item(Media(self.root/'unrelated.wav'),track=2,uid='user-item'))
        self.pool.fail_append = None
        self.run_import()
        self.assertEqual(len(self.timeline.tracks),4)
        self.assertEqual(self.timeline.tracks[1][0].GetUniqueId(),'user-item')

    def test_latest_result_localization_and_cli_bypasses_token(self):
        self.assertEqual(latest_completed(self.root,self.job),self.path)
        receipt = self.run_import()
        with using_language('en'):
            self.assertIn('files: 2; tracks: A2–A3',import_message(receipt))
        from resolve_mvsep.cli import main
        with patch('resolve_mvsep.import_results.run_import',return_value=0) as run, \
             patch('resolve_mvsep.cli.load_mvsep_settings',side_effect=AssertionError('no token needed')):
            self.assertEqual(main(self.root,['--import-job',str(self.path),'--import-target','timeline']),0)
            run.assert_called_once_with(self.root,self.path.resolve(),'timeline')

    def test_five_stems_after_ten_tracks_report_exact_actual_location(self):
        for i in range(2,11):
            self.timeline.AddTrack('audio','stereo')
            self.timeline.SetTrackName('audio',i,f'Existing {i}')
        for stem in ('drums','bass','instrumental'):
            file = self.root/f'source_{stem}_001.wav'; file.write_bytes(b'fake audio')
            self.job.result_files.append(str(file))
        self.job.save(self.path)
        receipt = self.run_import()
        self.assertTrue(timeline_confirmed(receipt))
        self.assertEqual(track_range(receipt),'A11–A15')
        self.assertEqual(receipt['timeline_name'],'Timeline')
        self.assertEqual(len(receipt['timeline_items']),5)
        self.assertIn('A11 — source_vocals_001.wav',track_details(receipt))
        self.assertIn('A15 — source_instrumental_001.wav',track_details(receipt))
        self.run_import()
        self.assertEqual(self.pool.appends,5)

    def test_returned_object_without_actual_timeline_membership_is_not_success(self):
        self.pool.ghost_reply = True
        with self.assertRaises(InspectionError): self.run_import()
        receipt = read_receipt(self.path)
        self.assertEqual(receipt['status'],'failed')
        self.assertFalse(timeline_confirmed(receipt))

    def test_final_read_detects_a_clip_removed_during_the_import(self):
        self.pool.remove_first = True
        with self.assertRaises(InspectionError): self.run_import()
        self.assertEqual(read_receipt(self.path)['status'],'failed')
        self.assertFalse(timeline_confirmed(read_receipt(self.path)))

    def test_receipt_without_readback_does_not_claim_current_confirmation(self):
        receipt = {'status':'completed','target':'timeline','timeline_items':{'a':{'track':11}}}
        self.assertFalse(timeline_confirmed(receipt))
        with using_language('en'):
            self.assertIn('earlier import',import_message(receipt))

    def test_display_ranges_preserve_gaps_and_sort_rows(self):
        receipt = {'target':'timeline','timeline_name':'Test','timeline_items':{
            'b.wav':{'track':8}, 'a.wav':{'track':4}, 'c.wav':{'track':6}, 'd.wav':{'track':7}}}
        self.assertEqual(track_range(receipt),'A4, A6–A8')
        text = track_details(receipt)
        self.assertLess(text.index('A4 —'),text.index('A8 —'))


if __name__ == '__main__': unittest.main()
