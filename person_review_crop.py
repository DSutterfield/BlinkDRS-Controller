"""Review-only margins for corroborated Person views without face features."""
import math


def review_region(shape,box,confidence):
    h,w=shape[:2];a,b,c,d=box
    px=max(8,math.ceil((c-a)*.08));py=max(8,math.ceil((d-b)*.06))
    sides=sum((a-px>=0,b-py>=0,c+px<=w,d+py<=h))
    return [max(0,a-px),max(0,b-py),min(w,c+px),min(h,d+py)],(sides,float(confidence))
