import unittest
from water_sequence import link_sequences

def obs(i,**kw):
    r=dict(id=i,camera='Water Bowl',clip=i,time=i*20.,quality=1.,model='test',hash=str(i),species='Cat',box=[.4,.4,.5,.5],vector=[1.,0.]);r.update(kw);return r

class SequenceTests(unittest.TestCase):
    def test_link_preserves_unknown(self):
        rows=[obs(1),obs(2,species='Unknown')];links=link_sequences(rows)
        self.assertEqual(len(links),1);self.assertEqual(rows[1]['species'],'Unknown')
    def test_conflicting_species(self):self.assertEqual(link_sequences([obs(1),obs(2,species='Dog')]),[])
    def test_gap(self):self.assertEqual(link_sequences([obs(1),obs(2,time=100.)]),[])
    def test_camera(self):self.assertEqual(link_sequences([obs(1),obs(2,camera='Other')]),[])
    def test_model(self):self.assertEqual(link_sequences([obs(1),obs(2,model='other')]),[])
    def test_duplicate(self):self.assertEqual(link_sequences([obs(1),obs(2,hash='1')]),[])
    def test_ambiguous(self):self.assertEqual(link_sequences([obs(1),obs(2),obs(3,clip=2,time=40.)]),[])
    def test_jump(self):self.assertEqual(link_sequences([obs(1),obs(2,box=[.8,.8,.9,.9])]),[])
    def test_bad_feature(self):self.assertEqual(link_sequences([obs(1),obs(2,vector=[float('nan'),0.])]),[])
    def test_quality(self):self.assertEqual(link_sequences([obs(1),obs(2,quality=.1)]),[])
    def test_same_clip(self):self.assertEqual(link_sequences([obs(1),obs(2,clip=1)]),[])
    def test_person(self):self.assertEqual(link_sequences([obs(1),obs(2,species='Person')]),[])
    def test_no_name_copy(self):
        rows=[obs(1,name='Known'),obs(2,name=None)];links=link_sequences(rows)
        self.assertEqual(len(links),1);self.assertIsNone(rows[1]['name']);self.assertNotIn('name',links[0])

if __name__=='__main__':unittest.main()
