import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
import numpy as np
import cv2
from cat_extra import CatSearchModels, cat_evidence
from subject_detail_consensus import SubjectConsensusModels
from identity_worker import sampled_frame_indices


class CatExtraTests(unittest.TestCase):
    def test_tail_coverage_preserves_regular_samples(self):
        frames,step=sampled_frame_indices(720,16)
        self.assertEqual(frames[:16],list(range(0,720,45)))
        self.assertEqual(frames[16:],[697,719])
        self.assertEqual(step,45)

    def test_short_clip_never_repeats_frames(self):
        self.assertEqual(sampled_frame_indices(3,16)[0],[0,1,2])
        self.assertEqual(sampled_frame_indices(17,16)[0][-1],16)

    def scene(self):
        m=Mock();m.cv2=cv2;m.type_references=[];m.pet_version='model'
        m.animal_type_embedding.return_value=([1.]+[0.]*383,'model',1.)
        detector=Mock();detector.detect.return_value=[dict(subject_type='Dog',confidence=.2,box=[50,50,90,100])]
        teacher=dict(subject_type='Cat',catalog_id=1,support_excluded=True,quality_score=1.,
                     model_version='model',vector=[1.]+[0.]*383)
        return CatSearchModels(m,cat_references=[teacher],proposal_detector=detector,catalog_id=1)

    @staticmethod
    def blank(scene,frame):
        scene.index+=1
        return []

    def test_unconfirmed_proposal_cannot_create_cat(self):
        s=self.scene()
        with patch.object(SubjectConsensusModels,'detect',self.blank),patch('cat_extra.cat_evidence',return_value=None):
            self.assertEqual(s.detect(np.zeros((200,200,3),np.uint8)),[])

    def test_moderate_proposal_requires_adjacent_matching_view(self):
        s=self.scene();frame=np.zeros((200,200,3),np.uint8)
        with patch.object(SubjectConsensusModels,'detect',self.blank),patch('cat_extra.cat_evidence',return_value=dict(strong=False)),patch('cat_extra.rank_types',return_value=[]),patch('cat_extra.foreground_supported',return_value=True):
            self.assertEqual(s.detect(frame),[])
            self.assertEqual(s.detect(frame)[0]['subject_type'],'Cat')

    def test_original_detection_and_crop_have_priority(self):
        s=self.scene();original=dict(subject_type='Cat',confidence=.8,box=[50,50,90,100])
        with patch.object(SubjectConsensusModels,'detect',return_value=[original]):
            self.assertEqual(s.detect(np.zeros((200,200,3),np.uint8)),[original])
        self.assertEqual(original['box'],[50,50,90,100])

    def test_augmented_cat_cannot_replace_supported_dog(self):
        s=self.scene();track=dict(subject_type='Dog')
        with patch.object(SubjectConsensusModels,'resolve_track_type',return_value='Dog'),patch('cat_extra.resolve_track_type') as augmented:
            self.assertEqual(s.resolve_track_type(track),'Dog')
            augmented.assert_not_called()

    def test_strong_conflicting_person_evidence_stays_unknown(self):
        s=self.scene();track=dict(subject_type='Cat',embedding=[1.]+[0.]*383,embedding_model='model',quality_score=.8)
        ranks=[dict(subject_type='Person',score=.9,independent_similarities=[.8,.7]),dict(subject_type='Cat',score=.1),dict(subject_type='Dog',score=0.)]
        with patch.object(SubjectConsensusModels,'resolve_track_type',return_value='Unknown'),patch('cat_extra.rank_types',return_value=ranks),patch('cat_extra.resolve_track_type') as augmented:
            self.assertEqual(s.resolve_track_type(track),'Unknown')
            augmented.assert_not_called()

    def test_augmented_dog_cannot_change_unknown_type(self):
        s=self.scene();track=dict(subject_type='Cat',embedding=[1.],embedding_model='model')
        with patch.object(SubjectConsensusModels,'resolve_track_type',return_value='Unknown'),patch('cat_extra.rank_types',return_value=[]),patch('cat_extra.resolve_track_type',return_value='Dog'):
            self.assertEqual(s.resolve_track_type(track),'Unknown')

    def test_last_frame_activity_is_analyzed(self):
        import tempfile
        from pathlib import Path
        from identity_worker import analyze_video
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'late-cat.mp4'
            writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),10,(64,64))
            for i in range(100):writer.write(np.full((64,64,3),255 if i==99 else 0,np.uint8))
            writer.release()
            m=SimpleNamespace(cv2=cv2,detector_version='test',
                detect=lambda f:[dict(subject_type='Cat',confidence=.9,box=[5,5,55,55])] if f.mean()>200 else [],
                embedding=lambda c,k:([1.]+[0.]*383,'test',.8))
            found=analyze_video(path,m)
            self.assertEqual(len(found),1)
            self.assertEqual(found[0]['subject_type'],'Cat')
            self.assertAlmostEqual(found[0]['first_seen_seconds'],9.9,places=1)

    def test_teaching_reference_does_not_supply_independent_support(self):
        from test_subject_type_check import gallery,vector,MODEL
        from subject_type_check import rank_types
        refs=[r for r in gallery() if r['subject_type']!='Cat' or r['catalog_id']==100]
        teacher=dict(subject_type='Cat',model_version=MODEL,quality_score=1.,vector=vector(1),
                     catalog_id=999,crop_hash='teacher',support_excluded=True)
        ranks=rank_types(refs+[teacher],vector(1),MODEL)
        cat=next(r for r in ranks if r['subject_type']=='Cat')
        self.assertEqual(len(cat['independent_similarities']),1)

    def test_background_veto_rejects_empty_texture(self):
        from cat_foreground import foreground_supported
        from test_subject_type_check import gallery,vector
        self.assertTrue(foreground_supported(gallery(),[vector(2)]*4,vector(1)))
        self.assertFalse(foreground_supported(gallery(),[vector(2)]*4,vector(2)))
        self.assertFalse(foreground_supported(gallery(),[],vector(1)))

    def test_human_example_only_matches_its_own_clip_and_model(self):
        s=self.scene();v=[1.]+[0.]*383
        self.assertTrue(s._teacher_matches(v,'model'))
        self.assertFalse(s._teacher_matches(v,'other-model'))
        other=CatSearchModels(s.models,cat_references=s.cat_references,proposal_detector=s.proposal_detector,
                              background_vectors=[],catalog_id=2)
        self.assertFalse(other._teacher_matches(v,'model'))

    def test_known_native_cat_does_not_expand_into_mixed_context(self):
        s=self.scene();frame=np.zeros((200,200,3),np.uint8);box=[50,50,90,100];vector=[1.]+[0.]*383
        with patch.object(s,'_trusted_native',return_value=True):
            self.assertEqual(s.choose_animal_type_features(frame,box,'Cat',vector,'model',1.),(vector,'model',1.,box))
        s.models.choose_animal_type_features.assert_not_called()

    def test_global_correction_requires_two_independent_cat_model_views(self):
        s=self.scene();track=dict(subject_type='Cat',embedding=[1.]+[0.]*383,embedding_model='model',quality_score=.8)
        def vote(t,h):return dict(seconds=t,crop_hash=h,scene_score=.8,score=.8,margin=.4,embedding_model='model',quality_score=.8)
        with patch.object(SubjectConsensusModels,'resolve_track_type',return_value='Unknown'),patch.object(s,'_species_veto',return_value=False),patch.object(s,'_known_cat',return_value=False),patch('cat_extra.resolve_track_type',return_value='Cat'):
            for votes in ([],[vote(0,'a')],[vote(0,'a'),vote(1,'a')],[vote(0,'a'),vote(.4,'b')]):
                self.assertEqual(s.resolve_track_type(dict(track,cat_type_votes=votes)),'Unknown')
            self.assertEqual(s.resolve_track_type(dict(track,cat_type_votes=[vote(0,'a'),vote(1,'b')])),'Cat')

    def test_cat_model_vote_does_not_override_dog_favoring_native_reference(self):
        s=self.scene();ranks=[dict(subject_type='Dog',score=.65),dict(subject_type='Cat',score=.3),dict(subject_type='Person',score=.05)]
        with patch('cat_extra.cat_evidence',return_value=dict(strong=True,score=.9,margin=.5)),patch.object(s,'_species_veto',return_value=False),patch('cat_extra.rank_types',return_value=ranks),patch('cat_extra.foreground_supported',return_value=True):
            self.assertIsNone(s.cat_type_vote(dict(cat_scene_score=.9),[1.]+[0.]*383,'model',1.,0.,b'image'))

    def test_reviewed_cat_continuation_requires_adjacent_overlap_and_native_cat_support(self):
        s=self.scene();v=[1.]+[0.]*383;box=[50,50,90,100]
        s.reviewed_cat_anchors=[(3,box,v,'model')]
        with patch('cat_extra.cat_evidence',return_value=dict(strong=False)),patch('cat_extra.rank_types',return_value=[dict(subject_type='Cat')]),patch.object(s,'_species_veto',return_value=False),patch('cat_extra.foreground_supported',return_value=True):
            self.assertTrue(s._continued_cat(box,v,'model',1.,4))
            self.assertFalse(s._continued_cat(box,v,'model',1.,5))
            self.assertFalse(s._continued_cat([120,120,160,170],v,'model',1.,4))
            self.assertFalse(s._continued_cat(box,[0.,1.]+[0.]*382,'model',1.,4))
            self.assertFalse(s._continued_cat(box,v,'other',1.,4))
            with patch('cat_extra.rank_types',return_value=[dict(subject_type='Dog')]):
                self.assertFalse(s._continued_cat(box,v,'model',1.,4))

    def test_continuation_vote_requires_the_approved_native_features(self):
        s=self.scene();v=[1.]+[0.]*383;s.current_cat_continuations=[(v,'model')]
        with patch.object(s,'_known_cat',return_value=False),patch('cat_extra.cat_evidence',return_value=dict(strong=False,score=.7,margin=.4)),patch.object(s,'_species_veto',return_value=False),patch('cat_extra.rank_types',return_value=[dict(subject_type='Cat')]),patch('cat_extra.foreground_supported',return_value=True):
            vote=s.cat_type_vote({},v,'model',1.,2.,b'native')
            self.assertTrue(vote['reviewed_cat_supported'])
            self.assertIsNone(s.cat_type_vote({},[0.,1.]+[0.]*382,'model',1.,2.,b'other'))

    def test_final_reviewed_track_needs_a_valid_second_native_view(self):
        import hashlib
        s=self.scene();a=[1.]+[0.]*383;b=[0.,1.]+[0.]*382
        def view(t,v,j):return dict(seconds=t,embedding=v,embedding_model='model',quality_score=1.,crop_jpeg=j)
        track=dict(subject_type='Cat',type_embedding=a,type_embedding_model='model',type_quality_score=1.,
                   type_views=[view(0.,a,b'first'),view(2.,b,b'second')])
        vote=dict(seconds=2.,crop_hash=hashlib.sha256(b'second').hexdigest(),embedding_model='model',quality_score=1.,score=.7,margin=.4,reviewed_cat_supported=True)
        with patch.object(SubjectConsensusModels,'resolve_track_type',return_value='Unknown'),patch.object(s,'_species_veto',return_value=False),patch.object(s,'_known_cat',side_effect=lambda v,m,q:v==a),patch('cat_extra.resolve_track_type',side_effect=lambda refs,t:'Cat' if len(t['type_views'])==2 else 'Unknown'):
            self.assertEqual(s.resolve_track_type(track),'Unknown')
            self.assertEqual(s.resolve_track_type(dict(track,cat_type_votes=[dict(vote,crop_hash='wrong')])),'Unknown')
            self.assertEqual(s.resolve_track_type(dict(track,cat_type_votes=[vote])),'Cat')


if __name__=='__main__':unittest.main()
