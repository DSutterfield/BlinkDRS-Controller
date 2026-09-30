"""Read camera signals and temperature on demand without camera commands."""
import asyncio
import math
import time

from blinkpy import api


def reading(value, unit):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    if unit == 'dbm' and -120 <= value < 0:
        return value
    if unit == 'level_5' and 0 <= value <= 5:
        return value
    return None


def sync_wifi_strength(blink, system):
    """Use the refreshed homescreen entry for this physical Sync Module."""
    home = getattr(blink, 'homescreen', None)
    modules = home.get('sync_modules', []) if isinstance(home, dict) else []
    module_id = getattr(system, 'sync_id', None)
    entry = next((item for item in modules or []
                  if isinstance(item, dict) and module_id is not None
                  and str(item.get('id')) == str(module_id)), None)
    if entry is None:
        entry = getattr(system, 'summary', None)
    return reading(entry.get('wifi_strength'), 'level_5') if isinstance(entry, dict) else None


def extract_signals(config):
    if not isinstance(config, dict):
        return {}
    levels = config.get('signals')
    levels = levels if isinstance(levels, dict) else {}
    result = {}
    # Blink's Doorbell config reports native Fahrenheit as temp, whereas
    # Outdoor configuration uses temperature. Zero is a valid temperature.
    temperature = config.get('temp', config.get('temperature'))
    if (not isinstance(temperature, bool) and isinstance(temperature, (int, float))
            and math.isfinite(temperature)):
        result['temperature_f'] = temperature
        result['temperature_c'] = round((temperature - 32) * 5 / 9, 1)
    for label, dbm_keys, level_key in (
        ('wifi', ('wifi_rssi', 'wifi_strength'), 'wifi'),
        ('sync', ('lfr_strength',), 'lfr'),
    ):
        value = reading(levels.get(level_key), 'level_5')
        unit = 'level_5'
        if value is None:
            unit = 'dbm'
            value = next((v for key in dbm_keys
                          if (v := reading(config.get(key), unit)) is not None), None)
        if value is not None:
            result[label + '_signal'] = value
            result[label + '_signal_unit'] = unit
    return result


class CameraSignals:
    """Bound cloud reads and cache them for one minute across UI refreshes."""
    def __init__(self):
        self._cache = {}
        self._lock = asyncio.Lock()

    async def read(self, blink):
        async with self._lock:
            result = {}
            slots = asyncio.Semaphore(3)

            async def load(camera, key):
                values = {}
                try:
                    async with slots:
                        async with asyncio.timeout(3):
                            kind = str(camera.product_type or '').lower()
                            if kind == 'tulip':
                                values = extract_signals(camera.sync.get_unique_info(camera.name))
                                url = (f'{blink.urls.base_url}/api/v1/accounts/{blink.account_id}'
                                       f'/networks/{camera.sync.network_id}/doorbells/{camera.camera_id}/config')
                                config = await api.http_get(blink, url)
                            elif kind in ('owl', 'hawk'):
                                config = await api.request_get_config(
                                    blink, camera.sync.network_id, camera.camera_id,
                                    product_type='owl')
                            elif kind == 'sedona':
                                config = await api.request_camera_info(
                                    blink, camera.sync.network_id, camera.camera_id)
                                entries = config.get('camera') if isinstance(config, dict) else None
                                config = next((item for item in entries or []
                                               if isinstance(item, dict) and
                                               str(item.get('id')) == str(camera.camera_id)), None)
                            else:
                                return
                            values.update(extract_signals(config))
                except Exception:
                    # Telemetry is optional; do not fail the camera list or
                    # present an old cached measurement as a fresh one.
                    pass
                self._cache[key] = (time.monotonic(), values)
                result[key] = values

            tasks = []
            for camera in blink.cameras.values():
                key = (str(camera.sync.network_id), str(camera.camera_id))
                cached = self._cache.get(key)
                if cached and time.monotonic() - cached[0] < 60:
                    result[key] = cached[1]
                else:
                    tasks.append(asyncio.create_task(load(camera, key)))
            try:
                if tasks:
                    await asyncio.wait(tasks, timeout=8)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            return result
