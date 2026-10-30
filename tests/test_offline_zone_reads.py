import tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from fastapi import HTTPException
from device_settings import DeviceSettings
from identity_motion_zones import cached_zones,remember_zones,MAX_CACHE_AGE

def response():
 return dict(basic_rows=1,basic_columns=2,sub_rows=1,sub_columns=1,cells=[0,1],revision='a'*64,mode='basic',advanced_available=True)

class OfflineZoneReads(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.db=Path(self.temp.name)/'catalog.sqlite'
  self.camera=NS(camera_id=9,product_type='sedona',online=False,sync=NS(network_id=2,available=True))
  self.service=DeviceSettings(NS(blink=NS(cameras={'camera':self.camera}),catalog_db_path=self.db))
 async def test_offline_camera_reads_cloud_zones_and_refreshes_expired_cache(self):
  remember_zones(self.db,9,2,response(),time.time()-MAX_CACHE_AGE-1)
  with patch('device_settings.execute_zones',AsyncMock(return_value=response())) as read:
   result=await self.service.execute('9','2',zones=True)
  read.assert_awaited_once_with(self.camera,None);self.assertEqual(result,response())
  policy=cached_zones(self.db,9,2,time.time());self.assertEqual(policy.cells,(0,1));self.assertLess(policy.settings_age_seconds,5)
 async def test_offline_sync_module_can_read_saved_cloud_zones(self):
  self.camera.online=True;self.camera.sync.available=False
  with patch('device_settings.execute_zones',AsyncMock(return_value=response())) as read:
   self.assertEqual(await self.service.execute('9','2',zones=True),response())
  read.assert_awaited_once()
 async def test_offline_zone_write_still_rejected_without_cloud_call(self):
  with patch('device_settings.execute_zones',AsyncMock()) as call:
   with self.assertRaises(HTTPException) as error:await self.service.execute('9','2',zones=True,zone_request=object())
  self.assertEqual(error.exception.status_code,503);call.assert_not_called()
 async def test_offline_regular_settings_and_setting_writes_still_rejected(self):
  for kwargs in [{},{'key':'night_vision','value':'on'}]:
   with patch('device_settings.execute_zones',AsyncMock()) as call:
    with self.assertRaises(HTTPException) as error:await self.service.execute('9','2',**kwargs)
   self.assertEqual(error.exception.status_code,503);call.assert_not_called()
 async def test_unavailable_cloud_does_not_freshen_old_cache(self):
  remember_zones(self.db,9,2,response(),time.time()-MAX_CACHE_AGE-1)
  with patch('device_settings.execute_zones',AsyncMock(side_effect=HTTPException(502,'Unrecognized motion-zone mask.'))):
   with self.assertRaises(HTTPException) as error:await self.service.execute('9','2',zones=True)
  self.assertEqual(error.exception.status_code,502)
  with self.assertRaises(ValueError):cached_zones(self.db,9,2,time.time())
 async def test_wrong_system_is_not_bypassed_by_read_permission(self):
  with patch('device_settings.execute_zones',AsyncMock()) as call:
   with self.assertRaises(HTTPException) as error:await self.service.execute('9','wrong',zones=True)
  self.assertEqual(error.exception.status_code,404);call.assert_not_called()
