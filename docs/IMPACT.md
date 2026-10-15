# Who Quorum is for, and what it changes

## The story in one minute

A support agent reads a partner feed that says the refund window is **90 days**. The real policy is 30. In a normal setup that number now lives in a vector store and in three conclusions the agents derived from it, and one refund has already gone out. Nobody can say what else it touched.

In Quorum the wrong fact is a node in a causal graph. One call quarantines it, and everything built on it goes with it, in under a millisecond (measured: 0.4 ms, `bench/results.json`). No later retrieval can return any of it: a retriever that asks gets the 30-day fact, not the poisoned one. The refund that already left is flagged for a person with a drafted correction notice. The Activity page reads, in plain words: *"auditor quarantined fact_90d"*, *"agent_scout looked up refund window"*, with who and when.

That scenario is run for real in `frontend/scripts/claude_e2e.mjs`: a real Claude plants the wrong fact, an auditor reverts it, and a fresh Claude, asked the same question, answers 30 days and does not repeat the 90.

## Who it helps

| If you are | What you get | What it replaces |
| --- | --- | --- |
| **A team running several AI agents or assistants** (support, finance ops, research) | One shared memory every AI reads and writes. A wrong fact is reverted once, for everyone, instead of being found and fixed in each agent's notes. | Pasting context between chats; each tool keeping its own notes. |
| **A platform or security owner** | Per-person tokens that expire, team-private facts, a rate limit, an audit log of every tool call and every console action, FalkorDB behind a password, TLS. | Shared API keys; no record of what an agent read or changed. |
| **A compliance or operations lead** | The Activity page: who did what, when, what it cost, what needed a person, exported as a spreadsheet. No graph reading required. | Asking engineers to dig through logs. |
| **A developer paying for tokens** | About half the tool-result tokens per lookup, and answers that cite fact ids instead of repeating text. Measured with real Claude sessions: 49-78% fewer tool-result tokens, up to 19% lower session cost on real-sized documents. | Pasting top-k chunks of everything into every prompt. |

## What the numbers say, and what they do not

- **Correctness of containment**: 0.0% poison leakage at depths 1 to 3 against 100% for a flat store with a TTL and for "delete the root fact only" (`bench/results.json`, run on FalkorDB). Single winner on 200 contended handoffs; zero illegal executions against quarantine races (`backend/tests/test_stress_and_concurrency.py`).
- **Retrieval quality**: Sonnet and Haiku, same 336-fact memory, same questions: reworded questions the vocabulary knows 24/24 with vectors against 19/24 and 17/24 with keywords alone (39 and 40 of 44 overall, against 33 and 31) (see [`TOKEN_SAVING.md`](TOKEN_SAVING.md#does-the-right-fact-come-back)).
- **Tokens**: tool results shrink about half, a whole session by single digits to low teens of a percent, because the AI client's own prompt dominates. The saving grows with memory size and session length (modelled in `bench/scale_bench.json`: 13.5% over 60 turns on 5,000 facts). We say this plainly because the honest number is the useful one.
- **Not claimed**: that Quorum makes agents correct. It makes a wrong belief *recoverable* and *traceable*, and it shows who held it.

## A rough cost view

Measured on real-sized documents: 10,811 tool-result tokens for five questions with a top-8 retriever against 2,347 with Quorum, so about **1,700 tokens saved per lookup**. For a team of 20 whose AIs each make 200 lookups a day that is about 340,000 tokens per person and 6.8 million a day across the team *in tool results*. Those tokens are also re-read on later turns, but providers discount cached prompt text heavily, which is why the measured whole-session saving is much smaller (5% of input tokens, 19% of cost on that memory). Plug in your own model's price and caching: `tokens saved x price per million / 1,000,000`. The Context page shows the live saved-token count from your own hub. This is arithmetic on measured numbers, not a measurement of your workload.

## What a first hour looks like

1. `docker compose up -d` and open the console. The **Get started** card on the Connect page walks the four steps and ticks them off from real state.
2. Add a person on **Team**, paste the Claude Code (or Cursor, VS Code, Claude Desktop) snippet, ask your AI a question.
3. Let it save something, or install the capture hook so it saves facts on its own.
4. Open **Activity** and read it back in plain words. Try `Inject poisoned webhook` in the console and watch the red wave.
