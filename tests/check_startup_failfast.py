import ast,asyncio,contextlib,sys
from pathlib import Path
from types import SimpleNamespace
tree=ast.parse(Path(sys.argv[1]).read_text(encoding='utf-8'))
cls=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='LiveViewBridge')
method=next(x for x in cls.body if isinstance(x,ast.AsyncFunctionDef) and x.name=='_wait_for_first_frame')
ns={'asyncio':asyncio,'contextlib':contextlib,'FIRST_FRAME_TIMEOUT_SECONDS':0.04}
exec(compile(ast.Module(body=[method],type_ignores=[]),'<startup>','exec'),ns)
async def pending(): await asyncio.Event().wait()
async def main():
    for mode in ['ready','feed_end','feed_error','decoder_end','simultaneous_end','timeout','cancel']:
        feed=asyncio.create_task(pending());frame=asyncio.create_task(pending())
        b=SimpleNamespace(_feed_task=feed,_frame_task=frame,_first_frame_event=asyncio.Event(),_active=True,_latest_frame=b'jpeg',_last_error=None)
        async def finish(): pass
        async def fail(): raise OSError('synthetic')
        if mode in ('feed_end','feed_error'):
            feed.cancel();await asyncio.gather(feed,return_exceptions=True)
            b._feed_task=asyncio.create_task(fail() if mode=='feed_error' else finish())
        if mode in ('decoder_end','simultaneous_end'):
            frame.cancel();await asyncio.gather(frame,return_exceptions=True)
            b._frame_task=asyncio.create_task(finish())
        if mode in ('ready','simultaneous_end'): b._first_frame_event.set()
        run=asyncio.create_task(ns['_wait_for_first_frame'](b))
        if mode=='cancel':
            await asyncio.sleep(0);run.cancel()
        expected=None if mode=='ready' else (TimeoutError if mode=='timeout' else asyncio.CancelledError if mode=='cancel' else RuntimeError)
        try:
            await run
            assert expected is None,mode
        except BaseException as e:
            assert expected and isinstance(e,expected),(mode,e)
        finally:
            for task in (b._feed_task,b._frame_task):task.cancel()
            await asyncio.gather(b._feed_task,b._frame_task,return_exceptions=True)
        await asyncio.sleep(0)
        assert len(asyncio.all_tasks())==1, 'Leaked startup waiter'
        print('PASS',mode)
asyncio.run(main())
