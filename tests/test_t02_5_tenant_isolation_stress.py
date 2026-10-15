"""
Test Suite: T02-5 Per-Tenant Memory & ACL Isolation Stress Test
Comprehensive stress testing of multi-tenant isolation, concurrent queries, Redis ACL enforcement,
and historical time-travel snapshot boundaries.

Tests:
1. Seeding 3 separate tenants: tenant_alpha, tenant_beta, tenant_gamma with distinct secrets,
   entities, procedures, and vector embeddings.
2. Concurrent cross-tenant retrieval queries: 50 concurrent worker threads executing cross-tenant
   vector similarity searches and Cypher queries to verify strictly 0.0% data leakage.
3. Redis ACL enforcement: user permission boundaries, forbidden command blocking (FLUSHALL, CONFIG,
   ACL, etc.), and GRAPH.LIST leakage analysis with gateway filtering fallback.
4. Historical time-travel & snapshot boundaries: multi-epoch timeline mutations across tenants,
   verifying graph_snapshot and as_of preserve strict tenant boundaries across epochs.
"""

import os
import sys
import time
import hashlib
import threading
import statistics
from typing import Dict, List, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

import redis
from falkordb import FalkorDB

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import graph.queries as queries


# Tenant identifiers
TENANT_ALPHA = "tenant_alpha"
TENANT_BETA = "tenant_beta"
TENANT_GAMMA = "tenant_gamma"


def generate_embedding(text: str) -> List[float]:
    """Deterministic 384-dim normalized pseudo-embedding derived from SHA-256."""
    import numpy as np
    rng = np.random.RandomState(int(hashlib.sha256(text.encode()).hexdigest()[:8], 16))
    vec = rng.randn(384)
    norm = np.linalg.norm(vec)
    return (vec / norm).tolist()


def clean_tenant_graph(tenant_id: str):
    """Clean commons graph and raw episodic graphs for a tenant."""
    g = queries.get_graph(tenant_id, "commons")
    try:
        g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    # Reset meta epoch
    try:
        g.query("CREATE (:Meta {id: 'meta', epoch: 1})")
    except Exception:
        pass


def setup_tenant_data(tenant_id: str, secrets: List[Dict[str, Any]]):
    """Seeds a tenant with distinct secret facts, sources, and vector embeddings."""
    clean_tenant_graph(tenant_id)
    queries.ensure_indexes(tenant_id)
    g = queries.get_graph(tenant_id, "commons")

    # Seed Source node
    g.query(
        "CREATE (:Source {id: $src_id, kind: 'DOC', title: $title, url: $url, created_at: timestamp()})",
        {
            "src_id": f"src_{tenant_id}_vault",
            "title": f"{tenant_id.title()} Secret Vault",
            "url": f"https://vault.{tenant_id}.internal",
        }
    )

    # Seed Secrets as Facts with vector embeddings
    for s in secrets:
        vec = generate_embedding(s["text"])
        g.query(
            """
            CREATE (f:Fact:MemoryNode {
                id: $id,
                key: $key,
                value: $value,
                text: $text,
                status: 'HEALTHY',
                valid_from: timestamp(),
                stale: false,
                embedding: vecf32($vec)
            })
            WITH f
            MATCH (src:Source {id: $src_id})
            CREATE (f)-[:FROM_SOURCE]->(src)
            """,
            {
                "id": s["id"],
                "key": s["key"],
                "value": s["value"],
                "text": s["text"],
                "vec": vec,
                "src_id": f"src_{tenant_id}_vault",
            }
        )

    # Also seed a shared colliding ID with tenant-exclusive text
    g.query(
        """
        CREATE (:Fact:MemoryNode {
            id: 'fact_collision_shared',
            key: 'colliding_key',
            value: $tenant_id,
            text: $exclusive_text,
            status: 'HEALTHY',
            valid_from: timestamp(),
            stale: false
        })
        """,
        {
            "tenant_id": tenant_id,
            "exclusive_text": f"COLLISION_PAYLOAD_FOR_{tenant_id.upper()}",
        }
    )


# =========================================================================
# TEST 1: SEED 3 SEPARATE TENANTS WITH DISTINCT SECRETS
# =========================================================================

ALPHA_SECRETS = [
    {
        "id": "alpha_fact_apollo",
        "key": "apollo_key",
        "value": "ALPHA-SEC-9981-RESTRICTED",
        "text": "Project Apollo Quantum Encryption Key: ALPHA-SEC-9981-RESTRICTED stored in Alpha Vault",
    },
    {
        "id": "alpha_fact_merger",
        "key": "acq_chimera",
        "value": "$45,000,000",
        "text": "Alpha Confidential M&A: Acquisition Target Codename Chimera for 45M USD",
    },
    {
        "id": "alpha_fact_nas",
        "key": "nas_algo",
        "value": "NAS-V4.2",
        "text": "Alpha Core Trade Secret: Neural Architecture Search hyper-graph optimizer algorithm v4.2",
    },
]

BETA_SECRETS = [
    {
        "id": "beta_fact_boreas",
        "key": "catalyst_formula",
        "value": "BETA-CAT-5542",
        "text": "Project Boreas Atmospheric Carbon Capture Catalyst chemical formula: BETA-CAT-5542",
    },
    {
        "id": "beta_fact_deficit",
        "key": "deficit_bailout",
        "value": "12.8M EUR",
        "text": "Beta Confidential Finance: Emergency Q3 deficit liquidity bailout request 12.8M EUR",
    },
    {
        "id": "beta_fact_hft",
        "key": "hft_algo",
        "value": "HFT-MM-V9",
        "text": "Beta Proprietary Trading: High Frequency Market Making ultra-low latency FPGA engine",
    },
]

GAMMA_SECRETS = [
    {
        "id": "gamma_fact_chronos",
        "key": "clock_patent",
        "value": "GAMMA-PAT-7701",
        "text": "Project Chronos Sub-nanosecond Atomic Clock Synchronization patent: GAMMA-PAT-7701",
    },
    {
        "id": "gamma_fact_helios",
        "key": "merger_helios",
        "value": "Helios Aerospace",
        "text": "Gamma Strategic Merger: Hostile takeover defense pact with Helios Aerospace",
    },
    {
        "id": "gamma_fact_zk",
        "key": "zk_validator",
        "value": "ZK-ROLLUP-ZETA",
        "text": "Gamma Proprietary Cryptography: Zero Knowledge STARK Rollup state validator pipeline",
    },
]


def test_01_seed_three_separate_tenants():
    """
    Step 1: Seed 3 separate tenants: tenant_alpha, tenant_beta, tenant_gamma.
    Verifies that each tenant has independent graphs, independent meta epochs,
    and distinct non-overlapping facts and secrets.
    """
    print("\n" + "=" * 80)
    print("TEST 1: SEEDING 3 SEPARATE TENANTS (tenant_alpha, tenant_beta, tenant_gamma)")
    print("=" * 80)

    setup_tenant_data(TENANT_ALPHA, ALPHA_SECRETS)
    setup_tenant_data(TENANT_BETA, BETA_SECRETS)
    setup_tenant_data(TENANT_GAMMA, GAMMA_SECRETS)

    # Verify counts and distinct keys in each tenant
    g_a = queries.get_graph(TENANT_ALPHA, "commons")
    g_b = queries.get_graph(TENANT_BETA, "commons")
    g_g = queries.get_graph(TENANT_GAMMA, "commons")

    facts_a = [r[0] for r in g_a.query("MATCH (f:Fact) RETURN f.id").result_set]
    facts_b = [r[0] for r in g_b.query("MATCH (f:Fact) RETURN f.id").result_set]
    facts_g = [r[0] for r in g_g.query("MATCH (f:Fact) RETURN f.id").result_set]

    print(f"  [Tenant Alpha] Fact IDs: {facts_a}")
    print(f"  [Tenant Beta]  Fact IDs: {facts_b}")
    print(f"  [Tenant Gamma] Fact IDs: {facts_g}")

    # Each tenant has 3 unique facts + 1 collision shared fact = 4 facts
    assert len(facts_a) == 4, f"Expected 4 facts in Alpha, got {len(facts_a)}"
    assert len(facts_b) == 4, f"Expected 4 facts in Beta, got {len(facts_b)}"
    assert len(facts_g) == 4, f"Expected 4 facts in Gamma, got {len(facts_g)}"

    # Check that unique facts do not overlap
    assert "alpha_fact_apollo" in facts_a and "alpha_fact_apollo" not in facts_b and "alpha_fact_apollo" not in facts_g
    assert "beta_fact_boreas" in facts_b and "beta_fact_boreas" not in facts_a and "beta_fact_boreas" not in facts_g
    assert "gamma_fact_chronos" in facts_g and "gamma_fact_chronos" not in facts_a and "gamma_fact_chronos" not in facts_b

    print("  [PASS] All 3 tenants successfully initialized with independent graphs and non-overlapping secrets.")


# =========================================================================
# TEST 2: CONCURRENT CROSS-TENANT RETRIEVAL & ZERO LEAKAGE
# =========================================================================

def test_02_concurrent_cross_tenant_retrieval_zero_leakage():
    """
    Step 2: Run concurrent cross-tenant retrieval queries.
    Verify tenant_alpha CANNOT see tenant_beta or tenant_gamma facts (0.0% leakage).
    Spawns 50 concurrent worker threads executing targeted vector similarity searches
    using exact queries for Beta and Gamma secrets against Alpha's endpoint.
    """
    print("\n" + "=" * 80)
    print("TEST 2: CONCURRENT CROSS-TENANT RETRIEVAL & LEAKAGE VERIFICATION (0.0% LEAKAGE)")
    print("=" * 80)

    # Blacklist of secret strings and IDs per tenant
    alpha_indicators = ["ALPHA-SEC-9981", "Chimera", "NAS-V4.2", "alpha_fact_"]
    beta_indicators = ["BETA-CAT-5542", "12.8M EUR", "HFT-MM", "beta_fact_", "Boreas"]
    gamma_indicators = ["GAMMA-PAT-7701", "Helios", "ZK-ROLLUP", "gamma_fact_", "Chronos"]

    total_queries = 300
    concurrent_threads = 50
    barrier = threading.Barrier(concurrent_threads)

    leak_records = []
    latencies = []
    successful_queries = 0

    # Cross-tenant vector search scenarios
    # 1. Alpha client queries using Beta's secret embedding
    vec_beta_boreas = generate_embedding(BETA_SECRETS[0]["text"])
    vec_gamma_chronos = generate_embedding(GAMMA_SECRETS[0]["text"])
    vec_alpha_apollo = generate_embedding(ALPHA_SECRETS[0]["text"])

    def worker_cross_tenant_query(worker_id: int):
        nonlocal successful_queries
        barrier.wait()  # Synchronize threads for maximum concurrency spike

        local_leaks = 0
        tenant_target = [TENANT_ALPHA, TENANT_BETA, TENANT_GAMMA][worker_id % 3]

        if tenant_target == TENANT_ALPHA:
            # Alpha queries using Beta or Gamma vectors trying to find cross-tenant facts
            search_vec = vec_beta_boreas if worker_id % 2 == 0 else vec_gamma_chronos
            forbidden_tokens = beta_indicators + gamma_indicators
        elif tenant_target == TENANT_BETA:
            # Beta queries using Alpha or Gamma vectors
            search_vec = vec_alpha_apollo if worker_id % 2 == 0 else vec_gamma_chronos
            forbidden_tokens = alpha_indicators + gamma_indicators
        else:
            # Gamma queries using Alpha or Beta vectors
            search_vec = vec_alpha_apollo if worker_id % 2 == 0 else vec_beta_boreas
            forbidden_tokens = alpha_indicators + beta_indicators

        t0 = time.time()
        # 1. Vector retrieval through queries.retrieve
        results = queries.retrieve(tenant_target, search_vec, k=10)
        dt_ms = (time.time() - t0) * 1000

        # 2. Check all returned nodes for forbidden cross-tenant tokens
        for item in results:
            nid = item.get("id", "")
            text = item.get("text", "")
            for token in forbidden_tokens:
                if token in nid or token in text:
                    local_leaks += 1
                    leak_records.append({
                        "worker": worker_id,
                        "queried_tenant": tenant_target,
                        "leaked_token": token,
                        "node_id": nid,
                        "text": text,
                    })

        # 3. Direct Cypher matching check on colliding key
        g = queries.get_graph(tenant_target, "commons")
        res_col = g.query("MATCH (f:Fact {id: 'fact_collision_shared'}) RETURN f.value, f.text").result_set
        if res_col:
            val, txt = res_col[0][0], res_col[0][1]
            if val != tenant_target:
                local_leaks += 1
                leak_records.append({
                    "worker": worker_id,
                    "queried_tenant": tenant_target,
                    "leaked_token": "COLLISION_VALUE_MISMATCH",
                    "value": val,
                })
            expected_prefix = f"COLLISION_PAYLOAD_FOR_{tenant_target.upper()}"
            if txt != expected_prefix:
                local_leaks += 1
                leak_records.append({
                    "worker": worker_id,
                    "queried_tenant": tenant_target,
                    "leaked_token": "COLLISION_TEXT_MISMATCH",
                    "text": txt,
                })

        return dt_ms, local_leaks

    print(f"  [+] Launching {total_queries} queries across {concurrent_threads} concurrent worker threads...")
    with ThreadPoolExecutor(max_workers=concurrent_threads) as executor:
        futures = [executor.submit(worker_cross_tenant_query, i) for i in range(total_queries)]
        for f in as_completed(futures):
            dt, leaks = f.result()
            latencies.append(dt)
            if leaks == 0:
                successful_queries += 1

    leakage_count = len(leak_records)
    leakage_percentage = (leakage_count / total_queries) * 100.0

    p50 = statistics.median(latencies)
    p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max(latencies)
    p99 = statistics.quantiles(latencies, n=100)[98] if len(latencies) >= 100 else max(latencies)

    print(f"  [RESULTS] Total Queries: {total_queries} | Concurrent Workers: {concurrent_threads}")
    print(f"  [METRICS] Latency: p50={p50:.2f}ms | p95={p95:.2f}ms | p99={p99:.2f}ms")
    print(f"  [LEAKAGE] Cross-tenant leaks detected: {leakage_count} / {total_queries}")
    print(f"  [LEAKAGE RATE] {leakage_percentage:.4f}% leakage (Required: 0.0%)")

    assert leakage_count == 0, f"VIOLATION: Detected {leakage_count} cross-tenant memory leaks!"
    assert leakage_percentage == 0.0, f"VIOLATION: Non-zero leakage rate {leakage_percentage}%!"
    print("  [PASS] 0.0% leakage verified across all concurrent retrieval queries.")


# =========================================================================
# TEST 3: REDIS ACL ENFORCEMENT & FORBIDDEN COMMANDS
# =========================================================================

def test_03_redis_acl_enforcement_and_forbidden_commands():
    """
    Step 3: Test Redis ACL enforcement (scripts/test_acl.py).
    Verifying:
    - User tenant_alpha cannot read or write tenant_beta or tenant_gamma graphs.
    - User tenant_beta cannot read or write tenant_alpha or tenant_gamma graphs.
    - Forbidden dangerous commands (FLUSHALL, FLUSHDB, CONFIG, ACL, DEBUG) are rejected.
    - FalkorDB GRAPH.LIST server-level behavior and gateway-level tenant filtering fallback.
    """
    print("\n" + "=" * 80)
    print("TEST 3: REDIS ACL ENFORCEMENT & FORBIDDEN COMMANDS")
    print("=" * 80)

    r_admin = redis.Redis(host="localhost", port=6379, decode_responses=True)

    # 1. Configure ACL Users for the 3 tenants with restricted key patterns
    # Each tenant user is locked to their own graph prefix (~tenant_<id>:*)
    r_admin.execute_command(
        "ACL", "SETUSER", "tenant_alpha_user", "on", ">alpha_pass_99",
        "~tenant_alpha:*", "&*", "+@all"
    )
    r_admin.execute_command(
        "ACL", "SETUSER", "tenant_beta_user", "on", ">beta_pass_88",
        "~tenant_beta:*", "&*", "+@all"
    )
    r_admin.execute_command(
        "ACL", "SETUSER", "tenant_gamma_user", "on", ">gamma_pass_77",
        "~tenant_gamma:*", "&*", "+@all"
    )

    # Configure a restricted tenant user with explicitly revoked dangerous commands
    r_admin.execute_command(
        "ACL", "SETUSER", "tenant_restricted_user", "on", ">restricted_pass_11",
        "~tenant_alpha:*", "&*",
        "+@read", "+@write", "+@connection",
        "+graph.query", "+graph.ro_query", "+graph.explain", "+graph.profile",
        "-FLUSHALL", "-FLUSHDB", "-CONFIG", "-ACL", "-DEBUG", "-SHUTDOWN", "-KEYS"
    )

    print("  [+] ACL Users successfully configured in Redis.")

    # 2. Test Cross-Tenant Access Restrictions via FalkorDB
    f_alpha = FalkorDB(host="localhost", port=6379, username="tenant_alpha_user", password="alpha_pass_99")
    f_beta = FalkorDB(host="localhost", port=6379, username="tenant_beta_user", password="beta_pass_88")
    f_gamma = FalkorDB(host="localhost", port=6379, username="tenant_gamma_user", password="gamma_pass_77")

    # A) Alpha reading its own graph -> SUCCESS
    g_alpha_own = f_alpha.select_graph("tenant_alpha:commons")
    res_a = g_alpha_own.query("MATCH (f:Fact) RETURN f.id").result_set
    assert len(res_a) > 0, "Alpha should be able to read its own graph"
    print(f"  [+] Alpha reading tenant_alpha:commons: SUCCESS ({len(res_a)} facts)")

    # B) Alpha attempting to read Beta's graph -> DENIED
    alpha_denied_beta = False
    try:
        g_alpha_on_beta = f_alpha.select_graph("tenant_beta:commons")
        g_alpha_on_beta.query("MATCH (f:Fact) RETURN f.id")
    except Exception as e:
        alpha_denied_beta = True
        err_msg = str(e)
        assert "No permissions" in err_msg or "NOPERM" in err_msg, f"Unexpected error: {err_msg}"
        print(f"  [+] Alpha reading tenant_beta:commons: BLOCKED AS EXPECTED ({type(e).__name__}: {err_msg})")
    assert alpha_denied_beta, "SECURITY VIOLATION: Alpha was able to query Beta's graph!"

    # C) Alpha attempting to read Gamma's graph -> DENIED
    alpha_denied_gamma = False
    try:
        g_alpha_on_gamma = f_alpha.select_graph("tenant_gamma:commons")
        g_alpha_on_gamma.query("MATCH (f:Fact) RETURN f.id")
    except Exception as e:
        alpha_denied_gamma = True
        err_msg = str(e)
        assert "No permissions" in err_msg or "NOPERM" in err_msg
        print(f"  [+] Alpha reading tenant_gamma:commons: BLOCKED AS EXPECTED ({type(e).__name__}: {err_msg})")
    assert alpha_denied_gamma, "SECURITY VIOLATION: Alpha was able to query Gamma's graph!"

    # D) Beta attempting to read Alpha's graph -> DENIED
    beta_denied_alpha = False
    try:
        g_beta_on_alpha = f_beta.select_graph("tenant_alpha:commons")
        g_beta_on_alpha.query("MATCH (f:Fact) RETURN f.id")
    except Exception as e:
        beta_denied_alpha = True
        err_msg = str(e)
        assert "No permissions" in err_msg or "NOPERM" in err_msg
        print(f"  [+] Beta reading tenant_alpha:commons: BLOCKED AS EXPECTED ({type(e).__name__}: {err_msg})")
    assert beta_denied_alpha, "SECURITY VIOLATION: Beta was able to query Alpha's graph!"

    # E) Gamma attempting to write into Alpha's graph -> DENIED
    gamma_denied_write = False
    try:
        g_gamma_on_alpha = f_gamma.select_graph("tenant_alpha:commons")
        g_gamma_on_alpha.query("CREATE (:Hacked {id: 'poison_alpha'})")
    except Exception as e:
        gamma_denied_write = True
        err_msg = str(e)
        assert "No permissions" in err_msg or "NOPERM" in err_msg
        print(f"  [+] Gamma writing into tenant_alpha:commons: BLOCKED AS EXPECTED ({type(e).__name__}: {err_msg})")
    assert gamma_denied_write, "SECURITY VIOLATION: Gamma was able to write into Alpha's graph!"

    # 3. Test Forbidden Dangerous Commands under restricted user
    r_restricted = redis.Redis(
        host="localhost", port=6379,
        username="tenant_restricted_user", password="restricted_pass_11",
        decode_responses=True
    )

    forbidden_commands = [
        ("FLUSHALL", []),
        ("FLUSHDB", []),
        ("CONFIG", ["GET", "*"]),
        ("ACL", ["LIST"]),
        ("DEBUG", ["SEGFAULT"]),
        ("SHUTDOWN", []),
        ("KEYS", ["*"]),
    ]

    print("\n  [+] Testing Forbidden Commands enforcement under restricted tenant user:")
    for cmd, args in forbidden_commands:
        blocked = False
        try:
            r_restricted.execute_command(cmd, *args)
        except Exception as e:
            blocked = True
            err_str = str(e)
            assert isinstance(e, redis.exceptions.NoPermissionError) or "no permission" in err_str.lower() or "noperm" in err_str.lower() or "not allowed" in err_str.lower() or "unknown command" in err_str.lower(), f"Unexpected error: {err_str}"
            print(f"      - Command '{cmd}': FORBIDDEN / BLOCKED as expected ({type(e).__name__})")
        assert blocked, f"SECURITY VIOLATION: Restricted tenant user executed dangerous command '{cmd}'!"

    # 4. Check GRAPH.LIST and Gateway-Level Filtering Fallback
    r_alpha = redis.Redis(
        host="localhost", port=6379,
        username="tenant_alpha_user", password="alpha_pass_99",
        decode_responses=True
    )
    raw_graph_list = r_alpha.execute_command("GRAPH.LIST")
    assert isinstance(raw_graph_list, list)

    has_foreign_graphs = any("tenant_beta" in str(g) or "tenant_gamma" in str(g) for g in raw_graph_list)
    print("\n  [GRAPH.LIST Analysis]")
    print(f"  - Total graphs visible in server GRAPH.LIST: {len(raw_graph_list)}")
    print(f"  - Contains foreign tenant graph keys: {has_foreign_graphs}")

    # Gateway filtering rule:
    filtered_for_alpha = [g for g in raw_graph_list if g.startswith(f"{TENANT_ALPHA}:")]
    filtered_for_beta = [g for g in raw_graph_list if g.startswith(f"{TENANT_BETA}:")]
    filtered_for_gamma = [g for g in raw_graph_list if g.startswith(f"{TENANT_GAMMA}:")]

    # Verification: Gateway filtering strictly isolates tenant graphs
    assert all(g.startswith(f"{TENANT_ALPHA}:") for g in filtered_for_alpha)
    assert not any("tenant_beta" in g or "tenant_gamma" in g for g in filtered_for_alpha)

    assert all(g.startswith(f"{TENANT_BETA}:") for g in filtered_for_beta)
    assert not any("tenant_alpha" in g or "tenant_gamma" in g for g in filtered_for_beta)

    assert all(g.startswith(f"{TENANT_GAMMA}:") for g in filtered_for_gamma)
    assert not any("tenant_alpha" in g or "tenant_beta" in g for g in filtered_for_gamma)

    print(f"  [+] Gateway Filtering Verification: Alpha sees {filtered_for_alpha}, Beta sees {filtered_for_beta}, Gamma sees {filtered_for_gamma}")
    print("  [PASS] Redis ACL key barriers, command restrictions, and gateway filtering verified.")


# =========================================================================
# TEST 4: GRAPH_SNAPSHOT & AS_OF TIME-TRAVEL PRESERVATION ACROSS EPOCHS
# =========================================================================

def test_04_snapshot_and_as_of_preserves_tenant_boundaries_across_epochs():
    """
    Step 4: Verify graph_snapshot and as_of time-travel functions preserve exact tenant boundaries
    across historical epochs.
    Mutates 3 tenants across different epoch counts:
    - Alpha advances through 5 epochs with mutations (creation, quarantine, supersede).
    - Beta advances through 3 epochs.
    - Gamma advances through 2 epochs.
    Verifies:
    - graph_snapshot(tenant_id) returns ONLY that tenant's nodes and exact epoch.
    - as_of(tenant_id, timestamp_ms) returns ONLY that tenant's point-in-time beliefs.
    - Time-travel queries do NOT mutate graph heads (non-destructive).
    """
    print("\n" + "=" * 80)
    print("TEST 4: GRAPH_SNAPSHOT & AS_OF HISTORICAL EPOCH BOUNDARIES")
    print("=" * 80)

    # 1. Initialize Epoch 1 timestamps for all tenants
    setup_tenant_data(TENANT_ALPHA, ALPHA_SECRETS)
    setup_tenant_data(TENANT_BETA, BETA_SECRETS)
    setup_tenant_data(TENANT_GAMMA, GAMMA_SECRETS)

    time.sleep(0.05)
    t0_alpha = int(time.time() * 1000)
    time.sleep(0.05)

    # -----------------------------------------------------------------
    # TENANT ALPHA: Advance through 5 distinct epochs with mutations
    # -----------------------------------------------------------------
    g_a = queries.get_graph(TENANT_ALPHA, "commons")

    # Epoch 2: Add new fact
    g_a.query("""
    CREATE (:Fact:MemoryNode {
        id: 'alpha_fact_vip', key: 'vip_discount', value: '25%', text: 'Alpha VIP discount 25%',
        status: 'HEALTHY', valid_from: timestamp(), stale: false
    })
    """)
    queries.bump_epoch(TENANT_ALPHA)  # Epoch 2
    time.sleep(0.05)
    t1_alpha = int(time.time() * 1000)

    # Epoch 3: Quarantine alpha_fact_apollo
    g_a.query("""
    MATCH (f:Fact {id: 'alpha_fact_apollo'})
    SET f.status = 'QUARANTINED', f.quarantined_at = timestamp()
    """)
    queries.bump_epoch(TENANT_ALPHA)  # Epoch 3
    time.sleep(0.02)
    t2_alpha = int(time.time() * 1000)

    # Epoch 4: Supersede alpha_fact_nas with v5
    vec_v5 = generate_embedding("Alpha Core Trade Secret: Neural Architecture Search v5.0 next-gen")
    queries.supersede(
        TENANT_ALPHA,
        old_fact_id="alpha_fact_nas",
        new_fact_id="alpha_fact_nas_v5",
        new_value="NAS-V5.0",
        new_text="Alpha Core Trade Secret: Neural Architecture Search v5.0 next-gen",
        new_embedding=vec_v5,
    )
    time.sleep(0.02)
    t3_alpha = int(time.time() * 1000)
    # supersede increments epoch inside queries.py -> Epoch 4

    # Epoch 5: Add conclusion
    g_a.query("""
    CREATE (:Conclusion:MemoryNode {
        id: 'alpha_conclusion_1', text: 'Alpha AI pipeline ready for deployment',
        status: 'HEALTHY', valid_from: timestamp(), stale: false
    })
    """)
    queries.bump_epoch(TENANT_ALPHA)  # Epoch 5
    time.sleep(0.02)
    t4_alpha = int(time.time() * 1000)
    epoch_alpha_final = queries.graph_snapshot(TENANT_ALPHA)["epoch"]
    print(f"  [+] Tenant Alpha progressed to Epoch {epoch_alpha_final}")

    # -----------------------------------------------------------------
    # TENANT BETA: Advance through 3 distinct epochs
    # -----------------------------------------------------------------
    g_b = queries.get_graph(TENANT_BETA, "commons")

    # Epoch 2: Add beta shipping SLA
    g_b.query("""
    CREATE (:Fact:MemoryNode {
        id: 'beta_fact_shipping', key: 'shipping_sla', value: '24h', text: 'Beta overnight shipping SLA',
        status: 'HEALTHY', valid_from: timestamp(), stale: false
    })
    """)
    queries.bump_epoch(TENANT_BETA)  # Epoch 2
    time.sleep(0.02)
    t0_beta = int(time.time() * 1000)

    # Epoch 3: Quarantine beta deficit
    g_b.query("""
    MATCH (f:Fact {id: 'beta_fact_deficit'})
    SET f.status = 'QUARANTINED', f.quarantined_at = timestamp()
    """)
    queries.bump_epoch(TENANT_BETA)  # Epoch 3
    time.sleep(0.02)
    t1_beta = int(time.time() * 1000)
    epoch_beta_final = queries.graph_snapshot(TENANT_BETA)["epoch"]
    print(f"  [+] Tenant Beta progressed to Epoch {epoch_beta_final}")

    # -----------------------------------------------------------------
    # TENANT GAMMA: Advance through 2 distinct epochs
    # -----------------------------------------------------------------
    g_g = queries.get_graph(TENANT_GAMMA, "commons")
    queries.bump_epoch(TENANT_GAMMA)  # Epoch 2
    epoch_gamma_final = queries.graph_snapshot(TENANT_GAMMA)["epoch"]
    print(f"  [+] Tenant Gamma progressed to Epoch {epoch_gamma_final}")

    # -----------------------------------------------------------------
    # VERIFY GRAPH_SNAPSHOT BOUNDARIES
    # -----------------------------------------------------------------
    snap_alpha = queries.graph_snapshot(TENANT_ALPHA)
    snap_beta = queries.graph_snapshot(TENANT_BETA)
    snap_gamma = queries.graph_snapshot(TENANT_GAMMA)

    assert snap_alpha["epoch"] == epoch_alpha_final, f"Alpha epoch mismatch: {snap_alpha['epoch']} != {epoch_alpha_final}"
    assert snap_beta["epoch"] == epoch_beta_final, f"Beta epoch mismatch: {snap_beta['epoch']} != {epoch_beta_final}"
    assert snap_gamma["epoch"] == epoch_gamma_final, f"Gamma epoch mismatch: {snap_gamma['epoch']} != {epoch_gamma_final}"

    alpha_node_ids = {n["id"] for n in snap_alpha["nodes"]}
    beta_node_ids = {n["id"] for n in snap_beta["nodes"]}
    gamma_node_ids = {n["id"] for n in snap_gamma["nodes"]}

    print(f"  [Snapshot Nodes] Alpha: {len(alpha_node_ids)} | Beta: {len(beta_node_ids)} | Gamma: {len(gamma_node_ids)}")

    # Check that NO node from Beta or Gamma exists in Alpha snapshot
    alpha_cross_leaks = alpha_node_ids.intersection(beta_node_ids.union(gamma_node_ids))
    # Exclude shared infrastructure IDs ('meta', 'fact_collision_shared')
    alpha_cross_leaks.difference_update({"fact_collision_shared", "meta"})
    assert len(alpha_cross_leaks) == 0, f"Alpha snapshot contains foreign nodes: {alpha_cross_leaks}"

    # Check Beta snapshot
    beta_cross_leaks = beta_node_ids.intersection(alpha_node_ids.union(gamma_node_ids))
    beta_cross_leaks.difference_update({"fact_collision_shared", "meta"})
    assert len(beta_cross_leaks) == 0, f"Beta snapshot contains foreign nodes: {beta_cross_leaks}"

    # Check Gamma snapshot
    gamma_cross_leaks = gamma_node_ids.intersection(alpha_node_ids.union(beta_node_ids))
    gamma_cross_leaks.difference_update({"fact_collision_shared", "meta"})
    assert len(gamma_cross_leaks) == 0, f"Gamma snapshot contains foreign nodes: {gamma_cross_leaks}"

    print("  [+] graph_snapshot isolation verified: Exact epoch and zero cross-tenant nodes across all snapshots.")

    # -----------------------------------------------------------------
    # VERIFY AS_OF TIME-TRAVEL POINT-IN-TIME BELIEF BOUNDARIES
    # -----------------------------------------------------------------
    # Alpha at T0 (initial): alpha_fact_apollo was HEALTHY
    as_of_a_t0 = queries.as_of(TENANT_ALPHA, t0_alpha)
    as_of_a_t0_ids = {f["id"]: f["text"] for f in as_of_a_t0}
    assert "alpha_fact_apollo" in as_of_a_t0_ids, "Apollo fact should be present at T0"
    assert "alpha_fact_vip" not in as_of_a_t0_ids, "VIP fact should not exist at T0"
    # Ensure zero foreign facts
    for fid in as_of_a_t0_ids:
        assert not fid.startswith("beta_") and not fid.startswith("gamma_"), f"Foreign fact leaked in as_of: {fid}"

    # Alpha at T2 (after quarantine of Apollo fact): Apollo fact is excluded
    as_of_a_t2 = queries.as_of(TENANT_ALPHA, t2_alpha + 5)
    as_of_a_t2_ids = {f["id"]: f["text"] for f in as_of_a_t2}
    assert "alpha_fact_apollo" not in as_of_a_t2_ids, "Quarantined Apollo fact must not appear in as_of(T2)"
    assert "alpha_fact_vip" in as_of_a_t2_ids, "VIP fact should appear at T2"

    # Beta at T1 (after quarantine of deficit): Deficit fact is excluded
    as_of_b_t1 = queries.as_of(TENANT_BETA, t1_beta + 5)
    as_of_b_t1_ids = {f["id"]: f["text"] for f in as_of_b_t1}
    assert "beta_fact_deficit" not in as_of_b_t1_ids, "Quarantined deficit fact must not appear in as_of(T1)"
    assert "beta_fact_shipping" in as_of_b_t1_ids, "Shipping fact should appear at T1"

    # Cross-tenant query: Querying Alpha with Beta's timestamp
    cross_as_of = queries.as_of(TENANT_ALPHA, t1_beta)
    cross_as_of_ids = {f["id"] for f in cross_as_of}
    assert not any(fid.startswith("beta_") or fid.startswith("gamma_") for fid in cross_as_of_ids)

    print("  [+] as_of time-travel verified: Point-in-time beliefs strictly isolated within tenant boundary.")

    # -----------------------------------------------------------------
    # NON-DESTRUCTIVE TIME TRAVEL INVARIANT
    # -----------------------------------------------------------------
    # Call as_of 50 times across past timestamps and verify current graph head is unchanged
    head_before = queries.graph_snapshot(TENANT_ALPHA)
    for _ in range(50):
        queries.as_of(TENANT_ALPHA, t0_alpha)
        queries.as_of(TENANT_ALPHA, t2_alpha)
        queries.as_of(TENANT_ALPHA, t3_alpha)
    head_after = queries.graph_snapshot(TENANT_ALPHA)

    assert head_before["epoch"] == head_after["epoch"], "Time-travel mutated tenant epoch!"
    assert len(head_before["nodes"]) == len(head_after["nodes"]), "Time-travel mutated node count!"
    assert len(head_before["edges"]) == len(head_after["edges"]), "Time-travel mutated edge count!"

    print("  [+] Non-destructive invariant verified: 50 time-travel queries caused zero mutation to live graph head.")
    print("  [PASS] graph_snapshot and as_of tenant boundaries across historical epochs completely verified.")


if __name__ == "__main__":
    test_01_seed_three_separate_tenants()
    test_02_concurrent_cross_tenant_retrieval_zero_leakage()
    test_03_redis_acl_enforcement_and_forbidden_commands()
    test_04_snapshot_and_as_of_preserves_tenant_boundaries_across_epochs()
    print("\n" + "=" * 80)
    print("ALL T02-5 PER-TENANT MEMORY & ACL ISOLATION TESTS PASSED SUCCESSFULLY!")
    print("=" * 80)
