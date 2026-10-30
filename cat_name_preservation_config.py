"""Separate opt-in for soft Cat review hints; default off, no auto-confirmation."""
import json,os
from pathlib import Path

def enabled():
    if 'BLINK_SOFT_CAT_REVIEW' in os.environ:return os.environ['BLINK_SOFT_CAT_REVIEW']=='1'
    try:return json.loads(Path(__file__).with_name('.soft-cat-review-pilot.json').read_text()).get('enabled') is True
    except (OSError,ValueError,AttributeError):return False
