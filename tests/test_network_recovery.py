"""Recovery tests use injected probes/commands and never change networking."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from network_recovery import NetworkRecovery, reconnect_network


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "state.json"
        self.now = 10000
        self.calls = 0
        self.results = []
        self.states = []

    def recovery(self, results, busy=lambda: False, fail_command=False):
        self.results = list(results)
        async def probe():
            return self.results.pop(0) if self.results else False
        async def sleep(seconds):
            self.now += seconds
            self.states.append(service.state)
        async def reconnect():
            self.calls += 1
            if fail_command:
                raise RuntimeError("Simulated NetworkManager failure")
        service = NetworkRecovery(self.path, busy, probe=probe,
                                  reconnect=reconnect, sleep=sleep,
                                  clock=lambda: self.now)
        return service

    async def test_healthy_never_reconnects(self):
        service = self.recovery([True])
        await service.run()
        self.assertEqual(service.state, "healthy")
        self.assertEqual(self.calls, 0)

    async def test_transient_failure_recovers_without_command(self):
        service = self.recovery([False, True])
        await service.run()
        self.assertEqual(service.state, "recovered")
        self.assertEqual(self.calls, 0)

    async def test_persistent_failure_reconnects_and_verifies(self):
        service = self.recovery([False, False, False, False, True])
        await service.run()
        self.assertEqual(self.calls, 1)
        self.assertEqual(service.state, "recovered")
        self.assertTrue(service.reachable)
        self.assertIn("retrying", self.states)
        self.assertIn("reconnecting", self.states)
        self.assertIn("verifying", self.states)

    async def test_router_outage_does_not_loop(self):
        service = self.recovery([False] * 10)
        await service.run()
        self.assertEqual(service.state, "failed")
        await service.run()
        self.assertEqual(service.state, "cooldown")
        self.assertEqual(self.calls, 1)

    async def test_cooldown_survives_service_restart(self):
        self.path.write_text(json.dumps({"last_attempt": self.now - 60}))
        service = self.recovery([False])
        await service.run()
        self.assertEqual(service.state, "cooldown")
        self.assertEqual(self.calls, 0)

    async def test_recovery_detected_during_cooldown(self):
        service = self.recovery([True])
        service.state = "cooldown"
        service.last_attempt = self.now - 60
        await service.run()
        self.assertEqual(service.state, "recovered")
        self.assertEqual(self.calls, 0)

    async def test_busy_camera_defers_reconnection(self):
        service = self.recovery([False] * 3, busy=lambda: True)
        await service.run()
        self.assertEqual(service.state, "deferred")
        self.assertEqual(self.calls, 0)
        self.assertFalse(self.path.exists())

    async def test_permission_failure_is_reported_and_rate_limited(self):
        service = self.recovery([False] * 5, fail_command=True)
        with self.assertLogs("network_recovery", level="ERROR"):
            await service.run()
        self.assertEqual(service.state, "failed")
        await service.run()
        self.assertEqual(service.state, "cooldown")
        self.assertEqual(self.calls, 1)

    async def test_multiple_clients_share_one_task(self):
        service = self.recovery([True])
        service.status()
        task = service.task
        service.status()
        self.assertIs(service.task, task)
        await task
        service.status()
        self.assertIs(service.task, task)
        await service.close()

    async def test_late_spontaneous_recovery_skips_network_change(self):
        service = self.recovery([False, False, False, True])
        await service.run()
        self.assertEqual(service.state, "recovered")
        self.assertEqual(self.calls, 0)

    async def test_reconnect_uses_existing_profile_and_default_interface(self):
        calls = []
        async def fake_command(*args):
            calls.append(args)
            if args[0] == "ip":
                return '[{"dev":"eth0","metric":700},{"dev":"wlan0","metric":600}]'
            if args[0] == "nmcli":
                return "42963b35-a145-3268-90c6-c942c867ccec"
            return "activated"
        with patch("network_recovery.command", fake_command):
            await reconnect_network()
        self.assertEqual(calls[-1], ("sudo", "-n", "nmcli", "--wait", "30", "connection",
                                    "up", "uuid", "42963b35-a145-3268-90c6-c942c867ccec",
                                    "ifname", "wlan0"))

    async def test_missing_default_route_uses_single_active_wifi_profile(self):
        calls = []
        async def fake_command(*args):
            calls.append(args)
            if args[0] == "ip":
                return "[]"
            if "--active" in args:
                return "42963b35-a145-3268-90c6-c942c867ccec:802-11-wireless:wlan0"
            if args[0] == "nmcli":
                return "42963b35-a145-3268-90c6-c942c867ccec"
            return "activated"
        with patch("network_recovery.command", fake_command):
            await reconnect_network()
        self.assertEqual(calls[-1][-2:], ("ifname", "wlan0"))


if __name__ == "__main__":
    unittest.main()
