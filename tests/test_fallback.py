import app as appmod

SAMPLE = ("⚠️ Model `Kimi-K2.6` encountered an error (HTTP 400 Bad Request). "
          "This response was generated using the fallback model `mimo`.\n\nyes")

def test_detects_the_observed_notice():
    fb = appmod.detect_fallback(SAMPLE)
    assert fb == {"failed": "Kimi-K2.6", "served_by": "mimo", "reason": "HTTP 400 Bad Request"}

def test_tolerates_missing_backticks_and_leading_whitespace():
    text = "  Model Kimi-K2.6 encountered an error (HTTP 502). Generated using the fallback model mimo. ok"
    fb = appmod.detect_fallback(text)
    assert fb and fb["served_by"] == "mimo" and fb["failed"] == "Kimi-K2.6"

def test_ignores_normal_replies_and_late_mentions():
    assert appmod.detect_fallback("A cartoon avatar of a man.") is None
    assert appmod.detect_fallback(None) is None and appmod.detect_fallback(42) is None
    # mention deep inside a long answer must not trigger
    late = ("x" * 400) + " the model encountered an error ... fallback model `y`"
    assert appmod.detect_fallback(late) is None
    # only one of the two phrases
    assert appmod.detect_fallback("Use a fallback model when needed.") is None
