"""Pi playback regression checks; standard library plus existing ffmpeg tools only."""
import json
import sqlite3
import subprocess
import tempfile
import unittest
import zipfile
import io
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from manual_crops import _frame_times, clip_frame_times, clip_frame, clip_frame_window, _video_info


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.db = self.root/'catalog.db'
        with closing(sqlite3.connect(self.db)) as conn,conn:
            conn.execute('CREATE TABLE clips(id INTEGER PRIMARY KEY, video_path TEXT, local_present INTEGER)')
            conn.execute("INSERT INTO clips VALUES(1,'1.mp4',1)")

    def test_variable_frame_times_and_cache(self):
        _frame_times.cache_clear()
        result = SimpleNamespace(stdout=json.dumps({'frames':[
            {'best_effort_timestamp_time':t} for t in ('2','2.04','2.09','2.13')]}).encode())
        with patch('manual_crops.subprocess.run',return_value=result) as run:
            self.assertEqual(_frame_times('fixture',1,1),(0,.04,.09,.13))
            _frame_times('fixture',1,1)
            self.assertEqual(run.call_count,1)
            _frame_times('fixture',2,2)
            self.assertEqual(run.call_count,2)

    def test_invalid_frame_times(self):
        for index,times in enumerate((['nan'],['0','0'],['0','-1'])):
            result=SimpleNamespace(stdout=json.dumps({'frames':[
                {'best_effort_timestamp_time':t} for t in times]}).encode())
            with patch('manual_crops.subprocess.run',return_value=result),self.assertRaises(ValueError):
                _frame_times('invalid'+str(index),1,1)

    def test_archive_and_clip_validation(self):
        with self.assertRaises(LookupError):clip_frame_times(self.db,self.root,999)
        with closing(sqlite3.connect(self.db)) as conn,conn:
            conn.execute("UPDATE clips SET video_path='../outside.mp4'")
        with self.assertRaises(ValueError):clip_frame_times(self.db,self.root,1)

    def test_every_decoded_frame_and_original_crop_size(self):
        video=self.root/'1.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=10:duration=1',
            '-threads','1','-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True,timeout=30)
        times=clip_frame_times(self.db,self.root,1)
        self.assertEqual(len(times),10)
        self.assertEqual(times[3],.3)
        images=[clip_frame(self.db,self.root,1,t) for t in times]
        self.assertEqual(len(set(images)),10)
        for index,jpeg in enumerate(images):
            expected=subprocess.run(['ffmpeg','-v','error','-i',str(video),'-vf',f'select=eq(n\\,{index})',
                '-frames:v','1','-threads','1','-f','image2pipe','-vcodec','mjpeg','-q:v','2','pipe:1'],
                capture_output=True,check=True,timeout=30).stdout
            self.assertEqual(jpeg,expected)
        crop=self.root/'crop.jpg'
        crop.write_bytes(clip_frame(self.db,self.root,1,times[3],(32,18,80,60)))
        probe=subprocess.run(['ffprobe','-v','error','-show_entries','stream=width,height','-of','json',str(crop)],
            capture_output=True,check=True,timeout=10)
        stream=json.loads(probe.stdout)['streams'][0]
        self.assertEqual((stream['width'],stream['height']),(80,60))
        for start,count in ((0,8),(8,8),(3,2)):
            with zipfile.ZipFile(io.BytesIO(clip_frame_window(self.db,self.root,1,start,count))) as pack:
                self.assertEqual(pack.namelist(),[f'{i}.jpg' for i in range(start,min(start+count,10))])
                for name in pack.namelist():
                    self.assertEqual(pack.read(name),images[int(name.split('.')[0])])

    def test_batch_bounds_and_metadata_cache(self):
        for start,count in ((-1,8),(0,0),(0,9),(1.5,1)):
            with self.assertRaises(ValueError):clip_frame_window(self.db,self.root,1,start,count)
        _video_info.cache_clear()
        result=SimpleNamespace(stdout=b'{"streams":[{"width":320,"height":180}],"format":{"duration":"1.0"}}')
        with patch('manual_crops.subprocess.run',return_value=result) as run:
            self.assertEqual(_video_info('metadata',1,1),(320,180,1.))
            _video_info('metadata',1,1)
            self.assertEqual(run.call_count,1)
            _video_info('metadata',2,2)
            self.assertEqual(run.call_count,2)

    def test_variable_rate_batch_matches_timestamp_selection(self):
        video=self.root/'1.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=20:duration=0.5',
            '-vf','setpts=if(lt(N\\,5)\\,N*0.05/TB\\,(0.25+(N-5)*0.1)/TB)',
            '-fps_mode','passthrough','-threads','1','-c:v','libx264','-bf','0','-pix_fmt','yuv420p',str(video)],check=True,timeout=30)
        times=clip_frame_times(self.db,self.root,1)
        self.assertGreater(len(set(round(b-a,3) for a,b in zip(times,times[1:]))),1)
        for start in (0,8):
            with zipfile.ZipFile(io.BytesIO(clip_frame_window(self.db,self.root,1,start,8))) as pack:
                for name in pack.namelist():
                    frame=int(name.split('.')[0])
                    self.assertEqual(pack.read(name),clip_frame(self.db,self.root,1,times[frame]))


if __name__=='__main__':unittest.main(verbosity=2)

