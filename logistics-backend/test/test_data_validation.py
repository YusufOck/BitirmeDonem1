import pytest
from pydantic import ValidationError
from api.schemas import StopInput

def test_stop_input_schema_valid():
    stop = StopInput(stop_sequence=1)
    assert stop.stop_sequence == 1

def test_stop_input_schema_invalid():
    with pytest.raises(ValidationError):
        StopInput(stop_sequence=0)
