import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import quality

def w(text, conf=0.9):
    return {"text": text, "confidence": conf}

def test_no_speech_is_empty():
    assert quality.classify_health(1.0, 0, 300.0, None, False) == "empty"

def test_speech_but_no_words_is_suspect():
    assert quality.classify_health(120.0, 0, 300.0, None, False) == "suspect"

def test_normal_speech_is_healthy():
    # 200 words over 120s speech = 100 wpm, good confidence
    assert quality.classify_health(120.0, 200, 300.0, 0.85, False) == "healthy"

def test_speech_but_sparse_text_is_suspect():
    # 10 words over 120s speech = 5 wpm → STT barely caught anything
    assert quality.classify_health(120.0, 10, 300.0, 0.9, False) == "suspect"

def test_low_confidence_is_suspect():
    assert quality.classify_health(120.0, 200, 300.0, 0.30, False) == "suspect"

def test_repetition_loop_is_suspect():
    assert quality.classify_health(120.0, 200, 300.0, 0.85, True) == "suspect"

def test_mean_confidence_ignores_none():
    assert quality.mean_confidence([w("a", 0.8), w("b", None), w("c", 0.6)]) == 0.7

def test_mean_confidence_empty_is_none():
    assert quality.mean_confidence([]) is None

def test_repetition_loop_detects_dominant_trigram():
    words = [w(t) for t in (["ok", "ok", "ok"] * 20)]
    assert quality.is_repetition_loop(words) is True

def test_repetition_loop_false_on_varied_text():
    words = [w(t) for t in "the quick brown fox jumps over the lazy dog again today".split()]
    assert quality.is_repetition_loop(words) is False
