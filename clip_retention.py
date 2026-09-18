"""Pi-owned recorded clip retention settings."""
import configparser
import io
import os
from pathlib import Path
from fastapi import HTTPException
from pydantic import BaseModel, Field

class ClipRetentionRequest(BaseModel):
    delete_after_days: int = Field(strict=True, ge=0, le=3650)

def load_clip_retention(settings_path):
    path=Path(settings_path)
    config=configparser.ConfigParser()
    config.read([path.with_name('settings.ini'),path])
    value=config.getint('download','delete_after_days',fallback=30)
    if not 0 <= value <= 3650:
        raise ValueError('Clip retention must be from 0 to 3650 days')
    return value

def save_clip_retention(settings_path, days):
    ClipRetentionRequest(delete_after_days=days)
    path=Path(settings_path)
    config=configparser.ConfigParser(); config.read(path)
    if not config.has_section('download'): config.add_section('download')
    config.set('download','delete_after_days',str(days))
    output=io.StringIO();config.write(output)
    temp=path.with_suffix('.clip-retention.tmp')
    with temp.open('w',encoding='utf-8') as stream:
        stream.write(output.getvalue());stream.flush();os.fsync(stream.fileno())
    temp.replace(path)

def install_clip_retention_api(app, controller):
    path=controller.faults.settings_path
    @app.get('/api/v1/settings/clip-retention')
    async def get_settings():
        try:
            return {'delete_after_days':load_clip_retention(path)}
        except (OSError,ValueError,configparser.Error) as exc:
            raise HTTPException(503,'Could not read local clip retention') from exc
    @app.put('/api/v1/settings/clip-retention')
    async def put_settings(request: ClipRetentionRequest):
        async with controller.archive_lock:
            try:
                save_clip_retention(path,request.delete_after_days)
            except (OSError,ValueError,configparser.Error) as exc:
                raise HTTPException(503,'Could not save local clip retention') from exc
        return {'delete_after_days':request.delete_after_days}
