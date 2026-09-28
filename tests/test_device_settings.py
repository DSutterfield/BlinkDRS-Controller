import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from device_settings import DeviceSettings, SettingChange, settings_capability, install_device_settings_api


class SettingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.camera = NS(camera_id=7, product_type='owl', online=True,
                         sync=NS(network_id=2, available=True, blink=object()),
                         async_set_night_vision=AsyncMock(return_value={}))
        self.controller = NS(blink=NS(cameras={'Mini': self.camera}))
        self.service = DeviceSettings(self.controller)
        self.read = AsyncMock(return_value={'illuminator_enable': 'auto'})
        patcher = patch('device_settings.api.request_get_config', self.read)
        patcher.start();self.addCleanup(patcher.stop)

    async def test_read_does_not_write(self):
        result = await self.service.execute('7', '2')
        self.assertEqual(result['profile'], 'original_mini')
        self.assertEqual(result['settings'][0]['value'], 'auto')
        self.camera.async_set_night_vision.assert_not_called()
        self.assertEqual(self.read.call_args.kwargs['product_type'], 'owl')

    async def test_confirmed_write(self):
        self.read.return_value = {'illuminator_enable': 'on'}
        result = await self.service.execute('7', '2', 'night_vision', 'on')
        self.assertEqual(result['settings'][0]['value'], 'on')
        self.camera.async_set_night_vision.assert_awaited_once_with('on')
        self.assertFalse(self.service.busy)

    async def test_current_mini_all_modes_use_v2_over_legacy(self):
        for mode, legacy in [('auto', 'auto'), ('on', 'on'), ('off', 'auto')]:
            self.read.return_value = {'illuminator_enable': legacy,
                                      'illuminator_enable_v2': mode,
                                      'night_vision_control': 'normal'}
            result = await self.service.execute('7', '2')
            self.assertEqual(result['settings'][0]['value'], mode)
            result = await self.service.execute('7', '2', 'night_vision', mode)
            self.assertEqual(result['settings'][0]['value'], mode)

    async def test_unknown_v2_does_not_fall_back_to_legacy_auto(self):
        for value in (None, True, False, 0, 2, '', 'unexpected'):
            self.read.return_value = {'illuminator_enable': 'auto', 'illuminator_enable_v2': value}
            with self.assertRaises(HTTPException) as caught:
                await self.service.execute('7', '2')
            self.assertEqual(caught.exception.status_code, 502)

    async def test_legacy_modes_supported_when_v2_absent(self):
        for value, expected in [('auto', 'auto'), ('on', 'on'), ('off', 'off'), (0, 'off'), (1, 'on'), (2, 'auto')]:
            self.read.return_value = {'illuminator_enable': value}
            result = await self.service.execute('7', '2')
            self.assertEqual(result['settings'][0]['value'], expected)

    async def test_rejected_write_not_reported_successful(self):
        self.camera.async_set_night_vision.return_value = None
        with self.assertRaises(HTTPException) as caught:
            await self.service.execute('7', '2', 'night_vision', 'on')
        self.assertEqual(caught.exception.status_code, 502)
        self.read.assert_not_called()

    async def test_mismatch_not_reported_successful(self):
        with self.assertRaises(HTTPException) as caught:
            await self.service.execute('7', '2', 'night_vision', 'on')
        self.assertEqual(caught.exception.status_code, 409)
        self.assertNotIn('Refresh', caught.exception.detail)
        self.assertEqual(self.read.await_count, 3)
        self.camera.async_set_night_vision.assert_awaited_once()

    async def test_unsupported_families_and_unknown_types_never_contact_blink(self):
        for kind in ('hawk', 'sedona', 'tulip', 'future-camera'):
            self.camera.product_type = kind
            self.assertFalse(settings_capability(self.camera)['available'])
            with self.assertRaises(HTTPException):
                await self.service.execute('7', '2', 'night_vision', 'on')
        self.read.assert_not_called()
        self.camera.async_set_night_vision.assert_not_called()

    async def test_wrong_system_offline_and_unknown_key(self):
        with self.assertRaises(HTTPException) as caught:
            await self.service.execute('7', '99')
        self.assertEqual(caught.exception.status_code, 404)
        self.camera.online = False
        with self.assertRaises(HTTPException) as caught:
            await self.service.execute('7', '2')
        self.assertEqual(caught.exception.status_code, 503)
        self.camera.online = True
        with self.assertRaises(HTTPException) as caught:
            await self.service.execute('7', '2', 'unknown', 'on')
        self.assertEqual(caught.exception.status_code, 404)
        self.read.assert_not_called()
        self.camera.async_set_night_vision.assert_not_called()

    async def test_unknown_read_values_not_guessed(self):
        for value in (None, True, False, 'unexpected', 9):
            self.read.return_value = {'illuminator_enable': value}
            with self.assertRaises(HTTPException):
                await self.service.execute('7', '2')
        self.read.return_value = {'illuminator_enable': 2}
        self.assertEqual((await self.service.execute('7', '2'))['settings'][0]['value'], 'auto')

    async def test_exception_and_cancellation_release_lock(self):
        self.read.side_effect = RuntimeError('private upstream detail')
        with self.assertRaises(HTTPException) as caught:
            await self.service.execute('7', '2')
        self.assertNotIn('private', caught.exception.detail)
        self.assertFalse(self.service.busy)
        entered = asyncio.Event()
        async def wait(*args, **kwargs):
            entered.set();await asyncio.sleep(60)
        self.read.side_effect = wait
        task = asyncio.create_task(self.service.execute('7', '2'))
        await entered.wait();self.assertTrue(self.service.busy)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(self.service.busy)

    async def test_same_camera_operations_are_serialized(self):
        active = 0
        async def read(*args, **kwargs):
            nonlocal active
            active += 1;self.assertEqual(active, 1)
            await asyncio.sleep(.02)
            active -= 1
            return {'illuminator_enable': 'auto'}
        self.read.side_effect = read
        await asyncio.gather(self.service.execute('7', '2'), self.service.execute('7', '2'))

    def test_validation_and_routes(self):
        for value in ('bad', True, 1, None):
            with self.assertRaises(ValidationError):SettingChange(value=value)
        with self.assertRaises(ValidationError):SettingChange(value='auto', extra='bad')
        app = FastAPI();install_device_settings_api(app, self.controller)
        self.assertIn('/api/v1/devices/{device_id}/settings', app.openapi()['paths'])


if __name__ == '__main__':unittest.main()
