import unittest,json,sqlite3,tempfile,time
from pathlib import Path
from io import BytesIO
from urllib.error import HTTPError,URLError
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from identity_motion_zones import read_camera_zones,cache_path,remember_zones,MAX_CACHE_AGE,load_camera_zones


def response(cells=None):return dict(basic_rows=1,basic_columns=2,sub_rows=1,sub_columns=1,cells=cells or [0,1],revision='a'*64)


class ZoneCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.db=Path(self.temp.name)/'catalog.sqlite'
    def fetch(self,device=9,system=2):return read_camera_zones(self.db,device,system,'http://localhost:8000')
    def test_live_read_always_refreshes_and_offline_uses_latest(self):
        with patch('identity_motion_zones.urlopen',side_effect=[BytesIO(json.dumps(response()).encode()),BytesIO(json.dumps(response([1,0])).encode()),URLError('offline')]):
            self.assertEqual(self.fetch().cells,(0,1));self.assertEqual(self.fetch().cells,(1,0));cached=self.fetch()
        self.assertEqual(cached.cells,(1,0));self.assertEqual(cached.settings_source,'cached')
    def test_missing_wrong_camera_and_expired_cache_never_unmask(self):
        with patch('identity_motion_zones.urlopen',side_effect=URLError('offline')):
            with self.assertRaises(ValueError):self.fetch()
            remember_zones(self.db,9,2,response(),time.time()-MAX_CACHE_AGE-1)
            with self.assertRaises(ValueError):self.fetch()
            remember_zones(self.db,9,2,response(),time.time())
            with self.assertRaises(ValueError):self.fetch(device=10)
            data=json.loads(cache_path(self.db,9,2).read_text());data['camera_id']='10';cache_path(self.db,9,2).write_text(json.dumps(data))
            with self.assertRaises(ValueError):self.fetch()
    def test_corrupt_or_future_cache_never_unmask(self):
        target=cache_path(self.db,9,2);target.parent.mkdir()
        for content in ('{','[]',json.dumps(dict(camera_id='9',system_id='2',received_at=time.time()+100,zones=response())),json.dumps(dict(camera_id='9',system_id='2',received_at=time.time(),zones=[]))):
            target.write_text(content)
            with patch('identity_motion_zones.urlopen',side_effect=URLError('offline')):
                with self.assertRaises(ValueError):self.fetch()
    def test_malformed_live_response_does_not_use_previous_cache(self):
        remember_zones(self.db,9,2,response(),time.time())
        for content in ('bad JSON',json.dumps(response([1,2])),'[]'):
            with patch('identity_motion_zones.urlopen',return_value=BytesIO(content.encode())):
                with self.assertRaisesRegex(ValueError,'Unrecognized'):self.fetch()
    def test_unknown_camera_or_unsupported_zone_format_does_not_use_cache(self):
        remember_zones(self.db,9,2,response(),time.time())
        for code,detail in ((404,'Camera not found'),(400,'Unsupported format'),(502,'Unrecognized motion-zone mask.')):
            error=HTTPError('http://localhost',code,'error',{},BytesIO(json.dumps(dict(detail=detail)).encode()))
            with patch('identity_motion_zones.urlopen',side_effect=error):
                with self.assertRaises(ValueError):self.fetch()
    def test_offline_camera_uses_verified_cache(self):
        remember_zones(self.db,9,2,response(),time.time())
        error=HTTPError('http://localhost',503,'offline',{},BytesIO(b'{"detail":"This camera is offline"}'))
        with patch('identity_motion_zones.urlopen',side_effect=error):self.assertEqual(self.fetch().cells,(0,1))
    def test_cache_write_failure_keeps_valid_live_policy(self):
        with patch('identity_motion_zones.urlopen',return_value=BytesIO(json.dumps(response()).encode())),patch('identity_motion_zones.remember_zones',side_effect=OSError('disk full')):
            self.assertEqual(self.fetch().cells,(0,1))
    def test_concurrent_cache_files_remain_complete_and_camera_specific(self):
        def save(i):remember_zones(self.db,9+(i%2),2,response([i%2,1-i%2]),time.time())
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(save,range(30)))
        for dev,cells in ((9,(0,1)),(10,(1,0))):
            with patch('identity_motion_zones.urlopen',side_effect=URLError('offline')):self.assertEqual(self.fetch(dev).cells,cells)
        self.assertEqual(list(cache_path(self.db,9,2).parent.glob('*.tmp')),[])
    def test_delayed_response_cannot_replace_newer_confirmed_policy(self):
        now=time.time()
        remember_zones(self.db,9,2,response([1,0]),now-.1)
        remember_zones(self.db,9,2,response([0,1]),now-5)
        with patch('identity_motion_zones.urlopen',side_effect=URLError('offline')):
            self.assertEqual(self.fetch().cells,(1,0))
    def test_future_cache_does_not_prevent_fresh_verified_policy(self):
        remember_zones(self.db,9,2,response([1,0]),time.time()+100)
        with patch('identity_motion_zones.urlopen',return_value=BytesIO(json.dumps(response()).encode())):
            self.assertEqual(self.fetch().cells,(0,1))
        with patch('identity_motion_zones.urlopen',side_effect=URLError('offline')):
            self.assertEqual(self.fetch().cells,(0,1))
    def test_mapping_read_does_not_initialize_schema_or_block_wal_writer(self):
        with sqlite3.connect(self.db) as c:
            c.executescript('PRAGMA journal_mode=WAL;CREATE TABLE clips(id,device_id);CREATE TABLE devices(id,system_id,blink_device_id);CREATE TABLE systems(id,blink_system_id);INSERT INTO clips VALUES(7,3);INSERT INTO devices VALUES(3,2,987);INSERT INTO systems VALUES(2,654);')
        blocker=sqlite3.connect(self.db);self.addCleanup(blocker.close);blocker.execute('BEGIN IMMEDIATE');blocker.execute('UPDATE devices SET blink_device_id=987')
        with patch('identity_motion_zones.urlopen',return_value=BytesIO(json.dumps(response()).encode())):self.assertEqual(load_camera_zones(self.db,7,'http://localhost:8000').cells,(0,1))
        with sqlite3.connect(self.db) as c:self.assertEqual({r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")},{'clips','devices','systems'})

class ZoneSettingsCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from types import SimpleNamespace as NS
        from device_settings import DeviceSettings
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.db=Path(self.temp.name)/'catalog.sqlite'
        self.camera=NS(camera_id=9,product_type='sedona',online=True,sync=NS(network_id=2,available=True))
        self.service=DeviceSettings(NS(blink=NS(cameras={'camera':self.camera}),catalog_db_path=self.db))
    async def test_confirmed_editor_read_and_save_replace_cached_policy(self):
        from unittest.mock import AsyncMock
        for req,cells in ((None,[0,1]),(object(),[1,0])):
            with patch('device_settings.execute_zones',AsyncMock(return_value=response(cells))):
                result=await self.service.execute('9','2',zones=True,zone_request=req)
            with patch('identity_motion_zones.urlopen',side_effect=URLError('offline')):
                self.assertEqual(read_camera_zones(self.db,9,2,'http://localhost').cells,tuple(cells))
    async def test_unconfirmed_save_cannot_replace_cached_policy(self):
        from unittest.mock import AsyncMock
        from fastapi import HTTPException
        remember_zones(self.db,9,2,response(),time.time())
        with patch('device_settings.execute_zones',AsyncMock(side_effect=HTTPException(502,'not confirmed'))):
            with self.assertRaises(HTTPException):await self.service.execute('9','2',zones=True,zone_request=object())
        with patch('identity_motion_zones.urlopen',side_effect=URLError('offline')):
            self.assertEqual(read_camera_zones(self.db,9,2,'http://localhost').cells,(0,1))

if __name__=='__main__':unittest.main()
