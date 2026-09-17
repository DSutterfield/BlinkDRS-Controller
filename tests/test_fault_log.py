import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from fault_log import FaultLog, BlinkErrorHandler, describe_error


class FaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 9, 17, tzinfo=timezone.utc)
        self.faults = FaultLog(self.root/'logs', self.root/'config/settings.local.ini', lambda: self.now)

    def test_transition_and_repeat_suppression(self):
        f = self.faults
        f.observe('x', 'Camera', True)
        self.assertEqual(f.tail(), '')
        f.observe('x', 'Camera', False, '503')
        f.observe('x', 'Camera', False, '503')
        f.observe('x', 'Camera', None)
        f.observe('x', 'Camera', False, '504')
        f.observe('x', 'Camera', True)
        self.assertEqual([line.split('\t')[1] for line in f.tail().splitlines()], ['FAULT', 'FAULT', 'RESTORED'])

    def test_recovery_survives_restart(self):
        self.faults.observe('x', 'Pi', False, 'OFFLINE')
        other = FaultLog(self.root/'logs', self.faults.settings_path, lambda: self.now)
        other.observe('x', 'Pi', False, 'OFFLINE')
        other.observe('x', 'Pi', True)
        self.assertEqual(len(other.tail().splitlines()), 2)

    def test_retention_boundary_and_malformed_preserved(self):
        f = self.faults
        f.append('FAULT', 'old', when=self.now-timedelta(days=89))
        f.append('FAULT', 'boundary', when=self.now)
        with f.path.open('a') as out: out.write('legacy line without timestamp\n')
        self.now += timedelta(days=90)
        self.assertEqual(f.prune(), 1)
        self.assertIn('boundary', f.tail())
        self.assertIn('legacy line', f.tail())

    def test_settings_preserve_other_values(self):
        p = self.faults.settings_path
        p.parent.mkdir(parents=True)
        p.write_text('[download]\npoll_interval_seconds=60\n')
        self.faults.save_days(120)
        self.assertIn('poll_interval_seconds = 60', p.read_text())
        self.assertEqual(FaultLog(self.root/'logs', p).days, 120)
        for value in (0, -1, 3651, 1.5, True):
            with self.assertRaises(ValueError): self.faults.save_days(value)

    def test_queued_events_idempotent_after_restart(self):
        self.faults.append('FAULT', 'Windows', entry_id='unique')
        other = FaultLog(self.root/'logs', self.faults.settings_path, lambda: self.now)
        other.append('FAULT', 'Windows', entry_id='unique')
        self.assertEqual(len(other.tail().splitlines()), 1)

    def test_expired_delayed_events_not_reintroduced(self):
        self.faults.append('FAULT', 'Windows', when=self.now-timedelta(days=91))
        self.assertEqual(self.faults.tail(), '')

    def test_offline_module_records_known_missing_cameras(self):
        sync = NS(network_id=1, name='Home', online=True, available=True)
        camera = NS(camera_id=2, name='Porch', sync=sync, online=True)
        blink = NS(sync={'home': sync}, cameras={'porch': camera})
        self.faults.devices(blink)
        sync.online = False
        sync.available = False
        blink.cameras = {}
        self.faults.devices(blink)
        self.assertIn('Camera: Porch', self.faults.tail())
        self.assertEqual(len(self.faults.tail().splitlines()), 2)

    def test_disk_failure_does_not_block_camera_work(self):
        with patch.object(self.faults, 'append', side_effect=OSError(28, 'disk full')):
            self.faults.safe_observe('x', 'Pi', False)
        self.assertIn('errno=28', self.faults.last_error)
        self.assertNotIn('x', self.faults.states)

    def test_library_error_codes_without_auth_data(self):
        handler = BlinkErrorHandler(self.faults)
        handler.emit(logging.LogRecord('blinkpy.auth', logging.ERROR, '', 0,
                                       'HTTP 401 token=private password=secret', (), None))
        handler.recovered()
        self.assertIn('401', self.faults.tail())
        self.assertNotIn('private', self.faults.tail())
        self.assertNotIn('secret', self.faults.tail())
        self.assertIn('RESTORED', self.faults.tail())

    def test_tail_bounded(self):
        for i in range(5): self.faults.append('FAULT', str(i))
        self.assertEqual(len(self.faults.tail(2).splitlines()), 2)

    def test_dns_port_is_not_an_error_code(self):
        message = 'Cannot connect to host rest.example.com:443 ssl:default [Temporary failure in name resolution]'
        code, detail = describe_error(message)
        self.assertEqual(code, 'DNS_LOOKUP_FAILED')
        self.assertIn('resolve', detail)
        self.assertIn('blink_dvr.log', detail)
        self.assertNotIn('443', code)

    def test_explicit_codes_only(self):
        for message, expected in [
                ('HTTP 401 token=secret', 'HTTP_401'),
                ('HTTP Error 503', 'HTTP_503'),
                ('HTTP/1.1 429 Too Many Requests', 'HTTP_429'),
                ("{'status_code': 523, 'id': 443}", 'REPORTED_STATUS_CODE:523'),
                ('error_code=2017', 'REPORTED_ERROR_CODE:2017'),
                ('Camera 443; waited 500 ms', 'ERROR'),
                ('https://example.com:443/?code=401 request failed', 'ERROR')]:
            with self.subTest(message=message):
                self.assertEqual(describe_error(message)[0], expected)

    def test_connection_summaries_never_copy_raw_secrets(self):
        for message, expected in [
                ('TimeoutError: token=secret', 'CONNECTION_TIMEOUT'),
                ('certificate verify failed password=secret', 'TLS_CONNECTION_FAILED'),
                ('Connection refused https://user:secret@example.com:443/', 'CONNECTION_REFUSED'),
                ('Connection reset by peer cookie=secret', 'CONNECTION_FAILED')]:
            code, detail = describe_error(message)
            self.assertEqual(code, expected)
            self.assertNotIn('secret', code + detail)

    def test_summer_and_winter_central_time(self):
        for when, expected in [
                (datetime(2026, 9, 17, 21, 3, 42, tzinfo=timezone.utc), '2026-09-17T16:03:42-05:00'),
                (datetime(2026, 1, 17, 21, 3, 42, tzinfo=timezone.utc), '2026-01-17T15:03:42-06:00')]:
            self.now = when
            self.faults.append('FAULT', 'test', when=when)
            self.assertEqual(self.faults.tail().splitlines()[-1].split('\t')[0], expected)

    def test_repeated_dst_hour_has_distinct_offsets(self):
        self.now = datetime(2026, 11, 1, 8, tzinfo=timezone.utc)
        for hour in (6, 7):
            self.faults.append('FAULT', 'test', when=datetime(2026, 11, 1, hour, 30, tzinfo=timezone.utc))
        lines = self.faults.tail().splitlines()
        self.assertTrue(lines[0].startswith('2026-11-01T01:30:00-05:00'))
        self.assertTrue(lines[1].startswith('2026-11-01T01:30:00-06:00'))

    def test_old_utc_entries_unchanged_and_mixed_retention(self):
        legacy = '2026-09-16T00:00:00+00:00\tFAULT\told\t\t\tBlinkDRS\t\n'
        self.faults.path.write_text(legacy)
        self.faults.append('RESTORED', 'test')
        self.assertTrue(self.faults.path.read_text().startswith(legacy))
        self.faults.save_days(1)
        self.now += timedelta(seconds=1)
        self.assertEqual(self.faults.prune(), 1)
        self.assertIn('-05:00', self.faults.tail())

    def test_delayed_windows_timestamp_converted_at_observation_time(self):
        observed = self.now - timedelta(hours=2)
        self.faults.append('FAULT', 'Windows', when=observed)
        parsed = datetime.fromisoformat(self.faults.tail().split('\t')[0])
        self.assertEqual(parsed, observed)
        self.assertEqual(parsed.utcoffset(), timedelta(hours=-5))


if __name__ == '__main__': unittest.main()
