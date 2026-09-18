import asyncio
import configparser
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from fastapi import FastAPI
from pydantic import ValidationError
from clip_retention import ClipRetentionRequest,install_clip_retention_api,load_clip_retention

class RetentionSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_reload_preserve_other_settings_and_disable(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path=root/'settings.local.ini'
            (root/'settings.ini').write_text('[download]\ndelete_after_days=30\n')
            path.write_text('[fault_log]\ndelete_after_days=90\n[download]\noutput_dir=/archive\n')
            app=FastAPI();install_clip_retention_api(app,SimpleNamespace(faults=SimpleNamespace(settings_path=path),archive_lock=asyncio.Lock()))
            def endpoint(method):return next(r.endpoint for r in app.routes if getattr(r,'path','')=='/api/v1/settings/clip-retention' and method in r.methods)
            self.assertEqual((await endpoint('GET')())['delete_after_days'],30)
            for days in (60,0):
                await endpoint('PUT')(ClipRetentionRequest(delete_after_days=days))
                self.assertEqual(load_clip_retention(path),days)
            c=configparser.ConfigParser();c.read(path)
            self.assertEqual(c['fault_log']['delete_after_days'],'90')
            self.assertEqual(c['download']['output_dir'],'/archive')
    async def test_reject_invalid_values(self):
        for value in (-1,3651,1.5,True,'30'):
            with self.assertRaises(ValidationError):ClipRetentionRequest(delete_after_days=value)
