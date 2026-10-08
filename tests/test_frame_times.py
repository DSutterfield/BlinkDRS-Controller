import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from PIL import Image
import io
import shutil
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_manual_crops import ManualCropTests
from identity_api import install_identity_api
from manual_crops import _frame_times, clip_frame_times, clip_frame


class FrameTests(unittest.TestCase):
    setUp = ManualCropTests.setUp

    def test_variable_timestamps_cache_and_invalid(self):
        _frame_times.cache_clear()
        result = SimpleNamespace(stdout=json.dumps({'frames':[
            {'best_effort_timestamp_time':t} for t in ('2.0','2.04','2.09','2.13')]}).encode())
        with patch('manual_crops.subprocess.run',return_value=result) as run:
            self.assertEqual(_frame_times('fixture',1,1),(0,.04,.09,.13))
            _frame_times('fixture',1,1)
            self.assertEqual(run.call_count,1)
            _frame_times('fixture',2,2)
            self.assertEqual(run.call_count,2)
        result.stdout=b'{"frames":[{"best_effort_timestamp_time":"nan"}]}'
        with patch('manual_crops.subprocess.run',return_value=result), self.assertRaises(ValueError):
            _frame_times('bad',1,1)

    def test_http_and_missing_clip(self):
        app=FastAPI()
        install_identity_api(app,SimpleNamespace(catalog_db_path=self.db,archive_root=self.db.parent))
        with TestClient(app) as client, patch('manual_crops.clip_frame_times',return_value=(0,.04,.09)):
            self.assertEqual(client.get('/api/v1/clips/catalog/1/frame-times').json(),[0,.04,.09])
        with self.assertRaises(LookupError):clip_frame_times(self.db,self.db.parent,999)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Requires ffmpeg and ffprobe on the Pi')
    def test_real_video_frame_and_original_crop(self):
        video=self.db.parent/'1.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=10:duration=1',
            '-threads','1','-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True,timeout=20)
        times=clip_frame_times(self.db,self.db.parent,1)
        self.assertEqual(len(times),10)
        self.assertEqual(times[3],.3)
        images=[clip_frame(self.db,self.db.parent,1,t) for t in times]
        self.assertEqual(len(set(images)),10)
        crop=clip_frame(self.db,self.db.parent,1,times[3],(32,18,80,60))
        self.assertEqual(Image.open(io.BytesIO(crop)).size,(80,60))
        # Each timestamp must select its own frame, including the final frame.
        for index,jpeg in enumerate(images):
            expected=subprocess.run(['ffmpeg','-v','error','-i',str(video),'-vf',f'select=eq(n\\,{index})',
                '-frames:v','1','-threads','1','-f','image2pipe','-vcodec','mjpeg','-q:v','2','pipe:1'],capture_output=True,check=True).stdout
            self.assertEqual(jpeg,expected)


if __name__=='__main__':unittest.main()
