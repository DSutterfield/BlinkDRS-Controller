import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from archive_maintenance import cleanup_expired_clips, reconcile_local_presence
from catalog_store import list_clips, open_catalog_notifications

class ArchiveMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clips = self.root/'clips'; self.clips.mkdir()
        self.db = self.root/'catalog.db'
        with closing(sqlite3.connect(self.db)) as c, c:
            c.executescript((Path(__file__).parents[1]/'sql/catalog_schema_v1.sql').read_text())
            for i in range(1,4):
                c.execute('INSERT INTO clips (id,filename,video_path,captured_at,watched,cloud_present) VALUES (?,?,?,?,0,1)',
                          (i,f'{i}.mp4',f'clips/{i}.mp4',f'2026-08-{i:02}T00:00:00'))
        for i in (1,2):
            (self.clips/f'{i}.mp4').write_bytes(b'video')
        os.utime(self.clips/'1.mp4',(1,1))
        (self.clips/'1.json').write_text('{}')
    def test_missing_deletes_all_references_and_notifies(self):
        from catalog_store import set_clip_validation
        watcher=open_catalog_notifications(self.db)
        self.addCleanup(watcher.close)
        (self.root/'clip_thumbs').mkdir();(self.root/'playback_cache').mkdir()
        for path in (self.clips/'3.json',self.root/'clip_thumbs/3.jpg',self.root/'playback_cache/3.loudnorm-v2.mp4'):
            path.write_bytes(b'old')
        set_clip_validation(self.db,3,'damaged','test','a'*64)
        baseline=watcher.execute('SELECT revision FROM catalog_notifications').fetchone()[0]
        self.assertEqual(reconcile_local_presence(self.db,self.clips),1)
        self.assertEqual(list_clips(self.db)['total'],2)
        self.assertGreater(watcher.execute('SELECT revision FROM catalog_notifications').fetchone()[0],baseline)
        self.assertEqual(watcher.execute('SELECT count(*) FROM clips').fetchone()[0],2)
        self.assertEqual(watcher.execute('SELECT count(*) FROM clip_validation').fetchone()[0],0)
        self.assertFalse((self.clips/'3.json').exists())
        self.assertEqual(list((self.root/'clip_thumbs').iterdir()),[])
        self.assertEqual(list((self.root/'playback_cache').iterdir()),[])
        self.assertEqual(reconcile_local_presence(self.db,self.clips),0)
    def test_manual_delete_stages_caches_and_restores_on_failure(self):
        from clip_delete import stage_clip_for_delete,restore_staged_clip,finalize_staged_clip
        from catalog_store import delete_clip_by_catalog_id,set_clip_validation
        (self.root/'playback_cache').mkdir()
        cache=self.root/'playback_cache/1.loudnorm-v2.mp4';cache.write_bytes(b'cache')
        clip={'id':1,'filename':'1.mp4'}
        stage=stage_clip_for_delete(self.root,clip)
        self.assertFalse(cache.exists());self.assertFalse((self.clips/'1.mp4').exists())
        restore_staged_clip(stage)
        self.assertEqual(cache.read_bytes(),b'cache')
        set_clip_validation(self.db,1,'damaged','test','a'*64)
        stage=stage_clip_for_delete(self.root,clip)
        self.assertTrue(delete_clip_by_catalog_id(self.db,1))
        finalize_staged_clip(stage)
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM clip_validation').fetchone()[0],0)
        self.assertFalse(cache.exists())
    def test_path_escape_rejected_before_deletion(self):
        with closing(sqlite3.connect(self.db)) as c,c:
            c.execute("UPDATE clips SET sidecar_path='../outside.json' WHERE id=3")
        with self.assertRaises(ValueError):reconcile_local_presence(self.db,self.clips)
        self.assertEqual(list_clips(self.db)['total'],3)
    def test_historical_orphan_cache_without_catalog_row_is_removed(self):
        (self.root/'playback_cache').mkdir()
        cache=self.root/'playback_cache/absent.loudnorm-v2.mp4';cache.write_bytes(b'old')
        reconcile_local_presence(self.db,self.clips)
        self.assertFalse(cache.exists())
    def test_retention_removes_expired_files_and_updates_catalog(self):
        self.assertEqual(cleanup_expired_clips(self.db,self.clips,30),1)
        self.assertFalse((self.clips/'1.mp4').exists())
        self.assertFalse((self.clips/'1.json').exists())
        self.assertTrue((self.clips/'2.mp4').exists())
        result=list_clips(self.db,limit=1)
        self.assertEqual(result['total'],1)
        self.assertEqual(result['clips'][0]['catalog_id'],2)
    def test_disabled_retention_keeps_old_video_but_fixes_missing_rows(self):
        self.assertEqual(cleanup_expired_clips(self.db,self.clips,0),0)
        self.assertTrue((self.clips/'1.mp4').exists())
        self.assertEqual(list_clips(self.db)['total'],2)
    def test_unavailable_directory_does_not_hide_catalog(self):
        with self.assertRaises(FileNotFoundError):
            reconcile_local_presence(self.db,self.root/'offline')
        self.assertEqual(list_clips(self.db)['total'],3)
    def test_io_error_does_not_commit_partial_scan(self):
        original=Path.stat
        def fail(path,*args,**kwargs):
            if path.name=='3.mp4': raise PermissionError('test')
            return original(path,*args,**kwargs)
        with patch.object(Path,'stat',fail), self.assertRaises(PermissionError):
            reconcile_local_presence(self.db,self.clips)
        self.assertEqual(list_clips(self.db)['total'],3)
    def test_sidecar_failure_still_marks_deleted_video_absent(self):
        original=Path.unlink
        def fail(path,*args,**kwargs):
            if path.name=='1.json': raise PermissionError('test')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',fail), self.assertRaises(PermissionError):
            cleanup_expired_clips(self.db,self.clips,30)
        self.assertFalse((self.clips/'1.mp4').exists())
        self.assertEqual(list_clips(self.db)['total'],1)
    def test_failed_video_delete_leaves_it_available(self):
        with patch.object(Path,'unlink',side_effect=PermissionError('test')), self.assertRaises(PermissionError):
            cleanup_expired_clips(self.db,self.clips,30)
        self.assertTrue((self.clips/'1.mp4').exists())
        self.assertEqual(list_clips(self.db)['total'],2)
if __name__=='__main__': unittest.main()
