import unittest,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import cv2,numpy as np
from person_review_crop import review_region
from identity_worker import analyze_video


class PersonReviewCropTests(unittest.TestCase):
    def test_margin_includes_head_and_sides_and_stays_inside_frame(self):
        box,rank=review_region((720,1280,3),[1085,366,1242,674],.976)
        self.assertEqual(box,[1072,347,1255,693]);self.assertEqual(rank[0],4)
        box,rank=review_region((720,1280,3),[1114,407,1277,717],.94)
        self.assertEqual(box,[1100,388,1280,720]);self.assertEqual(rank[0],2)

    def replay(self,kind='Person',consensus=True,face=False,quality=1.):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'views.mp4';writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),10,(200,200))
            for i in range(100):writer.write(np.full((200,200,3),50 if i<99 else 200,np.uint8))
            writer.release();calls=[];index=[0]
            def detect(frame):
                n=index[0];index[0]+=1
                return [dict(subject_type=kind,confidence=.94 if n==0 else .98,box=[110,40,199,199] if n==0 else [90,30,180,185],detail_person_candidate=True,person_model_consensus=consensus)]
            def embedding(crop,k):calls.append(crop.shape[:2]);return ([1.,0.] if face else None,'face' if face else None,quality)
            m=SimpleNamespace(cv2=cv2,detector_version='test',detect=detect,embedding=embedding)
            with patch('identity_worker.sampled_frame_indices',return_value=([90,99],10)):tracks=analyze_video(path,m)
            return tracks,calls

    def test_fuller_later_view_wins_equal_sharpness_and_features_stay_native(self):
        tracks,calls=self.replay();t=tracks[0]
        self.assertEqual(t['crop_seconds'],9.9);self.assertEqual(t['confidence'],.98)
        self.assertEqual(calls,[(159,89),(155,90)])
        self.assertEqual(t['box'],[90,30,180,185]);self.assertLess(t['review_crop_box'][1],30)
        image=cv2.imdecode(np.frombuffer(t['crop_jpeg'],np.uint8),cv2.IMREAD_COLOR)
        self.assertGreater(image.mean(),150);self.assertIsNone(t['embedding'])

    def test_original_uncorroborated_person_keeps_native_first_crop(self):
        tracks,_=self.replay(consensus=False);t=tracks[0]
        self.assertEqual(t['crop_seconds'],9.0);self.assertEqual(t['review_crop_box'],[110,40,199,199])

    def test_face_embedding_keeps_existing_native_crop_policy(self):
        tracks,_=self.replay(face=True);t=tracks[0]
        self.assertEqual(t['crop_seconds'],9.0);self.assertEqual(t['review_crop_box'],[110,40,199,199]);self.assertEqual(t['embedding'],[1.,0.])

    def test_low_quality_person_is_not_padded(self):
        tracks,_=self.replay(quality=.1);self.assertIsNone(tracks[0]['person_review_rank'])

    def test_cat_crop_policy_is_unchanged(self):
        tracks,_=self.replay(kind='Cat');t=tracks[0]
        self.assertEqual(t['crop_seconds'],9.0);self.assertIsNone(t['person_review_rank'])
