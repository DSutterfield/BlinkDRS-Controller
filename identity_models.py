"""Offline CPU inference. Downloads occur only in the explicit preparation command."""
import hashlib
from pathlib import Path


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def prepare_models(folder):
    import torch
    from torchvision.models.detection import SSDLite320_MobileNet_V3_Large_Weights
    from transformers import AutoImageProcessor, AutoModel
    from huggingface_hub import hf_hub_download
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    weights = SSDLite320_MobileNet_V3_Large_Weights.COCO_V1
    state = torch.hub.load_state_dict_from_url(weights.url, model_dir=str(folder), check_hash=True, weights_only=True)
    del state
    processor = AutoImageProcessor.from_pretrained('facebook/dinov2-small', trust_remote_code=False, use_fast=False)
    model = AutoModel.from_pretrained('facebook/dinov2-small', trust_remote_code=False, use_safetensors=True)
    processor.save_pretrained(folder/'dinov2-small')
    model.save_pretrained(folder/'dinov2-small', safe_serialization=True)
    for repo, name in (
        ('opencv/face_detection_yunet', 'face_detection_yunet_2023mar.onnx'),
        ('opencv/face_recognition_sface', 'face_recognition_sface_2021dec.onnx')):
        import shutil
        downloaded = hf_hub_download(repo_id=repo, filename=name)
        shutil.copyfile(downloaded, folder/name)


class LocalModels:
    def __init__(self, folder):
        import torch
        import cv2
        from torchvision.models.detection import ssdlite320_mobilenet_v3_large, SSDLite320_MobileNet_V3_Large_Weights
        from transformers import AutoImageProcessor, AutoModel
        self.torch, self.cv2 = torch, cv2
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
        cv2.setNumThreads(1)
        folder = Path(folder)
        detector_file = folder/'ssdlite320_mobilenet_v3_large_coco-a79551df.pth'
        self.detector = ssdlite320_mobilenet_v3_large(weights=None, weights_backbone=None).eval()
        self.detector.load_state_dict(torch.load(detector_file, map_location='cpu', weights_only=True))
        self.detector_transform = SSDLite320_MobileNet_V3_Large_Weights.COCO_V1.transforms()
        self.detector_version = 'ssdlite-coco:'+file_hash(detector_file)
        self.processor = AutoImageProcessor.from_pretrained(folder/'dinov2-small', local_files_only=True, trust_remote_code=False, use_fast=False)
        self.pet_model = AutoModel.from_pretrained(folder/'dinov2-small', local_files_only=True, trust_remote_code=False, use_safetensors=True).eval()
        self.pet_version = 'dinov2-small-cls-v1:'+file_hash(folder/'dinov2-small/model.safetensors')
        face_file = folder/'face_detection_yunet_2023mar.onnx'
        recognition_file = folder/'face_recognition_sface_2021dec.onnx'
        self.face_detector = cv2.FaceDetectorYN.create(str(face_file), '', (320,320), .9, .3, 100)
        self.face_recognizer = cv2.FaceRecognizerSF.create(str(recognition_file), '')
        self.face_version = 'sface-align-v1:'+file_hash(recognition_file)

    def detect(self, bgr):
        from PIL import Image
        from identity_worker import overlap
        height,width = bgr.shape[:2]
        views = [(bgr,0,0)]
        # Overlapping views retain detail for smaller pets in wide camera scenes.
        if max(height,width) >= 640:
            crop_w,crop_h = int(width*.6),int(height*.6)
            for y in (0,height-crop_h):
                for x in (0,width-crop_w):
                    views.append((bgr[y:y+crop_h,x:x+crop_w],x,y))
        candidates = []
        kinds = {1:'Person',3:'Vehicle',4:'Vehicle',6:'Vehicle',8:'Vehicle',17:'Cat',18:'Dog'}
        for view,x,y in views:
            rgb = Image.fromarray(self.cv2.cvtColor(view,self.cv2.COLOR_BGR2RGB))
            with self.torch.inference_mode():
                result = self.detector([self.detector_transform(rgb)])[0]
            for label,score,box in zip(result['labels'],result['scores'],result['boxes']):
                label,score = int(label),float(score)
                if label not in kinds or score < (.85 if kinds[label]=='Vehicle' else .65 if label==1 else .55):
                    continue
                if len(views)>1 and (x or y or view.shape != bgr.shape) and kinds[label] in ('Person','Vehicle'):
                    continue
                box=box.tolist()
                candidates.append({'subject_type':kinds[label],'confidence':score,'full_view':view.shape==bgr.shape,
                                   'box':[box[0]+x,box[1]+y,box[2]+x,box[3]+y]})
        def containment(a,b):
            intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
            area=min((a[2]-a[0])*(a[3]-a[1]),(b[2]-b[0])*(b[3]-b[1]))
            return intersection/area if area>0 else 0
        kept=[]
        for candidate in sorted(candidates,key=lambda item:(item['full_view'],item['confidence']),reverse=True):
            if not any(other['subject_type']==candidate['subject_type'] and (overlap(other['box'],candidate['box'])>.35 or containment(other['box'],candidate['box'])>.7) for other in kept):
                kept.append(candidate)
            if len(kept)>=12: break
        for item in kept: item.pop("full_view")
        return kept

    def embedding(self, crop, kind):
        if kind == 'Vehicle':
            return None, None, 0  # Category only: no named vehicle matching.
        from PIL import Image
        if min(crop.shape[:2]) < 48:
            return None, None, 0
        gray = self.cv2.cvtColor(crop, self.cv2.COLOR_BGR2GRAY)
        quality = min(1., float(self.cv2.Laplacian(gray, self.cv2.CV_64F).var()) / 100.)
        if quality < .2:
            return None, None, quality
        if kind == 'Person':
            self.face_detector.setInputSize((crop.shape[1],crop.shape[0]))
            _, faces = self.face_detector.detect(crop)
            if faces is None or len(faces) != 1 or min(faces[0][2:4]) < 32:
                return None, None, quality
            aligned = self.face_recognizer.alignCrop(crop, faces[0])
            vector = self.face_recognizer.feature(aligned).reshape(-1).tolist()
            return vector, self.face_version, quality
        image = Image.fromarray(self.cv2.cvtColor(crop, self.cv2.COLOR_BGR2RGB))
        with self.torch.inference_mode():
            output = self.pet_model(**self.processor(images=image, return_tensors='pt'))
        return output.last_hidden_state[0,0].tolist(), self.pet_version, quality

    def animal_type_embedding(self, crop):
        """Type features accept small animals; individual embedding stays unchanged."""
        from PIL import Image
        if min(crop.shape[:2]) < 16:
            return None, None, 0
        gray = self.cv2.cvtColor(crop, self.cv2.COLOR_BGR2GRAY)
        quality = min(1., float(self.cv2.Laplacian(gray, self.cv2.CV_64F).var()) / 100.)
        if quality < .2:
            return None, None, quality
        image = Image.fromarray(self.cv2.cvtColor(crop, self.cv2.COLOR_BGR2RGB))
        with self.torch.inference_mode():
            output = self.pet_model(**self.processor(images=image, return_tensors='pt'))
        return output.last_hidden_state[0,0].tolist(), self.pet_version, quality

    def _legacy_animal_type_features(self, frame, box, claimed_type, vector, model, quality, person_region=False):
        """Guarded context fallback for uncertain usable animal crops only."""
        from subject_type_check import resolve_animal_type, rank_types
        if vector is None or quality < .2:
            return vector, model, quality, list(box)
        if resolve_animal_type(getattr(self, 'type_references', []), claimed_type, vector, model,
                               person_region=person_region) != 'Unknown':
            return vector, model, quality, list(box)
        h,w=frame.shape[:2];a,b,c,d=box;cx=(a+c)/2;cy=(b+d)/2
        variants=[]
        for pad in (.25,.5):
            cw=(c-a)*(1+2*pad);ch=(d-b)*(1+2*pad)
            region=[max(0,int(cx-cw/2)),max(0,int(cy-ch/2)),min(w,int(cx+cw/2)),min(h,int(cy+ch/2))]
            x1,y1,x2,y2=region
            v,m,q=self.animal_type_embedding(frame[y1:y2,x1:x2])
            ranks=rank_types(getattr(self,'type_references',[]),v,m)
            if q < .2 or len(ranks)<3:
                return vector,model,quality,list(box)
            variants.append((v,m,q,region,ranks))
        medium,wide=variants
        mt,wt=medium[4][0],wide[4][0]
        ms,ws=mt['independent_similarities'],wt['independent_similarities']
        if (wt['subject_type'] in ('Cat','Dog') and mt['subject_type']==wt['subject_type']
                and mt['score']>=.5 and mt['score']-medium[4][1]['score']>=.1
                and wt['score']>=.75 and wt['score']-wide[4][1]['score']>=.3
                and len(ms)>=2 and ms[1]>=.5 and len(ws)>=2 and ws[1]>=.5):
            return wide[:4]
        return vector,model,quality,list(box)

    def confirmed_animal_context(self, frame, box, pads=(.1,.25,.5)):
        """Require two strongly supported context crops to agree on animal Type."""
        from subject_type_check import rank_types
        h,w=frame.shape[:2];a,b,c,d=box;cx=(a+c)/2;cy=(b+d)/2
        variants=[]
        for pad in pads:
            cw=(c-a)*(1+2*pad);ch=(d-b)*(1+2*pad)
            region=[max(0,int(cx-cw/2)),max(0,int(cy-ch/2)),min(w,int(cx+cw/2)),min(h,int(cy+ch/2))]
            x1,y1,x2,y2=region
            v,m,q=self.animal_type_embedding(frame[y1:y2,x1:x2])
            ranks=rank_types(getattr(self,'type_references',[]),v,m)
            if q<.2 or len(ranks)<3:continue
            top=ranks[0];support=top['independent_similarities']
            if (top['score']<.75 or top['score']-ranks[1]['score']<.3
                    or len(support)<2 or support[1]<.5):continue
            if top['subject_type']=='Person':return None
            if top['subject_type'] in ('Cat','Dog'):
                variants.append((v,m,q,region,top['subject_type'],top['score']))
        if len(variants)<2 or len({v[4] for v in variants})!=1:return None
        return max(variants,key=lambda v:v[5])[:4]

    def confirm_weak_animal_proposal(self, frame, box, claimed_type, person_region=False):
        """Weak detector proposals need two strong Type confirmations, never Person."""
        from subject_type_check import resolve_animal_type
        if claimed_type not in ('Cat','Dog') or person_region:return False
        a,b,c,d=box
        v,m,q=self.animal_type_embedding(frame[b:d,a:c])
        if q<.2 or resolve_animal_type(getattr(self,'type_references',[]),claimed_type,v,m) is None:return False
        return self.confirmed_animal_context(frame,box) is not None

    def choose_animal_type_features(self, frame, box, claimed_type, vector, model, quality, person_region=False):
        from subject_type_check import resolve_animal_type
        result=self._legacy_animal_type_features(frame,box,claimed_type,vector,model,quality,person_region)
        if (claimed_type not in ('Cat','Dog') or vector is None or quality<.2
                or result[3]!=list(box)
                or resolve_animal_type(getattr(self,'type_references',[]),claimed_type,vector,model,person_region)!='Unknown'):
            return result
        return self.confirmed_animal_context(frame,box) or result

    def type_embedding(self, crop):
        # Use the same visual space for every Type, including people and vehicles.
        return self.embedding(crop, 'Cat')

    def resolve_animal_type(self, kind, vector, model, person_region=False):
        from subject_type_check import resolve_animal_type
        return resolve_animal_type(getattr(self, 'type_references', []), kind, vector, model, person_region=person_region)

    def resolve_track_type(self, track):
        from subject_type_check import resolve_track_type
        return resolve_track_type(getattr(self, 'type_references', []), track)

    def accepts_animal_type(self, kind, vector, model):
        from subject_type_check import animal_type_supported
        return animal_type_supported(getattr(self, 'type_references', []), kind, vector, model)

    def accepts_vehicle(self, crop):
        from subject_type_learning import vehicle_supported
        references = getattr(self, 'type_references', [])
        if not references:
            return True
        vector, model, quality = self.type_embedding(crop)
        return vector is None or vehicle_supported(references, vector, model)
