"""Drain a header-complete MPEG-TS output and serve bounded local recording clients."""
import asyncio
import contextlib
import os


class RecordingRelay:
    def __init__(self):
        self.server=None
        self.read_fd=None
        self.write_fd=None
        self.task=None
        self.clients=set()

    @property
    def url(self):
        return f'tcp://127.0.0.1:{self.server.sockets[0].getsockname()[1]}'

    async def start(self):
        self.read_fd,self.write_fd=os.pipe()
        os.set_blocking(self.read_fd,False)
        try:
            self.server=await asyncio.start_server(self._join,'127.0.0.1',0)
            self.task=asyncio.create_task(self._pump())
        except BaseException:
            await self.stop()
            raise

    def close_parent_writer(self):
        if self.write_fd is not None:
            os.close(self.write_fd);self.write_fd=None

    async def _join(self,reader,writer):
        self.clients.add(writer)
        try:await reader.read()
        except (ConnectionError,asyncio.CancelledError):pass
        finally:
            self.clients.discard(writer)
            writer.close()

    async def _pump(self):
        try:
            while True:
                try:data=os.read(self.read_fd,64*1024)
                except BlockingIOError:
                    await asyncio.sleep(0.01);continue
                if not data:break
                for writer in tuple(self.clients):
                    if writer.is_closing():continue
                    try:
                        writer.write(data)
                        await asyncio.wait_for(writer.drain(),0.25)
                    except (ConnectionError,OSError,asyncio.TimeoutError):
                        writer.close()
        finally:
            for writer in tuple(self.clients):writer.close()

    async def stop(self):
        self.close_parent_writer()
        if self.server:
            self.server.close();await self.server.wait_closed();self.server=None
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):await self.task
            self.task=None
        for writer in tuple(self.clients):writer.close()
        self.clients.clear()
        if self.read_fd is not None:os.close(self.read_fd);self.read_fd=None
