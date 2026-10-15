import unittest,json,sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import test_identities
from identity_store import connect,create_identity,assign_identity,get_clip_identities,remove_detection
from identity_analysis import publish_detections,match_identity,learn_confirmed,pending_type_corrections
from subject_type_learning import pending,save_reference,load_references
from evaluation_holdout import blocked,excluded_ids,list_holdouts,video_path
from cat_type_variants import load_variants
from archive_maintenance import cleanup_expired_clips


class HoldoutTests(unittest.TestCase):
    setUp=test_identities.IdentityTests.setUp

    def evidence(self,cid=1,kind='Dog'):
        publish_detections(self.db,cid,str(cid),[dict(key='view',subject_type=kind,confidence=.8,first_seen_seconds=0,last_seen_seconds=1,detector_model='model',crop_jpeg=b'native-crop',embedding=[1,0],embedding_model='model',quality_score=.8)])
        return get_clip_identities(self.db,cid)[0]['detection_id']

    def hold(self):
        copy=self.db.parent/'bai-evaluation/test/1.mp4';copy.parent.mkdir(parents=True,exist_ok=True);copy.write_bytes(b'preserved recording')
        with connect(self.db) as c:
            c.execute("INSERT OR IGNORE INTO devices(id,blink_device_id,name) VALUES(1,'cam','Yard')")
            c.execute("UPDATE clips SET blink_media_id='hold-1',device_id=1,captured_at='2026-10-03T00:00:00Z' WHERE id=1")
            c.execute('INSERT INTO evaluation_holdout_clips(blink_media_id,catalog_id,dataset_id,device_id,camera,captured_at,video_sha256,copy_path) VALUES(?,?,?,?,?,?,?,?)',('hold-1',1,'test',1,'Yard','2026-10-03T00:00:00Z','hash',str(copy)))
        return copy

    def test_confirmed_answer_does_not_teach_identity_or_type(self):
        did=self.evidence();self.hold();iid=create_identity(self.db,'Test Dog','Dog')['identity_id'];assign_identity(self.db,1,iid,'Dog',did)
        self.assertEqual(pending(self.db,'model'),[])
        with connect(self.db) as c:
            self.assertEqual(c.execute('select count(*) from identity_samples').fetchone()[0],0)
            self.assertEqual(c.execute('select subject_type,name,crop_jpeg,active from evaluation_holdout_labels').fetchone()[:],('Dog','Test Dog',b'native-crop',1))
        save_reference(self.db,dict(detection_id=did,subject_type='Dog',crop_jpeg=b'native-crop'),[1,0],'model',.8)
        self.assertEqual(load_references(self.db,'model',2),[])

    def test_write_guard_blocks_direct_and_inflight_training(self):
        did=self.evidence();self.hold();iid=create_identity(self.db,'Blocked Dog','Dog')['identity_id']
        with connect(self.db) as c:
            c.execute('INSERT INTO identity_samples(identity_id,detection_id,embedding,model_version,quality_score) VALUES(?,?,?,?,?)',(iid,did,'[1,0]','model',.8))
            c.execute("INSERT INTO subject_type_samples VALUES(?,'Dog','[1,0]','model',.8,'hash')",(did,))
            self.assertEqual(c.execute('select count(*) from subject_type_samples').fetchone()[0],0)
            self.assertEqual(c.execute('select count(*) from identity_samples').fetchone()[0],0)

    def test_existing_references_are_excluded_without_deleting_labels(self):
        did=self.evidence();iid=create_identity(self.db,'Dog','Dog')['identity_id'];assign_identity(self.db,1,iid,'Dog',did)
        save_reference(self.db,dict(detection_id=did,subject_type='Dog',crop_jpeg=b'native-crop'),[1,0],'model',.8)
        self.hold()
        self.assertEqual(load_references(self.db,'model',2),[])
        with connect(self.db) as c:self.assertIsNone(match_identity(c,'Dog','model',[1,0],.9,.05)[0]);self.assertEqual(c.execute('select confirmed from clip_detections where detection_id=?',(did,)).fetchone()[0],1)

    def test_nearby_same_camera_is_excluded_but_other_cases_learn(self):
        self.hold()
        with connect(self.db) as c:
            for cid,dev,when in [(3,1,'00:02:00'),(4,1,'00:06:00'),(5,None,'00:02:00')]:
                c.execute("INSERT INTO clips(id,filename,video_path,local_present,blink_media_id,device_id,captured_at) VALUES(?,?,?,1,?,?,?)",(cid,f'{cid}.mp4',f'{cid}.mp4',str(cid),dev,'2026-10-03T'+when+'Z'))
            self.assertEqual(excluded_ids(c),{1,3})
        did=self.evidence(2);iid=create_identity(self.db,'Normal Dog','Dog')['identity_id'];assign_identity(self.db,2,iid,'Dog',did)
        with connect(self.db) as c:self.assertEqual(c.execute('select count(*) from identity_samples').fetchone()[0],1)

    def test_answers_and_recording_survive_real_retention_cleanup(self):
        import os,time
        root=self.db.parent;clips=root/'clips';clips.mkdir();(root/'clip_thumbs').mkdir();(root/'playback_cache').mkdir()
        original=clips/'1.mp4';original.write_bytes(b'normal recording');(clips/'2.mp4').write_bytes(b'normal recording')
        did=self.evidence();copy=self.hold();assign_identity(self.db,1,None,'Dog',did)
        os.utime(original,(time.time()-40*86400,)*2)
        cleanup_expired_clips(self.db,clips,30)
        self.assertFalse(original.exists());self.assertTrue(copy.exists())
        rows=list_holdouts(self.db);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['reviewed_answers'],1);self.assertTrue(rows[0]['copy_available'])

    def test_reused_catalog_id_does_not_inherit_old_membership(self):
        self.hold()
        with connect(self.db) as c:
            c.execute('delete from clips where id=1')
            c.execute("INSERT INTO clips(id,filename,video_path,local_present,blink_media_id,captured_at) VALUES(1,'new.mp4','new.mp4',1,'different','2026-10-15')")
            self.assertFalse(blocked(c,1))

    def test_manual_correction_is_an_answer_not_pending_training(self):
        did=self.evidence();self.hold();assign_identity(self.db,1,None,'Cat',did)
        self.assertEqual(pending_type_corrections(self.db),[])
        with connect(self.db) as c:self.assertEqual(c.execute('select subject_type from evaluation_holdout_labels where active=1').fetchone()[0],'Cat')
        remove_detection(self.db,1,did)
        with connect(self.db) as c:self.assertEqual(c.execute('select active from evaluation_holdout_labels').fetchone()[0],0)

    def test_cat_variants_and_same_clip_teacher_are_blocked(self):
        did=self.evidence(kind='Cat');self.hold();assign_identity(self.db,1,None,'Cat',did)
        with connect(self.db) as c:c.execute("update clip_detections set model_version='manual-crop:v1' where detection_id=?",(did,))
        models=SimpleNamespace(pet_version='model',animal_type_embedding=Mock())
        refs=[dict(catalog_id=1,subject_type='Cat',detection_id=did)]
        self.assertEqual(load_variants(self.db,models,refs,cache_path=self.db.parent/'cache.db',teaching_clip=1,review_manifest=False),[])
        models.animal_type_embedding.assert_not_called()

    def test_video_path_rejects_unrelated_files(self):
        copy=self.hold();self.assertEqual(video_path(self.db,'hold-1'),copy)
        with connect(self.db) as c:c.execute('update evaluation_holdout_clips set copy_path=?',(str(self.db),))
        with self.assertRaises(ValueError):video_path(self.db,'hold-1')

    def test_confirmed_unknown_dog_is_not_a_veto_example(self):
        from dog_identity import _unknown_veto
        did=self.evidence();assign_identity(self.db,1,None,'Dog',did)
        with connect(self.db) as c:self.assertTrue(_unknown_veto(c,'model',[1,0],2,1.))
        self.hold()
        with connect(self.db) as c:self.assertFalse(_unknown_veto(c,'model',[1,0],2,1.))

    def test_household_ranker_drops_heldout_samples(self):
        from identity_candidates import rank_candidates
        model='dinov2-small-cls-v1:holdout-test';a=create_identity(self.db,'A','Dog')['identity_id'];b=create_identity(self.db,'B','Dog')['identity_id']
        with connect(self.db) as c:
            for n in range(6):
                iid=a if n%2==0 else b;v=[1.,0.] if iid==a else [0.,1.];v+= [0.]*382
                did=c.execute("insert into clip_detections(catalog_id,identity_id,subject_type,confirmed) values(?,?,'Dog',1)",(1 if n==0 else 2,iid)).lastrowid
                c.execute("insert into identity_evidence(detection_id,catalog_id,detection_key,subject_type,crop_jpeg,embedding,embedding_model,quality_score) values(?,?,?,'Dog',?,?,?,1)",(did,1 if n==0 else 2,str(n),b'image',json.dumps(v),model));learn_confirmed(c,did)
            self.assertEqual(len(rank_candidates(c,'Dog',model,[1.,0.]+[0.]*382,99)),2)
        self.hold()
        with connect(self.db) as c:self.assertEqual(rank_candidates(c,'Dog',model,[1.,0.]+[0.]*382,99),[])

    def test_static_cat_teaching_image_is_not_even_opened(self):
        import hashlib
        from unittest.mock import patch
        did=self.evidence(2,kind='Cat');self.hold();data=b'native-crop'
        refs=[dict(detection_id=did,catalog_id=2,subject_type='Cat',vector=[1.]+[0.]*383,model_version='model',quality_score=1.,crop_hash=hashlib.sha256(data).hexdigest())]
        manifest=self.db.parent/'review.json';manifest.write_text(json.dumps(dict(examples=[dict(subject_type='Cat',catalog_id=1,image_file='must-not-be-opened.jpg')])));digest=hashlib.sha256(manifest.read_bytes()).hexdigest()
        models=SimpleNamespace(pet_version='model',animal_type_embedding=Mock(),cv2=SimpleNamespace(imdecode=lambda *a:None,IMREAD_COLOR=1))
        with patch('cat_type_variants.REVIEW_MANIFEST_SHA256',digest):result=load_variants(self.db,models,refs,cache_path=self.db.parent/'static-cache.db',teaching_clip=1,review_manifest=manifest)
        self.assertEqual(result,refs);models.animal_type_embedding.assert_not_called()
