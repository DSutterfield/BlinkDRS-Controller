"""Sightings routes and browser review panel."""
from pathlib import Path
from fastapi import Query
from fastapi.responses import HTMLResponse,Response
from pydantic import BaseModel,Field
from sightings import sightings,review_groups,confirm_batch,track_views,track_view_image
class ReviewMember(BaseModel):
 detection_id:int=Field(gt=0)
 snapshot:str=Field(min_length=64,max_length=64)
class BatchReview(BaseModel):
 identity_id:int=Field(gt=0)
 members:list[ReviewMember]=Field(min_length=1,max_length=50)
def install_sightings_api(app,run):
 @app.get('/sightings',response_class=HTMLResponse)
 def panel():return HTMLResponse(Path(__file__).with_name('sightings.html').read_text(encoding='utf-8'),headers={'Cache-Control':'no-store'})
 @app.get('/api/v1/sightings')
 def query(start:str,end:str,camera:str|None=None,identity_id:int|None=Query(default=None,gt=0),status:str='all',limit:int=Query(default=100,ge=1,le=200),offset:int=Query(default=0,ge=0)):
  return run(sightings,start,end,camera,identity_id,status,limit,offset)
 @app.get('/api/v1/sightings/review-groups')
 def groups(start:str,end:str,camera:str|None=None):return run(review_groups,start,end,camera)
 @app.post('/api/v1/sightings/confirm-batch')
 def confirm(body:BatchReview):return run(confirm_batch,body.identity_id,[m.model_dump() for m in body.members])
 @app.get('/api/v1/clips/catalog/{catalog_id}/identities/{detection_id}/views')
 def views(catalog_id:int,detection_id:int):return run(track_views,catalog_id,detection_id)
 @app.get('/api/v1/clips/catalog/{catalog_id}/identities/{detection_id}/views/{view_id}/image')
 def view_image(catalog_id:int,detection_id:int,view_id:int):return Response(run(track_view_image,catalog_id,detection_id,view_id),media_type='image/jpeg',headers={'Cache-Control':'no-store'})
