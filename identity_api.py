"""Manual identity foundation; automatic inference is a later stage."""
from fastapi import HTTPException
from fastapi.responses import Response
from identity_analysis import queue_clip, job_status, crop_image
from pydantic import BaseModel, Field
from typing import Literal, Optional
from identity_store import (list_identities, create_identity, get_clip_identities,
                            assign_identity, remove_detection, delete_identity, mark_unknown_motion)


class NewIdentity(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    subject_type: Literal['Person', 'Cat', 'Dog']


class Assignment(BaseModel):
    identity_id: Optional[int] = Field(default=None, gt=0)
    subject_type: Literal['Person', 'Cat', 'Dog']
    detection_id: Optional[int] = Field(default=None, gt=0)


class ManualCrop(BaseModel):
    seconds: float = Field(ge=0, allow_inf_nan=False)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=16)
    height: int = Field(ge=16)
    identity_id: Optional[int] = Field(default=None, gt=0)
    subject_type: Literal['Person','Cat','Dog']


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

    @app.get('/api/v1/clips/catalog/{catalog_id}/analysis')
    def analysis_status(catalog_id: int):
        return run(job_status, catalog_id)

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

    @app.get('/api/v1/identities')
    def identities():
        return run(list_identities)

    @app.post('/api/v1/identities')
    def add_identity(body: NewIdentity):
        return run(create_identity, body.name, body.subject_type)

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
