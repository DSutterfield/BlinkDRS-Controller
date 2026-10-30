"""Opt-in review-only pilot shared by services and helper RPC processes."""
import json,os
from pathlib import Path
SETTINGS_PATH=Path(__file__).with_name('.dog-review-pilot.json')

def enabled():
    if 'BLINK_DOG_REVIEW_PILOT' in os.environ:return os.environ['BLINK_DOG_REVIEW_PILOT']=='1'
    try:return json.loads(SETTINGS_PATH.read_text()).get('enabled') is True
    except (OSError,ValueError,AttributeError):return False
