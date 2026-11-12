# Quorum API Contract

**Contract version: 0.1.0 (DRAFT, not yet frozen)**
Owners: Janani (graph and query-log fields), Aaditya (endpoints and events), Hamza (consumer: frontend).
Companion file: `contracts/events.schema.json` (JSON Schema for the SSE envelope and events).

## 0. How this file works
- Once all three owners sign off (Section 11), this file and `events.schema.json` are authoritative for every field name, enum and shape. If code and contract disagree, the code is wrong.
- After the freeze, any change needs all three owners to agree, a bump of `contract_version`, and a line in the change log (Section 12). Adding an optional field is a minor bump; renaming, removing or changing the meaning of a field is a major bump.
- Aaditya serves FastAPI response models that match this file and exposes them at `/openapi.json`. Hamza generates TypeScript types from that and validates every response and event at runtime.

## 1. Conventions
- Base path `/api`, same origin as the frontend.
- Auth: `Authorization: Bearer <token>`. The token determines the **tenant** and the **role** (`agent | auditor | human`). The tenant is never taken from a request body or URL.
- Ids are strings (`fact_...`, `conc_...`, `act_...`, `hand_...`, `src_...`).
- Times are `Ms` (integer epoch milliseconds, FalkorDB `timestamp()`).
- Every response carries an `X-Request-Id` header. Query-log entries and SSE events caused by a request carry the same `request_id`.
- Unknown extra fields must be tolerated by consumers; unknown enum values must render neutrally.
- Errors: HTTP status plus body `{ "error": { "code": "...", "message": "...", "details": {} } }`.

| Code | Typical status | Meaning |
|---|---|---|
| `UNAUTHENTICATED` | 401 | missing or invalid token |
| `FORBIDDEN_TENANT` | 403 | token's tenant may not read that graph |
| `FORBIDDEN_ROLE` | 403 | role may not do this (for example an agent token completing a REVIEW) |
| `NOT_FOUND` | 404 | unknown id |
| `CONFLICT` | 409 | state does not allow it (already claimed, already executed) |
| `GUARD_REFUSED` | 409 | execute guard returned zero rows (an ancestor is not healthy) |
| `VALIDATION` | 422 | bad request body or parameters |
| `FALKORDB_UNAVAILABLE` | 503 | FalkorDB is not running or not set up. Every API and MCP call is refused; `error.details` has `reason`, `url` and `steps`. Sent with `Retry-After: 3`. |
| `INTERNAL` | 500 | unexpected failure |

## 2. Core types

```ts
type Ms = number;
type Status = 'HEALTHY' | 'SUPERSEDED' | 'QUARANTINED';
type NodeKind = 'source' | 'fact' | 'conclusion' | 'action';
type EdgeType = 'LEADS_TO' | 'SUPPORTS' | 'CONTRADICTS' | 'SUPERSEDES' | 'FROM_SOURCE';
type Tier = 'root' | 'established' | 'new';

interface GraphNode {
  id: string;
  kind: NodeKind;
  is_decision?: boolean;              // conclusion that is also a Decision
  label: string;                      // server-made, <= 60 chars
  text?: string; key?: string; value?: string;
  status: Status;                     // never null
  stale: boolean;
  valid_from?: Ms; valid_to?: Ms | null;
  quarantined_at?: Ms | null; released_at?: Ms | null;
  quarantine_root?: string | null;
  depth?: number | null;              // hop depth from the root of the latest cascade
  source_ref?: string; quote?: string; origin?: string;
  asserted_by?: { id: string; name: string; tier: Tier };
  source?: { kind: 'DOC' | 'TICKET' | 'CHAT' | 'WEBHOOK'; title: string; url: string; version: string };
  owner_team?: string;
  action?: {
    type: string;
    reversibility: 'REVERSIBLE' | 'COMPENSABLE' | 'IRREVERSIBLE';
    executed: boolean; confirmed: boolean;
    gate: 'PASSED' | 'BLOCKED';
    procedure_id?: string | null;
    compensation?: {
      status: 'PENDING_COMPENSATION' | 'DISPATCHING' | 'COMPENSATED' | 'FAILED';
      attempts: number;
    } | null;
  };
}

interface GraphEdge { id: string; source: string; target: string; type: EdgeType }

interface GraphResponse {
  contract_version: string;
  tenant: string;
  epoch: number;
  seq: number;                        // last SSE seq reflected in this snapshot
  as_of: Ms | null;                   // null = now
  generated_at: Ms;
  truncated: boolean;
  nodes: GraphNode[];                 // ALL statuses, not just healthy
  edges: GraphEdge[];
}

interface Handoff {
  id: string;
  kind: 'TASK' | 'REVIEW' | 'ESCALATION';
  task: string;
  status: 'PENDING' | 'IN_PROGRESS' | 'COMPLETED' | 'TIMED_OUT';
  assigned_to?: string; claimed_by?: string; claimed_at?: Ms; lease_until?: Ms;
  context_root_id?: string;           // the CONTEXT_ROOT anchor (fact or action id)
}
```

### Action display state (consumers derive this with one pure function)
| Fields | Shown as |
|---|---|
| HEALTHY, not executed, gate PASSED | Staged |
| HEALTHY, not executed, gate BLOCKED | Held for review |
| executed, not confirmed | Running |
| executed, confirmed, HEALTHY | Ran |
| QUARANTINED, not executed | Cancelled |
| QUARANTINED, executed, compensation PENDING_COMPENSATION or DISPATCHING | Rolling back |
| QUARANTINED, executed, compensation COMPENSATED | Rolled back |
| QUARANTINED, executed, compensation FAILED | Rollback failed |
| QUARANTINED, executed, IRREVERSIBLE | Needs a person |

## 3. REST endpoints

`GET /api/health` needs no token. It answers 200 `{status:"ok", falkordb:{up,url,latency_ms,version,error}, steps[], warnings[]}` when FalkorDB is ready and 503 with `status:"blocked"` and the fix steps when it is not. The console polls it and shows its setup screen instead of the product.

All require a valid token. "Roles" lists who may call.

| Method and path | Roles | Request | Response |
|---|---|---|---|
| `GET /meta` | any | none | `{ contract_version, mode: 'replay'\|'live', tenant, role, epoch, seq, replay_available }` |
| `GET /graph` | any | `?as_of=<Ms>` optional | `GraphResponse` |
| `GET /node/{id}` | any | none | `{ node: GraphNode, path: GraphNode[], descendant_count: number, status_history: { ts: Ms; status: Status; reason: string }[] }` (`path` ordered from root sources to the node) |
| `POST /scenario/{step}` | auditor, human | step is `inject_poison \| run_agents \| audit \| supersede \| restore \| reset` | `{ run_id: string, accepted: boolean }` (progress over SSE) |
| `GET /blast/{id}/preview` | any | none | `{ root_id, node_ids: string[], facts: number, conclusions: number, actions: { staged: number, executed_reversible: number, executed_compensable: number, executed_irreversible: number } }` (read-only dry run) |
| `GET /blast/{id}/report` | any | none | `{ root_id, quarantined_at: Ms, ms_to_containment: number, facts: number, conclusions: number, actions_cancelled: number, actions_compensated: number, actions_escalated: number, node_ids_by_depth: string[][] }` |
| `GET /handoffs` | any | none | `Handoff[]` |
| `POST /handoffs/{id}/claim` | agent (TASK only), human | none | `Handoff` (409 `CONFLICT` if lost the race) |
| `POST /handoffs/{id}/review` | **human only** | `{ decision: 'approve'\|'reject', note?: string }` | `Handoff` (403 `FORBIDDEN_ROLE` for other roles) |
| `GET /actions` | any | none | `GraphNode[]` (actions only, newest first) |
| `GET /agents` | any | none | `{ id, name, role, tier: Tier, last_active?: Ms }[]` |
| `GET /agents/{id}/resume` | any | none | `{ session_id, summary, memories: { fact_id, text, source_ref, quote }[] }` |
| `GET /procedures` | any | none | `{ id, task_type, steps: string[], uses, successes, failures, rank_score, seeded: boolean }[]` |
| `GET /prompt-diff` | any | `?agent=<id>` | `{ epoch_before, epoch_after, before: { node_id, text, status }[], after: { node_id, text, status }[], removed_ids: string[] }` |
| `POST /brain/ask` | any | `{ question: string }` | `{ refused: boolean, reason?: string, answer?: string, citations: { source_title, url, version, quote, owner_team, valid_from: Ms, stale: boolean }[] }` |
| `GET /who-knows` | any | `?topic=<name>` | `{ person, team, evidence_count: number, source_ids: string[] }[]` |
| `GET /decisions/{id}/trace` | any | none | `{ claim, quote, source_title, url, version, owner }[]` |
| `GET /invariants` | any | none | `{ ok: boolean, checked_at: Ms, checks: { name: string, ok: boolean, value: number }[] }` |
| `GET /cypher-log` | any | `?request_id=&limit=` | `{ ts: Ms, request_id?: string, name: string, cypher: string, ms: number, rows: number }[]` (parameters redacted) |
| `GET /bench/results` | any | none | see below |
| `GET /tenants/denied-demo` | any | none | `{ tenant, attempted_graph, result: 'DENIED'\|'ALLOWED', detail, visible_graph_names: string[] }` |

`/bench/results` shape (written by `make proof` as `bench/results.json`, served as-is):
```ts
{
  generated_at: Ms;
  params: Record<string, string | number>;
  concurrency: { agents: number; ops_per_s: number; p50_ms: number; p95_ms: number; p99_ms: number };
  handoff_race: { handoffs: number; claimers: number; single_winner_rate: number };
  invalidation_under_load: { violations: number };
  poison: { depth: number; systems: { name: string; leaked_rate: number; wrongly_quarantined: number; ms_to_containment: number }[] }[];
}
```

## 4. Server-sent events

`GET /api/events` (the client uses a fetch-based SSE client so the `Authorization` header works; honors `Last-Event-ID`).

Envelope for every event:
```json
{ "seq": 412, "ts": 1792051200000, "epoch": 14, "type": "status_changed", "request_id": "req_91ac",
  "data": { "id": "fact_90d", "status": "QUARANTINED", "depth": 0, "root_id": "fact_90d" } }
```
- `seq` is monotonic per tenant; the SSE `id:` field equals `seq`.
- If a client reconnects with a `Last-Event-ID` the server can no longer replay, it sends `resync_required`, and the client refetches `/graph`.
- A `heartbeat` event is sent at least every 15 seconds.

| `type` | `data` |
|---|---|
| `node_added` | `{ node: GraphNode }` |
| `edge_added` | `{ edge: GraphEdge }` |
| `status_changed` | `{ id, status: Status, stale?: boolean, depth?: number, root_id?: string }` |
| `epoch_changed` | `{ epoch: number, reason: 'quarantine' \| 'restore' \| 'supersede' }` |
| `gate_blocked` | `{ action_id, handoff_id, reason: string }` |
| `action_executed` | `{ action_id, confirmed: boolean }` |
| `compensation_updated` | `{ action_id, status: 'PENDING_COMPENSATION' \| 'DISPATCHING' \| 'COMPENSATED' \| 'FAILED', attempts: number }` |
| `handoff_claimed` | `{ handoff_id, winner: string, losers: string[], lease_until: Ms }` |
| `handoff_updated` | `{ handoff: Handoff }` |
| `invariant_status` | `{ ok: boolean, violations: number }` |
| `scenario_progress` | `{ run_id, step: string, state: 'started' \| 'finished' \| 'failed', detail?: string }` |
| `resync_required` | `{}` |
| `heartbeat` | `{}` |

Ordering rule for cascades: when a quarantine runs, `status_changed` events carry `depth` (0 for the root) so the UI can animate the wave; `epoch_changed` is sent after the last `status_changed` of that cascade; compensation events follow.

## 5. Examples

Minimal `GET /graph` response:
```json
{
  "contract_version": "0.1.0", "tenant": "acme", "epoch": 14, "seq": 412, "as_of": null,
  "generated_at": 1792051200000, "truncated": false,
  "nodes": [
    { "id": "src_policy_v3", "kind": "source", "label": "Refund policy v3", "status": "HEALTHY", "stale": false,
      "source": { "kind": "DOC", "title": "Refund policy", "url": "https://acme.example/policy", "version": "3" } },
    { "id": "fact_30d", "kind": "fact", "label": "Refund window is 30 days", "status": "HEALTHY", "stale": false,
      "key": "refund_window_days", "value": "30", "quote": "Refunds are allowed within 30 days.",
      "asserted_by": { "id": "agent_root", "name": "Policy owner", "tier": "root" } },
    { "id": "fact_90d", "kind": "fact", "label": "Refund window is 90 days", "status": "QUARANTINED", "stale": false,
      "key": "refund_window_days", "value": "90", "quarantined_at": 1792051190000, "quarantine_root": "fact_90d", "depth": 0,
      "origin": "webhook:partner-feed" },
    { "id": "act_refund_2", "kind": "action", "label": "Refund order 1042", "status": "QUARANTINED", "stale": false, "depth": 2,
      "action": { "type": "refund", "reversibility": "COMPENSABLE", "executed": true, "confirmed": true, "gate": "PASSED",
                  "compensation": { "status": "COMPENSATED", "attempts": 1 } } }
  ],
  "edges": [
    { "id": "e1", "source": "src_policy_v3", "target": "fact_30d", "type": "FROM_SOURCE" },
    { "id": "e2", "source": "fact_90d", "target": "act_refund_2", "type": "LEADS_TO" }
  ]
}
```

Error body:
```json
{ "error": { "code": "FORBIDDEN_ROLE", "message": "Only a human may complete a REVIEW handoff.", "details": { "handoff_id": "hand_7" } } }
```

## 6. Semantics everyone must agree on
- **as-of:** `GET /graph?as_of=T` returns what was believed at T: facts with `valid_from <= T` and (`valid_to` null or `> T`), excluding nodes whose latest quarantine window covers T (`quarantined_at <= T` and (`released_at` null or `> T`)). Only the latest quarantine window per node is known (documented limitation).
- **`depth`:** hop distance from `quarantine_root` in the most recent cascade; null for nodes never in a cascade.
- **Statuses in `/graph`:** all three statuses are returned so the UI can show ghosts and quarantined nodes.
- **Seeded values:** anything pre-loaded rather than learned in the run (for example procedure scores) carries `seeded: true`.
- **Replay vs live:** `mode` says whether agents are playing cached LLM outputs or calling the model; nothing else in the API changes.

## 7. Open items to settle at the freeze
1. How the UI gets its tokens in the demo (default: static demo tokens in runtime config, never committed).
2. Whether `depth` is on `/graph` nodes (latest cascade) and on events, or only on events.
3. Node count limits and `truncated` behavior; paging if ever needed.
4. What `scenario/reset` does to seeded data and how long it takes.
5. Whether `scenario` steps are idempotent if clicked twice.
6. Optional extras if time allows: `POST /facts/{id}/quarantine` and `POST /facts/{id}/restore` (auditor or human) for manual control from the UI.

## 8. Role matrix (summary)
- `agent`: read endpoints; claim TASK handoffs; MCP tools.
- `auditor`: everything an agent can do, plus scenario controls and quarantine/restore.
- `human`: everything an auditor can do, plus REVIEW and ESCALATION decisions.
- Tenant mismatch on any endpoint: 403 `FORBIDDEN_TENANT` (and the denied-read demo shows it).

## 9. Versioning
- `GET /meta.contract_version` is the single source. The frontend refuses to start on a different major version and shows a banner on a different minor version.

## 10. Testing the contract
- Janani: query-log entries, `/graph` node fields, cascade output and `bench/results.json` validate against these shapes in `make proof`.
- Aaditya: pydantic models generate `/openapi.json`; SSE events validate against `events.schema.json` before sending.
- Hamza: `npm run contract:check` diffs the live `/openapi.json` against the checked-in snapshot and validates all fixtures.

## 11. Sign-off (change to "agreed" with date when frozen)
| Owner | Status | Date |
|---|---|---|
| Janani | pending | |
| Aaditya | pending | |
| Hamza | pending | |

## 12. Change log
| Version | Date | Change |
|---|---|---|
| 0.1.0 | draft | Initial proposal |
- Added workspace, MCP activity, token-stats and context-preview endpoints, `mcp_call` and `workspace_changed` events, four MCP tools, and `scope_team` on nodes; see `docs/CONTRACT_PROPOSALS.md` for the list and rules.
- Added `FALKORDB_UNAVAILABLE` (503) and `GET /api/health`. `GET /graph` `seq` is now the event stream's sequence number. Optional fields may be `null`. Unknown SSE event types are tolerated and keep `seq` contiguous. `GET /bench/results` returns the last recorded `bench/results.json` run, with `engine`, and baselines' `ms_to_containment` may be `null` (not measured).
