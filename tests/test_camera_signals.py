import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from camera_signals import CameraSignals, extract_signals, sync_wifi_strength


class SignalTests(unittest.IsolatedAsyncioTestCase):
    def test_sync_module_identity_and_refreshed_value(self):
        system = NS(sync_id=10, network_id=20, summary={'wifi_strength': 5})
        blink = NS(homescreen={'sync_modules': [
            {'id': 20, 'wifi_strength': 1}, {'id': '10', 'wifi_strength': 3}]})
        self.assertEqual(sync_wifi_strength(blink, system), 3)
        blink.homescreen['sync_modules'][1]['wifi_strength'] = 0
        self.assertEqual(sync_wifi_strength(blink, system), 0)
        del blink.homescreen['sync_modules'][1]['wifi_strength']
        self.assertIsNone(sync_wifi_strength(blink, system))

    def test_sync_module_fallback_and_unknown(self):
        self.assertEqual(sync_wifi_strength(NS(), NS(summary={'wifi_strength': 4})), 4)
        for value in (None, True, -50, 6, 'unknown'):
            self.assertIsNone(sync_wifi_strength(NS(), NS(summary={'wifi_strength': value})))
        self.assertIsNone(sync_wifi_strength(NS(), NS()))

    def setUp(self):
        self.doorbell_config = AsyncMock(return_value={'temp': 79})
        patcher = patch('camera_signals.api.http_get', new=self.doorbell_config)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_temperature_fahrenheit_zero_and_invalid(self):
        self.assertEqual(extract_signals({'temp': 79})['temperature_f'], 79)
        self.assertEqual(extract_signals({'temperature': 32})['temperature_c'], 0)
        self.assertEqual(extract_signals({'temp': 0})['temperature_f'], 0)
        for value in (None, True, 'unknown', float('nan')):
            self.assertNotIn('temperature_f', extract_signals({'temp': value}))

    def test_native_scales_and_zero(self):
        self.assertEqual(extract_signals({'wifi_strength': -56, 'lfr_strength': -79}),
                         {'wifi_signal': -56, 'wifi_signal_unit': 'dbm',
                          'sync_signal': -79, 'sync_signal_unit': 'dbm'})
        self.assertEqual(extract_signals({'signals': {'wifi': 0, 'lfr': 5}}),
                         {'wifi_signal': 0, 'wifi_signal_unit': 'level_5',
                          'sync_signal': 5, 'sync_signal_unit': 'level_5'})
        self.assertEqual(extract_signals({'wifi_rssi': -23})['wifi_signal'], -23)

    def test_unknown_or_invalid_is_not_zero(self):
        for config in (None, {}, {'signals': False}, {'wifi_strength': 0},
                       {'signals': {'wifi': True, 'lfr': 99}},
                       {'wifi_rssi': float('nan'), 'lfr_strength': -999}):
            self.assertEqual(extract_signals(config), {})

    def cameras(self):
        sync = NS(network_id=2, get_unique_info=Mock(return_value={'signals': {'wifi': 5, 'lfr': 4}}))
        return NS(urls=NS(base_url='https://example.invalid'), account_id=1,
                  cameras={str(i): NS(camera_id=str(i), name=str(i), product_type=kind, sync=sync)
                           for i, kind in enumerate(('owl', 'hawk', 'sedona', 'tulip'))})

    async def test_read_only_sources_cache_and_camera_identity(self):
        blink = self.cameras()
        service = CameraSignals()
        with patch('camera_signals.api.request_get_config', new=AsyncMock(return_value={'wifi_rssi': -30})) as mini, \
             patch('camera_signals.api.request_camera_info', new=AsyncMock(return_value={
                 'camera': [{'id': 'wrong', 'lfr_strength': -15}, {'id': '2', 'lfr_strength': -80}]})) as outdoor:
            first = await service.read(blink)
            self.assertEqual(first[('2', '2')]['sync_signal'], -80)
            self.assertEqual(first[('2', '3')]['wifi_signal_unit'], 'level_5')
            self.assertEqual(first[('2', '3')]['temperature_f'], 79)
            self.assertEqual(first[('2', '1')]['wifi_signal'], -30)
            self.assertEqual(await service.read(blink), first)
            self.assertEqual(mini.await_count, 2)
            self.assertEqual(outdoor.await_count, 1)
            self.doorbell_config.assert_awaited_once()

    async def test_missing_temperature_keeps_doorbell_signals(self):
        self.doorbell_config.side_effect = OSError('Unavailable')
        blink = self.cameras()
        blink.cameras = {'doorbell': blink.cameras['3']}
        result = await CameraSignals().read(blink)
        self.assertEqual(result[('2', '3')]['sync_signal'], 4)
        self.assertNotIn('temperature_f', result[('2', '3')])

    async def test_failed_telemetry_does_not_fail_other_cameras(self):
        blink = self.cameras()
        with patch('camera_signals.api.request_get_config', new=AsyncMock(side_effect=OSError())), \
             patch('camera_signals.api.request_camera_info', new=AsyncMock(return_value=None)):
            result = await CameraSignals().read(blink)
            self.assertEqual(result[('2', '3')]['sync_signal'], 4)
            self.assertNotIn('wifi_signal', result.get(('2', '0'), {}))

    async def test_cancel_drains_requests_and_releases_lock(self):
        entered, finished = asyncio.Event(), asyncio.Event()
        async def pending(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()
        service = CameraSignals()
        with patch('camera_signals.api.request_get_config', new=pending), \
             patch('camera_signals.api.request_camera_info', new=AsyncMock(return_value=None)):
            task = asyncio.create_task(service.read(self.cameras()))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(finished.is_set())
            self.assertFalse(service._lock.locked())
