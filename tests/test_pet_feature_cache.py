import unittest
from types import SimpleNamespace
import numpy as np
import pet_feature_cache as cache
class CacheTests(unittest.TestCase):
 def setUp(self):self.owner=SimpleNamespace(pet_version='v1',pet_model=object(),processor=object());self.calls=0
 def compute(self):self.calls+=1;return [1.,2.]
 def call(self,c):return cache.pet_vector(self.owner,c,self.compute)
 def test_exact_pixels_and_defensive_copy(self):
  c=np.zeros((48,48,3),np.uint8);a=self.call(c);a[0]=99;self.assertEqual(self.call(c.copy()),[1.,2.]);self.assertEqual(self.calls,1)
 def test_changed_pixels_miss(self):
  c=np.zeros((48,48,3),np.uint8);self.call(c);c[0,0,0]=1;self.call(c);self.assertEqual(self.calls,2)
 def test_model_version_and_object_miss(self):
  c=np.zeros((48,48,3),np.uint8);self.call(c);self.owner.pet_version='v2';self.call(c);self.owner.pet_model=object();self.call(c);self.assertEqual(self.calls,3)
 def test_shape_and_dtype_miss(self):
  c=np.zeros((48,48,3),np.uint8);self.call(c);self.call(c.reshape(24,96,3));self.call(c.astype(np.int8));self.assertEqual(self.calls,3)
 def test_limits(self):
  for i in range(70):self.call(np.full((48,48,3),i,np.uint8))
  self.assertLessEqual(len(self.owner._exact_pet_vectors),cache.MAX_ENTRIES);self.assertLessEqual(sum(len(v[0]) for v in self.owner._exact_pet_vectors.values()),cache.MAX_PIXEL_BYTES)
 def test_oversized_not_retained(self):
  c=np.zeros((1800,1800,3),np.uint8);self.call(c);self.assertEqual(len(self.owner._exact_pet_vectors),0)
if __name__=='__main__':unittest.main()
