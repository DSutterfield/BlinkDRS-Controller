"""Camera-type settings adapters. No camera commands run during discovery."""
import asyncio
import json

from blinkpy import api
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field


class SettingChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    # Wire values remain strings for the existing choice renderer. Adapters
    # validate each setting's narrower range and convert to Blink's field type.
    value: str = Field(strict=True, pattern=r'^(?:off|on|auto|low|medium|high|[1-9]|[1-5][0-9]|60)$')


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
        if value not in self.options:
            raise HTTPException(422, 'Night vision must be Auto, On, or Off.')
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


class OutdoorSettings:
    profile = 'outdoor_sedona'
    label = 'Blink Outdoor'
    options = [str(value) for value in range(1, 10)]
    numeric_settings = {
        'motion_sensitivity': ('Motion sensitivity (1 low, 9 high)', 'motion_sensitivity', 1, 9),
        'clip_length': ('Motion clip length (seconds)', 'video_length', 5, 60),
        'retrigger_time': ('Retrigger time (seconds)', 'alert_interval', 10, 60),
    }

    async def read(self, camera):
        # BlinkPy labels this shared /network/.../camera/... route "catalina".
        # Live sedona responses verified its camera-list envelope and field.
        data = await api.request_get_config(camera.sync.blink, camera.sync.network_id,
                                            camera.camera_id, product_type='catalina')
        entries = data.get('camera') if isinstance(data, dict) else None
        if (not isinstance(entries, list) or len(entries) != 1 or
                not isinstance(entries[0], dict) or
                str(entries[0].get('id')) != str(camera.camera_id)):
            raise HTTPException(502, 'Blink did not return configuration for this camera.')
        config = entries[0]
        maximum = config.get('clip_max_length')
        if type(maximum) is not int or not 5 <= maximum <= 60:
            raise HTTPException(502, 'Blink did not return a recognized maximum clip length.')
        settings = []
        for key, (label, field, lower, upper) in self.numeric_settings.items():
            if key == 'clip_length':
                upper = maximum
            options = [str(value) for value in range(lower, upper + 1)]
            value = config.get(field)
            if type(value) not in (int, str) or str(value) not in options:
                raise HTTPException(502, f'Blink did not return a recognized {label.lower()}.')
            settings.append({'key': key, 'label': label, 'kind': 'choice',
                             'value': str(value), 'options': options})
        # This feature is model-dependent. Never infer support from the field alone.
        if config.get('early_termination_supported') is True:
            value = config.get('early_termination')
            if type(value) is not bool:
                raise HTTPException(502, 'Blink did not return a recognized end clip early setting.')
            settings.append({'key': 'end_clip_early', 'label': 'End clip early if motion stops',
                             'kind': 'choice', 'value': 'on' if value else 'off',
                             'options': ['on', 'off']})
        return settings

    async def write(self, camera, key, value):
        if key not in self.numeric_settings and key != 'end_clip_early':
            raise HTTPException(404, 'This setting is not supported for this camera type.')
        if key in self.numeric_settings:
            _, field, lower, upper = self.numeric_settings[key]
            options = [str(number) for number in range(lower, upper + 1)]
            if type(value) is not str or value not in options:
                raise HTTPException(422, f'This setting must be a whole number from {lower} to {upper}.')
            blink_value = int(value)
        else:
            field = 'early_termination'
            if value not in ('on', 'off'):
                raise HTTPException(422, 'End clip early must be On or Off.')
            blink_value = value == 'on'
        # Verify model-specific limits/support immediately before a clip change.
        if key in ('clip_length', 'end_clip_early'):
            current = next((item for item in await self.read(camera) if item['key'] == key), None)
            if current is None:
                raise HTTPException(400, 'This setting is not supported for this camera.')
            if value not in current['options']:
                raise HTTPException(422, 'This value is not supported for this camera.')
        # Send only the requested field; never replay an entire configuration.
        response = await api.request_update_config(
            camera.sync.blink, camera.sync.network_id, camera.camera_id,
            product_type='catalina', data=json.dumps({field: blink_value}))
        if response is None or response.status != 200:
            raise HTTPException(502, 'Blink did not accept the camera setting command.')
        result = await response.json()
        if (not isinstance(result, dict) or result.get('command') != 'config_set' or
                result.get('state_condition') in ('failed', 'error')):
            raise HTTPException(502, 'Blink did not accept the camera setting command.')
        # Acceptance alone is not confirmation. Retry reads, never the write.
        for attempt in range(3):
            confirmed = await self.read(camera)
            if any(item['key'] == key and item['value'] == value for item in confirmed):
                return confirmed
            if attempt < 2:
                await asyncio.sleep(.5)
        raise HTTPException(409, 'Blink has not confirmed the requested setting.')


class DoorbellSettings:
    profile = 'doorbell_tulip'
    label = 'Blink Video Doorbell'
    # Matched to oBa Low/Medium/High on the actual tulip Doorbell.
    intensities = {'low': 1, 'medium': 4, 'high': 7}
    numeric_settings = {
        'motion_sensitivity': ('Motion sensitivity (1 low, 9 high)', 1, 9),
        'clip_length': ('Motion clip length (seconds)', 5, 30),
        'retrigger_time': ('Retrigger time (seconds)', 10, 60),
    }

    def url(self, camera):
        blink = camera.sync.blink
        return (f'{blink.urls.base_url}/api/v1/accounts/{blink.account_id}'
                f'/networks/{camera.sync.network_id}/doorbells/{camera.camera_id}/config')

    async def read(self, camera):
        config = await api.http_get(camera.sync.blink, self.url(camera))
        if not isinstance(config, dict):
            raise HTTPException(502, 'Blink did not return Doorbell configuration.')
        maximum = config.get('clip_length_max')
        if type(maximum) is not int or not 5 <= maximum <= 30:
            raise HTTPException(502, 'Blink did not return a recognized Doorbell clip limit.')
        settings = []
        for key, (label, lower, upper) in self.numeric_settings.items():
            if key == 'clip_length': upper = maximum
            options = [str(number) for number in range(lower, upper + 1)]
            value = config.get(key)
            if type(value) not in (int, str) or str(value) not in options:
                raise HTTPException(502, f'Blink did not return a recognized {label.lower()}.')
            settings.append({'key': key, 'label': label, 'kind': 'choice',
                             'value': str(value), 'options': options})
        if config.get('early_termination_supported') is True:
            value = config.get('early_termination')
            if type(value) is not bool:
                raise HTTPException(502, 'Blink did not return a recognized end clip early setting.')
            settings.append({'key': 'end_clip_early', 'label': 'End clip early if motion stops',
                             'kind': 'choice', 'value': 'on' if value else 'off', 'options': ['on', 'off']})
        mode = config.get('illuminator_enable_v2')
        if mode not in ('auto', 'on', 'off'):
            raise HTTPException(502, 'Blink did not return a recognized Doorbell night vision mode.')
        settings.append({'key': 'night_vision', 'label': 'Night vision', 'kind': 'choice',
                         'value': mode, 'options': ['auto', 'on', 'off']})
        intensity = config.get('illuminator_intensity')
        if type(intensity) is not int or intensity not in self.intensities.values():
            raise HTTPException(502, 'Blink did not return a recognized Doorbell IR intensity.')
        settings.append({'key': 'ir_intensity', 'label': 'IR intensity', 'kind': 'choice',
                         'value': next(label for label, number in self.intensities.items() if number == intensity),
                         'options': list(self.intensities)})
        return settings

    async def write(self, camera, key, value):
        if key not in self.numeric_settings and key not in ('end_clip_early', 'night_vision', 'ir_intensity'):
            raise HTTPException(404, 'This setting is not supported for this camera type.')
        settings = await self.read(camera)
        current = next((item for item in settings if item['key'] == key), None)
        if current is None:
            raise HTTPException(400, 'This setting is not supported for this Doorbell.')
        if type(value) is not str or value not in current['options']:
            raise HTTPException(422, 'This value is not supported for this Doorbell setting.')
        if current['value'] == value:
            return settings  # A fresh read already confirms this value; no write needed.
        field = key
        blink_value = value
        if key in self.numeric_settings: blink_value = int(value)
        elif key == 'end_clip_early': field, blink_value = 'early_termination', value == 'on'
        elif key == 'night_vision': field = 'illuminator_enable'  # v2 is the readback field
        elif key == 'ir_intensity': field, blink_value = 'illuminator_intensity', self.intensities[value]
        response = await api.http_post(camera.sync.blink, self.url(camera), json=False,
                                       data=json.dumps({field: blink_value}))
        if response is None or response.status != 200:
            raise HTTPException(502, 'Blink did not accept the Doorbell setting command.')
        result = await response.json()
        if (not isinstance(result, dict) or result.get('command') != 'config_set' or
                result.get('state_condition', result.get('state')) in ('failed', 'error') or 'code' in result):
            raise HTTPException(502, 'Blink did not accept the Doorbell setting command.')
        for attempt in range(3):
            confirmed = await self.read(camera)
            if any(item['key'] == key and item['value'] == value for item in confirmed):
                return confirmed
            if attempt < 2: await asyncio.sleep(.5)
        raise HTTPException(409, 'Blink has not confirmed the requested setting.')



# Keep family support explicit; shared methods alone do not establish support.
SETTINGS_PROFILES = {
    'owl': OriginalMiniSettings(),
    'hawk': None,       # Mini 2: future adapter
    'sedona': OutdoorSettings(),
    'tulip': DoorbellSettings(),
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
                    return {'profile': adapter.profile, 'label': adapter.label,
                            'settings': setting if isinstance(setting, list) else [setting]}
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
