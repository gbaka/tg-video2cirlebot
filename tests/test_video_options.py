"""Fragment input validation in seconds and mm:ss notation."""
import importlib

import pytest


@pytest.mark.parametrize("text,expected", [
    ("10 30", (10.0, 30.0)), ("01:20 00:30", (80.0, 30.0)),
    ("1:02:03 15.5", (3723.0, 15.5)), ("0 60", (0.0, 60.0)),
])
def test_fragment_input(text, expected):
    parser = importlib.import_module("services.video_options").parse_fragment
    assert parser(text, 60) == expected

@pytest.mark.parametrize("text", ["", "10", "-1 30", "1 0", "0 61", "nan 10", "inf 10",
                                      "1 30 junk", "1:99 30", "1:2:3:4 5", "x y"])
def test_invalid_fragment(text):
    parser = importlib.import_module("services.video_options").parse_fragment
    assert parser(text, 60) is None
