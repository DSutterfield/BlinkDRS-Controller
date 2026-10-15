"""Offline animal-location proposals; species is validated separately."""
from pathlib import Path
import hashlib
import numpy as np

MODEL_FILE = 'yolo11s-cat-v1.onnx'
MODEL_SHA256 = '4adb6d0246798aebadda6fd62ee1d909f5ab522d735d1067592f7d4e5a5cd4a6'


class CatProposalDetector:
    def __init__(self, cv2, path):
        self.cv2=cv2
        path=Path(path)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=MODEL_SHA256:
            raise ValueError('Cat proposal model fingerprint mismatch')
        self.net=cv2.dnn.readNetFromONNX(str(path))
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self.version='yolo11s-cat-onnx-v1:'+MODEL_SHA256
        self.people=[]

    def detect(self, frame):
        h,w=frame.shape[:2]
        scale=min(640/w,640/h)
        nw,nh=round(w*scale),round(h*scale)
        x,y=(640-nw)//2,(640-nh)//2
        image=np.full((640,640,3),114,np.uint8)
        image[y:y+nh,x:x+nw]=self.cv2.resize(frame,(nw,nh))
        self.net.setInput(self.cv2.dnn.blobFromImage(image,1/255.,(640,640),swapRB=True,crop=False))
        raw=self.net.forward()
        if raw.shape!=(1,84,8400) or not np.isfinite(raw).all():
            raise ValueError('Invalid Cat proposal model output')
        # Reuse this same full-scene inference for Person corroboration.
        # These proposals remain separate from the animal location list.
        scores=raw[0,4:,:]
        boxes=[];confidence=[]
        for i in np.where((scores.argmax(axis=0)==0)&(scores[0]>=.80))[0]:
            cx,cy,bw,bh=raw[0,:4,i]
            a,b=max(0,float((cx-bw/2-x)/scale)),max(0,float((cy-bh/2-y)/scale))
            c,e=min(w,float((cx+bw/2-x)/scale)),min(h,float((cy+bh/2-y)/scale))
            if min(c-a,e-b)<48:continue
            boxes.append([a,b,c-a,e-b]);confidence.append(float(scores[0,i]))
        keep=self.cv2.dnn.NMSBoxes(boxes,confidence,.80,.45)
        self.people=[]
        for i in np.asarray(keep).reshape(-1):
            a,b,bw,bh=boxes[int(i)]
            self.people.append(dict(subject_type='Person',confidence=confidence[int(i)],box=[a,b,a+bw,b+bh],
                                    detector_model='yolo11s-person-onnx-v1:'+MODEL_SHA256))
        self.people=sorted(self.people,key=lambda d:d['confidence'],reverse=True)[:12]
        output=[]
        for label,kind in ((15,'Cat'),(16,'Dog')):
            scores=raw[0,4:,:]
            indices=np.where((scores.argmax(axis=0)==label)&(scores[label]>=.10))[0]
            boxes=[];confidence=[]
            for i in indices:
                cx,cy,bw,bh=raw[0,:4,i]
                a,b=max(0,float((cx-bw/2-x)/scale)),max(0,float((cy-bh/2-y)/scale))
                c,e=min(w,float((cx+bw/2-x)/scale)),min(h,float((cy+bh/2-y)/scale))
                if min(c-a,e-b)<16:continue
                boxes.append([a,b,c-a,e-b]);confidence.append(float(scores[label,i]))
            keep=self.cv2.dnn.NMSBoxes(boxes,confidence,.10,.45)
            for i in np.asarray(keep).reshape(-1):
                a,b,bw,bh=boxes[int(i)]
                output.append(dict(subject_type=kind,confidence=confidence[int(i)],
                                   box=[a,b,a+bw,b+bh],detector_model=self.version))
        return sorted(output,key=lambda d:d['confidence'],reverse=True)[:12]
