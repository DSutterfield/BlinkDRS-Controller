import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from device_settings import DeviceSettings


class MiniSettingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = dict(motion_sensitivity=5, clip_length=30, clip_length_max=30,
                           retrigger_time=10, early_termination=False, early_termination_supported=True,
                           illuminator_enable='auto', illuminator_enable_v2='auto',
                           illuminator_intensity=4, led_state='recording')
        self.camera = NS(camera_id=7, product_type='owl', online=True,
                         sync=NS(network_id=2, available=True, blink=object()))
        self.service = DeviceSettings(NS(blink=NS(cameras={'Mini':self.camera})))
        self.read = AsyncMock(side_effect=lambda *a,**k:dict(self.config))
        async def write(*args, **kwargs):
            data=json.loads(kwargs['data'])
            if 'illuminator_enable' in data:
                data['illuminator_enable_v2']=data.pop('illuminator_enable')
            self.config.update(data)
            return NS(status=200,json=AsyncMock(return_value={'command':'config_set'}))
        self.write=AsyncMock(side_effect=write)
        for name,mock in [('request_get_config',self.read),('request_update_config',self.write)]:
            p=patch('device_settings.api.'+name,mock);p.start();self.addCleanup(p.stop)

    async def test_both_families_expose_controls_with_model_limit(self):
        for kind in ('owl','hawk'):
            self.camera.product_type=kind
            result=await self.service.execute('7','2')
            settings={s['key']:s for s in result['settings']}
            self.assertEqual(set(settings),{'night_vision','motion_sensitivity','clip_length',
                                           'retrigger_time','end_clip_early','ir_intensity','status_led'})
            self.assertEqual(settings['clip_length']['options'],[str(n) for n in range(5,31)])
            self.assertEqual(settings['ir_intensity']['value'],'medium')
            self.assertEqual(self.read.call_args.kwargs['product_type'],'owl')
        self.write.assert_not_called()

    async def test_each_additional_control_changes_one_field_and_confirms(self):
        for kind in ('owl','hawk'):
            self.camera.product_type=kind
            for key,target,field,native in [('motion_sensitivity','6','motion_sensitivity',6),
                    ('clip_length','20','clip_length',20),('retrigger_time','20','retrigger_time',20),
                    ('end_clip_early','on','early_termination',True),('ir_intensity','low','illuminator_intensity',1),
                    ('ir_intensity','high','illuminator_intensity',7),('ir_intensity','medium','illuminator_intensity',4)]:
                before=dict(self.config);self.write.reset_mock()
                result=await self.service.execute('7','2',key,target)
                self.assertEqual(next(s['value'] for s in result['settings'] if s['key']==key),target)
                if before[field]!=native:
                    self.write.assert_awaited_once()
                    self.assertEqual(json.loads(self.write.call_args.kwargs['data']),{field:native})
                    self.assertEqual(self.write.call_args.kwargs['product_type'],'owl')
                else:self.write.assert_not_called()
                self.assertEqual({k:v for k,v in self.config.items() if k!=field},
                                 {k:v for k,v in before.items() if k!=field})
            self.config.update(motion_sensitivity=5,clip_length=30,retrigger_time=10,early_termination=False)

    async def test_mini2_night_vision_uses_owl_route_and_v2_readback(self):
        self.camera.product_type='hawk'
        for mode in ('off','on','auto'):
            self.write.reset_mock()
            result=await self.service.execute('7','2','night_vision',mode)
            self.assertEqual(next(s['value'] for s in result['settings'] if s['key']=='night_vision'),mode)
            self.assertEqual(json.loads(self.write.call_args.kwargs['data']),{'illuminator_enable':mode})
            self.assertEqual(self.write.call_args.kwargs['product_type'],'owl')

    async def test_invalid_or_unreported_controls_never_write(self):
        for key,value in [('motion_sensitivity','10'),('clip_length','60'),('retrigger_time','9'),
                          ('end_clip_early','auto'),('ir_intensity','4'),('clip_length',True)]:
            with self.assertRaises(HTTPException):await self.service.execute('7','2',key,value)
        self.config['early_termination_supported']=False
        with self.assertRaises(HTTPException):await self.service.execute('7','2','end_clip_early','on')
        for field in ('motion_sensitivity','clip_length_max','retrigger_time','illuminator_intensity'):
            self.config[field]=None
        result=await self.service.execute('7','2')
        self.assertEqual([s['key'] for s in result['settings']],['night_vision','status_led'])
        self.write.assert_not_called()

    async def test_failed_or_unconfirmed_changes_never_repeat_write(self):
        self.write.side_effect=None
        for response,status in [(None,502),(NS(status=200,json=AsyncMock(return_value={'code':307})),502),
                               (NS(status=200,json=AsyncMock(return_value={'command':'config_set'})),409)]:
            self.write.reset_mock();self.write.return_value=response
            with patch('device_settings.asyncio.sleep',new=AsyncMock()):
                with self.assertRaises(HTTPException) as error:
                    await self.service.execute('7','2','clip_length','20')
            self.assertEqual(error.exception.status_code,status)
            self.write.assert_awaited_once();self.assertFalse(self.service.busy)


if __name__=='__main__':unittest.main()
