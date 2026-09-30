import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from device_settings import DeviceSettings, SettingChange


class StatusLedTests(unittest.IsolatedAsyncioTestCase):
    def setup_camera(self, kind):
        self.config = dict(led_state='off', led_enabled=True, button_led_mode='button_press_only',
                           illuminator_enable_v2='auto', illuminator_intensity=7,
                           id=7, motion_sensitivity=5, clip_max_length=60, video_length=30,
                           alert_interval=10, clip_length_max=30, clip_length=10, retrigger_time=10)
        if kind == 'tulip': self.config['valid_status_led_modes'] = ['off', 'recording']
        camera = NS(product_type=kind, camera_id=7, online=True,
                    sync=NS(network_id=2, available=True,
                            blink=NS(account_id=3, urls=NS(base_url='https://example.invalid'))))
        self.service = DeviceSettings(NS(blink=NS(cameras={'test': camera})))
        async def read(*args, **kwargs):
            return {'camera': [dict(self.config)]} if kind == 'sedona' else dict(self.config)
        async def write(*args, **kwargs):
            self.config.update(json.loads(kwargs['data']))
            return NS(status=200, json=AsyncMock(return_value={'command': 'config_set'}))
        self.read = AsyncMock(side_effect=read)
        self.write = AsyncMock(side_effect=write)
        for name, mock in [('request_get_config', self.read), ('http_get', self.read),
                           ('request_update_config', self.write), ('http_post', self.write)]:
            patcher = patch('device_settings.api.'+name, mock)
            patcher.start(); self.addCleanup(patcher.stop)

    async def test_all_families_options_single_field_and_readback(self):
        for kind in ('owl', 'hawk', 'sedona', 'tulip'):
            self.setup_camera(kind)
            result = await self.service.execute('7', '2')
            led = next(s for s in result['settings'] if s['key'] == 'status_led')
            self.assertEqual(led['options'], ['on', 'off', 'recording'] if kind in ('owl','hawk') else ['off','recording'])
            self.write.assert_not_called()
            for mode in led['options']:
                self.config['led_state'] = 'recording' if mode == 'off' else 'off'
                self.write.reset_mock()
                result = await self.service.execute('7','2','status_led',mode)
                self.write.assert_awaited_once()
                self.assertEqual(json.loads(self.write.call_args.kwargs['data']), {'led_state': mode})
                self.assertEqual(next(s['value'] for s in result['settings'] if s['key']=='status_led'),mode)
                self.assertTrue(self.config['led_enabled'])
                self.assertEqual(self.config['button_led_mode'],'button_press_only')
                if kind != 'tulip':
                    self.assertEqual(self.write.call_args.kwargs['product_type'], 'catalina' if kind=='sedona' else 'owl')
            if kind == 'hawk': self.assertIn('night_vision', [s['key'] for s in result['settings']])

    async def test_invalid_values_and_same_value_never_write(self):
        self.setup_camera('tulip')
        for mode in ('on','auto','future',True,1):
            with self.assertRaises(HTTPException): await self.service.execute('7','2','status_led',mode)
        await self.service.execute('7','2','status_led','off')
        self.write.assert_not_called()
        self.assertEqual(SettingChange(value='recording').value,'recording')

    async def test_reported_modes_restrict_options_unknown_fields_do_not_break_others(self):
        self.setup_camera('owl')
        self.config['valid_status_led_modes']=['off','future']
        with self.assertRaises(HTTPException): await self.service.execute('7','2','status_led','on')
        for value in (None, True, 0, 'future'):
            self.config['led_state']=value
            result=await self.service.execute('7','2')
            self.assertEqual([s['key'] for s in result['settings']],['motion_sensitivity', 'clip_length', 'retrigger_time', 'night_vision', 'ir_intensity'])
            with self.assertRaises(HTTPException): await self.service.execute('7','2','status_led','off')
        self.write.assert_not_called()

    async def test_rejection_and_mismatch_cannot_report_success_or_repeat_write(self):
        self.setup_camera('hawk')
        self.write.side_effect=None
        for response in (None, NS(status=503), NS(status=200,json=AsyncMock(return_value={'code':307})),
                         NS(status=200,json=AsyncMock(return_value={'command':'config_set','state':'failed'}))):
            self.write.return_value=response
            with self.assertRaises(HTTPException) as error:
                await self.service.execute('7','2','status_led','recording')
            self.assertEqual(error.exception.status_code,502)
        self.write.reset_mock()
        self.write.return_value=NS(status=200,json=AsyncMock(return_value={'command':'config_set'}))
        with patch('device_settings.asyncio.sleep',new=AsyncMock()):
            with self.assertRaises(HTTPException) as error:
                await self.service.execute('7','2','status_led','recording')
        self.assertEqual(error.exception.status_code,409)
        self.write.assert_awaited_once()
        self.assertFalse(self.service.busy)

    async def test_mini2_unknown_settings_remain_unsupported(self):
        self.setup_camera('hawk')
        with self.assertRaises(HTTPException): await self.service.execute('7','2','unknown','on')
        self.read.assert_not_called(); self.write.assert_not_called()


if __name__ == '__main__': unittest.main()
