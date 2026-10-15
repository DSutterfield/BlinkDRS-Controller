import unittest,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import cv2,numpy as np
from person_scene_search import PersonConsensusModels
from cat_extra import CatSearchModels
from identity_worker import analyze_video


class PersonConsensusTests(unittest.TestCase):
    def scene(self,first=.94,second=.88,box=None):
        s=PersonConsensusModels.__new__(PersonConsensusModels)
        s.models=SimpleNamespace(cv2=cv2)
        box=box or [50,20,150,180]
        s.person_search_candidates=[dict(subject_type='Person',confidence=first,box=box,detector_model='fasterrcnn')]
        s.proposal_detector=SimpleNamespace(people=[dict(subject_type='Person',confidence=second,box=box,detector_model='yolo')])
        return s

    def test_two_models_recover_person_below_original_cutoff(self):
        s=self.scene()
        with patch.object(CatSearchModels,'detect',return_value=[]):found=s.detect(np.zeros((200,200,3),np.uint8))
        self.assertEqual(len(found),1);self.assertEqual(found[0]['subject_type'],'Person')
        self.assertTrue(found[0]['person_model_consensus'])

    def test_each_model_and_overlap_are_required(self):
        for first,second in ((.899,.9),(.99,.799)):
            s=self.scene(first,second)
            with patch.object(CatSearchModels,'detect',return_value=[]):self.assertEqual(s.detect(np.zeros((200,200,3),np.uint8)),[])
        s=self.scene();s.proposal_detector.people[0]['box']=[160,20,199,180]
        with patch.object(CatSearchModels,'detect',return_value=[]):self.assertEqual(s.detect(np.zeros((200,200,3),np.uint8)),[])

    def test_person_class_and_usable_native_size_are_required(self):
        s=self.scene();s.proposal_detector.people[0]['subject_type']='Cat'
        with patch.object(CatSearchModels,'detect',return_value=[]):self.assertEqual(s.detect(np.zeros((200,200,3),np.uint8)),[])
        s=self.scene(box=[50,50,80,100])
        with patch.object(CatSearchModels,'detect',return_value=[]):self.assertEqual(s.detect(np.zeros((200,200,3),np.uint8)),[])

    def test_original_person_crop_and_other_subjects_are_preserved(self):
        s=self.scene();person=dict(subject_type='Person',confidence=.7,box=[55,25,145,175]);cat=dict(subject_type='Cat',confidence=.8,box=[10,150,40,190])
        with patch.object(CatSearchModels,'detect',return_value=[person,cat]):found=s.detect(np.zeros((200,200,3),np.uint8))
        self.assertEqual(len(found),2);self.assertEqual(person['box'],[55,25,145,175]);self.assertTrue(person['person_model_consensus']);self.assertEqual(found[1],cat)

    def test_missing_second_detector_cannot_add_person(self):
        s=self.scene();s.proposal_detector=None
        with patch.object(CatSearchModels,'detect',return_value=[]):self.assertEqual(s.detect(np.zeros((200,200,3),np.uint8)),[])

    def test_agreeing_single_frame_is_kept_without_face_embedding(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'person.mp4';writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),10,(100,100))
            for i in range(100):writer.write(np.full((100,100,3),255 if i==99 else 0,np.uint8))
            writer.release()
            for consensus in (False,True):
                m=SimpleNamespace(cv2=cv2,detector_version='test',embedding=lambda c,k:(None,None,1.),
                    detect=lambda frame:[dict(subject_type='Person',confidence=.94,box=[10,0,90,99],detail_person_candidate=True,person_model_consensus=consensus)] if frame.mean()>200 else [])
                tracks=analyze_video(path,m)
                self.assertEqual(len(tracks),int(consensus))
                if consensus:self.assertEqual(tracks[0]['subject_type'],'Person');self.assertIsNone(tracks[0]['embedding'])
