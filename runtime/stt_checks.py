# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""A pass over what speech recognition returned, before it is typed anywhere.

Ported from Caroline (backend-py/app/stt_sanity.py): the same deterministic checks (what is known never to
be speech: the recognizer's inventions on silence and noise, one phrase looping, no letters at all), and the
same rule for the optional model step: a FIX is accepted only if it stays close to what was heard. Whatever
goes wrong in the model step leaves the transcript as recognition gave it: a real utterance is never lost.

Not ported: Caroline's per-user adaptation (stt_adaptation.py) learns from what the user edits in her chat,
which a keyboard does not provide; it comes with Caroline itself.
"""
import difflib, re

MODEL_MIN_WORDS = 3  # shorter utterances are left to the code checks ("да", "стоп")
FIX_MIN_SIMILARITY = 0.6
LOOP_MIN_REPEATS = 4

_HALLUCINATIONS = re.compile(
    r"^\W*(?:"
    r"субтитры\s+(?:сделал|создавал|создал|делал|подготовил)\w*\s+\S+(?:\s+\S+)?"
    r"|редактор\s+субтитров\b.*"
    r"|корректор\s+\S+(?:\s+\S+)?"
    r"|продолжение\s+следует"
    r"|спасибо\s+за\s+(?:просмотр|внимание)"
    r"|подписывайтесь\s+на\s+(?:наш\s+)?канал.*"
    r"|ставьте\s+лайки?.*"
    r"|дякую\s+за\s+(?:перегляд|увагу)"
    r"|thanks?\s+(?:you\s+)?for\s+watching.*"
    r"|subtitles\s+by\b.*"
    r"|(?:\[|\()\s*(?:музыка|music|аплодисменты|applause|смех|laughter|тишина|silence)\s*(?:\]|\))"
    r")\W*$",
    re.IGNORECASE | re.DOTALL,
)
_LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)


def _looping(words):
    for size in (1, 2, 3):
        if len(words) < size * LOOP_MIN_REPEATS:
            continue
        unit = words[:size]
        if all(words[i:i + size] == unit for i in range(0, len(words) - len(words) % size, size)) and len(words) % size == 0:
            return True
    return False


def check_by_code(text):
    """(verdict, text): ("garbage", "") for noise, ("ok", text) for a short utterance, None when the model should judge."""
    s = text.strip()
    if not s or not _LETTERS.search(s):
        return "garbage", ""
    if _HALLUCINATIONS.match(s):
        return "garbage", ""
    words = re.findall(r"\w+", s.lower().replace("ё", "е"))
    if _looping(words):
        return "garbage", ""
    if len(words) < MODEL_MIN_WORDS:
        return "ok", s
    return None


def _prompt(text):
    return (
        "CRITICAL: a program parses your reply. Reply with exactly one of: OK | FIX: <the corrected text> | GARBAGE\n\n"
        "Below is what a speech recognizer heard a user say, in their own language. "
        "Judge only whether it is a meaningful, connected utterance.\n"
        "- OK: it makes sense as it is. Casual speech, filler words, slang, an unfinished thought, a name or a term "
        "you do not know, a question you cannot answer -- all of that is OK. When in doubt, OK.\n"
        "- FIX: it makes sense except for a word or two that are plainly a mishearing of something that sounds "
        "alike and fits (\"по лохе это должно работать\" -> \"по логике это должно работать\"). Give the whole "
        "utterance back with only those words replaced. Never rephrase, complete, shorten, translate or "
        "answer it; never touch names, numbers or wording that already makes sense.\n"
        "- GARBAGE: it is not speech addressed to anyone -- words with no connection to each other, the "
        "recognizer's own filler, text in another language than the speaker's with no sense in it.\n\n"
        f"Heard:\n---\n{text}\n---\n\nReply now."
    )


def _accept_fix(heard, fixed):
    a, b = heard.lower().replace("ё", "е"), fixed.lower().replace("ё", "е")
    if not fixed.strip() or abs(len(fixed) - len(heard)) > max(8, len(heard) * 0.3):
        return False
    if set(re.findall(r"\d+", b)) - set(re.findall(r"\d+", a)):  # a digit is never a mishearing repair
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= FIX_MIN_SIMILARITY


def check_transcript(text, resolve=None):
    """The whole pass; `resolve(prompt) -> reply` is the model step. Returns the text to use ("" = nothing said). Never raises."""
    by_code = check_by_code(text)
    if by_code is not None:
        return by_code[1]
    heard = text.strip()
    if resolve is None:
        return heard
    try:
        raw = str(resolve(_prompt(heard)) or "").strip()
    except Exception:  # noqa: BLE001 -- a real utterance is never lost to this pass
        return heard
    upper = raw.upper()
    if upper.startswith("GARBAGE"):
        return ""
    if upper.startswith("FIX"):
        fixed = raw.split(":", 1)[1].strip().strip("\"«»") if ":" in raw else ""
        return fixed if fixed and fixed != heard and _accept_fix(heard, fixed) else heard
    return heard
