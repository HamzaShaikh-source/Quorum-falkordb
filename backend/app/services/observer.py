"""
Passive premise extraction.

Agents are bad at calling remember_fact: they forget, or they save the wrong thing. The observer reads text the agent
already produced (a chat transcript, a tool result, a ticket thread) and proposes the sentences that look like facts a
team would want to keep: a number with a unit, a policy ("must", "within", "never"), a named limit or date.

It is rule based and conservative, with no model call. Every stored fact keeps the sentence it came from as its `quote`
and an origin of observer:<source>, so a person can see where it came from; the usual contradiction check applies, so a
conflicting number opens a review instead of silently replacing the old one.
"""
import hashlib
import re
from typing import Any, Dict, List, Optional

_UNIT = r"(?:days?|hours?|hrs?|minutes?|mins?|seconds?|secs?|weeks?|months?|years?|%|percent|usd|eur|gbp|dollars?|euros?|pounds?|ms|gb|mb|tb|kb|users?|seats?|requests?|calls?|tokens?|retries|attempts?)"
_NUM_UNIT = re.compile(rf"(?:[$€£]\s?\d[\d,.]*\s?[kmb]?|\b\d[\d,.]*\s?{_UNIT}\b)", re.I)
_POLICY = re.compile(r"\b(must|shall|should|required|requires?|never|always|only|at most|at least|no more than|no later than|within|policy|limit|deadline|sla|cap|quota|threshold|maximum|minimum|expires?|valid for|allowed|forbidden|prohibited)\b", re.I)
_DATE = re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2}(?:,? \d{4})?|v?\d+\.\d+(?:\.\d+)?)\b", re.I)
_HEDGE = re.compile(r"\b(maybe|perhaps|i think|i guess|probably|might|not sure|let me|i'll|i will|we could|could be|seems|looks like|appears|try |trying|let's|should i|do you want|would you like|hopefully|assume|assuming)\b", re.I)
# Narration about what the agent just did is not a fact about the world.
_NARRATION = re.compile(r"\b(i ran|i've|i have|we ran|ran|took|passed|failed|completed|compiled|installed|created|updated|deleted|edited|wrote|opened|saw|noticed|checked|finished|started|loaded|fixed|added|removed)\b", re.I)
_STARTERS = {"The", "This", "That", "These", "Those", "All", "Any", "Each", "Every", "There", "Our", "Your", "Its", "Their", "Some", "Most", "Both", "When", "If", "For", "After", "Before", "Then", "Also", "Note", "Here", "Now", "Sure", "Yes", "Okay", "First", "Second", "Third", "Finally", "However", "Because", "Since"}
_STOP = set("the a an and or of to in on at by for with from is are was were be been it this that these those as if so we you i they their our your its has have had will would can could may also then than into about per".split())
# Never keep a sentence that looks like it carries a credential, whatever else it says.
_SECRET = re.compile(r"(\[redacted\]|sk-[A-Za-z0-9_-]{12,}|qrm_[a-z0-9_-]{6,}|AKIA[0-9A-Z]{12,}|ghp_[A-Za-z0-9]{20,}|xox[abp]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{20,}|bearer\s+[A-Za-z0-9._~+/-]{16,}|(?:-----)?BEGIN [A-Z ]*PRIVATE KEY|(?:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*\S{6,})", re.I)
_CODE_FENCE = re.compile(r"```(?:.*?```|.*$)", re.S)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+|(?<=;)\s+")


def _norm(s: str) -> str:
    return re.sub(r"[^\w\s]", "", re.sub(r"\s+", " ", s.lower()), flags=re.UNICODE).strip()


def fingerprint(s: str) -> str:
    return hashlib.blake2b(_norm(s).encode("utf8"), digest_size=8).hexdigest()


def _content_words(s: str) -> List[str]:
    return [w for w in re.findall(r"[a-z][a-z0-9]{2,}", s.lower()) if w not in _STOP]


def candidates(text: str, max_facts: int = 12) -> List[Dict[str, Any]]:
    """Sentences that look like durable facts, best first. Each: {text, key, value, confidence, signals}."""
    text = _CODE_FENCE.sub(" ", text or "")
    out: List[Dict[str, Any]] = []
    seen = set()
    for raw in _SENT_SPLIT.split(text):
        s = re.sub(r"^[\s>*\-•#\d.)]+", "", raw).strip()
        if not (24 <= len(s) <= 320) or s.endswith("?"):
            continue
        if _SECRET.search(raw) or _SECRET.search(s) or _HEDGE.search(s) or _NARRATION.search(s) or s.count("`") >= 4 or s.startswith(("http", "{", "[", "<")):
            continue
        signals = []
        score = 0
        nu = _NUM_UNIT.search(s)
        if nu:
            score += 2
            signals.append("number")
        if _POLICY.search(s):
            score += 1
            signals.append("policy")
        if _DATE.search(s):
            score += 1
            signals.append("date")
        if any(w not in _STARTERS for w in re.findall(r"\b[A-Z][a-z]{3,}\b", s)) or re.search(r"\b[A-Z]{2,}\b", s):
            score += 1
            signals.append("named")
        if score < 3:
            continue
        fp = fingerprint(s)
        if fp in seen:
            continue
        seen.add(fp)
        value: Optional[str] = nu.group(0).strip() if nu else None
        key: Optional[str] = None
        if value:
            named = [w.lower() for w in re.findall(r"\b[A-Z][a-z]{3,}\b", s) if w not in _STARTERS] + [w.lower() for w in re.findall(r"\b[A-Z]{2,}\b", s)]
            entity_tokens = list(dict.fromkeys(named))
            concepts = sorted(set(w for w in _content_words(_NUM_UNIT.sub(" ", s)) if w not in set(entity_tokens)))[:4]
            key = "_".join(entity_tokens + concepts)[:60] or None
        out.append({"text": s, "key": key, "value": value, "confidence": round(min(1.0, 0.25 * score), 2), "signals": signals, "fingerprint": fp})
    out.sort(key=lambda c: -c["confidence"])
    return out[: max(0, max_facts)]
