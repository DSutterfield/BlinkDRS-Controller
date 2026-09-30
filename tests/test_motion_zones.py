import copy
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from pydantic import ValidationError
import motion_zones as zones
from motion_zones import ZoneChange

class ZoneTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.raw={'zone_mask':[1]*256,'privacy_zones':[{'x':10,'y':20,'width':30,'height':40}],
                  'use_analytics_for_motion':False,'basic_zone_rows':8,'basic_zone_columns':8,'sub_zone_rows':2,'sub_zone_columns':2}
        blink=NS(urls=NS(base_url='https://example.test'),account_id=1)
        self.camera=NS(product_type='hawk',camera_id=7,sync=NS(network_id=2,blink=blink))
    def change(self,cells=None,mode='advanced',revision=None):
        return ZoneChange(revision=revision or zones.revision(self.raw),mode=mode,cells=cells or self.raw['zone_mask'])
    async def test_read_never_posts(self):
        with patch.object(zones.api,'http_get',AsyncMock(return_value=self.raw)),patch.object(zones.api,'http_post',AsyncMock()) as post:
            result=await zones.execute_zones(self.camera)
            self.assertEqual(result['basic_rows'],8);self.assertEqual(result['cells'],[1]*256);post.assert_not_called()
    async def test_save_preserves_privacy_metadata_and_confirms_fresh_read(self):
        cells=[1]*256;cells[0]=0
        confirmed=copy.deepcopy(self.raw);confirmed['zone_mask']=cells;confirmed['use_analytics_for_motion']=True
        response=NS(status=200,json=AsyncMock(return_value={'command':'config_set'}))
        with patch.object(zones.api,'http_get',AsyncMock(side_effect=[self.raw,confirmed])),patch.object(zones.api,'http_post',AsyncMock(return_value=response)) as post:
            result=await zones.execute_zones(self.camera,self.change(cells))
            import json
            payload=json.loads(post.call_args.kwargs['data'])
            self.assertEqual(payload['privacy_zones'],self.raw['privacy_zones']);self.assertEqual(payload['basic_zone_columns'],8)
            self.assertTrue(payload['use_analytics_for_motion']);self.assertEqual(result['cells'],cells)
    async def test_conflict_never_posts(self):
        with patch.object(zones.api,'http_get',AsyncMock(return_value=self.raw)),patch.object(zones.api,'http_post',AsyncMock()) as post:
            with self.assertRaises(HTTPException) as error:await zones.execute_zones(self.camera,self.change(revision='0'*64))
            self.assertEqual(error.exception.status_code,409);post.assert_not_called()
    async def test_reject_partial_basic_and_bad_length(self):
        for cells in ([0]+[1]*255,[1]*255):
            with patch.object(zones.api,'http_get',AsyncMock(return_value=self.raw)),patch.object(zones.api,'http_post',AsyncMock()) as post:
                with self.assertRaises(HTTPException):await zones.execute_zones(self.camera,self.change(cells,'basic'))
                post.assert_not_called()
    async def test_unconfirmed_save_never_reports_success(self):
        cells=[0]+[1]*255
        response=NS(status=200,json=AsyncMock(return_value={'command':'config_set'}))
        with patch.object(zones.api,'http_get',AsyncMock(return_value=self.raw)),patch.object(zones.api,'http_post',AsyncMock(return_value=response)),patch.object(zones.asyncio,'sleep',AsyncMock()):
            with self.assertRaises(HTTPException) as error:await zones.execute_zones(self.camera,self.change(cells))
            self.assertEqual(error.exception.status_code,409)
    async def test_noop_never_posts(self):
        with patch.object(zones.api,'http_get',AsyncMock(return_value=self.raw)),patch.object(zones.api,'http_post',AsyncMock()) as post:
            await zones.execute_zones(self.camera,self.change());post.assert_not_called()
    def test_dimensions_binary_and_privacy_required(self):
        for field,value in [('zone_mask',[True]*256),('zone_mask',[1]*255),('basic_zone_rows',0),('privacy_zones',None),('use_analytics_for_motion',1)]:
            raw=copy.deepcopy(self.raw);raw[field]=value
            with self.assertRaises(HTTPException):zones.decode_v2(raw)
        for cells in ([True],[1.0],['1']):
            with self.assertRaises(ValidationError):self.change(cells)
    def test_block_indices_do_not_transpose(self):
        self.assertEqual(zones.block_indices(0,0,8,2,2),[0,1,16,17])
        self.assertEqual(zones.block_indices(0,7,8,2,2),[14,15,30,31])
        self.assertEqual(zones.block_indices(7,0,8,2,2),[224,225,240,241])
        self.assertEqual(zones.block_indices(7,7,8,2,2),[238,239,254,255])

if __name__=='__main__':unittest.main()

class LegacyZoneTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.raw={'zone_version':'v1','motion_regions':0x41ffffef,'advanced_motion_regions':[4095]*25}
        self.camera=NS(product_type='owl',camera_id=7,sync=NS(network_id=2,blink=object()))
    def test_known_basic_corners_and_mode_flag(self):
        decoded=zones.decode_v1(self.raw)
        self.assertEqual(decoded[:4],(5,5,1,1));self.assertEqual(decoded[4][0],1);self.assertEqual(decoded[4][4],0)
    def test_unknown_advanced_never_reinterpreted(self):
        for mask,advanced in [(0x80000000,[4095]*25),(0x1ffffff,[4094]+[4095]*24),(True,[4095]*25)]:
            raw=dict(self.raw,motion_regions=mask,advanced_motion_regions=advanced)
            with self.assertRaises(HTTPException):zones.decode_v1(raw)
    async def test_write_only_basic_field_and_confirm(self):
        target=[1]*25;target[0]=0
        original=dict(self.raw)
        confirmed=dict(self.raw,motion_regions=0x41fffffe)
        response=NS(status=200,json=AsyncMock(return_value={'command':'config_set'}))
        request=ZoneChange(revision=zones.revision(self.raw),mode='basic',cells=target)
        with patch.object(zones.api,'request_get_config',AsyncMock(side_effect=[original,confirmed])),patch.object(zones.api,'request_update_config',AsyncMock(return_value=response)) as write:
            result=await zones.execute_zones(self.camera,request)
            import json
            self.assertEqual(json.loads(write.call_args.kwargs['data']),{'motion_regions':0x41fffffe})
            self.assertEqual(result['cells'],target);self.assertFalse(result['advanced_available'])
