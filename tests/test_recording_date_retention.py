from datetime import datetime,timezone,timedelta
from contextlib import closing
import os,sqlite3
from unittest.mock import patch
import test_archive_maintenance as base
from archive_maintenance import cleanup_expired_clips,recording_timestamp

class RecordingDateRetentionTests(base.ArchiveMaintenanceTests):
    now=datetime(2026,11,2,18,tzinfo=timezone.utc).timestamp()
    def date(self,cid,value):
        with closing(sqlite3.connect(self.db)) as c,c:c.execute('update clips set captured_at=? where id=?',(value,cid))
    def setUp(self):
        super().setUp()
        for cid in (1,2):self.date(cid,'2026-11-02T18:00:00+00:00')
    def test_recording_expired_even_when_newly_copied(self):
        self.date(1,'2026-10-10T12:00:00Z');os.utime(self.clips/'1.mp4',(self.now,self.now))
        self.assertEqual(cleanup_expired_clips(self.db,self.clips,15,now=self.now),1)
        self.assertFalse((self.clips/'1.mp4').exists());self.assertTrue((self.clips/'2.mp4').exists())
    def test_old_file_time_does_not_expire_new_recording(self):
        os.utime(self.clips/'1.mp4',(1,1))
        self.assertEqual(cleanup_expired_clips(self.db,self.clips,15,now=self.now),0)
        self.assertTrue((self.clips/'1.mp4').exists())
    def test_exact_cutoff_and_offset_are_consistent(self):
        self.date(1,'2026-10-18T13:00:00-05:00')
        self.date(2,'2026-10-18T17:59:59Z')
        self.assertEqual(cleanup_expired_clips(self.db,self.clips,15,now=self.now),1)
        self.assertTrue((self.clips/'1.mp4').exists());self.assertFalse((self.clips/'2.mp4').exists())
    def test_invalid_and_untracked_dates_are_kept(self):
        self.date(1,'invalid');(self.clips/'orphan.mp4').write_bytes(b'original');os.utime(self.clips/'orphan.mp4',(1,1))
        self.assertEqual(cleanup_expired_clips(self.db,self.clips,15,now=self.now),0)
        self.assertTrue((self.clips/'1.mp4').exists());self.assertTrue((self.clips/'orphan.mp4').exists())
    def test_oldest_recording_first_and_batch_bound(self):
        self.date(1,'2026-10-10');self.date(2,'2026-10-01');os.utime(self.clips/'2.mp4',(self.now,self.now))
        self.assertEqual(cleanup_expired_clips(self.db,self.clips,15,now=self.now,max_delete=1),1)
        self.assertTrue((self.clips/'1.mp4').exists());self.assertFalse((self.clips/'2.mp4').exists())
    def test_corrected_capture_date_wins_before_deletion(self):
        import archive_maintenance as module
        self.date(1,'2026-10-01');original=module.clip_artifact_paths
        def corrected(root,filename,*args,**kwargs):
            if filename=='1.mp4':self.date(1,'2026-11-02T18:00:00Z')
            return original(root,filename,*args,**kwargs)
        with patch('archive_maintenance.clip_artifact_paths',side_effect=corrected):
            self.assertEqual(cleanup_expired_clips(self.db,self.clips,15,now=self.now),0)
        self.assertTrue((self.clips/'1.mp4').exists())
    def test_legacy_utc_and_date_only(self):
        self.assertEqual(recording_timestamp('2026-10-18T18:00:00'),recording_timestamp('2026-10-18T18:00:00Z'))
        self.assertEqual(recording_timestamp('2026-10-18'),recording_timestamp('2026-10-18T00:00:00+00:00'))
        for invalid in (None,'','nonsense','2026-99-99'):
            self.assertIsNone(recording_timestamp(invalid))
    # Base class has its own date fixtures; new cases replace those dates.
    # Do not run inherited original-fixture assertions a second time.
for name in list(base.ArchiveMaintenanceTests.__dict__):
    if name.startswith('test_') and name not in RecordingDateRetentionTests.__dict__:
        setattr(RecordingDateRetentionTests,name,None)
