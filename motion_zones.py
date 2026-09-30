"""Motion-only zone editing; camera-reported dimensions and optimistic saves."""
import asyncio
import copy
import hashlib
import json
from typing import Literal
from blinkpy import api
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt

class ZoneChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str = Field(strict=True, pattern=r'^[0-9a-f]{64}$')
    mode: Literal['basic', 'advanced']
    cells: list[StrictInt] = Field(min_length=1, max_length=4096)

def revision(raw):
    return hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def v2_url(camera):
    blink = camera.sync.blink
    kind = {'hawk': 'owls', 'sedona': 'cameras', 'tulip': 'doorbells'}[camera.product_type]
    return (f'{blink.urls.base_url}/api/v2/accounts/{blink.account_id}'
            f'/networks/{camera.sync.network_id}/{kind}/{camera.camera_id}/zones')

def decode_v2(raw):
    if not isinstance(raw, dict):
        raise HTTPException(502, 'Blink did not return motion zones.')
    dimensions = [raw.get(key) for key in ('basic_zone_rows', 'basic_zone_columns', 'sub_zone_rows', 'sub_zone_columns')]
    if any(type(n) is not int or not 1 <= n <= 32 for n in dimensions):
        raise HTTPException(502, 'Unrecognized motion-zone dimensions.')
    rows, columns, sub_rows, sub_columns = dimensions
    cells = raw.get('zone_mask')
    if (not isinstance(cells, list) or len(cells) != rows*columns*sub_rows*sub_columns or
            len(cells) > 4096 or any(type(n) is not int or n not in (0, 1) for n in cells)):
        raise HTTPException(502, 'Unrecognized motion-zone mask.')
    if not isinstance(raw.get('privacy_zones'), list) or type(raw.get('use_analytics_for_motion')) is not bool:
        raise HTTPException(502, 'Incomplete zone configuration; changes are unavailable.')
    return rows, columns, sub_rows, sub_columns, cells.copy()

def block_indices(row, column, columns, sub_rows, sub_columns):
    width = columns*sub_columns
    return [(row*sub_rows+y)*width+column*sub_columns+x
            for y in range(sub_rows) for x in range(sub_columns)]

def infer_mode(rows, columns, sub_rows, sub_columns, cells):
    for row in range(rows):
        for column in range(columns):
            values = {cells[i] for i in block_indices(row,column,columns,sub_rows,sub_columns)}
            if len(values) > 1: return 'advanced'
    return 'basic'

async def read_raw(camera):
    if camera.product_type == 'owl':
        raw = await api.request_get_config(camera.sync.blink, camera.sync.network_id,
                                           camera.camera_id, product_type='owl')
        grid = decode_v1(raw)
        raw = {key:raw[key] for key in ('motion_regions','advanced_motion_regions','zone_version')}
        return raw, grid
    raw = await api.http_get(camera.sync.blink, v2_url(camera))
    return raw, decode_v2(raw)

def decode_v1(raw):
    if not isinstance(raw,dict) or raw.get('zone_version') != 'v1':
        raise HTTPException(400, 'Unrecognized Original Mini motion-zone format.')
    mask = raw.get('motion_regions')
    advanced = raw.get('advanced_motion_regions')
    if (type(mask) is not int or mask < 0 or mask & ~(0x40000000 | 0x1ffffff) or
            not isinstance(advanced,list) or len(advanced) != 25 or
            any(type(n) is not int or n != 4095 for n in advanced)):
        raise HTTPException(400, 'This Mini has Advanced or unrecognized zones. Basic editing is unavailable until that format is verified.')
    return 5, 5, 1, 1, [(mask >> i) & 1 for i in range(25)]

async def write_v1(camera, raw, decoded, request):
    mask = 0x40000000 | sum(n << i for i,n in enumerate(request.cells))
    response = await api.request_update_config(camera.sync.blink, camera.sync.network_id,
                                               camera.camera_id, product_type='owl',
                                               data=json.dumps({'motion_regions':mask}))
    if response is None or response.status != 200:
        raise HTTPException(502, 'Blink did not accept the motion zones.')
    result = await response.json()
    if not isinstance(result,dict) or result.get('command') != 'config_set' or 'code' in result or result.get('state_condition',result.get('state')) in ('failed','error'):
        raise HTTPException(502, 'Blink did not accept the motion zones.')
    for attempt in range(3):
        confirmed, grid = await read_raw(camera)
        if (confirmed['motion_regions'] == mask and grid[4] == request.cells and
                confirmed['advanced_motion_regions'] == raw['advanced_motion_regions']):
            return present(camera, confirmed, grid)
        if attempt < 2: await asyncio.sleep(.5)
    raise HTTPException(409, 'Blink has not confirmed the requested motion zones. Reload to check.')

def present(camera, raw, decoded):
    rows, columns, sub_rows, sub_columns, cells = decoded
    return {'basic_rows':rows, 'basic_columns':columns, 'sub_rows':sub_rows,
            'sub_columns':sub_columns, 'cells':cells,
            'mode':infer_mode(*decoded), 'advanced_available':camera.product_type != 'owl', 'revision':revision(raw)}

async def execute_zones(camera, request=None):
    raw, decoded = await read_raw(camera)
    current = present(camera, raw, decoded)
    if request is None: return current
    if request.revision != current['revision']:
        raise HTTPException(409, 'Motion zones changed since this editor loaded. Reload before applying.')
    if len(request.cells) != len(current['cells']) or any(n not in (0,1) for n in request.cells):
        raise HTTPException(422, 'The zone selection does not match this camera grid.')
    if camera.product_type == 'owl' and request.mode != 'basic':
        raise HTTPException(422, 'Original Mini Advanced zones are not yet verified.')
    if request.mode == 'basic' and infer_mode(*decoded[:4], request.cells) != 'basic':
        raise HTTPException(422, 'Basic zones must select complete blocks.')
    if request.cells == current['cells']: return current
    if camera.product_type == 'owl':
        return await write_v1(camera, raw, decoded, request)
    payload = copy.deepcopy(raw)
    payload['zone_mask'] = request.cells
    payload['use_analytics_for_motion'] = any(n == 0 for n in request.cells)
    response = await api.http_post(camera.sync.blink, v2_url(camera), json=False, data=json.dumps(payload))
    if response is None or response.status != 200:
        raise HTTPException(502, 'Blink did not accept the motion zones.')
    result = await response.json()
    if not isinstance(result,dict) or result.get('command') != 'config_set' or 'code' in result or result.get('state_condition') in ('failed','error'):
        raise HTTPException(502, 'Blink did not accept the motion zones.')
    for attempt in range(3):
        confirmed, grid = await read_raw(camera)
        if (grid[:4] == decoded[:4] and grid[4] == request.cells and
                confirmed.get('privacy_zones') == raw['privacy_zones'] and
                confirmed.get('use_analytics_for_motion') == payload['use_analytics_for_motion']):
            return present(camera, confirmed, grid)
        if attempt < 2: await asyncio.sleep(.5)
    raise HTTPException(409, 'Blink has not confirmed the requested motion zones. Reload to check.')
