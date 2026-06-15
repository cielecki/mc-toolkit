"""Shared hallucination / boilerplate filter for whisper output.

ONE copy of the logic that was duplicated across voicememos/transcribe.py,
space-survivor/whisper_words.py, and voice-mode/listen.py. mlx-whisper emits
YouTube-outro / subtitle-credit boilerplate and repetition loops on borderline or
silent audio even after VAD; these gates drop them.
"""

# Substring match, lowercased. Subtitle-credit / outro boilerplate Whisper invents.
BOILERPLATE = (
    "amara.org", "napisy stworzone przez", "napisy: ", "subtitles by",
    "wszystkie prawa zastrzeżone", "dziękuję za uwagę", "dziękuję za oglądanie",
    "zapraszam do subskrypcji", "zapraszam na kanał", "do zobaczenia",
    "thanks for watching", "thank you for watching",
)


def is_boilerplate(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return True
    return any(b in t for b in BOILERPLATE)


def drop_segment(seg: dict) -> bool:
    """Whisper's own hallucination/repetition gates + the boilerplate filter.
    `compression_ratio > 2.4` is the standard openai-whisper signal for a degenerate
    repetition loop ("placu internetu placu internetu…"); a very low `avg_logprob`
    means Whisper had no real signal. Drop those segments whole."""
    if is_boilerplate(seg.get("text", "")):
        return True
    cr = seg.get("compression_ratio")
    if isinstance(cr, (int, float)) and cr > 2.4:
        return True  # repetition loop
    lp = seg.get("avg_logprob")
    if isinstance(lp, (int, float)) and lp < -1.0:
        return True  # no real signal — likely hallucinated
    return False
