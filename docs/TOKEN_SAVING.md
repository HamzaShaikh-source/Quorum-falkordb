# Token saving: what Quorum does, and what was measured

## What an agent gets

Quorum keeps one organized memory and gives an agent the smallest slice that answers its question. All of this runs in the backend (`backend/app/services/context_pack.py`) and is exposed as MCP tools:

| Tool | What it returns | Why it is cheap |
| --- | --- | --- |
| `retrieve_context(query, format="compact", budget_tokens)` | One line per node: `F3|label|text|<S1` with short ids and the sources that prove it | Healthy, in-scope nodes only; ranked by rarity-weighted word coverage; ancestors for provenance; duplicates removed; trimmed to the budget |
| `get_map()` | Areas, hubs and how to ask, a few hundred tokens | Orientation without loading the graph |
| `get_node(id)` | One node with its parents and children, by short id (`F3`) or full id | A few lines instead of a document |
| `find_path(from_id, to_id)` | The shortest causal chain | Provenance without the subgraph |
| `delta()` | What stopped being true since this agent's last read, plus new matches | Refresh without re-reading |

`retrieve_context` still returns the full JSON by default (`format="full"`), so existing callers are unchanged. Every response carries `tokens` (its size) and `tokens_baseline` (what a typical top-8 retriever returns for the same call: the best keyword matches as full JSON, healthy or not). Both come from one estimator, about 4.5 characters per token for words and one per symbol, within roughly 15% of a real tokenizer, with identical results in Python and TypeScript (a shared fixture test enforces it).

Quorum also never hands an agent a quarantined, superseded or stale fact, and never one outside the agent's team scope. That is correctness, not saving, but a plain retriever cannot do it at all.

Every call is recorded (who, tool, tokens, baseline). `GET /api/token-stats` sums the read calls; the header counter, the Context page and each member's "tokens saved" come from it. Per call the saving can be negative, and it is not clipped.

## What was measured with a real AI

`frontend/scripts/token_bench.mjs` runs fresh Claude Code sessions against the live stack. The same five questions are answered from the same 160-fact memory, three ways:

- **plain**: `retrieve_context(query, k=8)` in the full format, what a typical top-8 memory returns.
- **compact**: `retrieve_context(query, format="compact", budget_tokens=400)`.
- **mapped**: `get_map` once, then the compact calls.

Two memories: short facts (one or two sentences) and real-sized documents (about 120 words each). Two runs per arm, medians. Every arm answered 5/5 correctly. Raw results are in `bench/token_bench_short.json` and `bench/token_bench_long.json`.

| Memory | Tool-result tokens per run (plain → compact) | Whole session, input tokens | Session cost |
| --- | --- | --- | --- |
| Short facts | 4,618 → 2,353 (49% fewer) | 160,809 → 159,362 (1% fewer) | $0.193 → $0.187 (3% less) |
| Real-sized documents | 10,811 → 2,347 (78% fewer) | 166,875 → 159,248 (5% fewer) | $0.229 → $0.186 (19% less) |

How to read it:

- **Tool results shrink a lot**, and the more text each fact carries the more they shrink, because compact lines are bounded by the budget while full results grow with the documents.
- **Whole sessions move less**, because Claude Code sends its own fixed prompt (about 37,000 tokens even for the word "ok") on every turn, and Quorum cannot change that. The saving shows in cost most, because tool results stay in the context for the turns that follow.
- **Turns matter more than lines.** Adding `get_map` added a turn and made the five-question session use 33% more input tokens (28% on the long memory). A turn costs far more than the map saves in a short session. The shipped agent rules therefore tell agents to call `get_map` only when they do not know where to look.
- Savings grow with the size of the memory and the length of the session. A five-question session on a 160-fact memory is a small case.

Reproduce: `cd frontend && node scripts/token_bench.mjs http://127.0.0.1:8000 2 long` (costs a few dollars of Claude usage; use `short` for the other memory).

## Does the right fact come back?

Saving tokens is worthless if the right fact is missing. Ranking is keyword coverage (weighted by rarity) **plus vector similarity** (see [`MCP.md`](MCP.md#ranking-keywords-plus-vectors)). Two benchmarks check it.

**Offline, 190 questions** (`python bench/retrieval_bench.py`, results in `bench/retrieval_bench.json`). A synthetic memory of 14 topics × 24 countries = 336 facts, each unique through its country. Percent of questions where the right fact is the top result (top 3 in brackets):

| How the question is worded | Keywords only | Keywords + vectors |
| --- | --- | --- |
| Same words as the fact | 100% (100%) | 100% (100%) |
| Different words the built-in vocabulary knows ("money back") | 38% (52%) | **87% (100%)** |
| Different words it does not know ("cash back") | 25% (38%) | 35% (40%) |
| Off-topic questions that correctly return nothing | 9 of 10 | 9 of 10 |

The miss on the off-topic row is a real shared word ("office"), not a guess. The paraphrases were written together with the vocabulary, so the middle row is optimistic, and the third row is the honest limit of the built-in vectors. A neural embedding endpoint is the way past it.

**Real AIs, same memory** (`python bench/ai_retrieval_bench.py`, results in `bench/ai_retrieval_bench.json`). Two Claude models (Sonnet and Haiku, through the `claude` CLI) each answered 44 questions (24 reworded with known vocabulary, 12 with unknown, 8 off-topic) from the 336-fact memory using only Quorum's MCP tools, on two hubs: one ranking with keywords only, one with keywords plus vectors. A correct answer has the exact number from the fact; an off-topic question must come back `NOT FOUND`.

| Model | Ranking | Different words, known (24) | Different words, unknown (12) | Off-topic (8) | Total (44) |
| --- | --- | --- | --- | --- | --- |
| Sonnet | keywords only | 19 | 6 | 8 | 33 |
| Sonnet | keywords + vectors | **24** | **7** | 8 | **39** |
| Haiku | keywords only | 17 | 6 | 8 | 31 |
| Haiku | keywords + vectors | **24** | **8** | 8 | **40** |

Read it as direction, not precision: 44 questions, one run per model per hub (a first 22-question run, on a different seed, gave the same picture: 19 and 19 with vectors against 11 and 9 without). Vectors rescue the paraphrases the vocabulary knows (24/24), help a little with the ones it does not (the model also rephrases its own query), and never invent an answer to an off-topic question. The first attempt at this benchmark was invalid for a good reason: the hub's rate limit throttled the bulk seeding and only half the facts were stored. The script now retries when throttled and refuses to run unless every fact is stored.

Cost and turns were the same in both arms (3 turns, about the same input tokens, about $1.50 for Haiku and $6.90 for Sonnet per 44-question arm); ranking does not change the token bill, it changes whether the answer is found.

## At scale, and in a long session

`python bench/scale_bench.py` (results in `bench/scale_bench.json`). In-process on one machine, no network.

| Facts in memory | First answer (builds vectors) | Warm answer, median | Warm, p95 |
| --- | --- | --- | --- |
| 1,000 | 342 ms | 15 ms | 21 ms |
| 5,000 | 1.2 s | 76 ms | 94 ms |
| 20,000 | 4.5 s | 381 ms | 425 ms |

Ranking looks at every healthy, visible fact, so the cost grows in a straight line (about 19 µs per fact per question). Tokenised word sets are cached, which took a 5,000-fact answer from about 205 ms to about 76 ms. In FalkorDB mode the vectors are computed when a fact is written and reloaded after a restart, so the "first answer" cost is paid once, at write time. Past tens of thousands of facts the next step is to let FalkorDB's vector index choose the candidates (`/api/context/semantic-check` already queries it); that is not the default path yet.

A 60-turn session on a 5,000-fact memory, **modelled** from the measured size of every answer. Whole-session input is the fixed Claude Code prompt (37,000 tokens a turn, measured earlier) plus every tool result still in the context:

| Strategy | Tool-result tokens | Whole-session input | Saved (results / whole session) |
| --- | --- | --- | --- |
| Top-8 retriever, full JSON | 29,654 | 3,123,241 | n/a |
| Quorum compact lines | 16,783 | 2,726,966 | 43% / 12.7% |
| Compact lines + `delta` on follow-ups | 15,960 | 2,702,164 | 46% / 13.5% |

That is the honest shape of the saving: about half of the tool-result tokens, and a little over a tenth of a long session, because the agent's own fixed prompt dominates. It is a model, not a real-AI run; the real-AI numbers are the ones above.
