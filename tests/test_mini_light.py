import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException, FastAPI
from pydantic import ValidationError
from device_settings import DeviceSettings, LightChange, SettingChange, install_device_settings_api


class MiniLightTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config=dict(illuminator_enable_v2='auto',led_state='off',spotlight_compatible=True,
                         spotlight_enabled=False,manual_light_duration=30,
                         manual_light_duration_options=[30,60,180,300,600],light_status='off')
        self.camera=NS(camera_id=7,product_type='hawk',online=True,
                       sync=NS(network_id=2,available=True,blink=object()))
        self.controller=NS(blink=NS(cameras={'Mini2':self.camera}))
        self.service=DeviceSettings(self.controller)
        self.read=AsyncMock(side_effect=lambda *a,**k:dict(self.config))
        async def config_write(*args,**kwargs):
            self.config.update(json.loads(kwargs['data']))
            return NS(status=200,json=AsyncMock(return_value={'command':'config_set'}))
        async def light_write(*args):
            self.config['light_status']='on' if args[-1] else 'off'
            return {'command':'accessory_lights_'+self.config['light_status']}
        self.write=AsyncMock(side_effect=config_write)
        self.light=AsyncMock(side_effect=light_write)
        for name,mock in [('request_get_config',self.read),('request_update_config',self.write),('request_floodlight',self.light)]:
            p=patch('device_settings.api.'+name,mock);p.start();self.addCleanup(p.stop)

    async def test_settings_are_mini2_only_and_follow_reported_timeout_options(self):
        result=await self.service.execute('7','2')
        self.assertEqual([s['key'] for s in result['settings']],
                         ['night_vision','status_led','manual_light_duration','motion_light_activation'])
        self.assertEqual(result['settings'][-2]['options'],['30','60','180','300','600'])
        self.config['manual_light_duration_options']=[30,60]
        with self.assertRaises(HTTPException):await self.service.execute('7','2','manual_light_duration','600')
        self.camera.product_type='owl'
        result=await self.service.execute('7','2')
        self.assertNotIn('manual_light_duration',[s['key'] for s in result['settings']])
        with self.assertRaises(HTTPException):await self.service.execute('7','2','motion_light_activation','on')
        with self.assertRaises(HTTPException):await self.service.execute('7','2',light=True)
        self.light.assert_not_called();self.write.assert_not_called()

    async def test_settings_change_only_selected_field_not_current_light(self):
        for key,value,field,native in [('manual_light_duration','600','manual_light_duration',600),
                                     ('motion_light_activation','on','spotlight_enabled',True)]:
            before=dict(self.config);self.write.reset_mock()
            result=await self.service.execute('7','2',key,value)
            self.assertEqual(json.loads(self.write.call_args.kwargs['data']),{field:native})
            self.assertEqual(next(s['value'] for s in result['settings'] if s['key']==key),value)
            self.assertEqual({k:v for k,v in before.items() if k!=field},{k:v for k,v in self.config.items() if k!=field})
        self.light.assert_not_called()

    async def test_light_toggle_confirms_state_without_changing_motion_preference(self):
        for enabled in (True,False):
            result=await self.service.execute('7','2',value=enabled,light=True)
            self.assertEqual(result['state'],'on' if enabled else 'off')
            self.assertFalse(self.config['spotlight_enabled']);self.assertEqual(self.config['manual_light_duration'],30)
        self.assertEqual(self.light.await_count,2);self.write.assert_not_called()
        await self.service.execute('7','2',value=False,light=True)
        self.assertEqual(self.light.await_count,2)

    async def test_rejected_or_unconfirmed_command_never_repeated(self):
        self.light.side_effect=None
        for result,status in [(None,502),({'code':307},502),({'command':'accessory_lights_on','state_condition':'failed'},502),
                              ({'command':'accessory_lights_on'},409)]:
            self.light.return_value=result;self.light.reset_mock()
            with patch('device_settings.asyncio.sleep',new=AsyncMock()):
                with self.assertRaises(HTTPException) as error:
                    await self.service.execute('7','2',value=True,light=True)
            self.assertEqual(error.exception.status_code,status);self.light.assert_awaited_once()
            self.assertFalse(self.service.busy)

    async def test_missing_support_wrong_camera_and_unknown_state_never_write(self):
        with self.assertRaises(HTTPException):await self.service.execute('7','99',value=True,light=True)
        self.config['light_status']='unknown'
        with self.assertRaises(HTTPException):await self.service.execute('7','2',value=True,light=True)
        self.config['spotlight_compatible']=False
        with self.assertRaises(HTTPException):await self.service.execute('7','2','manual_light_duration','60')
        self.light.assert_not_called();self.write.assert_not_called()

    def test_request_validation_and_routes(self):
        for value in ('true',1,None):
            with self.assertRaises(ValidationError):LightChange(enabled=value)
        for value in ('180','300','600'):self.assertEqual(SettingChange(value=value).value,value)
        app=FastAPI();install_device_settings_api(app,self.controller)
        self.assertEqual(set(app.openapi()['paths']['/api/v1/devices/{device_id}/light']),{'get','put'})


if __name__=='__main__':unittest.main()
