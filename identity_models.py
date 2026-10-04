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
        kinds = {1:'Person',17:'Cat',18:'Dog'}
        for view,x,y in views:
            rgb = Image.fromarray(self.cv2.cvtColor(view,self.cv2.COLOR_BGR2RGB))
            with self.torch.inference_mode():
                result = self.detector([self.detector_transform(rgb)])[0]
            for label,score,box in zip(result['labels'],result['scores'],result['boxes']):
                label,score = int(label),float(score)
                if label not in kinds or score < (.65 if label==1 else .55):
                    continue
                if len(views)>1 and (x or y or view.shape != bgr.shape) and label==1:
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
