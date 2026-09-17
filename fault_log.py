"""Durable, human-readable fault transitions. No credentials or response bodies."""
import configparser
import json
import logging
import os
import re
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
CENTRAL_TIME = ZoneInfo('America/Chicago')


def describe_error(message, fallback='ERROR'):
    """Extract explicitly labeled codes, never ports, IDs, or arbitrary numbers.

    Descriptions are fixed text: raw library messages can contain credentials,
    signed stream URLs, account information, and entire server responses.
    """
    message = str(message)
    code_text = re.sub(r'\b[a-z][a-z0-9+.-]*://\S+', '', message, flags=re.IGNORECASE)
    codes = []
    for match in re.finditer(
            r'\bHTTP(?:/\d(?:\.\d)?)?\s+(?:(?:status|error)(?:\s+code)?\s*)?[:=]?\s*([1-5]\d{2})\b',
            code_text, re.IGNORECASE):
        codes.append('HTTP_' + match.group(1))
    for match in re.finditer(
            r'''\b(status_code|error_code|code)['"]?\s*[:=]\s*['"]?(-?\d{1,8})\b''',
            code_text, re.IGNORECASE):
        codes.append('REPORTED_' + match.group(1).upper() + ':' + match.group(2))
    lower = message.lower()
    if any(text in lower for text in ('name resolution', 'name or service not known', 'getaddrinfo failed', 'nodename nor servname')):
        kind, detail = 'DNS_LOOKUP_FAILED', "The Pi could not resolve the server's name to an IP address."
    elif any(text in lower for text in ('timed out', 'timeout', 'timeouterror')):
        kind, detail = 'CONNECTION_TIMEOUT', 'The request or connection did not complete before its timeout.'
    elif any(text in lower for text in ('certificate verify failed', 'certificate_verify_failed', 'sslerror', 'sslcertverificationerror')):
        kind, detail = 'TLS_CONNECTION_FAILED', 'The secure connection or certificate check failed.'
    elif 'connection refused' in lower:
        kind, detail = 'CONNECTION_REFUSED', 'The remote endpoint refused the connection.'
    elif any(text in lower for text in ('cannot connect to host', 'connection error', 'connection reset', 'server disconnected', 'network is unreachable')):
        kind, detail = 'CONNECTION_FAILED', 'The connection to the remote endpoint failed or was interrupted.'
    elif codes:
        kind, detail = '', 'The request failed with the explicitly reported code shown here.'
    else:
        kind, detail = fallback, 'The operation reported a warning or error without an explicit numeric code.'
    code = ';'.join(dict.fromkeys(([kind] if kind else []) + codes))
    return code, detail + ' Details: blink_dvr.log in the same folder as this fault log.'


def clean(value):
    text = str(value or '').replace('\t', ' ').replace('\r', ' ').replace('\n', ' ')
    text = re.sub(r'https?://\S+', '[URL omitted]', text)
    text = re.sub(r'(?i)(password|token|authorization|secret|cookie)\s*[:=]\s*\S+',
                  r'\1=[redacted]', text)
    return text[:400]


def error_code(exc):
    for name in ('status', 'status_code', 'errno', 'code'):
        value = getattr(exc, name, None)
        if value is not None:
            return f'{type(exc).__name__}:{name}={clean(value)}'
    return type(exc).__name__


class FaultLog:
    def __init__(self, directory, settings_path, clock=None):
        self.path = Path(directory) / 'fault_error.log'
        self.state_path = Path(directory) / 'fault_state.json'
        self.settings_path = Path(settings_path)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.RLock()
        self.states = {}
        self.ids = set()
        self.days = 90
        self.last_error = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        config = configparser.ConfigParser()
        config.read(self.settings_path)
        try:
            self.days = self.validate_days(config.getint('fault_log', 'delete_after_days', fallback=90))
        except ValueError:
            log.warning('Invalid fault log retention; using 90 days')
        try:
            self.states = json.loads(self.state_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        self.prune()
        if self.path.exists():
            with self.path.open(encoding='utf-8') as stream:
                for line in stream:
                    parts = line.rstrip('\n').split('\t')
                    if len(parts) == 7:
                        self.ids.add(parts[6])

    @staticmethod
    def validate_days(days):
        if type(days) is not int or not 1 <= days <= 3650:
            raise ValueError('Enter a whole number from 1 to 3650 days.')
        return days

    @staticmethod
    def atomic_write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + '.tmp')
        with temp.open('w', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)

    def save_days(self, days):
        days = self.validate_days(days)
        with self.lock:
            config = configparser.ConfigParser()
            config.read(self.settings_path)
            if not config.has_section('fault_log'):
                config.add_section('fault_log')
            config.set('fault_log', 'delete_after_days', str(days))
            import io
            text = io.StringIO()
            config.write(text)
            self.atomic_write(self.settings_path, text.getvalue())
            self.days = days
        return days

    def append(self, event, source, code='', message='', when=None, entry_id=''):
        with self.lock:
            if entry_id and entry_id in self.ids:
                return
            when = when or self.clock()
            if when < self.clock() - timedelta(days=self.days):
                return
            fields = [when.astimezone(CENTRAL_TIME).isoformat(), event, source, code, message, 'BlinkDRS', entry_id]
            with self.path.open('a', encoding='utf-8', newline='\n') as stream:
                stream.write('\t'.join(clean(value) for value in fields) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            if entry_id:
                self.ids.add(entry_id)

    def observe(self, key, source, healthy, code='', message=''):
        """Unknown does not imply healthy; never invent recovery from missing data."""
        if healthy is None:
            return
        with self.lock:
            previous = self.states.get(key)
            state = {'healthy': bool(healthy), 'code': clean(code) if not healthy else '', 'source': clean(source)}
            if previous == state:
                return
            if not healthy:
                self.append('FAULT', source, code, message)
            elif previous and not previous['healthy']:
                self.append('RESTORED', source, '', message or 'Connection reestablished.')
            self.states[key] = state
            self.atomic_write(self.state_path, json.dumps(self.states, indent=2))

    def safe_observe(self, *args, **kwargs):
        try:
            self.observe(*args, **kwargs)
            self.last_error = None
        except OSError as exc:
            self.last_error = error_code(exc)
            log.error('Fault log write failed: %s', self.last_error)

    def devices(self, blink):
        for system in blink.sync.values():
            self.safe_observe('sync:' + str(system.network_id),
                              f'Sync Module: {system.name} ({system.network_id})',
                              bool(system.online and system.available), 'DEVICE_OFFLINE',
                              'Blink reported the Sync Module status. DEVICE_OFFLINE is a BlinkDRS status, not a vendor error code.')
            if not (system.online and system.available):
                for key, state in list(self.states.items()):
                    if key.startswith('camera:' + str(system.network_id) + ':'):
                        self.safe_observe(key, state['source'], False, 'DEVICE_OFFLINE',
                                          'Camera unreachable because its Sync Module is offline.')
        for camera in blink.cameras.values():
            self.safe_observe('camera:' + str(camera.sync.network_id) + ':' + str(camera.camera_id),
                              f'Camera: {camera.name} ({camera.camera_id}); system: {camera.sync.name}',
                              bool(camera.online and camera.sync.available), 'DEVICE_OFFLINE',
                              'Blink reported the camera or its Sync Module status.')

    def prune(self):
        with self.lock:
            if not self.path.exists():
                return 0
            cutoff = self.clock() - timedelta(days=self.days)
            removed = 0
            temp = self.path.with_suffix('.prune.tmp')
            with self.path.open(encoding='utf-8') as source, temp.open('w', encoding='utf-8', newline='\n') as target:
                for line in source:
                    try:
                        expired = datetime.fromisoformat(line.split('\t', 1)[0]) < cutoff
                    except (ValueError, TypeError):
                        expired = False  # Keep unrecognized evidence; never erase it by guessing.
                    if expired:
                        removed += 1
                    else:
                        target.write(line)
                target.flush()
                os.fsync(target.fileno())
            temp.replace(self.path)
            return removed

    def tail(self, limit=2000):
        with self.lock:
            if not self.path.exists():
                return ''
            with self.path.open(encoding='utf-8') as stream:
                return ''.join(deque(stream, maxlen=limit))


class BlinkErrorHandler(logging.Handler):
    """Keep BlinkPy warning/error codes without retaining API bodies or auth data."""
    def __init__(self, faults):
        super().__init__(logging.WARNING)
        self.faults = faults
        self.revision = 0

    def emit(self, record):
        self.revision += 1
        code, detail = describe_error(record.getMessage(), record.levelname)
        self.faults.safe_observe('blinkpy:' + record.name, 'Blink library: ' + record.name,
                                False, code, detail)

    def recovered(self):
        for key in list(self.faults.states):
            if key.startswith('blinkpy:'):
                self.faults.safe_observe(key, 'Blink library: ' + key[8:], True,
                                        message='A full Blink poll completed without library warnings or errors.')
