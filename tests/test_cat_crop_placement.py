import unittest
from pathlib import Path
from types import SimpleNamespace
import cat_crop_placement,identity_worker,animal_scene_search

class RollbackTests(unittest.TestCase):
    def test_refinement_disabled_without_reading_or_embedding(self):
        tracks=[dict(subject_type='Cat',crop_jpeg=b'confirmed-pixels',embedding=[1.,0.],views=[])]
        self.assertIs(cat_crop_placement.refine('unused',tracks,SimpleNamespace(),{0:object()},15),tracks)
        self.assertEqual(tracks[0]['crop_jpeg'],b'confirmed-pixels')
    def test_original_worker_and_scene_paths_restored(self):
        self.assertNotIn('cat_crop_placement',Path(identity_worker.__file__).read_text())
        self.assertNotIn('full_scene_detections',Path(animal_scene_search.__file__).read_text())

if __name__=='__main__':unittest.main()
