import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from identity_motion_zones import MotionZones,load_camera_zones,permits,search_view


def zones(rows,cols,cells):return MotionZones(dict(basic_rows=rows,basic_columns=cols,sub_rows=1,sub_columns=1,cells=cells))


class ZoneDiscoveryTests(unittest.TestCase):
    def test_asymmetric_row_major_and_boundaries(self):
        z=zones(2,3,[0,1,0,1,0,1]);shape=(200,300,3)
        for r in range(2):
            for c in range(3):self.assertEqual(z.allows([c*100,r*100,c*100+99,r*100+99],shape),bool(z.cells[r*3+c]))
        self.assertTrue(z.allows([90,0,110,100],shape)) # center on col 1 boundary
        self.assertFalse(z.allows([0,0,0,100],shape))
        self.assertFalse(z.allows([float('nan'),0,20,20],shape))
        self.assertTrue(z.allows([-100,100,20,200],shape)) # clip before locating center
        self.assertTrue(z.allows([250,150,350,250],shape))

    def test_crossing_centers_and_search_tiles(self):
        z=zones(1,2,[0,1]);shape=(100,200,3)
        self.assertFalse(z.allows([40,20,140,80],shape))
        self.assertTrue(z.allows([60,20,160,80],shape))
        self.assertFalse(z.intersects([0,0,100,100],shape))
        self.assertTrue(z.intersects([99,0,101,100],shape))
        self.assertFalse(zones(1,1,[0]).intersects([0,0,200,100],shape))

    def test_bad_shapes_and_masks_rejected(self):
        for cells in ([True],[2],[],[0,1]):
            with self.assertRaises(ValueError):zones(1,1,cells)
        with self.assertRaises(ValueError):zones(0,1,[1])

    def test_camera_mapping_and_no_unmasked_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'c.sqlite'
            with sqlite3.connect(db) as c:
                c.executescript('CREATE TABLE clips(id,device_id);CREATE TABLE devices(id,system_id,blink_device_id);CREATE TABLE systems(id,blink_system_id);INSERT INTO clips VALUES(7,3);INSERT INTO devices VALUES(3,2,987);INSERT INTO systems VALUES(2,654);')
            from io import BytesIO
            with patch('identity_motion_zones.urlopen',return_value=BytesIO(json.dumps(dict(basic_rows=1,basic_columns=2,sub_rows=1,sub_columns=1,cells=[0,1])).encode())) as get:
                z=load_camera_zones(db,7,'http://localhost:8000')
                self.assertEqual(z.cells,(0,1));self.assertIn('/987/motion-zones?system_id=654',get.call_args.args[0])
            with patch('identity_motion_zones.urlopen',side_effect=OSError('offline')):
                self.assertEqual(load_camera_zones(db,7,'http://localhost:8000').settings_source,'cached')
            from identity_motion_zones import cache_path
            cache_path(db,987,654).unlink()
            with patch('identity_motion_zones.urlopen',side_effect=OSError('offline')):
                with self.assertRaisesRegex(ValueError,'retry analysis'):load_camera_zones(db,7,'http://localhost:8000')
            with self.assertRaisesRegex(ValueError,'no camera/system mapping'):load_camera_zones(db,8,'http://localhost:8000')

    def test_excluded_proposal_never_reaches_type_embedding(self):
        import numpy as np
        from animal_detail_models import DetailedAnimalSceneModels
        from types import SimpleNamespace
        from unittest.mock import Mock
        model=Mock();model.motion_zones=zones(1,2,[0,1]);s=DetailedAnimalSceneModels(model,detail=object())
        self.assertEqual(s.type_evidence(np.zeros((100,200,3),np.uint8),dict(box=[0,0,80,80]),[]),(None,None))
        model.animal_type_embedding.assert_not_called()

    def test_worker_filters_before_crop_embedding_and_all_off_skips_detector(self):
        import numpy as np
        from identity_worker import analyze_video
        from types import SimpleNamespace
        from unittest.mock import Mock
        capture=Mock();capture.isOpened.return_value=True;capture.get.side_effect=[1.,1.];capture.read.return_value=(True,np.zeros((100,200,3),np.uint8))
        m=Mock();m.cv2=SimpleNamespace(VideoCapture=Mock(return_value=capture),CAP_PROP_FPS=1,CAP_PROP_FRAME_COUNT=2,CAP_PROP_POS_FRAMES=3)
        m.motion_zones=zones(1,2,[0,1]);m.detect.return_value=[dict(subject_type='Cat',box=[0,0,80,80])]
        m.resolve_track_type=None
        with patch('identity_worker.time.sleep'):self.assertEqual(analyze_video('fake',m),[])
        m.embedding.assert_not_called()
        capture.get.side_effect=[1.,1.];m.motion_zones=zones(1,1,[0]);m.detect.reset_mock()
        with patch('identity_worker.time.sleep'):self.assertEqual(analyze_video('fake',m),[])
        m.detect.assert_not_called()

if __name__=='__main__':unittest.main()
