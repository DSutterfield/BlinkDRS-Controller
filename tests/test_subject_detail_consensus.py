import unittest
from types import SimpleNamespace
from unittest.mock import patch
import hashlib
import cv2
import numpy as np
from pathlib import Path
import tempfile
import io
from PIL import Image
from contextlib import nullcontext
from subject_detail_consensus import SubjectConsensusModels
from subject_type_check import in_person_region
from identity_worker import analyze_video


def ranks(kind='Dog',score=.6,second=.35,support=.6):
    kinds=[kind]+[k for k in ('Person','Cat','Dog') if k!=kind]
    return [dict(subject_type=k,score=s,independent_similarities=[.7,support])
            for k,s in zip(kinds,(score,second,.05))]


class ConsensusTests(unittest.TestCase):
    def model(self,result='Unknown'):
        m=SubjectConsensusModels.__new__(SubjectConsensusModels)
        m.models=SimpleNamespace(resolve_track_type=lambda t:result,type_references=[])
        m.crop_checks=0;m.checkpoint=lambda:None
        return m

    def track(self):
        return dict(subject_type='Dog',type_embedding=None,type_views=[],
                    detail_type_votes=[dict(seconds=0,crop_hash='a',score=.60,margin=.25),
                                       dict(seconds=1,crop_hash='b',score=.58,margin=.20)])

    def test_dog_requires_repeated_distinct_supported_views(self):
        m=self.model();t=self.track();self.assertEqual(m.resolve_track_type(t),'Dog')
        t['detail_type_votes']=t['detail_type_votes'][:1];self.assertEqual(m.resolve_track_type(t),'Unknown')
        for field,value in (('seconds',.1),('crop_hash','a'),('score',.4),('margin',0)):
            t=self.track();t['detail_type_votes'][1][field]=value
            self.assertEqual(m.resolve_track_type(t),'Unknown')

    def test_supported_cat_and_conflicting_cat_are_preserved(self):
        self.assertEqual(self.model('Cat').resolve_track_type(self.track()),'Cat')
        t=self.track();t['type_embedding']=[1];t['type_embedding_model']='test'
        with patch('subject_detail_consensus.resolve_animal_type',return_value='Cat'):
            self.assertEqual(self.model().resolve_track_type(t),'Unknown')

    def test_vote_requires_detail_detector_and_independent_type_support(self):
        m=self.model();d=dict(detail_dog_supported=True)
        with patch('subject_detail_consensus.rank_types',return_value=ranks()):
            self.assertIsNotNone(m.detail_type_vote(d,[1],'model',1,1,b'crop'))
            self.assertIsNone(m.detail_type_vote({},[1],'model',1,1,b'crop'))
            self.assertIsNone(m.detail_type_vote(d,[1],'model',.1,1,b'crop'))
        for r in (ranks('Cat'),ranks(score=.49),ranks(second=.55),ranks(support=.54)):
            with patch('subject_detail_consensus.rank_types',return_value=r):
                self.assertIsNone(m.detail_type_vote(d,[1],'model',1,1,b'crop'))

    def test_strong_person_context_does_not_expand_a_weak_original_box(self):
        person=dict(subject_type='Person',confidence=.65,box=[0,0,100,100],detail_person_box=[10,10,50,80])
        self.assertTrue(in_person_region(dict(box=[20,20,40,40]),[person]))
        self.assertFalse(in_person_region(dict(box=[70,70,90,90]),[person]))

    def test_scene_keeps_original_person_crop_and_requires_overlapping_dog(self):
        m=self.model();m.raw_cache={None:[dict(subject_type='Dog',confidence=.85,box=[100,0,180,80])]}
        m.detail_people=[dict(subject_type='Person',confidence=.98,box=[0,0,80,100])]
        person=dict(subject_type='Person',confidence=.65,box=[5,5,75,95])
        dog=dict(subject_type='Dog',confidence=.6,box=[100,0,180,80])
        with patch('subject_detail_consensus.RefinedAnimalSceneModels.detect',return_value=[person,dog]):
            found=m.detect(np.zeros((200,200,3),np.uint8))
        self.assertEqual(len(found),2);self.assertEqual(person['box'],[5,5,75,95])
        self.assertEqual(person['detail_person_box'],[0,0,80,100]);self.assertTrue(dog['detail_dog_supported'])

    def test_new_person_is_marked_supplemental_and_tiny_proposals_are_ignored(self):
        m=self.model();m.raw_cache={};m.detail_people=[dict(subject_type='Person',confidence=.98,box=[0,0,80,100]),
                                                     dict(subject_type='Person',confidence=.99,box=[100,0,120,20])]
        with patch('subject_detail_consensus.RefinedAnimalSceneModels.detect',return_value=[]):
            found=m.detect(np.zeros((200,200,3),np.uint8))
        self.assertEqual(len(found),1);self.assertTrue(found[0]['detail_person_candidate'])

    def test_raw_person_cache_uses_only_high_confidence_full_scene_proposals(self):
        m=self.model();m.raw_cache={};m.checkpoint=lambda:None
        m.models=SimpleNamespace(torch=SimpleNamespace(inference_mode=nullcontext),cv2=cv2)
        result=dict(labels=[1,1,18],scores=[.94,.96,.4],
                    boxes=[np.array([0,0,80,100]),np.array([0,0,80,100]),np.array([100,0,180,80])])
        m.detail=SimpleNamespace(detector=lambda images:[result],transform=lambda image:image,version='detail-test')
        animals=m.raw_detail_detections(np.zeros((200,200,3),np.uint8))
        self.assertEqual([d['subject_type'] for d in animals],['Dog'])
        self.assertEqual(len(m.detail_people),1);self.assertEqual(m.detail_people[0]['confidence'],.96)

    def crop_model(self,score=.97,rival=.3):
        m=self.model();m.models.torch=SimpleNamespace(inference_mode=nullcontext)
        result=dict(labels=[18,1],scores=[score,rival],boxes=[np.array([0,0,96,96]),np.array([0,0,96,96])])
        m.detail=SimpleNamespace(detector=lambda images:[result],transform=lambda image:image)
        data=io.BytesIO();Image.new('RGB',(96,96),'gray').save(data,format='JPEG')
        t=self.track();t.update(type_embedding=[1],type_embedding_model='test',type_quality_score=1,crop_jpeg=data.getvalue(),detail_type_votes=[])
        return m,t

    def test_uncertain_dog_crop_requires_independent_high_confidence_model_and_references(self):
        m,t=self.crop_model()
        with patch('subject_detail_consensus.rank_types',return_value=ranks(score=.65,second=.4,support=.65)):
            self.assertTrue(m.confirm_uncertain_dog_crop(t));self.assertEqual(m.crop_checks,1)
        for score,rival in ((.94,.3),(.97,.8)):
            m,t=self.crop_model(score,rival)
            with patch('subject_detail_consensus.rank_types',return_value=ranks(score=.65,second=.4,support=.65)):
                self.assertFalse(m.confirm_uncertain_dog_crop(t))

    def test_crop_fallback_is_bounded_and_never_reclassifies_cat_proposals(self):
        m,t=self.crop_model();t['subject_type']='Cat'
        self.assertFalse(m.confirm_uncertain_dog_crop(t));self.assertEqual(m.crop_checks,0)
        t['subject_type']='Dog';m.crop_checks=4
        self.assertFalse(m.confirm_uncertain_dog_crop(t))

    def test_crop_fallback_rejects_weak_or_missing_type_support(self):
        for r in ([],ranks('Cat'),ranks(score=.59),ranks(second=.5),ranks(support=.59)):
            m,t=self.crop_model()
            with patch('subject_detail_consensus.rank_types',return_value=r):
                self.assertFalse(m.confirm_uncertain_dog_crop(t));self.assertEqual(m.crop_checks,0)

    def test_supplemental_person_requires_two_frames_original_single_frame_stays(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'clip.mp4';out=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),4,(96,96))
            for i in range(4):out.write(np.full((96,96,3),i*30,np.uint8))
            out.release()
            def model(count,supplemental):
                calls=[0]
                def detect(frame):
                    calls[0]+=1
                    return [dict(subject_type='Person',confidence=.98,box=[0,0,80,80],detail_person_candidate=supplemental)] if calls[0]<=count else []
                return SimpleNamespace(cv2=cv2,detect=detect,embedding=lambda *a:(None,None,1),detector_version='test')
            with patch('identity_worker.time.sleep'):
                self.assertEqual(analyze_video(path,model(1,True)),[])
                found=analyze_video(path,model(2,True));self.assertEqual(len(found),1)
                self.assertIsNone(found[0]['embedding']);self.assertEqual(found[0]['subject_type'],'Person')
                self.assertEqual(len(analyze_video(path,model(1,False))),1)

    def test_usable_lower_quality_frames_still_contribute_independent_type_votes(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'clip.mp4';out=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),1,(96,96))
            for i in range(4):out.write(np.full((96,96,3),i*30,np.uint8))
            out.release();qualities=iter((1.,.8,.6,.4))
            m=SimpleNamespace(cv2=cv2,detect=lambda frame:[dict(subject_type='Dog',confidence=.9,box=[0,0,80,80])],
                embedding=lambda *args:([1.,0.],'test',next(qualities)),detector_version='test',
                detail_type_vote=lambda d,v,m,q,s,j:dict(seconds=s,crop_hash=hashlib.sha256(j).hexdigest(),score=.65,margin=.25),
                resolve_track_type=lambda t:'Dog' if len(t.get('detail_type_votes',[]))>=2 else 'Unknown')
            with patch('identity_worker.time.sleep'):
                found=analyze_video(path,m)
            self.assertEqual(len(found),1);self.assertEqual(found[0]['subject_type'],'Dog')
            self.assertEqual(len(found[0]['detail_type_votes']),4)
            self.assertEqual(found[0]['quality_score'],1.)


if __name__=='__main__':unittest.main()
