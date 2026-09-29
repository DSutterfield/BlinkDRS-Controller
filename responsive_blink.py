"""Controller-only Blink adapter: retain download pacing without blocking the API."""
import asyncio
import logging

import aiofiles
import aiofiles.ospath
from blinkpy.blinkpy import Blink

log = logging.getLogger(__name__)


class ResponsiveBlink(Blink):
    async def _parse_downloaded_items(self, result, camera, path, delay, debug,
                                      filename_format=None):
        # Match the installed BlinkPy downloader's filtering and naming. Its
        # time.sleep(delay) blocks our shared API loop; use asyncio.sleep here.
        formatter = filename_format or self._format_filename_default
        for item in result:
            try:
                created_at = item['created_at']
                camera_name = item['device_name']
                deleted = item['deleted']
                address = item['media']
            except KeyError:
                log.info('Missing clip information, skipping...')
                continue
            if camera_name not in camera and 'all' not in camera:
                continue
            if deleted:
                continue
            filename = formatter(created_at, camera_name, path)
            if debug:
                print(f'Camera: {camera_name}, Timestamp: {created_at}, '
                      f'Address: {address}, Filename: {filename}')
            else:
                if await aiofiles.ospath.isfile(filename):
                    continue
                response = await self.do_http_get(address)
                try:
                    async with aiofiles.open(filename, 'wb') as video:
                        await video.write(await response.read())
                finally:
                    response.release()
                log.info('Downloaded video to %s', filename)
            if delay > 0:
                await asyncio.sleep(delay)
