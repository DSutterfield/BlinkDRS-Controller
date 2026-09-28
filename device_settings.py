"""Camera-type settings adapters. No camera commands run during discovery."""
import asyncio
from typing import Literal

from blinkpy import api
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict


class SettingChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    value: Literal['off', 'on', 'auto']


class OriginalMiniSettings:
    profile = 'original_mini'
    label = 'Blink Mini'
    options = ['auto', 'on', 'off']

    async def read(self, camera):
        data = await api.request_get_config(camera.sync.blink, camera.sync.network_id,
                                            camera.camera_id, product_type='owl')
        # Current Minis report legacy illuminator_enable='auto' even when Off.
        # The v2 field preserves the actual mode. Fall back only when absent;
        # an unrecognized v2 value must not be masked by a plausible legacy one.
        has_v2 = isinstance(data, dict) and 'illuminator_enable_v2' in data
        key = 'illuminator_enable_v2' if has_v2 else 'illuminator_enable'
        value = data.get(key) if isinstance(data, dict) else None
        if not has_v2 and type(value) is int:
            value = {0: 'off', 1: 'on', 2: 'auto'}.get(value)
        if value not in self.options:
            raise HTTPException(502, 'Blink did not return a recognized night vision setting.')
        return {'key': 'night_vision', 'label': 'Night vision', 'kind': 'choice',
                'value': value, 'options': self.options}

    async def write(self, camera, key, value):
        if key != 'night_vision':
            raise HTTPException(404, 'This setting is not supported for this camera type.')
        result = await camera.async_set_night_vision(value)
        if result is None:
            raise HTTPException(502, 'Blink did not accept the night vision command.')
        # Read fresh configuration; never assume an accepted command took effect.
        for attempt in range(3):
            confirmed = await self.read(camera)
            if confirmed['value'] == value:
                return confirmed
            if attempt < 2:
                await asyncio.sleep(.5)
        raise HTTPException(409, 'Blink has not confirmed the requested setting.')


# Deliberately separate profiles: inherited BlinkPy methods do not establish
# support for Mini 2, Outdoor, or Doorbell configuration routes.
SETTINGS_PROFILES = {
    'owl': OriginalMiniSettings(),
    'hawk': None,       # Mini 2: future adapter
    'sedona': None,     # Outdoor: future adapter
    'tulip': None,      # Doorbell: future adapter
}


def settings_capability(camera):
    adapter = SETTINGS_PROFILES.get(getattr(camera, 'product_type', None))
    return {'profile': adapter.profile if adapter else None, 'available': adapter is not None}


class DeviceSettings:
    def __init__(self, controller):
        self.controller = controller
        self.locks = {}

    @property
    def busy(self):
        return any(lock.locked() for lock in self.locks.values())

    async def execute(self, device_id, system_id, key=None, value=None):
        blink = self.controller.blink
        if blink is None:
            raise HTTPException(503, 'Blink controller is not connected.')
        camera = next((c for c in blink.cameras.values()
                       if str(c.camera_id) == device_id and str(c.sync.network_id) == system_id), None)
        if camera is None:
            raise HTTPException(404, 'Camera was not found in this system.')
        adapter = SETTINGS_PROFILES.get(camera.product_type)
        if adapter is None:
            raise HTTPException(400, 'Additional settings are not yet supported for this camera type.')
        if not (camera.online and camera.sync.available):
            raise HTTPException(503, 'This camera or its Sync Module is offline.')
        lock = self.locks.setdefault((system_id, device_id), asyncio.Lock())
        try:
            async with asyncio.timeout(25):
                async with lock:
                    setting = (await adapter.read(camera) if key is None
                               else await adapter.write(camera, key, value))
                    return {'profile': adapter.profile, 'label': adapter.label, 'settings': [setting]}
        except HTTPException:
            raise
        except TimeoutError as exc:
            raise HTTPException(504, 'Camera settings request timed out.') from exc
        except Exception as exc:
            raise HTTPException(502, 'Could not communicate with Blink camera settings.') from exc


def install_device_settings_api(app, controller):
    service = DeviceSettings(controller)

    @app.get('/api/v1/devices/{device_id}/settings')
    async def read_settings(device_id: str, system_id: str):
        return await service.execute(device_id, system_id)

    @app.put('/api/v1/devices/{device_id}/settings/{key}')
    async def write_setting(device_id: str, key: str, system_id: str, request: SettingChange):
        return await service.execute(device_id, system_id, key, request.value)

    return service
