import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from fault_api import install_fault_api, RetentionRequest, ClientFault
from fault_log import FaultLog


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.faults = FaultLog(root/'logs', root/'settings.ini')
        self.app = FastAPI()
        self.calls = 0
        def status():
            self.calls += 1
            return {'internet_reachable': False}
        install_fault_api(self.app, NS(faults=self.faults, archive_dir=root, catalog_db_path=root/'catalog.db'), NS(status=status))

    def endpoint(self, path, method='GET'):
        return next(route.endpoint for route in self.app.routes if getattr(route, 'path', '') == path and method in route.methods)

    async def test_read_save_cleanup_and_no_external_deletions(self):
        self.assertEqual((await self.endpoint('/api/v1/settings/fault-log')())['delete_after_days'], 90)
        await self.endpoint('/api/v1/settings/fault-log', 'PUT')(RetentionRequest(delete_after_days=30))
        self.assertEqual(self.faults.days, 30)
        self.assertEqual(await self.endpoint('/api/v1/fault-log/cleanup', 'POST')(), {'removed': 0})
        self.assertEqual((await self.endpoint('/api/v1/fault-log')())['text'], '')

    async def test_client_retries_one_log_entry(self):
        request = ClientFault(entry_id='unique', observed_at=datetime.now(timezone.utc), client='Test PC', event='FAULT', code='HTTP_503')
        for _ in range(2): await self.endpoint('/api/v1/fault-log/client', 'POST')(request)
        self.assertEqual(len(self.faults.tail().splitlines()), 1)
        self.assertIn('HTTP_503', self.faults.tail())

    async def test_invalid_events_and_retention_rejected(self):
        for days in (0, 3651):
            with self.assertRaises(ValidationError): RetentionRequest(delete_after_days=days)
        with self.assertRaises(ValidationError):
            ClientFault(entry_id='x', observed_at=datetime.now(timezone.utc), client='PC', event='DELETE', code='')
        request = ClientFault(entry_id='x', observed_at=datetime.now(), client='PC', event='FAULT', code='')
        with self.assertRaises(HTTPException): await self.endpoint('/api/v1/fault-log/client', 'POST')(request)

    async def test_monitor_runs_without_windows_requests_and_stops(self):
        for start in self.app.router.on_startup: await start()
        await asyncio.sleep(.02)
        self.assertGreater(self.calls, 0)
        self.assertIn('Pi internet connection', self.faults.tail())
        for stop in self.app.router.on_shutdown: await stop()

    async def test_full_controller_registers_fault_routes_and_shuts_down(self):
        from controller_api import create_app
        root = Path(self.temp.name)
        (root/'clips').mkdir()
        controller = NS(faults=self.faults, archive_root=root, archive_dir=root/'clips',
                        catalog_db_path=root/'catalog.db', archive_lock=asyncio.Lock(), blink=None)
        app = create_app(controller)
        paths = app.openapi()['paths']
        self.assertIn('/api/v1/fault-log', paths)
        self.assertIn('/api/v1/settings/fault-log', paths)
        self.assertIn('/api/v1/liveview/recording/start', paths)
        for shutdown in app.router.on_shutdown: await shutdown()


if __name__ == '__main__': unittest.main()
