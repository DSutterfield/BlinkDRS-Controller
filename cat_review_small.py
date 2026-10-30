"""Repair only missing Cat identity features AFTER native detection/cleanup."""
def enrich(tracks,models):
    for track in tracks:
        if track.get('subject_type')!='Cat' or track.get('embedding') is not None:continue
        import numpy as np
        crop=models.cv2.imdecode(np.frombuffer(track['crop_jpeg'],np.uint8),models.cv2.IMREAD_COLOR)
        if crop is None:continue
        vector,model,quality=models.review_embedding(crop,'Cat')
        if vector is None or quality<.2:continue
        track.update(embedding=vector,embedding_model=model,quality_score=quality)
        if not track.get('views'):
            from identity_worker import retain_view
            retain_view(track,track.get('crop_seconds',track['first_seen_seconds']),track['crop_jpeg'],vector,model,quality,type_result='Cat')
    return tracks
