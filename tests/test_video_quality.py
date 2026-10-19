import unittest,json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from fastapi import HTTPException
from device_settings import (video_quality_settings, write_video_quality, SettingChange,
 OriginalMiniSettings,Mini2Settings,OutdoorSettings,DoorbellSettings)

class QualityTests(unittest.IsolatedAsyncioTestCase):
 def test_choices_and_validation(self):
  for value,label in [('best','Best'),('standard','Standard'),('saver','Saver')]:
   row=video_quality_settings({'video_quality':value})[0]
   self.assertEqual(row['value'],label);self.assertEqual(row['options'],['Best','Standard','Saver'])
   self.assertEqual(SettingChange(value=label).value,label)
  for value in (True,1,None,'unexpected'):
   self.assertEqual(video_quality_settings({'video_quality':value}),[])
  self.assertEqual(video_quality_settings({'video_quality':'standard','video_quality_support':['standard','saver']})[0]['options'],['Standard','Saver'])
  self.assertEqual(video_quality_settings({'video_quality':'best','video_quality_support':['standard']}),[])
 async def test_all_family_routes_and_single_field(self):
  for kind,cls in [('owl',OriginalMiniSettings),('hawk',Mini2Settings),('sedona',OutdoorSettings),('tulip',DoorbellSettings)]:
   a=cls();a.read=AsyncMock(side_effect=[video_quality_settings({'video_quality':'standard'}),video_quality_settings({'video_quality':'best'})])
   c=NS(product_type=kind,camera_id=7,sync=NS(network_id=2,blink=NS(account_id=9,urls=NS(base_url='https://example.invalid'))))
   response=NS(status=200,json=AsyncMock(return_value={'command':'config_set'}))
   with patch('device_settings.api.request_update_config',AsyncMock(return_value=response)) as update,patch('device_settings.api.http_post',AsyncMock(return_value=response)) as post:
    result=await a.write(c,'video_quality','Best');self.assertEqual(result[0]['value'],'Best')
    called=post if kind=='tulip' else update;self.assertEqual(json.loads(called.call_args.kwargs['data']),{'video_quality':'best'})
    self.assertEqual(called.await_count,1)
    if kind!='tulip':self.assertEqual(called.call_args.kwargs['product_type'],'catalina' if kind=='sedona' else 'owl')
 async def test_noop_unsupported_and_restrictions(self):
  for settings,value,code in [(video_quality_settings({'video_quality':'best'}),'Best',None),([], 'Best',400),(video_quality_settings({'video_quality':'standard','video_quality_support':['standard']}),'Best',422)]:
   a=NS(read=AsyncMock(return_value=settings))
   with patch('device_settings.api.request_update_config',AsyncMock()) as update:
    if code:
     with self.assertRaises(HTTPException) as e:await write_video_quality(a,NS(),value)
     self.assertEqual(e.exception.status_code,code)
    else:self.assertEqual(await write_video_quality(a,NS(),value),settings)
    update.assert_not_called()
 async def test_rejection_and_unconfirmed_are_not_success(self):
  for response,code in [(None,502),(NS(status=200,json=AsyncMock(return_value={'command':'config_set','state':'failed'})),502),(NS(status=200,json=AsyncMock(return_value={'command':'config_set'})),409)]:
   a=NS(read=AsyncMock(return_value=video_quality_settings({'video_quality':'standard'})))
   c=NS(product_type='owl',camera_id=7,sync=NS(network_id=2,blink=object()))
   with patch('device_settings.api.request_update_config',AsyncMock(return_value=response)) as update,patch('device_settings.asyncio.sleep',AsyncMock()):
    with self.assertRaises(HTTPException) as e:await write_video_quality(a,c,'Best')
    self.assertEqual(e.exception.status_code,code);self.assertEqual(update.await_count,1)
