import json
import unittest
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
import test_device_settings


class OutdoorIrTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        test_device_settings.OutdoorSettingsTests.setUp(self)
        self.config = self.read.return_value['camera'][0]
        self.config.update(illuminator_enable=2, illuminator_intensity=4)
        async def write(*args, **kwargs):
            self.config.update(json.loads(kwargs['data']))
            return self.response
        self.write.side_effect=write

    async def test_all_modes_use_native_numbers_and_isolated_payload(self):
        for key,field,modes in [('night_vision','illuminator_enable',{'off':0,'on':1,'auto':2}),
                               ('ir_intensity','illuminator_intensity',{'low':1,'high':7,'medium':4})]:
            for mode,number in modes.items():
                before=dict(self.config);self.write.reset_mock()
                result=await self.service.execute('7','2',key,mode)
                self.assertEqual(next(s['value'] for s in result['settings'] if s['key']==key),mode)
                self.write.assert_awaited_once()
                self.assertEqual(json.loads(self.write.call_args.kwargs['data']),{field:number})
                self.assertEqual(self.write.call_args.kwargs['product_type'],'catalina')
                self.assertEqual({k:v for k,v in before.items() if k!=field},
                                 {k:v for k,v in self.config.items() if k!=field})

    async def test_same_values_and_invalid_choices_never_write(self):
        for key,value in [('night_vision','auto'),('ir_intensity','medium')]:
            await self.service.execute('7','2',key,value)
        for key,value in [('night_vision','2'),('ir_intensity','4'),('night_vision',True)]:
            with self.assertRaises(HTTPException):await self.service.execute('7','2',key,value)
        self.write.assert_not_called()

    async def test_unknown_fields_omitted_without_blocking_other_settings(self):
        for value in (None,True,False,'auto',9):
            self.config.update(illuminator_enable=value,illuminator_intensity=value)
            result=await self.service.execute('7','2')
            self.assertNotIn('night_vision',[s['key'] for s in result['settings']])
            self.assertNotIn('ir_intensity',[s['key'] for s in result['settings']])
            self.assertIn('motion_sensitivity',[s['key'] for s in result['settings']])
            with self.assertRaises(HTTPException):await self.service.execute('7','2','night_vision','off')
        self.write.assert_not_called()

    async def test_mismatched_readback_never_repeats_write(self):
        self.write.side_effect=None
        with patch('device_settings.asyncio.sleep',new=AsyncMock()):
            with self.assertRaises(HTTPException) as error:
                await self.service.execute('7','2','night_vision','off')
        self.assertEqual(error.exception.status_code,409)
        self.write.assert_awaited_once()


if __name__=='__main__':unittest.main()
