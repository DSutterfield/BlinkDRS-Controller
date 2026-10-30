"""Explicit opt-in. No configuration file means the pilot is disabled."""
import json,os
from pathlib import Path
def enabled():
    if 'BLINK_NAME_FIRST_PILOT' in os.environ:return os.environ['BLINK_NAME_FIRST_PILOT']=='1'
    try:return json.loads(Path(__file__).with_name('.name-first-review-pilot.json').read_text()).get('enabled') is True
    except (OSError,ValueError,AttributeError):return False
