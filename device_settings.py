"""Camera-type settings adapters. No camera commands run during discovery."""
import asyncio
import json

from blinkpy import api
from motion_zones import ZoneChange, execute_zones
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field


class SettingChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    # Wire values remain strings for the existing choice renderer. Adapters
    # validate each setting's narrower range and convert to Blink's field type.
    value: str = Field(strict=True, pattern=r'^(?:off|on|recording|auto|low|medium|high|[1-9]|[1-5][0-9]|60|180|300|600)$')


class LightChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = Field(strict=True)


def status_led_settings(config, options):
    """Omit unknown LED fields without blocking other camera settings."""
    if 'valid_status_led_modes' in config:
        reported = config['valid_status_led_modes']
        if not isinstance(reported, list):
            return []
        options = [value for value in options if value in reported]
    value = config.get('led_state')
    if value not in options:
        return []
    return [{'key': 'status_led', 'label': 'Status LED (recording includes Live View)',
             'kind': 'choice', 'value': value, 'options': options}]


async def write_status_led(adapter, camera, value):
    settings = await adapter.read(camera)
    current = next((item for item in settings if item['key'] == 'status_led'), None)
    if current is None:
        raise HTTPException(400, 'Status LED is not supported by this camera configuration.')
    if type(value) is not str or value not in current['options']:
        raise HTTPException(422, 'This Status LED mode is not supported by this camera.')
    if current['value'] == value:
        return settings
    # Preserve led_enabled and the Doorbell button LED: change only status mode.
    payload = json.dumps({'led_state': value})
    if camera.product_type == 'tulip':
        response = await api.http_post(camera.sync.blink, adapter.url(camera), json=False, data=payload)
    else:
        route = 'owl' if camera.product_type in ('owl', 'hawk') else 'catalina'
        response = await api.request_update_config(camera.sync.blink, camera.sync.network_id,
                                                   camera.camera_id, product_type=route, data=payload)
    if response is None or response.status != 200:
        raise HTTPException(502, 'Blink did not accept the Status LED command.')
    result = await response.json()
    if (not isinstance(result, dict) or result.get('command') != 'config_set' or
            result.get('state_condition', result.get('state')) in ('failed', 'error') or 'code' in result):
        raise HTTPException(502, 'Blink did not accept the Status LED command.')
    for attempt in range(3):
        confirmed = await adapter.read(camera)
        if any(item['key'] == 'status_led' and item['value'] == value for item in confirmed):
            return confirmed
        if attempt < 2:
            await asyncio.sleep(.5)
    raise HTTPException(409, 'Blink has not confirmed the requested Status LED mode.')


class OriginalMiniSettings:
    profile = 'original_mini'
    label = 'Blink Mini'
    options = ['auto', 'on', 'off']
    intensities = {'low': 1, 'medium': 4, 'high': 7}
    numeric_settings = {
        'motion_sensitivity': ('Motion sensitivity (1 low, 9 high)', 1, 9),
        'clip_length': ('Motion clip length (seconds)', 5, 30),
        'retrigger_time': ('Retrigger time (seconds)', 10, 60),
    }

    def additional_settings(self, config):
        settings = []
        for key, (label, lower, upper) in self.numeric_settings.items():
            if key == 'clip_length':
                maximum = config.get('clip_length_max')
                if type(maximum) is not int or not lower <= maximum <= upper:
                    continue
                upper = maximum
            value = config.get(key)
            options = [str(number) for number in range(lower, upper + 1)]
            if type(value) in (int, str) and str(value) in options:
                settings.append({'key': key, 'label': label, 'kind': 'choice',
                                 'value': str(value), 'options': options})
        if config.get('early_termination_supported') is True and type(config.get('early_termination')) is bool:
            settings.append({'key': 'end_clip_early', 'label': 'End clip early if motion stops',
                             'kind': 'choice', 'value': 'on' if config['early_termination'] else 'off',
                             'options': ['on', 'off']})
        intensity = config.get('illuminator_intensity')
        if type(intensity) is int and intensity in self.intensities.values():
            settings.append({'key': 'ir_intensity', 'label': 'IR intensity', 'kind': 'choice',
                             'value': next(label for label, number in self.intensities.items() if number == intensity),
                             'options': list(self.intensities)})
        return settings

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
        return [{'key': 'night_vision', 'label': 'Night vision', 'kind': 'choice',
                 'value': value, 'options': self.options}] + self.additional_settings(data) + status_led_settings(data, ['on', 'off', 'recording'])

    async def write_config(self, camera, key, value):
        # Mini 2 uses the same verified owl route, not BlinkPy's unsupported hawk route.
        settings = await self.read(camera)
        current = next((item for item in settings if item['key'] == key), None)
        if current is None:
            raise HTTPException(400, 'This setting is not supported by this camera configuration.')
        if type(value) is not str or value not in current['options']:
            raise HTTPException(422, 'This value is not supported for this camera setting.')
        if current['value'] == value:
            return settings
        field, blink_value = key, value
        if key in self.numeric_settings: blink_value = int(value)
        elif key == 'end_clip_early': field, blink_value = 'early_termination', value == 'on'
        elif key == 'night_vision': field = 'illuminator_enable'
        elif key == 'ir_intensity': field, blink_value = 'illuminator_intensity', self.intensities[value]
        elif key == 'manual_light_duration': blink_value = int(value)
        elif key == 'motion_light_activation': field, blink_value = 'spotlight_enabled', value == 'on'
        response = await api.request_update_config(camera.sync.blink, camera.sync.network_id,
                                                   camera.camera_id, product_type='owl',
                                                   data=json.dumps({field: blink_value}))
        if response is None or response.status != 200:
            raise HTTPException(502, 'Blink did not accept the camera setting command.')
        result = await response.json()
        if (not isinstance(result, dict) or result.get('command') != 'config_set' or
                result.get('state_condition', result.get('state')) in ('failed', 'error') or 'code' in result):
            raise HTTPException(502, 'Blink did not accept the camera setting command.')
        for attempt in range(3):
            confirmed = await self.read(camera)
            if any(item['key'] == key and item['value'] == value for item in confirmed):
                return confirmed
            if attempt < 2: await asyncio.sleep(.5)
        raise HTTPException(409, 'Blink has not confirmed the requested setting.')

    async def write(self, camera, key, value):
        if key == 'status_led':
            return await write_status_led(self, camera, value)
        if key in self.numeric_settings or key in ('end_clip_early', 'ir_intensity'):
            return await self.write_config(camera, key, value)
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
            if any(item['key'] == key and item['value'] == value for item in confirmed):
                return confirmed
            if attempt < 2:
                await asyncio.sleep(.5)
        raise HTTPException(409, 'Blink has not confirmed the requested setting.')


class Mini2Settings(OriginalMiniSettings):
    profile = 'mini_2'
    label = 'Blink Mini 2'

    def additional_settings(self, config):
        settings = super().additional_settings(config)
        if config.get('spotlight_compatible') is not True:
            return settings
        reported = config.get('manual_light_duration_options')
        if isinstance(reported, list):
            options = [str(n) for n in (30, 60, 180, 300, 600)
                       if any(type(v) is int and v == n for v in reported)]
            value = config.get('manual_light_duration')
            if type(value) is int and str(value) in options:
                settings.append({'key': 'manual_light_duration', 'label': 'Light: timeout after manual activation (seconds)',
                                 'kind': 'choice', 'value': str(value), 'options': options})
        value = config.get('spotlight_enabled')
        if type(value) is bool:
            settings.append({'key': 'motion_light_activation', 'label': 'Light: motion activation',
                             'kind': 'choice', 'value': 'on' if value else 'off', 'options': ['on', 'off']})
        return settings

    async def write(self, camera, key, value):
        if key in ('night_vision', 'manual_light_duration', 'motion_light_activation'):
            return await self.write_config(camera, key, value)
        return await super().write(camera, key, value)

    async def read_light(self, camera):
        config = await api.request_get_config(camera.sync.blink, camera.sync.network_id,
                                              camera.camera_id, product_type='owl')
        if not isinstance(config, dict) or config.get('spotlight_compatible') is not True:
            raise HTTPException(400, 'This camera does not report a supported spotlight.')
        state = config.get('light_status')
        if state not in ('on', 'off'):
            raise HTTPException(502, 'Blink did not return a recognized light state.')
        return {'state': state}

    async def write_light(self, camera, enabled):
        current = await self.read_light(camera)
        state = 'on' if enabled else 'off'
        if current['state'] == state:
            return current
        result = await api.request_floodlight(camera.sync.blink, camera.sync.network_id,
                                            camera.camera_id, enabled)
        if (not isinstance(result, dict) or result.get('command') != 'accessory_lights_' + state or
                result.get('state_condition', result.get('state')) in ('failed', 'error') or 'code' in result):
            raise HTTPException(502, 'Blink did not accept the light command. The camera may be busy.')
        for attempt in range(5):
            confirmed = await self.read_light(camera)
            if confirmed['state'] == state:
                return confirmed
            if attempt < 4: await asyncio.sleep(.5)
        raise HTTPException(409, 'Blink has not confirmed the requested light state.')


class OutdoorSettings:
    profile = 'outdoor_sedona'
    label = 'Blink Outdoor'
    night_modes = {'off': 0, 'on': 1, 'auto': 2}
    intensities = {'low': 1, 'medium': 4, 'high': 7}
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
        for key, label, field, mapping in (
                ('night_vision', 'Night vision', 'illuminator_enable', self.night_modes),
                ('ir_intensity', 'IR intensity', 'illuminator_intensity', self.intensities)):
            value = config.get(field)
            if type(value) is int and value in mapping.values():
                settings.append({'key': key, 'label': label, 'kind': 'choice',
                                 'value': next(mode for mode, number in mapping.items() if number == value),
                                 'options': ['auto', 'on', 'off'] if key == 'night_vision' else list(mapping)})
        return settings + status_led_settings(config, ['off', 'recording'])

    async def write(self, camera, key, value):
        if key == 'status_led':
            return await write_status_led(self, camera, value)
        if key not in self.numeric_settings and key not in ('end_clip_early', 'night_vision', 'ir_intensity'):
            raise HTTPException(404, 'This setting is not supported for this camera type.')
        if key in self.numeric_settings:
            _, field, lower, upper = self.numeric_settings[key]
            options = [str(number) for number in range(lower, upper + 1)]
            if type(value) is not str or value not in options:
                raise HTTPException(422, f'This setting must be a whole number from {lower} to {upper}.')
            blink_value = int(value)
        elif key in ('night_vision', 'ir_intensity'):
            field, mapping = (('illuminator_enable', self.night_modes) if key == 'night_vision'
                              else ('illuminator_intensity', self.intensities))
            if type(value) is not str or value not in mapping:
                raise HTTPException(422, 'This value is not supported for this camera setting.')
            blink_value = mapping[value]
        else:
            field = 'early_termination'
            if value not in ('on', 'off'):
                raise HTTPException(422, 'End clip early must be On or Off.')
            blink_value = value == 'on'
        # Verify model-specific limits/support immediately before a clip change.
        if key in ('clip_length', 'end_clip_early', 'night_vision', 'ir_intensity'):
            settings = await self.read(camera)
            current = next((item for item in settings if item['key'] == key), None)
            if current is None:
                raise HTTPException(400, 'This setting is not supported for this camera.')
            if value not in current['options']:
                raise HTTPException(422, 'This value is not supported for this camera.')
            if key in ('night_vision', 'ir_intensity') and current['value'] == value:
                return settings
        # Send only the requested field; never replay an entire configuration.
        response = await api.request_update_config(
            camera.sync.blink, camera.sync.network_id, camera.camera_id,
            product_type='catalina', data=json.dumps({field: blink_value}))
        if response is None or response.status != 200:
            raise HTTPException(502, 'Blink did not accept the camera setting command.')
        result = await response.json()
        if (not isinstance(result, dict) or result.get('command') != 'config_set' or
                result.get('state_condition', result.get('state')) in ('failed', 'error') or 'code' in result):
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
        return settings + status_led_settings(config, ['off', 'recording'])

    async def write(self, camera, key, value):
        if key == 'status_led':
            return await write_status_led(self, camera, value)
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
    'hawk': Mini2Settings(),
    'sedona': OutdoorSettings(),
    'tulip': DoorbellSettings(),
}


def settings_capability(camera):
    adapter = SETTINGS_PROFILES.get(getattr(camera, 'product_type', None))
    return {'profile': adapter.profile if adapter else None, 'available': adapter is not None}


# Apply the same presentation order to fresh reads and confirmed changes.
SETTING_ORDER = {key: index for index, key in enumerate((
    'motion_sensitivity', 'clip_length', 'retrigger_time', 'end_clip_early',
    'night_vision', 'ir_intensity', 'status_led', 'manual_light_duration', 'motion_light_activation'))}


class DeviceSettings:
    def __init__(self, controller):
        self.controller = controller
        self.locks = {}

    @property
    def busy(self):
        return any(lock.locked() for lock in self.locks.values())

    async def execute(self, device_id, system_id, key=None, value=None, *, light=False, zones=False, zone_request=None):
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
        if light and camera.product_type != 'hawk':
            raise HTTPException(400, 'Light control is only supported for Mini 2 cameras.')
        if not (camera.online and camera.sync.available):
            raise HTTPException(503, 'This camera or its Sync Module is offline.')
        lock = self.locks.setdefault((system_id, device_id), asyncio.Lock())
        try:
            async with asyncio.timeout(25):
                async with lock:
                    if zones:
                        return await execute_zones(camera, zone_request)
                    if light:
                        return (await adapter.read_light(camera) if value is None
                                else await adapter.write_light(camera, value))
                    setting = (await adapter.read(camera) if key is None
                               else await adapter.write(camera, key, value))
                    settings = setting if isinstance(setting, list) else [setting]
                    return {'profile': adapter.profile, 'label': adapter.label,
                            'settings': sorted(settings, key=lambda item: SETTING_ORDER.get(item['key'], len(SETTING_ORDER)))}
        except HTTPException:
            raise
        except TimeoutError as exc:
            raise HTTPException(504, 'Camera settings request timed out.') from exc
        except Exception as exc:
            raise HTTPException(502, 'Could not communicate with Blink camera settings.') from exc


def install_device_settings_api(app, controller):
    service = DeviceSettings(controller)

    @app.get('/api/v1/devices/{device_id}/motion-zones')
    async def read_zones(device_id: str, system_id: str):
        return await service.execute(device_id, system_id, zones=True)

    @app.put('/api/v1/devices/{device_id}/motion-zones')
    async def write_zones(device_id: str, system_id: str, request: ZoneChange):
        return await service.execute(device_id, system_id, zones=True, zone_request=request)

    @app.get('/api/v1/devices/{device_id}/light')
    async def read_light(device_id: str, system_id: str):
        return await service.execute(device_id, system_id, light=True)

    @app.put('/api/v1/devices/{device_id}/light')
    async def write_light(device_id: str, system_id: str, request: LightChange):
        return await service.execute(device_id, system_id, value=request.enabled, light=True)

    @app.get('/api/v1/devices/{device_id}/settings')
    async def read_settings(device_id: str, system_id: str):
        return await service.execute(device_id, system_id)

    @app.put('/api/v1/devices/{device_id}/settings/{key}')
    async def write_setting(device_id: str, key: str, system_id: str, request: SettingChange):
        return await service.execute(device_id, system_id, key, request.value)

    return service
