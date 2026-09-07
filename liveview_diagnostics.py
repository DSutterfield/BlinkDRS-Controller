"""Monotonic startup milestones, shared safely with the controller API thread."""
import json
import logging
import threading
import time
import uuid


class StartupDiagnostics:
    def __init__(self, started_at=None, clock=time.monotonic):
        self._clock = clock
        self._started_at = clock() if started_at is None else started_at
        self._lock = threading.Lock()
        self._marks = {}
        self._delivery = {}
        self._session_id = uuid.uuid4().hex

    def mark(self, name):
        with self._lock:
            if name not in self._marks:
                self._marks[name] = round((self._clock() - self._started_at) * 1000, 3)

    def pulse(self, name, size=0):
        """Record bounded timing summaries, never media payloads."""
        now = (self._clock() - self._started_at) * 1000
        with self._lock:
            entry = self._delivery.setdefault(name, {
                "count": 0, "bytes": 0, "first_ms": now, "last_ms": now,
                "max_gap_ms": 0, "gap_count": 0, "gaps": [],
            })
            if entry["count"]:
                gap = now - entry["last_ms"]
                entry["max_gap_ms"] = max(entry["max_gap_ms"], gap)
                if gap >= 500:
                    entry["gap_count"] += 1
                    if len(entry["gaps"]) < 128:
                        entry["gaps"].append({"from_ms": round(entry["last_ms"], 3),
                                              "to_ms": round(now, 3),
                                              "duration_ms": round(gap, 3)})
            entry["count"] += 1
            entry["bytes"] += size
            entry["last_ms"] = now

    def snapshot(self):
        with self._lock:
            now = (self._clock() - self._started_at) * 1000
            delivery = {name: {**entry, "gaps": [dict(g) for g in entry["gaps"]],
                              "current_age_ms": round(now - entry["last_ms"], 3)}
                        for name, entry in self._delivery.items()}
            return {"session_id": self._session_id, "elapsed_ms": dict(self._marks),
                    "delivery": delivery}

    def log(self, outcome):
        logging.getLogger("blink_dvr.liveview").info(
            "LiveView startup %s", json.dumps({"outcome": outcome, **self.snapshot()})
        )
