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
        for kind in ('future-camera',):
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


CLIP_FIELDS = {'video_length': 30, 'clip_max_length': 60, 'alert_interval': 10, 'early_termination_supported': True, 'early_termination': True}


class OutdoorSettingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.camera = NS(camera_id=7, product_type='sedona', online=True,
                         sync=NS(network_id=2, available=True, blink=object()))
        self.service = DeviceSettings(NS(blink=NS(cameras={'Outdoor': self.camera})))
        self.read = AsyncMock(return_value={'camera': [{**CLIP_FIELDS, 'id': 7, 'motion_sensitivity': 9}]})
        self.response = NS(status=200, json=AsyncMock(return_value={
            'command': 'config_set', 'state_condition': 'done'}))
        self.write = AsyncMock(return_value=self.response)
        for name, mock in [('request_get_config', self.read), ('request_update_config', self.write)]:
            patcher = patch('device_settings.api.' + name, mock)
            patcher.start();self.addCleanup(patcher.stop)

    async def test_live_shape_read_and_descriptor(self):
        result = await self.service.execute('7', '2')
        self.assertEqual(result['profile'], 'outdoor_sedona')
        self.assertEqual(result['settings'][0]['value'], '9')
        self.assertEqual(result['settings'][0]['options'], list('123456789'))
        self.assertTrue(settings_capability(self.camera)['available'])
        self.assertEqual(self.read.call_args.kwargs['product_type'], 'catalina')
        self.write.assert_not_called()

    async def test_bad_read_values_and_camera_identity_rejected(self):
        for value in [None, True, False, 0, 10, 1.0, '09', 'auto', '']:
            self.read.return_value = {'camera': [{**CLIP_FIELDS, 'id': 7, 'motion_sensitivity': value}]}
            with self.assertRaises(HTTPException):await self.service.execute('7', '2')
        for data in [None, {}, {'camera': []}, {'camera': {}},
                     {'camera': [{**CLIP_FIELDS, 'id': 8, 'motion_sensitivity': 9}]},
                     {'camera': [{'motion_sensitivity': 9}]}]:
            self.read.return_value = data
            with self.assertRaises(HTTPException):await self.service.execute('7', '2')

    async def test_write_only_one_numeric_field_and_confirm_all_values(self):
        import json
        for value in range(1, 10):
            self.write.reset_mock()
            self.read.return_value = {'camera': [{**CLIP_FIELDS, 'id': 7, 'motion_sensitivity': value}]}
            result = await self.service.execute('7', '2', 'motion_sensitivity', str(value))
            self.assertEqual(result['settings'][0]['value'], str(value))
            self.write.assert_awaited_once()
            self.assertEqual(json.loads(self.write.call_args.kwargs['data']), {'motion_sensitivity': value})
            self.assertEqual(self.write.call_args.kwargs['product_type'], 'catalina')

    async def test_invalid_values_and_keys_never_write(self):
        for key, value in [('motion_sensitivity', v) for v in ('auto', '0', '10', '1.5', True, 1)] + [('unknown', 'auto')]:
            with self.assertRaises(HTTPException):await self.service.execute('7', '2', key, value)
        self.write.assert_not_called();self.read.assert_not_called()

    async def test_rejection_and_busy_never_report_success(self):
        for response in [None, NS(status=503), NS(status=200, json=AsyncMock(return_value={'code': 307})),
                         NS(status=200, json=AsyncMock(return_value={'command': 'config_set', 'state_condition': 'failed'}))]:
            self.write.return_value = response
            with self.assertRaises(HTTPException) as error:
                await self.service.execute('7', '2', 'motion_sensitivity', '9')
            self.assertEqual(error.exception.status_code, 502)
        self.read.assert_not_called()

    async def test_delayed_readback_and_mismatch_do_not_repeat_write(self):
        self.read.side_effect = [{'camera': [{**CLIP_FIELDS, 'id': 7, 'motion_sensitivity': value}]} for value in [9, 8]]
        result = await self.service.execute('7', '2', 'motion_sensitivity', '8')
        self.assertEqual(result['settings'][0]['value'], '8');self.write.assert_awaited_once()
        self.write.reset_mock();self.read.side_effect = None
        with self.assertRaises(HTTPException) as error:
            await self.service.execute('7', '2', 'motion_sensitivity', '8')
        self.assertEqual(error.exception.status_code, 409);self.write.assert_awaited_once()

    async def test_mini_rejects_sensitivity_values_before_cloud_write(self):
        self.camera.product_type = 'owl';self.camera.async_set_night_vision = AsyncMock()
        with self.assertRaises(HTTPException):await self.service.execute('7', '2', 'night_vision', '9')
        self.camera.async_set_night_vision.assert_not_called()

    async def test_clip_descriptors_and_model_limit(self):
        result = await self.service.execute('7', '2')
        settings = {item['key']: item for item in result['settings']}
        self.assertEqual(set(settings), {'motion_sensitivity', 'clip_length', 'retrigger_time', 'end_clip_early'})
        self.assertEqual(settings['clip_length']['value'], '30')
        self.assertEqual(settings['clip_length']['options'][0], '5')
        self.assertEqual(settings['clip_length']['options'][-1], '60')
        self.assertEqual(settings['retrigger_time']['options'][0], '10')
        self.assertEqual(settings['retrigger_time']['options'][-1], '60')
        self.assertEqual(settings['end_clip_early']['value'], 'on')
        self.read.return_value['camera'][0]['clip_max_length'] = 30
        result = await self.service.execute('7', '2')
        self.assertEqual(result['settings'][1]['options'][-1], '30')
        with self.assertRaises(HTTPException):await self.service.execute('7', '2', 'clip_length', '60')
        self.write.assert_not_called()

    async def test_clip_changes_send_only_requested_field_and_return_all_settings(self):
        import json
        for key, field, value, expected in [
            ('clip_length', 'video_length', '5', 5), ('clip_length', 'video_length', '60', 60),
            ('retrigger_time', 'alert_interval', '10', 10), ('retrigger_time', 'alert_interval', '60', 60),
            ('end_clip_early', 'early_termination', 'on', True),
            ('end_clip_early', 'early_termination', 'off', False)]:
            self.write.reset_mock()
            self.read.return_value['camera'][0][field] = expected
            result = await self.service.execute('7', '2', key, value)
            self.write.assert_awaited_once()
            payload = json.loads(self.write.call_args.kwargs['data'])
            self.assertEqual(payload, {field: expected})
            self.assertIs(type(payload[field]), type(expected))
            self.assertEqual(len(result['settings']), 4)
            self.assertEqual(next(item['value'] for item in result['settings'] if item['key'] == key), value)

    async def test_invalid_clip_inputs_never_contact_blink(self):
        for key, values in [('clip_length', ['4', '61', '5.5', 'auto', 5, True]),
                            ('retrigger_time', ['9', '61', 'auto', 10, False]),
                            ('end_clip_early', ['auto', '1', True, None])]:
            for value in values:
                with self.assertRaises(HTTPException):await self.service.execute('7', '2', key, value)
        self.read.assert_not_called();self.write.assert_not_called()

    async def test_unsupported_early_end_is_omitted_and_cannot_be_written(self):
        self.read.return_value['camera'][0]['early_termination_supported'] = False
        result = await self.service.execute('7', '2')
        self.assertNotIn('end_clip_early', [item['key'] for item in result['settings']])
        with self.assertRaises(HTTPException):await self.service.execute('7', '2', 'end_clip_early', 'on')
        self.write.assert_not_called()

    async def test_invalid_clip_readback_never_defaults(self):
        config = self.read.return_value['camera'][0]
        for field, values in [('video_length', [None, True, 4, 61, '5.0']),
                              ('alert_interval', [None, False, 9, 61]),
                              ('clip_max_length', [None, True, 4, 61]),
                              ('early_termination', [None, 0, 1, 'false'])]:
            original = config[field]
            for value in values:
                config[field] = value
                with self.assertRaises(HTTPException):await self.service.execute('7', '2')
            config[field] = original

    async def test_clip_confirmation_checks_requested_key_not_an_unrelated_value(self):
        # Sensitivity is 9 but clip length is 30: asking for 9 seconds must not
        # pass merely because another setting already contains the string "9".
        with self.assertRaises(HTTPException) as error:
            await self.service.execute('7', '2', 'clip_length', '9')
        self.assertEqual(error.exception.status_code, 409)
        self.write.assert_awaited_once()

    def test_wire_choice_validation(self):
        for value in ['on', 'off', '5', '10', '30', '60']:
            self.assertEqual(SettingChange(value=value).value, value)
        for value in ['0', '61', '5.5', '05', True, 30, None]:
            with self.assertRaises(ValidationError):SettingChange(value=value)


class DoorbellTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.blink = NS(account_id=3, urls=NS(base_url='https://example.invalid'))
        self.camera = NS(camera_id=7, product_type='tulip', online=True,
                         sync=NS(network_id=2, available=True, blink=self.blink))
        self.service = DeviceSettings(NS(blink=NS(cameras={'Doorbell': self.camera})))
        self.config = dict(clip_length_max=30, clip_length=10, retrigger_time=10,
                           motion_sensitivity=9, early_termination=True,
                           early_termination_supported=True, illuminator_enable_v2='auto',
                           illuminator_intensity=7)
        self.read = AsyncMock(side_effect=lambda *args, **kwargs: dict(self.config))
        self.response = NS(status=200, json=AsyncMock(return_value={'id': 1, 'network_id': 2, 'command': 'config_set'}))
        self.wait = AsyncMock(return_value=True)
        async def write(*args, **kwargs):
            import json
            payload = json.loads(kwargs['data'])
            if 'illuminator_enable' in payload:
                payload['illuminator_enable_v2'] = payload.pop('illuminator_enable')
            self.config.update(payload)
            return self.response
        self.write = AsyncMock(side_effect=write)
        for name, mock in [('http_get', self.read), ('http_post', self.write), ('wait_for_command', self.wait)]:
            patcher = patch('device_settings.api.' + name, mock)
            patcher.start();self.addCleanup(patcher.stop)

    async def test_doorbell_route_limit_and_modes(self):
        result = await self.service.execute('7', '2')
        self.assertEqual(result['profile'], 'doorbell_tulip')
        self.assertEqual(result['settings'][1]['options'][-1], '30')
        self.assertEqual(result['settings'][-2]['value'], 'auto')
        self.assertEqual(self.read.call_args.args[1], 'https://example.invalid/api/v1/accounts/3/networks/2/doorbells/7/config')
        self.write.assert_not_called()

    async def test_already_current_value_is_confirmed_by_read_without_write(self):
        result = await self.service.execute('7', '2', 'clip_length', '10')
        self.assertEqual(result['settings'][1]['value'], '10')
        self.read.assert_awaited_once();self.write.assert_not_called()

    async def test_every_control_isolated_payload_and_full_confirmation(self):
        import json
        for key, value, field, expected in [('motion_sensitivity', '6', 'motion_sensitivity', 6),
            ('clip_length', '30', 'clip_length', 30), ('retrigger_time', '60', 'retrigger_time', 60),
            ('end_clip_early', 'off', 'early_termination', False),
            ('night_vision', 'off', 'illuminator_enable', 'off'),
            ('night_vision', 'on', 'illuminator_enable', 'on'),
            ('night_vision', 'auto', 'illuminator_enable', 'auto'),
            ('ir_intensity', 'low', 'illuminator_intensity', 1),
            ('ir_intensity', 'medium', 'illuminator_intensity', 4),
            ('ir_intensity', 'high', 'illuminator_intensity', 7)]:
            self.write.reset_mock()
            result = await self.service.execute('7', '2', key, value)
            self.write.assert_awaited_once()
            self.assertEqual(json.loads(self.write.call_args.kwargs['data']), {field: expected})
            self.assertEqual(len(result['settings']), 6)
            self.assertEqual(next(s['value'] for s in result['settings'] if s['key']==key), value)

    async def test_invalid_values_and_unsupported_keys_cannot_write(self):
        for key, value in [('clip_length', '60'), ('clip_length', '4'), ('retrigger_time', '9'),
                           ('night_vision', '1'), ('ir_intensity', '4'), ('ir_intensity', 'auto'), ('end_clip_early', 'auto'), ('unknown', 'on')]:
            with self.assertRaises(HTTPException):await self.service.execute('7', '2', key, value)
        self.write.assert_not_called()
        self.config['early_termination_supported'] = False
        with self.assertRaises(HTTPException):await self.service.execute('7', '2', 'end_clip_early', 'off')
        self.write.assert_not_called()

    async def test_missing_unknown_and_legacy_ir_never_guessed(self):
        self.config['illuminator_enable'] = 'auto'
        for key, value in [('clip_length_max', 60), ('clip_length', True), ('retrigger_time', None),
                           ('illuminator_enable_v2', None), ('illuminator_enable_v2', 'unknown'), ('illuminator_intensity', True),
                           ('illuminator_intensity', 2), ('illuminator_intensity', None)]:
            original = self.config[key];self.config[key] = value
            with self.assertRaises(HTTPException):await self.service.execute('7', '2')
            self.config[key] = original

    async def test_busy_rejection_and_mismatch_do_not_retry_write(self):
        self.write.side_effect = None
        for response in [None, NS(status=503), NS(status=200, json=AsyncMock(return_value={'code':307}))]:
            self.write.return_value = response
            with self.assertRaises(HTTPException):await self.service.execute('7', '2', 'clip_length', '20')
        self.write.reset_mock();self.write.return_value = self.response
        with self.assertRaises(HTTPException) as error:await self.service.execute('7', '2', 'clip_length', '20')
        self.assertEqual(error.exception.status_code, 409);self.write.assert_awaited_once()


if __name__ == '__main__':unittest.main()
