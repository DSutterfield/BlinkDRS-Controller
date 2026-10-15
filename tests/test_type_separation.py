import unittest
from unittest.mock import patch
from subject_type_check import resolve_track_type
from test_subject_type_check import gallery,vector,MODEL

class SeparationTests(unittest.TestCase):
    def test_small_cat_type_does_not_require_identity_features(self):
        track=dict(subject_type='Cat',embedding=None,embedding_model=None,quality_score=0,
                   type_embedding=vector(1),type_embedding_model=MODEL,type_quality_score=1,type_views=[])
        self.assertEqual(resolve_track_type(gallery(),track),'Cat')
        self.assertIsNone(track['embedding'])
        self.assertEqual(track['quality_score'],0)

    def test_small_dog_type_does_not_require_identity_features(self):
        track=dict(subject_type='Dog',embedding=None,embedding_model=None,quality_score=0,
                   type_embedding=vector(2),type_embedding_model=MODEL,type_quality_score=1,type_views=[])
        self.assertEqual(resolve_track_type(gallery(),track),'Dog')

    def test_explicit_unusable_type_cannot_fall_back_to_identity(self):
        track=dict(subject_type='Cat',embedding=vector(1),embedding_model=MODEL,quality_score=1,
                   type_embedding=None,type_embedding_model=None,type_quality_score=0,type_views=[])
        self.assertEqual(resolve_track_type(gallery(),track),'Unknown')

    def test_person_path_ignores_animal_type_features(self):
        track=dict(subject_type='Person',type_embedding=vector(1),type_quality_score=0)
        with patch('subject_type_check.rank_types',side_effect=AssertionError('Person path changed')):
            self.assertEqual(resolve_track_type(gallery(),track),'Person')

    def test_legacy_track_behavior_preserved(self):
        track=dict(subject_type='Cat',embedding=vector(1),embedding_model=MODEL,quality_score=1,views=[])
        self.assertEqual(resolve_track_type(gallery(),track),'Cat')

if __name__=='__main__':unittest.main()
