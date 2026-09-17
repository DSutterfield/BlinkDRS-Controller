"""Fault log endpoints and independent Pi health sampling."""
import asyncio
import contextlib
import logging
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException
from pydantic import BaseModel, Field
from fault_log import error_code


class RetentionRequest(BaseModel):
    delete_after_days: int = Field(ge=1, le=3650)


class ClientFault(BaseModel):
    entry_id: str = Field(min_length=1, max_length=100)
    observed_at: datetime
    client: str = Field(min_length=1, max_length=100)
    event: str = Field(pattern='^(FAULT|RESTORED)$')
    code: str = Field(max_length=100)


def install_fault_api(app, controller, recovery, liveview=None):
    faults = controller.faults
    monitor_task = None

    def settings():
        return {'delete_after_days': faults.days, 'path': str(faults.path),
                'write_error': faults.last_error}

    @app.get('/api/v1/settings/fault-log')
    async def get_settings():
        return settings()

    @app.put('/api/v1/settings/fault-log')
    async def put_settings(request: RetentionRequest):
        try:
            faults.save_days(request.delete_after_days)
        except (OSError, ValueError) as exc:
            raise HTTPException(503, 'Could not save fault retention: ' + error_code(exc)) from exc
        return settings()

    @app.post('/api/v1/fault-log/cleanup')
    async def cleanup():
        try:
            return {'removed': faults.prune()}
        except OSError as exc:
            raise HTTPException(503, 'Fault log cleanup failed: ' + error_code(exc)) from exc

    @app.get('/api/v1/fault-log')
    async def read_log():
        try:
            return {**settings(), 'text': faults.tail(), 'limit': 2000}
        except OSError as exc:
            raise HTTPException(503, 'Fault log could not be read: ' + error_code(exc)) from exc

    @app.post('/api/v1/fault-log/client')
    async def client_fault(request: ClientFault):
        when = request.observed_at
        if when.tzinfo is None or when > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise HTTPException(422, 'A valid observed timestamp with timezone is required.')
        try:
            faults.append(request.event, 'Windows connection to Pi: ' + request.client,
                          request.code, 'Observed by Windows; delivered when Pi was reachable.',
                          when, request.entry_id)
        except OSError as exc:
            raise HTTPException(503, 'Fault log write failed: ' + error_code(exc)) from exc
        return {'saved': True}

    async def monitor():
        count = 0
        while True:
            try:
                status = recovery.status()
                faults.safe_observe('internet', 'Pi internet connection', status['internet_reachable'],
                                    'CONNECTIVITY_CHECK_FAILED', 'Internet connectivity check result.')
                faults.safe_observe('archive', 'Pi archive storage', controller.archive_dir.is_dir(),
                                    'ARCHIVE_UNAVAILABLE', 'Archive directory availability check.')
                faults.safe_observe('catalog', 'Pi clip catalog', controller.catalog_db_path.is_file(),
                                    'CATALOG_UNAVAILABLE', 'Clip catalog availability check.')
                if count % 360 == 0:
                    faults.prune()
                count += 1
            except Exception as exc:
                logging.getLogger(__name__).error('Fault monitor failed: %s', error_code(exc))
            await asyncio.sleep(10)

    async def start():
        nonlocal monitor_task
        monitor_task = asyncio.create_task(monitor())

    async def stop():
        if monitor_task:
            monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor_task

    app.router.add_event_handler('startup', start)
    app.router.add_event_handler('shutdown', stop)
