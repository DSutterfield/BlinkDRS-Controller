"""Manual identity foundation; automatic inference is a later stage."""
from fastapi import HTTPException, Query
from fastapi.responses import Response
from identity_analysis import queue_clip, job_status, crop_image
from pydantic import BaseModel, Field
from typing import Literal, Optional
from identity_store import (list_identities, create_identity, get_clip_identities,
                            assign_identity, remove_detection, delete_identity, mark_unknown_motion, update_identity)


class NewIdentity(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    subject_type: Literal['Person', 'Cat', 'Dog']


class ReferencePhoto(BaseModel):
    filename: str = Field(min_length=1, max_length=150)
    image_base64: str = Field(min_length=1, max_length=11184812)


class Assignment(BaseModel):
    identity_id: Optional[int] = Field(default=None, gt=0)
    subject_type: Literal['Person', 'Cat', 'Dog', 'Vehicle', 'Unknown']
    detection_id: Optional[int] = Field(default=None, gt=0)


class ManualCrop(BaseModel):
    seconds: float = Field(ge=0, allow_inf_nan=False)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=16)
    height: int = Field(ge=16)
    identity_id: Optional[int] = Field(default=None, gt=0)
    subject_type: Literal['Person','Cat','Dog','Vehicle','Unknown']


def install_identity_api(app, controller):
    def run(operation, *args):
        if not controller.catalog_db_path.is_file():
            raise HTTPException(503, 'Local clip catalog is unavailable')
        try:
            return operation(controller.catalog_db_path, *args)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    from sightings_api import install_sightings_api
    install_sightings_api(app, run)

    @app.get('/api/v1/clips/catalog/{catalog_id}/analysis')
    def analysis_status(catalog_id: int):
        return run(job_status, catalog_id)

    @app.get('/api/v1/clips/catalog/{catalog_id}/frame-times')
    def frame_times(catalog_id: int):
        from manual_crops import clip_frame_times
        return run(clip_frame_times, controller.archive_root, catalog_id)

    @app.get('/api/v1/clips/catalog/{catalog_id}/frames')
    def frame_window(catalog_id: int, start: int = Query(ge=0), count: int = Query(default=8,ge=1,le=8)):
        from manual_crops import clip_frame_window
        return Response(run(clip_frame_window,controller.archive_root,catalog_id,start,count),
            media_type='application/zip',headers={'Cache-Control':'no-store'})

    @app.get('/api/v1/clips/catalog/{catalog_id}/frame')
    def frame(catalog_id: int, seconds: float = 0):
        from manual_crops import clip_frame
        return Response(run(clip_frame, controller.archive_root, catalog_id, seconds), media_type='image/jpeg', headers={'Cache-Control':'no-store'})

    @app.post('/api/v1/clips/catalog/{catalog_id}/manual-crops')
    def manual_crop(catalog_id: int, body: ManualCrop):
        from manual_crops import save_crop
        return run(save_crop, controller.archive_root, catalog_id, body.seconds,
                   (body.x,body.y,body.width,body.height), body.identity_id, body.subject_type)

    @app.post('/api/v1/clips/catalog/{catalog_id}/analysis')
    def analyze(catalog_id: int):
        status = run(job_status, catalog_id)
        if not status['worker_available']:
            raise HTTPException(503, 'Local AI worker is not running. Start the identity worker before analyzing clips.')
        return run(queue_clip, catalog_id)

    @app.get('/api/v1/clips/catalog/{catalog_id}/identities/{detection_id}/image')
    def subject_image(catalog_id: int, detection_id: int):
        return Response(run(crop_image, catalog_id, detection_id), media_type='image/jpeg', headers={'Cache-Control': 'no-store'})

    from reference_photos import list_photos, add_photo, photo_image, remove_photo, retry_photo

    @app.get('/api/v1/about')
    def about():
        from release_info import VERSION, UPDATED
        return {'program_name':'Blink Controller','version':VERSION,'date_updated':UPDATED,'api_version':'1'}

    @app.get('/api/v1/identities/{identity_id}/photos')
    def photos(identity_id: int):
        return run(list_photos, identity_id)

    @app.post('/api/v1/identities/{identity_id}/photos')
    def upload_photo(identity_id: int, body: ReferencePhoto):
        return run(add_photo, identity_id, body.filename, body.image_base64)

    @app.get('/api/v1/identities/{identity_id}/photos/{photo_id}/image')
    def reference_image(identity_id: int, photo_id: int):
        return Response(run(photo_image,identity_id,photo_id),media_type='image/jpeg',headers={'Cache-Control':'no-store'})

    @app.delete('/api/v1/identities/{identity_id}/photos/{photo_id}')
    def delete_photo(identity_id: int, photo_id: int):
        return run(remove_photo,identity_id,photo_id)

    @app.post('/api/v1/identities/{identity_id}/photos/{photo_id}/retry')
    def retry_reference(identity_id: int, photo_id: int):
        return run(retry_photo,identity_id,photo_id)

    @app.get('/api/v1/identities')
    def identities():
        return run(list_identities)

    @app.post('/api/v1/identities')
    def add_identity(body: NewIdentity):
        return run(create_identity, body.name, body.subject_type)

    @app.put('/api/v1/identities/{identity_id}')
    def edit_identity(identity_id: int, body: NewIdentity):
        return run(update_identity, identity_id, body.name, body.subject_type)

    @app.delete('/api/v1/identities/{identity_id}')
    def delete_profile(identity_id: int):
        return run(delete_identity, identity_id)

    @app.get('/api/v1/clips/catalog/{catalog_id}/identities')
    def labels(catalog_id: int):
        return run(get_clip_identities, catalog_id)

    @app.post('/api/v1/clips/catalog/{catalog_id}/unknown-motion')
    def no_visible_subject(catalog_id: int):
        return run(mark_unknown_motion, catalog_id)

    @app.put('/api/v1/clips/catalog/{catalog_id}/identities')
    def assign(catalog_id: int, body: Assignment):
        return run(assign_identity, catalog_id, body.identity_id, body.subject_type, body.detection_id)

    @app.delete('/api/v1/clips/catalog/{catalog_id}/identities/{detection_id}')
    def remove(catalog_id: int, detection_id: int):
        return run(remove_detection, catalog_id, detection_id)

