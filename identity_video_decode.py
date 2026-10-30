"""Read exact sampled source frames, recovering failed seeks sequentially."""
import logging


class SampledFrameReader:
    def __init__(self,path,cv2,checkpoint=None):
        self.path=str(path)
        self.cv2=cv2
        self.checkpoint=checkpoint
        self.capture=cv2.VideoCapture(self.path)
        self.next_index=None

    def read(self,index):
        if self.next_index is None:
            self.capture.set(self.cv2.CAP_PROP_POS_FRAMES,index)
            ok,frame=self.capture.read()
            if ok and frame is not None:return frame
            logging.warning('Sample seek failed at frame %s; retrying source sequentially: %s',index,self.path)
            self.capture.release()
            self.capture=self.cv2.VideoCapture(self.path)
            self.next_index=0
        if index<self.next_index:
            raise ValueError('Sequential recovery requires increasing sample indices.')
        while self.next_index<=index:
            if self.checkpoint and self.next_index%32==0:self.checkpoint()
            ok,frame=self.capture.read()
            if not ok or frame is None:
                raise ValueError('A sampled video frame could not be decoded after sequential retry '
                                 f'(target {index}, stopped at {self.next_index}).')
            self.next_index+=1
        return frame

    def close(self):
        self.capture.release()
