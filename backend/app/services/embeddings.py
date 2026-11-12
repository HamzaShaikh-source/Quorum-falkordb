"""
Semantic ranking for retrieve_context.

Keyword matching misses paraphrases ("money back" vs "refund"). This module adds a second signal: a vector per node,
compared with cosine similarity, blended with the IDF keyword score.

Two providers:
  local   (default) A deterministic hashed vector with no model and no network. Features are word stems, a built-in
          concept lexicon (refund = reimbursement = money back), word pairs and character trigrams, so it handles
          inflection, typos and the paraphrases the lexicon knows. It is NOT a neural model: a paraphrase using words
          outside the lexicon and sharing no stems will still miss.
  openai  Any OpenAI-compatible /v1/embeddings endpoint (OpenAI, Ollama, LM Studio, vLLM). Set QUORUM_EMBED_PROVIDER=openai,
          QUORUM_EMBED_URL, QUORUM_EMBED_MODEL (and QUORUM_EMBED_API_KEY if needed). Use this for broad coverage.

Vectors are cached per node and stored in FalkorDB (property `embedding` on the node, with a vector index), so a restart
does not recompute them and FalkorDB can answer nearest-neighbour queries (see /api/context/semantic-check).
"""
from collections import OrderedDict
import hashlib
import re
import threading
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from app.config import settings

DIM = 768

# Each line is one concept. Words in the same line are treated as the same idea. Plain business, support, finance,
# security and operations vocabulary; extend freely, it is only data.
_CONCEPTS = """
refund reimburse reimbursement repay repayment payback chargeback credit moneyback money_back pay_back give_back
price cost fee charge tariff rate pricing amount billing invoice bill
window deadline cutoff period duration timeframe allowance limit how_long time_limit
days day daily
hours hour hourly
minutes minute
week weeks weekly
month months monthly
year years yearly annual annually
customer client buyer shopper user account
employee staff worker teammate personnel
manager supervisor lead boss owner
approve approval authorize authorise sanction signoff permit allow
reject deny refuse decline block forbid
cancel terminate revoke void abort stop call_off
delete remove erase purge wipe drop
create add new open register enrol enroll
update change edit modify amend revise
delay late overdue slow lag behind running_late
fast quick rapid speedy prompt
secure security safe protect protected
password credential secret passphrase login signin
token key apikey secret
leak breach exposure exposed compromise compromised
policy rule guideline regulation procedure standard
escalate escalation raise forward hand_off
priority urgent critical severe important
bug defect issue problem fault error failure incident outage
fix repair resolve patch remedy
release deploy launch ship rollout publish
rollback revert undo restore roll_back go_back
backup snapshot archive copy
database datastore storage db
server host machine node instance
latency delay responsetime ping
throughput bandwidth capacity volume
shipping delivery dispatch courier freight postage
return send back
warranty guarantee assurance coverage
contract agreement terms deal
renew renewal extend extension prolong
expire expiry lapse end
discount rebate promotion promo coupon voucher offer sale
tax vat gst duty levy
salary pay wage payroll compensation remuneration
leave vacation holiday absence timeoff pto time_off day_off
hire recruit onboard onboarding
fire dismiss terminate offboard let_go
meeting call standup sync session
report summary overview digest
ticket case request query
complaint grievance dispute objection
satisfaction csat rating score feedback
sla servicelevel commitment
region area territory zone location market geography
europe eu emea european
america usa us states north
asia apac asian
vendor supplier provider partner
inventory stock supply warehouse
order purchase transaction sale
payment pay remit settle settlement transfer
fraud scam abuse misuse suspicious
audit review inspect inspection check verify validation
compliance regulatory legal lawful gdpr
privacy personal pii confidential
support helpdesk assistance service
contact email reach message notify notification alert
phone call telephone
chat conversation dialogue thread
limit cap maximum max ceiling quota
minimum min floor least
increase raise grow rise boost
decrease reduce lower drop cut shrink
approve accepted confirmed granted
contradict conflict inconsistent disagree mismatch
stale outdated obsolete old expired
healthy valid correct good ok
quarantine isolate isolated contained containment
cascade ripple propagate downstream
agent bot assistant ai llm model
""".strip().splitlines()

_STEM_SUFFIXES = ("ations", "ation", "ments", "ment", "ings", "ing", "edly", "ied", "ies", "ed", "ly", "es", "s")
_WORD = re.compile(r"[a-z0-9]{2,}")
_STOP = set("is on do to of in at by a an it be or as if so up we me my i am no yes get got make made take like just very much many more most some such only own same too use used using one two long often what how can could will the and for are was were with that this from what which who how when where why does did can its has have not you your our their about into than then them they will would should could been being also any all per via out off over under a an of to in on at by is it be or as if so".split())


def stem(w: str) -> str:
    for suf in _STEM_SUFFIXES:
        if len(w) > len(suf) + 2 and w.endswith(suf):
            w = w[: -len(suf)]
            break
    return w


def _build_lexicon() -> Tuple[Dict[str, str], List[Tuple[str, str]]]:
    lex: Dict[str, str] = {}
    phrases: List[Tuple[str, str]] = []
    for i, line in enumerate(_CONCEPTS):
        canon = f"c{i}"
        for w in line.split():
            if "_" in w:
                phrases.append((w.replace("_", " "), canon))
                continue
            lex.setdefault(w, canon)
            lex.setdefault(stem(w), canon)
    return lex, phrases


_LEX, _PHRASES = _build_lexicon()


def _hash(feature: str) -> Tuple[int, float]:
    d = hashlib.blake2b(feature.encode("utf8"), digest_size=8).digest()
    n = int.from_bytes(d, "little")
    return n % DIM, 1.0 if (n >> 63) & 1 else -1.0


def local_vector(text: str) -> np.ndarray:
    words = [w for w in _WORD.findall((text or "").lower())]
    v = np.zeros(DIM, dtype=np.float32)
    kept = [w for w in words if w not in _STOP]
    stems = [stem(w) for w in kept]

    def add(feat: str, weight: float) -> None:
        i, sgn = _hash(feat)
        v[i] += sgn * weight

    for w, s in zip(kept, stems, strict=True):
        add("w:" + s, 1.0)
        concept = _LEX.get(w) or _LEX.get(s)
        if concept:
            add("k:" + concept, 2.2)
        if len(w) >= 5:
            padded = f"^{w}$"
            for j in range(len(padded) - 2):
                add("t:" + padded[j : j + 3], 0.12)
    for a, b in zip(stems, stems[1:], strict=False):
        add(f"b:{a}_{b}", 0.5)
    low = " ".join(words)
    for phrase, canon in _PHRASES:
        if phrase in low:
            add("k:" + canon, 2.2)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


class _OpenAIProvider:
    def __init__(self):
        self.lock = threading.Lock()

    def embed(self, texts: Sequence[str]) -> List[np.ndarray]:
        import httpx

        url = settings.EMBED_URL.rstrip("/")
        if not url.endswith("/embeddings"):
            url += "/embeddings"
        headers = {"content-type": "application/json"}
        if settings.EMBED_API_KEY:
            headers["authorization"] = f"Bearer {settings.EMBED_API_KEY}"
        r = httpx.post(url, headers=headers, json={"model": settings.EMBED_MODEL, "input": list(texts)}, timeout=15)
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
        out = []
        for d in data:
            a = np.asarray(d["embedding"], dtype=np.float32)
            n = float(np.linalg.norm(a))
            out.append(a / n if n > 0 else a)
        return out

_openai = _OpenAIProvider()
_cache: OrderedDict[str, Tuple[str, np.ndarray]] = OrderedDict()  # node id -> (text hash, vector)
MAX_CACHE = 50000
_lock = threading.Lock()
_provider_failed_at = 0.0


def provider_name() -> str:
    if settings.EMBED_PROVIDER == "openai" and settings.EMBED_URL:
        return f"openai:{settings.EMBED_MODEL or 'default'}"
    return "local"


def dimension() -> int:
    return DIM if provider_name() == "local" else (next((v.shape[0] for _, v in _cache.values()), DIM))


def node_text(n) -> str:
    return f"{n.label} {n.text or ''} {n.key or ''} {n.value or ''}"


def _digest(s: str) -> str:
    return hashlib.blake2b(s.encode("utf8"), digest_size=8).hexdigest() + provider_name()


def _cache_touch(k: str) -> None:
    if hasattr(_cache, "move_to_end"):
        _cache.move_to_end(k)


def _cache_put(k: str, val: Tuple[str, np.ndarray]) -> None:
    _cache[k] = val
    if hasattr(_cache, "move_to_end"):
        _cache.move_to_end(k)
    while len(_cache) > MAX_CACHE:
        if hasattr(_cache, "popitem"):
            _cache.popitem(last=False)
        else:
            del _cache[next(iter(_cache))]


def vectors_for(nodes: Iterable) -> Dict[str, np.ndarray]:
    """Vectors for the nodes, from cache where the text has not changed."""
    global _provider_failed_at
    import time

    nodes = list(nodes)
    out: Dict[str, np.ndarray] = {}
    missing: List[Tuple[str, str, str]] = []
    with _lock:
        for n in nodes:
            t = node_text(n)
            d = _digest(t)
            hit = _cache.get(n.id)
            if hit and hit[0] == d:
                _cache_touch(n.id)
                out[n.id] = hit[1]
            else:
                missing.append((n.id, t, d))
    if missing:
        texts = [t for _, t, _ in missing]
        vecs: List[np.ndarray]
        if provider_name() != "local" and time.time() - _provider_failed_at > 30:
            try:
                vecs = []
                for i in range(0, len(texts), 64):
                    vecs += _openai.embed(texts[i : i + 64])
            except Exception:
                _provider_failed_at = time.time()
                vecs = [local_vector(t) for t in texts]  # degrade to the local vectors rather than fail a retrieval
        else:
            vecs = [local_vector(t) for t in texts]
        with _lock:
            for (nid, _, d), v in zip(missing, vecs, strict=True):
                # Never cache a fallback vector under the neural provider's digest.
                if provider_name() == "local" or v.shape[0] != DIM or time.time() - _provider_failed_at > 30:
                    _cache_put(nid, (d, v))
                out[nid] = v
    return out


def digest_for(n) -> str:
    return _digest(node_text(n))


def peek(n) -> Optional[np.ndarray]:
    """The cached vector for a node if it is current, without computing anything."""
    with _lock:
        hit = _cache.get(n.id)
    return hit[1] if hit and hit[0] == digest_for(n) else None


def seed_cache(node_id: str, digest: str, vec: Sequence[float]) -> None:
    """Load a vector persisted in FalkorDB (already checked against the node's current digest) so a restart does not recompute it."""
    with _lock:
        _cache_put(node_id, (digest, np.asarray(vec, dtype=np.float32)))


def query_vector(q: str) -> np.ndarray:
    global _provider_failed_at
    import time

    if provider_name() != "local" and time.time() - _provider_failed_at > 30:
        try:
            return _openai.embed([q])[0]
        except Exception:
            _provider_failed_at = time.time()
    return local_vector(q)


def cosine_scores(query: str, nodes: Sequence) -> Dict[str, float]:
    """Cosine similarity of the query to each node, 0..1 (negatives clipped). Vectors of a different size are skipped."""
    if not nodes:
        return {}
    qv = query_vector(query)
    vecs = vectors_for(nodes)
    valid_nodes = [n for n in nodes if n.id in vecs and vecs[n.id].shape == qv.shape]
    if not valid_nodes:
        return {}
    if len(valid_nodes) > 16:
        mat = np.stack([vecs[n.id] for n in valid_nodes])
        dots = np.nan_to_num(np.clip(np.dot(mat, qv), 0.0, 1.0), nan=0.0)
        return {n.id: float(d) for n, d in zip(valid_nodes, dots, strict=True)}
    return {n.id: float(np.nan_to_num(min(1.0, max(0.0, float(np.dot(qv, vecs[n.id])))), nan=0.0)) for n in valid_nodes}


def as_list(vec: np.ndarray) -> List[float]:
    return [round(float(x), 5) for x in vec]


def forget(node_ids: Iterable[str]) -> None:
    with _lock:
        for i in node_ids:
            _cache.pop(i, None)
