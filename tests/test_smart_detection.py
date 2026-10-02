import asyncio,copy,json,unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from fastapi import HTTPException
from device_settings import DeviceSettings,SMART_DETECTION,smart_detection_settings

class SmartDetectionTests(unittest.IsolatedAsyncioTestCase):
 def setup_camera(self,kind):
  self.config=dict(id=7,motion_sensitivity=5,clip_max_length=60,video_length=30,alert_interval=10,
   clip_length_max=30,clip_length=10,retrigger_time=10,illuminator_enable_v2='auto',illuminator_intensity=4,
   detection_modes=dict(person_detection=True,vehicle_detection=False,motion_detection=True,future_mode=True))
  self.camera=NS(product_type=kind,camera_id=7,online=True,sync=NS(network_id=2,available=True,
   blink=NS(account_id=3,urls=NS(base_url='https://example.invalid'))))
  self.service=DeviceSettings(NS(blink=NS(cameras={'test':self.camera})))
  async def read(*a,**k):return {'camera':[copy.deepcopy(self.config)]} if kind=='sedona' else copy.deepcopy(self.config)
  async def write(*a,**k):
   self.config.update(json.loads(k['data']))
   return NS(status=200,json=AsyncMock(return_value={'command':'config_set'}))
  self.read=AsyncMock(side_effect=read);self.write=AsyncMock(side_effect=write)
  for name,mock in [('http_get',self.read),('request_get_config',self.read),('http_post',self.write)]:
   p=patch('device_settings.api.'+name,mock);p.start();self.addCleanup(p.stop)
 async def test_each_switch_each_family_preserves_other_fields(self):
  for kind,route in [('hawk','/api/v1/accounts/3/networks/2/owls/7/config'),('sedona','/api/v2/accounts/3/networks/2/cameras/7/config'),('tulip','/api/v1/accounts/3/networks/2/doorbells/7/config')]:
   self.setup_camera(kind)
   settings=(await self.service.execute('7','2'))['settings']
   self.assertEqual([x['label'] for x in settings if x['key'] in SMART_DETECTION],['Person Detected','Vehicle Detected','Other Motion'])
   for key,(_,field) in SMART_DETECTION.items():
    for value in ('off','on'):
     self.config['detection_modes'][field]=value!='on';before=copy.deepcopy(self.config);self.write.reset_mock()
     result=await self.service.execute('7','2',key,value)
     self.assertEqual(next(x['value'] for x in result['settings'] if x['key']==key),value)
     self.write.assert_awaited_once();self.assertTrue(self.write.call_args.args[1].endswith(route))
     expected=copy.deepcopy(before['detection_modes']);expected[field]=value=='on'
     self.assertEqual(json.loads(self.write.call_args.kwargs['data']),{'detection_modes':expected})
     before['detection_modes']=expected;self.assertEqual(self.config,before)
 async def test_original_mini_excludes_and_rejects_switches(self):
  self.setup_camera('owl')
  self.assertFalse(any(x['key'] in SMART_DETECTION for x in (await self.service.execute('7','2'))['settings']))
  with self.assertRaises(HTTPException):await self.service.execute('7','2','smart_person','off')
  self.write.assert_not_called()
 async def test_unknown_or_incomplete_modes_omitted(self):
  for modes in (None,{},dict(person_detection=True,vehicle_detection=1,motion_detection=False)):
   self.assertEqual(smart_detection_settings({'detection_modes':modes}),[])
 async def test_invalid_and_unchanged_values_do_not_write(self):
  self.setup_camera('hawk')
  await self.service.execute('7','2','smart_person','on')
  for value in ('auto','1',True):
   with self.assertRaises(HTTPException):await self.service.execute('7','2','smart_person',value)
  self.write.assert_not_called()
 async def test_unconfirmed_write_is_not_repeated(self):
  self.setup_camera('hawk');self.write.side_effect=None
  self.write.return_value=NS(status=200,json=AsyncMock(return_value={'command':'config_set'}))
  with patch('device_settings.asyncio.sleep',new=AsyncMock()):
   with self.assertRaises(HTTPException) as e:await self.service.execute('7','2','smart_person','off')
  self.assertEqual(e.exception.status_code,409);self.write.assert_awaited_once()
 async def test_compact_acknowledgement_confirms_and_checks_network(self):
  self.setup_camera('hawk')
  original=self.write.side_effect
  async def compact(*a,**k):
   await original(*a,**k)
   return NS(status=200,json=AsyncMock(return_value={'id':123,'network_id':2,'state':'done'}))
  self.write.side_effect=compact
  await self.service.execute('7','2','smart_person','off')
  self.write.side_effect=None
  self.write.return_value=NS(status=200,json=AsyncMock(return_value={'id':123,'network_id':8,'state':'done'}))
  with self.assertRaises(HTTPException) as e:await self.service.execute('7','2','smart_person','on')
  self.assertEqual(e.exception.status_code,502)
 async def test_rejected_write(self):
  self.setup_camera('tulip');self.write.side_effect=None;self.write.return_value=NS(status=403)
  with self.assertRaises(HTTPException) as e:await self.service.execute('7','2','smart_person','off')
  self.assertEqual(e.exception.status_code,502)
 async def test_wrong_outdoor_camera_rejected(self):
  self.setup_camera('sedona');self.config['id']=8
  with self.assertRaises(HTTPException):await self.service.execute('7','2','smart_person','off')
  self.write.assert_not_called()

if __name__=='__main__':unittest.main()
