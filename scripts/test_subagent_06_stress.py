"""
Stress Testing Suite for T03-1: Company Brain Corpus & Multi-Source Graph Ingestion (scripts/seed.py)
Subagent 06 - Quorum Project

Tests:
1. Seed Execution & Corpus Verification:
   - 25 synthetic corpus items (DOC, TICKET, CHAT)
   - 12 People, 4 Teams, 3 Projects, 3 Systems
   - 6 Agents across 3 tiers, 2 Procedures
2. Stress Test Concurrent Re-seeding:
   - Run seed.py concurrently across 3 different tenants (alpha, beta, gamma)
   - Multi-round concurrent re-seeding under load
   - Measure latency percentiles (min, p50, p95, p99, max)
   - Verify zero cross-tenant contamination and perfect data isolation
3. Vector Embeddings Verification:
   - Verify 384-dimensional embeddings on all Facts and Sources
   - Verify L2 normalization (||v|| ~= 1.0 within tolerance)
   - Test FalkorDB native vector index search (CALL db.idx.vector.queryNodes)
4. Edge Schema Integrity Verification:
   - AUTHORS: Person -> Source with role, created_at
   - OWNS: Team -> (Source, System, Project) with level, since
   - RESOLVES: Person -> Source (TICKET) with resolution, resolved_at
   - SUPPORTS: (System->Project, Source->Project, Fact->Decision, Fact->Action) with valid properties
5. Integration with Track 03 Query Functions:
   - who_knows query leaderboard
   - provenance causal trace
"""

import os
import sys
import time
import math
import json
import hashlib
import threading
import statistics
import numpy as np
from typing import Dict, List, Any, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

_current_dir = os.path.dirname(os.path.abspath(__file__))
_root_dir = os.path.abspath(os.path.join(_current_dir, ".."))
if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)

from graph import queries
from scripts.seed import seed_acme_commons, generate_embedding

TENANT_SINGLE = "s06_test_single"
TENANT_ALPHA = "s06_tenant_alpha"
TENANT_BETA = "s06_tenant_beta"
TENANT_GAMMA = "s06_tenant_gamma"


def format_stats(latencies_ms: List[float]) -> Dict[str, float]:
    if not latencies_ms:
        return {}
    s = sorted(latencies_ms)
    n = len(s)
    return {
        "count": n,
        "min_ms": round(s[0], 2),
        "p50_ms": round(s[int(n * 0.50)], 2),
        "p95_ms": round(s[min(n - 1, int(n * 0.95))], 2),
        "p99_ms": round(s[min(n - 1, int(n * 0.99))], 2),
        "max_ms": round(s[-1], 2),
        "avg_ms": round(sum(s) / n, 2),
    }


# =========================================================================
# TEST 1: SEED EXECUTION & COMPLETE CORPUS VERIFICATION
# =========================================================================
def test_1_seed_execution_and_corpus_counts() -> Dict[str, Any]:
    print("\n" + "=" * 80)
    print("TEST 1: SEED.PY EXECUTION & CORPUS ENTITY VERIFICATION")
    print("=" * 80)

    t0 = time.time()
    seed_acme_commons(TENANT_SINGLE)
    seed_ms = (time.time() - t0) * 1000

    g = queries.get_graph(TENANT_SINGLE, "commons")
    errors = []

    # 1. Verify 25 Corpus Items across Sources (DOC, TICKET, CHAT)
    res_sources = g.query("MATCH (s:Source) RETURN s.kind, count(s) ORDER BY s.kind")
    sources_by_kind = {row[0]: row[1] for row in res_sources.result_set}
    total_sources = sum(sources_by_kind.values())

    print(f"-> Sources seeded: Total={total_sources}, Breakdown={sources_by_kind} (seeded in {seed_ms:.1f}ms)")
    if total_sources != 25:
        errors.append(f"Expected exactly 25 Sources, found {total_sources}")
    if sources_by_kind.get("DOC") != 9:
        errors.append(f"Expected 9 DOC sources, found {sources_by_kind.get('DOC')}")
    if sources_by_kind.get("TICKET") != 8:
        errors.append(f"Expected 8 TICKET sources, found {sources_by_kind.get('TICKET')}")
    if sources_by_kind.get("CHAT") != 8:
        errors.append(f"Expected 8 CHAT sources, found {sources_by_kind.get('CHAT')}")

    # 2. Verify 12 People
    res_people = g.query("MATCH (p:Person) RETURN count(p)")
    people_count = res_people.result_set[0][0]
    print(f"-> People seeded: {people_count} (expected 12)")
    if people_count != 12:
        errors.append(f"Expected 12 People, found {people_count}")

    # Verify each Person has MEMBER_OF exactly 1 Team
    res_mem = g.query("MATCH (p:Person)-[:MEMBER_OF]->(t:Team) RETURN count(p)")
    mem_count = res_mem.result_set[0][0]
    if mem_count != 12:
        errors.append(f"Expected 12 MEMBER_OF relationships, found {mem_count}")

    # 3. Verify 4 Teams
    res_teams = g.query("MATCH (t:Team) RETURN count(t)")
    team_count = res_teams.result_set[0][0]
    print(f"-> Teams seeded: {team_count} (expected 4)")
    if team_count != 4:
        errors.append(f"Expected 4 Teams, found {team_count}")

    # 4. Verify 3 Projects
    res_proj = g.query("MATCH (pr:Project) RETURN count(pr), collect(pr.id)")
    proj_count = res_proj.result_set[0][0]
    proj_ids = res_proj.result_set[0][1]
    print(f"-> Projects seeded: {proj_count} (expected 3, ids={proj_ids})")
    if proj_count != 3:
        errors.append(f"Expected 3 Projects, found {proj_count}")
    expected_projects = {"proj_portal", "proj_gateway", "proj_compliance_vault"}
    if set(proj_ids) != expected_projects:
        errors.append(f"Expected project IDs {expected_projects}, got {set(proj_ids)}")

    # 5. Verify 3 Systems
    res_sys = g.query("MATCH (sys:System) RETURN count(sys), collect(sys.id)")
    sys_count = res_sys.result_set[0][0]
    sys_ids = res_sys.result_set[0][1]
    print(f"-> Systems seeded: {sys_count} (expected 3, ids={sys_ids})")
    if sys_count != 3:
        errors.append(f"Expected 3 Systems, found {sys_count}")
    expected_systems = {"sys_zendesk", "sys_stripe", "sys_slack"}
    if set(sys_ids) != expected_systems:
        errors.append(f"Expected system IDs {expected_systems}, got {set(sys_ids)}")

    # 6. Verify 3 Topics
    res_top = g.query("MATCH (top:Topic) RETURN count(top)")
    top_count = res_top.result_set[0][0]
    print(f"-> Topics seeded: {top_count} (expected 3)")
    if top_count != 3:
        errors.append(f"Expected 3 Topics, found {top_count}")

    # 7. Verify 25 Facts connected to Sources
    res_facts = g.query("MATCH (f:Fact)-[:FROM_SOURCE]->(s:Source) RETURN count(f)")
    fact_count = res_facts.result_set[0][0]
    print(f"-> Facts connected to Sources: {fact_count} (expected 25)")
    if fact_count != 25:
        errors.append(f"Expected 25 Facts with FROM_SOURCE edges, found {fact_count}")

    # 8. Verify Agents and Procedures
    res_agents = g.query("MATCH (a:Agent) RETURN count(a)")
    agent_count = res_agents.result_set[0][0]
    res_procs = g.query("MATCH (p:Procedure) RETURN count(p)")
    proc_count = res_procs.result_set[0][0]
    print(f"-> Supporting nodes: Agents={agent_count} (expected 6), Procedures={proc_count} (expected 2)")
    if agent_count != 6:
        errors.append(f"Expected 6 Agents, found {agent_count}")
    if proc_count != 2:
        errors.append(f"Expected 2 Procedures, found {proc_count}")

    passed = len(errors) == 0
    print(f"-> TEST 1 VERDICT: {'PASSED' if passed else 'FAILED'}")
    if errors:
        for err in errors:
            print(f"   [!] {err}")

    return {
        "test": "test_1_seed_execution_and_corpus_counts",
        "passed": passed,
        "seed_duration_ms": seed_ms,
        "counts": {
            "sources_total": total_sources,
            "sources_by_kind": sources_by_kind,
            "people": people_count,
            "teams": team_count,
            "projects": proj_count,
            "systems": sys_count,
            "topics": top_count,
            "facts": fact_count,
            "agents": agent_count,
            "procedures": proc_count,
        },
        "errors": errors,
    }


# =========================================================================
# TEST 2: CONCURRENT RE-SEEDING STRESS TEST (3 TENANTS)
# =========================================================================
def test_2_concurrent_reseed_3_tenants() -> Dict[str, Any]:
    print("\n" + "=" * 80)
    print("TEST 2: STRESS TEST CONCURRENT RE-SEEDING ACROSS 3 TENANTS")
    print("=" * 80)

    tenants = [TENANT_ALPHA, TENANT_BETA, TENANT_GAMMA]
    num_rounds = 5
    print(f"-> Running {num_rounds} rounds of concurrent seed.py across tenants: {tenants}")

    all_seed_latencies: List[float] = []
    errors = []

    for round_idx in range(1, num_rounds + 1):
        barrier = threading.Barrier(len(tenants))
        round_results: Dict[str, Any] = {}

        def seed_worker(t_id: str):
            barrier.wait()
            t_start = time.time()
            try:
                seed_acme_commons(t_id)
                dur = (time.time() - t_start) * 1000
                return {"tenant": t_id, "success": True, "duration_ms": dur, "error": None}
            except Exception as e:
                dur = (time.time() - t_start) * 1000
                return {"tenant": t_id, "success": False, "duration_ms": dur, "error": str(e)}

        with ThreadPoolExecutor(max_workers=len(tenants)) as executor:
            futures = {executor.submit(seed_worker, tid): tid for tid in tenants}
            for fut in as_completed(futures):
                res = fut.result()
                round_results[res["tenant"]] = res
                all_seed_latencies.append(res["duration_ms"])
                if not res["success"]:
                    errors.append(f"Round {round_idx} Tenant {res['tenant']} failed: {res['error']}")

        round_durs = [f"{t}: {round_results[t]['duration_ms']:.1f}ms" for t in tenants]
        print(f"   Round {round_idx}/{num_rounds} completed in parallel -> {', '.join(round_durs)}")

    # Post-concurrency verification: Inspect each tenant's graph for data integrity and isolation
    print("\n-> Verifying graph schema & tenant isolation across all 3 seeded graphs...")
    tenant_checks = {}
    for tid in tenants:
        g = queries.get_graph(tid, "commons")
        n_src = g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0]
        n_people = g.query("MATCH (p:Person) RETURN count(p)").result_set[0][0]
        n_teams = g.query("MATCH (t:Team) RETURN count(t)").result_set[0][0]
        n_proj = g.query("MATCH (pr:Project) RETURN count(pr)").result_set[0][0]
        n_sys = g.query("MATCH (sys:System) RETURN count(sys)").result_set[0][0]
        n_facts = g.query("MATCH (f:Fact) RETURN count(f)").result_set[0][0]

        tenant_checks[tid] = {
            "sources": n_src, "people": n_people, "teams": n_teams,
            "projects": n_proj, "systems": n_sys, "facts": n_facts
        }

        if n_src != 25 or n_people != 12 or n_teams != 4 or n_proj != 3 or n_sys != 3 or n_facts != 25:
            errors.append(f"Tenant '{tid}' has corrupted node counts after concurrent reseed: {tenant_checks[tid]}")

    stats = format_stats(all_seed_latencies)
    print(f"-> Concurrency Latency Stats: p50={stats['p50_ms']}ms, p95={stats['p95_ms']}ms, max={stats['max_ms']}ms, avg={stats['avg_ms']}ms")
    print(f"-> Tenant Node Counts: {tenant_checks}")

    passed = len(errors) == 0
    print(f"-> TEST 2 VERDICT: {'PASSED' if passed else 'FAILED'}")
    if errors:
        for err in errors:
            print(f"   [!] {err}")

    return {
        "test": "test_2_concurrent_reseed_3_tenants",
        "passed": passed,
        "rounds": num_rounds,
        "total_operations": len(all_seed_latencies),
        "latency_stats": stats,
        "tenant_checks": tenant_checks,
        "errors": errors,
    }


# =========================================================================
# TEST 3: VECTOR EMBEDDING VERIFICATION (384-DIM NORMALIZED FLOATS)
# =========================================================================
def test_3_vector_embeddings_verification() -> Dict[str, Any]:
    print("\n" + "=" * 80)
    print("TEST 3: VECTOR EMBEDDINGS 384-DIM NORMALIZATION & SEARCH VERIFICATION")
    print("=" * 80)

    g = queries.get_graph(TENANT_SINGLE, "commons")
    errors = []

    # 1. Verify all 25 Facts have populated 384-dim normalized float vectors
    res_facts = g.query("MATCH (f:Fact) RETURN f.id, f.embedding, f.text")
    fact_rows = res_facts.result_set
    print(f"-> Inspecting embeddings on {len(fact_rows)} Fact nodes...")

    if len(fact_rows) != 25:
        errors.append(f"Expected 25 Facts, got {len(fact_rows)}")

    norms: List[float] = []
    sample_vec: List[float] = []

    for fid, embedding, text in fact_rows:
        if embedding is None:
            errors.append(f"Fact '{fid}' has null embedding")
            continue

        vec = list(embedding)
        if len(vec) != 384:
            errors.append(f"Fact '{fid}' embedding dimension is {len(vec)}, expected 384")

        # Verify all elements are floats
        if not all(isinstance(x, (float, np.floating)) for x in vec):
            errors.append(f"Fact '{fid}' contains non-float values in vector")

        # Verify L2 Euclidean norm is 1.0 (unit vector)
        norm = float(np.linalg.norm(vec))
        norms.append(norm)
        if abs(norm - 1.0) > 1e-3:
            errors.append(f"Fact '{fid}' embedding norm {norm:.6f} != 1.0 (unnormalized!)")

        if not sample_vec:
            sample_vec = vec

    # 2. Verify all 25 Sources have populated 384-dim normalized float vectors
    res_sources = g.query("MATCH (s:Source) RETURN s.id, s.embedding")
    source_rows = res_sources.result_set
    print(f"-> Inspecting embeddings on {len(source_rows)} Source nodes...")

    for sid, embedding in source_rows:
        if embedding is None:
            errors.append(f"Source '{sid}' has null embedding")
            continue
        vec = list(embedding)
        if len(vec) != 384:
            errors.append(f"Source '{sid}' embedding dimension is {len(vec)}, expected 384")
        norm = float(np.linalg.norm(vec))
        if abs(norm - 1.0) > 1e-3:
            errors.append(f"Source '{sid}' embedding norm {norm:.6f} != 1.0 (unnormalized!)")

    # 3. Test FalkorDB native vector index search
    print("-> Testing FalkorDB native vector index search (CALL db.idx.vector.queryNodes)...")
    test_query_text = "customer refund 30 day return policy window"
    query_vec = generate_embedding(test_query_text)
    q_norm = float(np.linalg.norm(query_vec))

    search_cypher = """
    CALL db.idx.vector.queryNodes('Fact', 'embedding', 5, vecf32($vec))
    YIELD node, score
    RETURN node.id, node.text, score
    """
    t0 = time.time()
    res_search = g.query(search_cypher, {"vec": query_vec})
    search_ms = (time.time() - t0) * 1000

    search_hits = res_search.result_set
    print(f"   Vector search completed in {search_ms:.2f}ms, returned {len(search_hits)} nearest facts:")
    for hid, htext, hscore in search_hits:
        print(f"   - [{hid}] score={hscore:.4f} -> '{htext[:60]}...'")

    if not search_hits:
        errors.append("Native vector index query returned 0 results")

    avg_norm = sum(norms) / len(norms) if norms else 0.0
    min_norm = min(norms) if norms else 0.0
    max_norm = max(norms) if norms else 0.0
    print(f"-> Vector Norm Summary: avg={avg_norm:.6f}, min={min_norm:.6f}, max={max_norm:.6f}")

    passed = len(errors) == 0
    print(f"-> TEST 3 VERDICT: {'PASSED' if passed else 'FAILED'}")
    if errors:
        for err in errors:
            print(f"   [!] {err}")

    return {
        "test": "test_3_vector_embeddings_verification",
        "passed": passed,
        "facts_evaluated": len(fact_rows),
        "sources_evaluated": len(source_rows),
        "dimensions": 384,
        "norm_summary": {
            "avg_norm": round(avg_norm, 6),
            "min_norm": round(min_norm, 6),
            "max_norm": round(max_norm, 6),
        },
        "vector_search_latency_ms": round(search_ms, 2),
        "vector_search_hits": len(search_hits),
        "errors": errors,
    }


# =========================================================================
# TEST 4: EDGE SCHEMA INTEGRITY (AUTHORS, OWNS, RESOLVES, SUPPORTS)
# =========================================================================
def test_4_edge_schema_integrity() -> Dict[str, Any]:
    print("\n" + "=" * 80)
    print("TEST 4: EDGE SCHEMA INTEGRITY (AUTHORS, OWNS, RESOLVES, SUPPORTS)")
    print("=" * 80)

    g = queries.get_graph(TENANT_SINGLE, "commons")
    errors = []

    # 1. AUTHORS edge verification: Person -> Source with role, created_at
    print("-> Verifying AUTHORS edges (Person -> Source)...")
    res_authors = g.query("""
    MATCH (src)-[r:AUTHORS]->(tgt)
    RETURN labels(src), src.id, labels(tgt), tgt.id, r.role, r.created_at
    """)
    authors_rows = res_authors.result_set
    print(f"   Found {len(authors_rows)} AUTHORS edges (expected 25)")
    if len(authors_rows) != 25:
        errors.append(f"Expected 25 AUTHORS edges, found {len(authors_rows)}")

    for src_lbls, src_id, tgt_lbls, tgt_id, role, created_at in authors_rows:
        if "Person" not in src_lbls:
            errors.append(f"AUTHORS edge source '{src_id}' missing :Person label (has {src_lbls})")
        if "Source" not in tgt_lbls:
            errors.append(f"AUTHORS edge target '{tgt_id}' missing :Source label (has {tgt_lbls})")
        if role != "author":
            errors.append(f"AUTHORS edge on '{tgt_id}' has invalid role '{role}'")
        if not isinstance(created_at, int) or created_at <= 0:
            errors.append(f"AUTHORS edge on '{tgt_id}' has invalid timestamp '{created_at}'")

    # 2. OWNS edge verification: Team -> (Source, System, Project) with level, since
    print("-> Verifying OWNS edges (Team -> Source | System | Project)...")
    res_owns = g.query("""
    MATCH (src)-[r:OWNS]->(tgt)
    RETURN labels(src), src.id, labels(tgt), tgt.id, r.level, r.since
    """)
    owns_rows = res_owns.result_set
    # 25 sources + 3 projects + 3 systems = 31 total
    print(f"   Found {len(owns_rows)} OWNS edges (expected 31: 25 Sources + 3 Projects + 3 Systems)")
    if len(owns_rows) != 31:
        errors.append(f"Expected 31 OWNS edges, found {len(owns_rows)}")

    targets_by_label: Dict[str, int] = {}
    for src_lbls, src_id, tgt_lbls, tgt_id, level, since in owns_rows:
        if "Team" not in src_lbls:
            errors.append(f"OWNS edge source '{src_id}' missing :Team label")
        primary_target_lbl = [l for l in tgt_lbls if l in ("Source", "System", "Project")][0]
        targets_by_label[primary_target_lbl] = targets_by_label.get(primary_target_lbl, 0) + 1

        if not level:
            errors.append(f"OWNS edge to '{tgt_id}' missing 'level' property")
        if not isinstance(since, int) or since <= 0:
            errors.append(f"OWNS edge to '{tgt_id}' invalid 'since' timestamp '{since}'")

    print(f"   OWNS target breakdown: {targets_by_label}")
    if targets_by_label.get("Source") != 25:
        errors.append(f"Expected 25 Sources owned by Teams, got {targets_by_label.get('Source')}")
    if targets_by_label.get("Project") != 3:
        errors.append(f"Expected 3 Projects owned by Teams, got {targets_by_label.get('Project')}")
    if targets_by_label.get("System") != 3:
        errors.append(f"Expected 3 Systems owned by Teams, got {targets_by_label.get('System')}")

    # 3. RESOLVES edge verification: Person -> Source (kind: 'TICKET') with resolution, resolved_at
    print("-> Verifying RESOLVES edges (Person -> Source [TICKET])...")
    res_resolves = g.query("""
    MATCH (src)-[r:RESOLVES]->(tgt:Source)
    RETURN labels(src), src.id, tgt.kind, tgt.id, r.resolution, r.resolved_at
    """)
    resolves_rows = res_resolves.result_set
    print(f"   Found {len(resolves_rows)} RESOLVES edges (expected 8 ticket resolutions)")
    if len(resolves_rows) != 8:
        errors.append(f"Expected 8 RESOLVES edges for tickets, found {len(resolves_rows)}")

    for src_lbls, src_id, t_kind, tgt_id, resolution, resolved_at in resolves_rows:
        if "Person" not in src_lbls:
            errors.append(f"RESOLVES source '{src_id}' missing :Person label")
        if t_kind != "TICKET":
            errors.append(f"RESOLVES target '{tgt_id}' is not a TICKET (is {t_kind})")
        if resolution != "RESOLVED":
            errors.append(f"RESOLVES edge to '{tgt_id}' has resolution='{resolution}' (expected 'RESOLVED')")
        if not isinstance(resolved_at, int) or resolved_at <= 0:
            errors.append(f"RESOLVES edge to '{tgt_id}' has invalid resolved_at '{resolved_at}'")

    # 4. SUPPORTS edge verification: System->Project, Source->Project, Fact->Decision/Action
    print("-> Verifying SUPPORTS edges across schema...")
    res_supports = g.query("""
    MATCH (src)-[r:SUPPORTS]->(tgt)
    RETURN labels(src), src.id, labels(tgt), tgt.id, properties(r)
    """)
    supports_rows = res_supports.result_set
    # 3 systems->projects + 3 sources->projects + 2 facts->decision/action = 8
    print(f"   Found {len(supports_rows)} SUPPORTS edges (expected 8)")
    if len(supports_rows) != 8:
        errors.append(f"Expected 8 SUPPORTS edges, found {len(supports_rows)}")

    supports_by_pattern: Dict[str, int] = {}
    for src_lbls, src_id, tgt_lbls, tgt_id, props in supports_rows:
        pattern = f"{src_lbls[0]}->{tgt_lbls[0]}"
        supports_by_pattern[pattern] = supports_by_pattern.get(pattern, 0) + 1

        # Check properties based on endpoint types
        if "System" in src_lbls and "Project" in tgt_lbls:
            if "criticality" not in props or "uptime_sla" not in props:
                errors.append(f"System->Project SUPPORTS missing criticality/uptime_sla: {props}")
        elif "Source" in src_lbls and "Project" in tgt_lbls:
            if "relevance" not in props:
                errors.append(f"Source->Project SUPPORTS missing relevance: {props}")
        elif "Fact" in src_lbls:
            if "confidence" not in props or "weight" not in props:
                errors.append(f"Fact SUPPORTS missing confidence/weight: {props}")

    print(f"   SUPPORTS endpoint breakdown: {supports_by_pattern}")

    passed = len(errors) == 0
    print(f"-> TEST 4 VERDICT: {'PASSED' if passed else 'FAILED'}")
    if errors:
        for err in errors:
            print(f"   [!] {err}")

    return {
        "test": "test_4_edge_schema_integrity",
        "passed": passed,
        "counts": {
            "authors": len(authors_rows),
            "owns": len(owns_rows),
            "resolves": len(resolves_rows),
            "supports": len(supports_rows),
        },
        "breakdowns": {
            "owns_by_target": targets_by_label,
            "supports_by_pattern": supports_by_pattern,
        },
        "errors": errors,
    }


# =========================================================================
# TEST 5: COMPANY BRAIN QUERY INTEGRATION (who_knows & provenance)
# =========================================================================
def test_5_query_integration() -> Dict[str, Any]:
    print("\n" + "=" * 80)
    print("TEST 5: COMPANY BRAIN QUERY INTEGRATION (who_knows & provenance)")
    print("=" * 80)

    errors = []

    # 1. who_knows query for 'top_refunds'
    print("-> Testing queries.who_knows(TENANT, 'top_refunds')...")
    res_refunds = queries.who_knows(TENANT_SINGLE, "top_refunds")
    print(f"   Leaderboard for 'top_refunds' ({len(res_refunds)} experts):")
    for row in res_refunds[:5]:
        print(f"   - {row['name']} ({row['person_id']}): evidence={row['evidence_count']}")

    if not res_refunds:
        errors.append("who_knows('top_refunds') returned empty leaderboard")
    elif res_refunds[0]["evidence_count"] < 1:
        errors.append("who_knows top expert has 0 evidence")

    # 2. who_knows query for 'top_fraud'
    print("-> Testing queries.who_knows(TENANT, 'top_fraud')...")
    res_fraud = queries.who_knows(TENANT_SINGLE, "top_fraud")
    print(f"   Leaderboard for 'top_fraud' ({len(res_fraud)} experts):")
    for row in res_fraud[:3]:
        print(f"   - {row['name']} ({row['person_id']}): evidence={row['evidence_count']}")
    if not res_fraud:
        errors.append("who_knows('top_fraud') returned empty leaderboard")

    # 3. provenance query for 'dec_standard_window_30d'
    print("-> Testing queries.provenance(TENANT, 'dec_standard_window_30d')...")
    res_prov = queries.provenance(TENANT_SINGLE, "dec_standard_window_30d")
    print(f"   Provenance trace ({len(res_prov)} records):")
    for r in res_prov:
        print(f"   - Claim: '{r['claim']}'")
        print(f"     Source: '{r['source']}' ({r['url']}) [version {r['version']}]")
        print(f"     Owner Team: '{r['owner']}'")

    if not res_prov:
        errors.append("provenance('dec_standard_window_30d') returned empty trace")
    else:
        p0 = res_prov[0]
        if not p0.get("source") or not p0.get("owner"):
            errors.append(f"Provenance missing source or owner: {p0}")

    passed = len(errors) == 0
    print(f"-> TEST 5 VERDICT: {'PASSED' if passed else 'FAILED'}")
    if errors:
        for err in errors:
            print(f"   [!] {err}")

    return {
        "test": "test_5_query_integration",
        "passed": passed,
        "top_refunds_experts": len(res_refunds),
        "top_fraud_experts": len(res_fraud),
        "provenance_records": len(res_prov),
        "errors": errors,
    }


# =========================================================================
# MAIN HARNESS RUNNER
# =========================================================================
def main():
    print("*" * 80)
    print("QUORUM SUBAGENT 06: T03-1 COMPANY BRAIN CORPUS & MULTI-SOURCE INGESTION")
    print("*" * 80)

    t_all_start = time.time()
    t1 = test_1_seed_execution_and_corpus_counts()
    t2 = test_2_concurrent_reseed_3_tenants()
    t3 = test_3_vector_embeddings_verification()
    t4 = test_4_edge_schema_integrity()
    t5 = test_5_query_integration()
    total_time_s = time.time() - t_all_start

    all_tests = [t1, t2, t3, t4, t5]
    all_passed = all(t["passed"] for t in all_tests)
    total_errors = sum(len(t["errors"]) for t in all_tests)

    print("\n" + "=" * 80)
    print("SUBAGENT 06 COMPREHENSIVE STRESS TEST SUMMARY")
    print("=" * 80)
    for t in all_tests:
        status = "PASSED [OK]" if t["passed"] else f"FAILED [{len(t['errors'])} errors]"
        print(f"  * {t['test']:<45}: {status}")

    print(f"\nFinal Verdict: {'ALL TESTS PASSED (100% HEALTHY)' if all_passed else 'FAILURES DETECTED'}")
    print(f"Total Execution Time: {total_time_s:.2f}s")
    print("=" * 80)

    summary_json = {
        "timestamp": time.time(),
        "verdict": "PASSED" if all_passed else "FAILED",
        "total_time_s": round(total_time_s, 2),
        "total_errors": total_errors,
        "results": {
            "test_1": t1,
            "test_2": t2,
            "test_3": t3,
            "test_4": t4,
            "test_5": t5,
        }
    }

    out_path = os.path.join(_current_dir, "subagent_06_report.json")
    with open(out_path, "w") as f:
        json.dump(summary_json, f, indent=2)
    print(f"\n[Artifact] Detailed report written to {out_path}")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
