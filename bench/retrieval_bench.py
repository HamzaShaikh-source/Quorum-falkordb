"""
Retrieval benchmark: does the right fact come back, for direct questions, paraphrases and unanswerable ones?

A synthetic memory of 14 topics x 24 entities (336 facts). Every fact is unique through its entity (a country), so each
question has exactly one right answer. Questions come in four kinds:

  direct       reuses the fact's own words ("What is the refund window in Chile?")
  para_known   different words for the topic, drawn from the built-in concept lexicon ("money back")
  para_unknown different words the lexicon does not know ("cash back"). This is the honest limit of the local vectors.
  none         questions the memory cannot answer (the system should return nothing)

Honest caveat: the paraphrases were written by the same people who wrote the lexicon, so para_known is optimistic. A
neural provider (QUORUM_EMBED_PROVIDER=openai) is the way to cover para_unknown.

  python bench/retrieval_bench.py            writes bench/retrieval_bench.json
"""
import json
import os
import random
import sys
import time
from pathlib import Path

os.environ.setdefault("QUORUM_PERSISTENCE", "memory")
ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

from app.graph.queries import get_graph_store  # noqa: E402
from app.models.common import NodeKind, Status  # noqa: E402
from app.models.graph import GraphNode  # noqa: E402
from app.services import context_pack as cp  # noqa: E402
from app.services.context_pack import estimate_tokens  # noqa: E402

PLACES = "Chile Norway Kenya Japan Brazil Poland Egypt Canada Vietnam Portugal Morocco Ireland Thailand Argentina Finland Peru Greece Nigeria Mexico Austria Croatia Ghana Iceland Uruguay".split()

# topic, fact template, direct question, paraphrase (lexicon words), paraphrase (words the lexicon lacks)
TOPICS = [
    ("refund", "Customers in {p} can request a refund within {n} days of delivery.", "What is the refund window in {p}?",
     "How long do shoppers in {p} have to get their money back?", "Up to when may people in {p} ask for cash back on a purchase?"),
    ("shipping", "Standard shipping to {p} takes {n} business days.", "How many days does shipping to {p} take?",
     "How long does delivery to {p} usually take?", "What is the transit time for parcels heading to {p}?"),
    ("password", "Staff in the {p} office must rotate passwords every {n} days.", "How often must the {p} office rotate passwords?",
     "How frequently do workers in {p} have to change their credentials?", "At what cadence should the {p} team refresh their login phrases?"),
    ("leave", "Employees in {p} receive {n} days of annual leave.", "How many days of annual leave in {p}?",
     "How much vacation do staff in {p} get?", "How many paid days away from work does a worker in {p} get?"),
    ("escalation", "A severe outage in {p} must be escalated to the on-call manager within {n} minutes.", "Within how many minutes must a severe outage in {p} be escalated?",
     "How quickly should a critical incident in {p} be handed off to the manager?", "What is the allowed wait before raising a major breakdown in {p} to the duty lead?"),
    ("backup", "Database snapshots for {p} are kept for {n} days.", "How long are database snapshots for {p} kept?",
     "For how many days do we retain backup copies of the {p} datastore?", "What is the shelf life of saved images of the {p} tables?"),
    ("vat", "Orders shipped to {p} include {n} percent VAT.", "What VAT percentage applies to orders for {p}?",
     "What tax rate is added to purchases sent to {p}?", "Which sales levy percentage is on baskets going to {p}?"),
    ("cancel", "Cancelling a contract in {p} after {n} days incurs a fee.", "After how many days does cancelling a contract in {p} cost a fee?",
     "When does terminating an agreement in {p} start to carry a charge?", "From which day does walking away from a deal in {p} become costly?"),
    ("discount", "Annual plans bought in {p} get a {n} percent discount.", "What discount do annual plans in {p} get?",
     "What rebate applies to yearly subscriptions bought in {p}?", "How much is knocked off twelve month plans in {p}?"),
    ("support", "Support tickets from {p} are answered within {n} hours.", "Within how many hours are tickets from {p} answered?",
     "How fast does the helpdesk respond to requests from {p}?", "What is the turnaround before someone replies to queries from {p}?"),
    ("warranty", "Hardware sold in {p} carries a warranty of {n} months.", "How many months of warranty does hardware in {p} have?",
     "How long is the guarantee on devices sold in {p}?", "For how many months are gadgets in {p} protected against faults?"),
    ("payment", "Vendor payments in {p} are settled within {n} days.", "Within how many days are vendor payments in {p} settled?",
     "How soon do we pay suppliers in {p}?", "What is the lead time before money reaches our merchants in {p}?"),
    ("audit", "The compliance audit in {p} happens every {n} months.", "How often is the compliance audit in {p}?",
     "How frequently is the regulatory review carried out in {p}?", "At what interval does the lawful-conformity inspection take place in {p}?"),
    ("onboarding", "New hires in {p} complete onboarding in {n} days.", "How many days does onboarding take for new hires in {p}?",
     "How long does it take to induct recruits in {p}?", "How many days until a newcomer in {p} is fully set up?"),
]

NONE_QS = [
    "What is the weather forecast for Mars colony Alpha?",
    "Who won the 1998 football world cup?",
    "How do I bake sourdough bread?",
    "What is the airspeed velocity of an unladen swallow?",
    "Summarise the plot of Hamlet.",
    "Which programming language has the most keywords?",
    "How tall is Mount Kilimanjaro?",
    "What colour is the office cat?",
    "What is the capital of Atlantis?",
    "How many moons does Jupiter have?",
]


def build(seed: int = 7):
    rng = random.Random(seed)
    store = get_graph_store("bench_retrieval")
    store.nodes.clear()
    facts = []
    t0 = 1_700_000_000_000
    for ti, (topic, tmpl, *_rest) in enumerate(TOPICS):
        for pi, place in enumerate(PLACES):
            n = rng.randint(3, 90)
            text = tmpl.format(p=place, n=n)
            node = GraphNode(id=f"{topic}_{place.lower()}", kind=NodeKind.FACT, label=text[:60], status=Status.HEALTHY, stale=False,
                             key=f"{topic}_{place.lower()}", value=str(n), text=text, valid_from=t0 + ti * 100 + pi)
            store.add_node(node)
            facts.append((topic, place, node.id))
    return store, facts, rng


def questions(facts, rng, per_kind=60):
    by = {t[0]: t for t in TOPICS}
    pool = rng.sample(facts, per_kind)
    qs = []
    for topic, place, fid in pool:
        _, _t, direct, known, unknown = by[topic]
        qs.append(("direct", direct.format(p=place), fid))
        qs.append(("para_known", known.format(p=place), fid))
        qs.append(("para_unknown", unknown.format(p=place), fid))
    for q in NONE_QS:
        qs.append(("none", q, None))
    return qs


def run(mode: str, store, qs, k_hit=(1, 3, 5)):
    nodes = [n for n in store.nodes.values()]
    idf = cp.idf_weights(nodes)
    rows = {}
    t0 = time.time()
    for kind, q, truth in qs:
        if mode == "keyword":
            qw = set(cp._words(q))
            ranked = sorted(((n, cp._relevance(n, qw, idf)) for n in nodes), key=lambda x: -x[1])
            ranked = [(n, s) for n, s in ranked if s > 0]
        else:
            ranked = [(n, s) for n, s, _ in cp.rank(nodes, q, idf) if s > 0]
        r = rows.setdefault(kind, {"n": 0, "hit": {k: 0 for k in k_hit}, "rr": 0.0, "returned_any": 0})
        r["n"] += 1
        ids = [n.id for n, _ in ranked]
        if ids:
            r["returned_any"] += 1
        if truth is not None and truth in ids:
            pos = ids.index(truth) + 1
            r["rr"] += 1.0 / pos
            for k in k_hit:
                if pos <= k:
                    r["hit"][k] += 1
    out = {}
    for kind, r in rows.items():
        if kind == "none":
            out[kind] = {"questions": r["n"], "answered_anyway": r["returned_any"], "clean_rate": round(1 - r["returned_any"] / r["n"], 3)}
        else:
            out[kind] = {"questions": r["n"], "recall@1": round(r["hit"][1] / r["n"], 3), "recall@3": round(r["hit"][3] / r["n"], 3),
                         "recall@5": round(r["hit"][5] / r["n"], 3), "mrr": round(r["rr"] / r["n"], 3)}
    out["_ms_per_question"] = round((time.time() - t0) / len(qs) * 1000, 2)
    return out


def tokens(store, qs):
    """What one answered question costs: top-5 as full JSON, versus the compact pack within 400 tokens."""
    nodes = [n for n in store.nodes.values()]
    idf = cp.idf_weights(nodes)
    full = compact = n = 0
    for kind, q, _truth in qs:
        if kind == "none":
            continue
        top = [x for x, s, _ in cp.rank(nodes, q, idf) if s > 0][:5]
        full += estimate_tokens(json.dumps({"context": [{"id": f.id, "key": f.key, "value": f.value, "text": f.text, "quote": f.quote} for f in top]}))
        compact += cp.pack(store, q, budget=400)["tokens"]
        n += 1
    return {"full_json_top5": round(full / n), "compact": round(compact / n), "saved_pct": round((full - compact) / full * 100, 1)}


def main():
    store, facts, rng = build()
    qs = questions(facts, rng)
    from app.services import embeddings

    res = {
        "facts": len(facts),
        "topics": len(TOPICS),
        "questions": len(qs),
        "embedding_provider": embeddings.provider_name(),
        "caveat": "The paraphrases were written together with the built-in vocabulary, so the known-words row is optimistic; the unknown-words row is the limit of the local vectors.",
        "keyword_only": run("keyword", store, qs),
        "hybrid": run("hybrid", store, qs),
        "tokens_per_answer": tokens(store, qs),
    }
    (ROOT / "bench" / "retrieval_bench.json").write_text(json.dumps(res, indent=2), encoding="utf8")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
