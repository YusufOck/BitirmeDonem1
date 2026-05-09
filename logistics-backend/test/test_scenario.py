import pytest
# We mock out the internal matrix adjustment since it's an internal function in routes.py
from api.schemas import SegmentConditionOverride

def test_segment_override_model():
    override = SegmentConditionOverride(
        from_stop_id="A",
        to_stop_id="B",
        extra_delay_min=5.0
    )
    assert override.extra_delay_min == 5.0
