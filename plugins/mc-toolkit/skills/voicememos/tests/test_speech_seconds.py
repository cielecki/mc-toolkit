import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "stt", "engines"))
import local

def test_speech_seconds_sums_ranges_over_sr():
    # two ranges: 16000 samples (1.0s) + 8000 samples (0.5s) at 16kHz → 1.5s
    ranges = [{"start": 0, "end": 16000}, {"start": 32000, "end": 40000}]
    assert local.speech_seconds_from_ranges(ranges) == 1.5

def test_speech_seconds_empty_is_zero():
    assert local.speech_seconds_from_ranges([]) == 0.0
