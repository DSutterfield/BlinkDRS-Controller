"""Bounded NetworkManager recovery, started by Windows health requests.

Never reboot, change Wi-Fi credentials, or disconnect a profile explicitly.
All subprocess arguments are fixed or obtained from the local NetworkManager.
"""
import asyncio
import contextlib
import json
import logging
import time
import uuid
from pathlib import Path
from fault_log import error_code

from aiohttp import ClientSession, ClientTimeout

log = logging.getLogger(__name__)


async def internet_probe():
    # An independent session also avoids mistaking a broken Blink session for
    # an internet outage. Either provider is sufficient; redirects do not count.
    async with ClientSession(timeout=ClientTimeout(total=3)) as session:
        async def check(url):
            try:
                async with session.get(url, allow_redirects=False) as response:
                    return response.status == 204
            except Exception:
                return False
        results = await asyncio.gather(
            check("https://connectivitycheck.gstatic.com/generate_204"),
            check("https://cp.cloudflare.com/generate_204"),
        )
        return any(results)


async def command(*args):
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 40)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError(stderr.decode(errors="replace").strip()[:300]
                           or f"{args[0]} exited with {process.returncode}")
    return stdout.decode().strip()


async def reconnect_network():
    routes = json.loads(await command("ip", "-j", "route", "show", "default"))
    routes = sorted(routes, key=lambda route: route.get("metric", 0))
    if routes and routes[0].get("dev"):
        interface = routes[0]["dev"]
    else:
        active = await command("nmcli", "-t", "-f", "UUID,TYPE,DEVICE",
                               "connection", "show", "--active")
        interfaces = [line.split(":")[2] for line in active.splitlines()
                      if len(line.split(":")) == 3 and line.split(":")[1] in
                      {"802-11-wireless", "802-3-ethernet"}]
        if len(interfaces) != 1:
            raise RuntimeError("No unambiguous active network interface was found.")
        interface = interfaces[0]
    profile = await command("nmcli", "-g", "GENERAL.CON-UUID", "device", "show", interface)
    profile = str(uuid.UUID(profile))
    await command("sudo", "-n", "nmcli", "--wait", "30", "connection", "up",
                  "uuid", profile, "ifname", interface)


class NetworkRecovery:
    def __init__(self, state_path, busy=lambda: False, *, probe=internet_probe,
                 reconnect=reconnect_network, sleep=asyncio.sleep, clock=time.time, faults=None):
        self.faults = faults
        self.path = Path(state_path)
        self.busy = busy
        self.probe = probe
        self.reconnect = reconnect
        self.sleep = sleep
        self.clock = clock
        self.task = None
        self.next_check = 0
        self.last_attempt = 0
        self.reachable = None
        self.state = "checking"
        self.message = "Checking the Pi's internet connection."
        self.updated_at = clock()
        try:
            self.last_attempt = float(json.loads(self.path.read_text())["last_attempt"])
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def status(self):
        # One task per controller, regardless of how many Windows clients poll.
        if (self.task is None or self.task.done()) and self.clock() >= self.next_check:
            self.task = asyncio.create_task(self.run())
        return {"state": self.state, "message": self.message,
                "internet_reachable": self.reachable, "updated_at": self.updated_at}

    def report(self, state, message):
        if state != self.state:
            log.info("Internet recovery: %s - %s", state, message)
        self.state, self.message, self.updated_at = state, message, self.clock()

    async def check(self):
        self.reachable = bool(await self.probe())
        if self.faults:
            self.faults.safe_observe('internet', 'Pi internet connection', self.reachable,
                                    'CONNECTIVITY_CHECK_FAILED', 'Internet connectivity check result.')
            if self.reachable:
                self.faults.safe_observe('network-recovery', 'Pi network recovery', True)
        if self.reachable:
            if self.state not in ("healthy", "checking"):
                self.report("recovered", "The Pi's internet connection is restored.")
            elif self.state != "recovered":
                self.report("healthy", "The Pi is connected to the internet.")
        return self.reachable

    async def run(self):
        try:
            if await self.check():
                return
            remaining = self.last_attempt + 900 - self.clock()
            if remaining > 0:
                self.report("cooldown", "Internet is still unavailable. "
                            f"The next reconnection attempt is in {int(remaining / 60) + 1} minute(s). "
                            "Check the router or internet provider if the outage continues.")
                return
            self.report("retrying", "The Pi is reachable locally, but internet checks failed. "
                        "Checking again before reconnecting.")
            # Three failed rounds across 30 seconds, not a single timeout.
            for _ in range(2):
                await self.sleep(15)
                if await self.check():
                    return
            if self.busy():
                self.report("deferred", "Internet is unavailable. Reconnection is waiting "
                            "for Live View, recording, or an archive operation to finish.")
                return
            # Persist the rate limit BEFORE changing the network, including
            # unsuccessful commands, so restarting the service cannot loop.
            self.last_attempt = self.clock()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"last_attempt": self.last_attempt}))
            temporary.replace(self.path)
            self.report("reconnecting", "Reconnecting the Pi's network connection. "
                        "BlinkDRS and the shared drive may briefly disconnect.")
            # Give Windows time to display the message before Wi-Fi changes.
            await self.sleep(10)
            if await self.check():
                return
            if self.busy():
                self.report("deferred", "Reconnection deferred because a camera or archive "
                            "operation started. Internet checks will continue.")
                return
            await self.reconnect()
            self.report("verifying", "The network reconnection finished. "
                        "Verifying the Pi's internet access.")
            for _ in range(6):
                await self.sleep(10)
                if await self.check():
                    return
            self.report("failed", "The Pi reconnected to the local network, but internet "
                        "access is still unavailable. Check the router or internet provider. "
                        "Automatic reconnection will wait 15 minutes between attempts.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.faults:
                self.faults.safe_observe('network-recovery', 'Pi network recovery', False,
                                        error_code(exc), 'Automatic network recovery failed.')
            log.exception("Internet recovery failed")
            self.report("failed", "Automatic network recovery could not complete. "
                        "Check the Pi's network configuration and controller log. "
                        "Internet checks will continue.")
        finally:
            self.next_check = self.clock() + 10

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
