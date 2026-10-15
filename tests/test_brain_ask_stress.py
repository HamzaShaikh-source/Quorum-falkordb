"""
Comprehensive Stress-Test Suite: T03-5 Grounded Q&A brain_ask query & citation enforcement

Specific tests:
1. Seed facts with varying content and test both vector similarity search (with 384-dim normalized embeddings) and keyword fallback search.
2. Verify that stale=True facts are strictly EXCLUDED from brain_ask results.
3. Verify structured refusal when no matching facts exist or confidence is below threshold.
4. Verify citation provenance: check that citations returned match exact healthy fact IDs with text quotes and author tiers.
5. Concurrently run 100 brain_ask queries across 10 threads while facts are being added and superseded.
"""

import time
import threading
import statistics
import hashlib
import numpy as np
from typing import Dict, List, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from graph import queries


TENANT_STRESS = "test_stress_brain_ask"


def generate_embedding(text: str) -> List[float]:
    """Deterministic 384-dim normalized embedding derived from SHA-256 hash."""
    rng = np.random.RandomState(int(hashlib.sha256(text.encode()).hexdigest()[:8], 16))
    vec = rng.randn(384)
    norm = np.linalg.norm(vec)
    return (vec / norm).tolist()


def clean_graph(tenant_id: str):
    """Clean all nodes in tenant commons graph."""
    g = queries.get_graph(tenant_id, "commons")
    try:
        g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    queries.ensure_indexes(tenant_id)


# =========================================================================
# TEST 1: Vector Similarity Search & Keyword Fallback Search
# =========================================================================

def test_vector_and_keyword_retrieval():
    """
    Test 1:
    - Seed facts across distinct domains with 384-dim normalized embeddings.
    - Test vector similarity search: query embedding retrieves correct target fact with high similarity.
    - Test keyword fallback search: query without embedding extracts longest clean token and retrieves correct fact.
    """
    clean_graph(TENANT_STRESS)
    g = queries.get_graph(TENANT_STRESS, "commons")

    facts_data = [
        {
            "id": "fact_refund_window",
            "key": "refund.window_policy",
            "text": "Acme customer refund requests are accepted within 30 calendar days of delivery.",
            "value": "30",
            "quote": "within 30 calendar days of delivery",
            "source_id": "src_refund_policy",
            "source_title": "Global Refund Policy v2",
            "team_id": "team_support",
            "team_name": "Customer Support Operations",
            "tier": "root"
        },
        {
            "id": "fact_gdpr_retention",
            "key": "compliance.gdpr_retention_days",
            "text": "Personal data and user session telemetry must be purged within 90 days per GDPR mandate.",
            "value": "90",
            "quote": "purged within 90 days per GDPR mandate",
            "source_id": "src_gdpr_policy",
            "source_title": "Data Privacy & Retention Standard",
            "team_id": "team_legal",
            "team_name": "Legal & Compliance",
            "tier": "root"
        },
        {
            "id": "fact_cert_rotation",
            "key": "infra.tls_cert_rotation_days",
            "text": "Production TLS and SSL security certificates rotate automatically every 60 days.",
            "value": "60",
            "quote": "rotate automatically every 60 days",
            "source_id": "src_tls_guide",
            "source_title": "Infrastructure Security Handbook",
            "team_id": "team_infra",
            "team_name": "Infrastructure Security",
            "tier": "established"
        },
        {
            "id": "fact_oncall_sla",
            "key": "ops.incident_response_minutes",
            "text": "Critical severity production incidents require oncall engineer acknowledgment within 15 minutes.",
            "value": "15",
            "quote": "acknowledgment within 15 minutes",
            "source_id": "src_oncall_sop",
            "source_title": "Incident Management Protocol",
            "team_id": "team_sre",
            "team_name": "Site Reliability Engineering",
            "tier": "established"
        },
        {
            "id": "fact_vpn_mfa",
            "key": "security.vpn_mfa_requirement",
            "text": "Corporate VPN access mandates hardware security key WebAuthn MFA authentication.",
            "value": "mfa_required",
            "quote": "mandates hardware security key WebAuthn MFA",
            "source_id": "src_vpn_guideline",
            "source_title": "Remote Access Security Policy",
            "team_id": "team_secops",
            "team_name": "Security Operations",
            "tier": "new"
        }
    ]

    print("\n--- [TEST 1] Seeding 5 Facts with 384-dim Embeddings & Provenance ---")
    for f in facts_data:
        vec = generate_embedding(f["text"])
        vec_str = ", ".join(str(x) for x in vec)
        cypher = f"""
        CREATE (s:Source {{
            id: '{f["source_id"]}',
            title: '{f["source_title"]}',
            url: 'https://kb.acme.corp/{f["source_id"]}',
            kind: 'DOC',
            version: '2.1'
        }})
        CREATE (t:Team {{id: '{f["team_id"]}', name: '{f["team_name"]}'}})
        CREATE (t)-[:OWNS]->(s)
        CREATE (f:Fact:MemoryNode {{
            id: '{f["id"]}',
            key: '{f["key"]}',
            text: '{f["text"]}',
            value: '{f["value"]}',
            quote: '{f["quote"]}',
            status: 'HEALTHY',
            stale: false,
            author_tier: '{f["tier"]}',
            embedding: vecf32([{vec_str}])
        }})
        CREATE (f)-[:FROM_SOURCE]->(s)
        """
        g.query(cypher)

    # 1A. Test Vector Similarity Search for each fact
    print("\n--- [TEST 1A] Testing Vector Similarity Search (384-dim embeddings) ---")
    for target in facts_data:
        q_vec = generate_embedding(target["text"])
        res = queries.brain_ask(TENANT_STRESS, question=f"Query about {target['key']}", embedding=q_vec, k=3)
        assert res["grounded"] is True, f"Expected grounded result for {target['id']}"
        assert res["refused"] is False
        assert len(res["citations"]) >= 1
        top_citation = res["citations"][0]
        assert top_citation["fact_id"] == target["id"], f"Expected top fact {target['id']}, got {top_citation['fact_id']}"
        assert top_citation["similarity"] >= 0.99, f"Expected near 1.0 similarity, got {top_citation['similarity']}"
        print(f"  [OK] Vector query for {target['id']}: top_citation={top_citation['fact_id']}, similarity={top_citation['similarity']}")

    # 1B. Test Keyword Fallback Search (embedding=None)
    print("\n--- [TEST 1B] Testing Keyword Fallback Search ---")
    test_keywords = [
        ("What is the refund calendar policy for customers?", "fact_refund_window"),
        ("What is the gdpr retention period for user data?", "fact_gdpr_retention"),
        ("How often do security certificates rotate?", "fact_cert_rotation"),
        ("What is the oncall SLA for critical incidents?", "fact_oncall_sla"),
        ("What are the hardware webauthn authentication rules?", "fact_vpn_mfa"),
    ]
    for question, expected_fact_id in test_keywords:
        res = queries.brain_ask(TENANT_STRESS, question=question, embedding=None, k=3)
        assert res["grounded"] is True, f"Expected grounded result for keyword query: '{question}'"
        assert res["refused"] is False
        assert len(res["citations"]) >= 1
        matched_ids = [c["fact_id"] for c in res["citations"]]
        assert expected_fact_id in matched_ids, f"Expected {expected_fact_id} in {matched_ids} for '{question}'"
        print(f"  [OK] Keyword query '{question[:40]}...': matched {matched_ids}")


# =========================================================================
# TEST 2: Strict Exclusion of Stale Facts
# =========================================================================

def test_stale_facts_strictly_excluded():
    """
    Test 2:
    - Verify that facts marked stale=True are NEVER included in brain_ask results.
    - Test topic with ONLY stale facts: brain_ask must return structured refusal.
    - Test topic with BOTH stale and fresh facts: brain_ask returns only the fresh facts.
    """
    clean_graph(TENANT_STRESS)
    g = queries.get_graph(TENANT_STRESS, "commons")

    # 1. Topic with ONLY stale fact
    stale_text = "Legacy deprecation policy 2021: old passwords expire every 180 days."
    vec_stale = generate_embedding(stale_text)
    vec_stale_str = ", ".join(str(x) for x in vec_stale)
    g.query(f"""
    CREATE (s:Source {{id: 'src_obsolete', title: 'Obsolete Password Doc', url: 'https://kb/obs', kind: 'DOC', version: '0.1'}})
    CREATE (f:Fact:MemoryNode {{
        id: 'f_stale_only',
        key: 'auth.stale_password_expiry',
        text: '{stale_text}',
        value: '180',
        quote: 'expire every 180 days',
        status: 'HEALTHY',
        stale: true,
        author_tier: 'root',
        embedding: vecf32([{vec_stale_str}])
    }})
    CREATE (f)-[:FROM_SOURCE]->(s)
    """)

    # Query stale-only fact via vector
    res_vec = queries.brain_ask(TENANT_STRESS, question="password expiry policy", embedding=vec_stale)
    assert res_vec["refused"] is True, "Expected refusal when only stale facts match via vector"
    assert res_vec["grounded"] is False
    assert res_vec["citations"] == []
    print("\n  [OK] Stale-only vector query correctly refused.")

    # Query stale-only fact via keyword
    res_kw = queries.brain_ask(TENANT_STRESS, question="Legacy deprecation policy 2021 passwords", embedding=None)
    assert res_kw["refused"] is True, "Expected refusal when only stale facts match via keyword"
    assert res_kw["grounded"] is False
    assert res_kw["citations"] == []
    print("  [OK] Stale-only keyword query correctly refused.")

    # 2. Topic with BOTH stale and fresh facts
    fresh_text = "Current 2026 security policy: passwords must be at least 16 characters and never expire."
    vec_fresh = generate_embedding(fresh_text)
    vec_fresh_str = ", ".join(str(x) for x in vec_fresh)
    g.query(f"""
    CREATE (s2:Source {{id: 'src_fresh', title: 'Current NIST Policy', url: 'https://kb/nist', kind: 'DOC', version: '3.0'}})
    CREATE (t:Team {{id: 'team_sec', name: 'Information Security'}})
    CREATE (t)-[:OWNS]->(s2)
    CREATE (f2:Fact:MemoryNode {{
        id: 'f_fresh_password_policy',
        key: 'auth.modern_password_policy',
        text: '{fresh_text}',
        value: '16_chars_no_expiry',
        quote: 'at least 16 characters and never expire',
        status: 'HEALTHY',
        stale: false,
        author_tier: 'established',
        embedding: vecf32([{vec_fresh_str}])
    }})
    CREATE (f2)-[:FROM_SOURCE]->(s2)
    """)

    # Query mixed topic
    res_mixed = queries.brain_ask(TENANT_STRESS, question="modern security policy passwords", embedding=None)
    assert res_mixed["grounded"] is True
    assert res_mixed["refused"] is False
    cited_ids = [c["fact_id"] for c in res_mixed["citations"]]
    assert "f_fresh_password_policy" in cited_ids
    assert "f_stale_only" not in cited_ids, "CRITICAL ERROR: Stale fact was included in brain_ask citations!"
    for c in res_mixed["citations"]:
        assert c["stale"] is False
    print(f"  [OK] Mixed fresh/stale topic: only fresh fact {cited_ids} returned, stale fact strictly excluded.")


# =========================================================================
# TEST 3: Structured Refusal on No Match or Below Confidence Threshold
# =========================================================================

def test_structured_refusal_and_threshold():
    """
    Test 3:
    - Structured refusal on completely ungrounded / out-of-domain query.
    - Confidence threshold enforcement: low similarity queries must be refused.
    - Queries meeting threshold must be accepted.
    """
    clean_graph(TENANT_STRESS)
    g = queries.get_graph(TENANT_STRESS, "commons")

    # Seed one base fact
    text = "Company standard coffee machine maintenance occurs every Monday morning."
    vec = generate_embedding(text)
    vec_str = ", ".join(str(x) for x in vec)
    g.query(f"""
    CREATE (s:Source {{id: 'src_coffee', title: 'Facilities Maintenance', url: 'https://kb/fac', kind: 'DOC', version: '1.0'}})
    CREATE (t:Team {{id: 'team_fac', name: 'Facilities Management'}})
    CREATE (t)-[:OWNS]->(s)
    CREATE (f:Fact:MemoryNode {{
        id: 'f_coffee_maint',
        key: 'office.coffee_maintenance',
        text: '{text}',
        value: 'monday',
        quote: 'maintenance occurs every Monday morning',
        status: 'HEALTHY',
        stale: false,
        author_tier: 'new',
        embedding: vecf32([{vec_str}])
    }})
    CREATE (f)-[:FROM_SOURCE]->(s)
    """)

    # 3A: No matching keyword (nonsense)
    res_no_match = queries.brain_ask(TENANT_STRESS, question="supercalifragilisticexpialidocious quantum broccoli", embedding=None)
    assert res_no_match["refused"] is True
    assert res_no_match["grounded"] is False
    assert res_no_match["answer"] is None
    assert res_no_match["citations"] == []
    assert "No verified, healthy, non-stale facts found" in res_no_match["refusal_reason"]
    print("\n  [OK] Non-matching keyword query returned structured refusal.")

    # 3B: Vector query with completely unrelated / orthogonal embedding and threshold=0.75
    unrelated_text = "Astrophysical spectroscopy of extrasolar planetary atmospheres."
    unrelated_vec = generate_embedding(unrelated_text)
    res_low_conf = queries.brain_ask(
        TENANT_STRESS,
        question="Tell me about exoplanet spectroscopy",
        embedding=unrelated_vec,
        threshold=0.75
    )
    assert res_low_conf["refused"] is True, "Expected refusal when similarity is below confidence threshold"
    assert res_low_conf["grounded"] is False
    assert res_low_conf["answer"] is None
    assert res_low_conf["citations"] == []
    assert "confidence threshold" in res_low_conf["refusal_reason"].lower()
    print(f"  [OK] Low confidence vector query (< 0.75) returned structured refusal: {res_low_conf['refusal_reason']}")

    # 3C: Vector query with matching embedding and threshold=0.75 -> accepted
    res_high_conf = queries.brain_ask(
        TENANT_STRESS,
        question="coffee machine schedule",
        embedding=vec,
        threshold=0.75
    )
    assert res_high_conf["grounded"] is True
    assert res_high_conf["refused"] is False
    assert len(res_high_conf["citations"]) >= 1
    assert res_high_conf["citations"][0]["similarity"] >= 0.75
    print(f"  [OK] High confidence vector query (similarity={res_high_conf['citations'][0]['similarity']}) accepted.")


# =========================================================================
# TEST 4: Citation Provenance (Fact IDs, Quotes, Author Tiers, Team)
# =========================================================================

def test_citation_provenance_and_author_tiers():
    """
    Test 4:
    - Verify citation provenance across 3 distinct author tiers: 'root', 'established', 'new'.
    - Check exact Fact ID, text quote, source title, url, version, owner team, and author tier.
    """
    clean_graph(TENANT_STRESS)
    g = queries.get_graph(TENANT_STRESS, "commons")

    # Create 3 facts, one for each author tier
    tiers_data = [
        {
            "id": "fact_tier_root",
            "key": "policy.root_governance",
            "text": "Root board policy: Annual financial audits are strictly mandatory.",
            "quote": "Annual financial audits are strictly mandatory",
            "tier": "root",
            "team": "Executive Board",
            "src": "Governance Charter 2026",
            "version": "1.0"
        },
        {
            "id": "fact_tier_established",
            "key": "ops.established_guideline",
            "text": "Established operations guideline: Deployments require dual code reviewer approval.",
            "quote": "Deployments require dual code reviewer approval",
            "tier": "established",
            "team": "Engineering Management",
            "src": "Deployment Standard SOP",
            "version": "2.4"
        },
        {
            "id": "fact_tier_new",
            "key": "webhook.new_ingest",
            "text": "External partner ingest note: Partner webhook endpoint timeout is set to 5000 milliseconds.",
            "quote": "Partner webhook endpoint timeout is set to 5000 milliseconds",
            "tier": "new",
            "team": "Partner Integrations",
            "src": "Partner Integration Spec",
            "version": "0.9-beta"
        }
    ]

    print("\n--- [TEST 4] Seeding Multi-Tier Facts & Verifying Citation Provenance ---")
    for t_item in tiers_data:
        vec = generate_embedding(t_item["text"])
        vec_str = ", ".join(str(x) for x in vec)
        cypher = f"""
        CREATE (s:Source {{
            id: 'src_{t_item["id"]}',
            title: '{t_item["src"]}',
            url: 'https://docs.acme.corp/{t_item["id"]}',
            kind: 'DOC',
            version: '{t_item["version"]}'
        }})
        CREATE (t:Team {{id: 'team_{t_item["id"]}', name: '{t_item["team"]}'}})
        CREATE (t)-[:OWNS]->(s)
        CREATE (f:Fact:MemoryNode {{
            id: '{t_item["id"]}',
            key: '{t_item["key"]}',
            text: '{t_item["text"]}',
            quote: '{t_item["quote"]}',
            value: 'verified',
            status: 'HEALTHY',
            stale: false,
            author_tier: '{t_item["tier"]}',
            embedding: vecf32([{vec_str}])
        }})
        CREATE (f)-[:FROM_SOURCE]->(s)
        """
        g.query(cypher)

    for t_item in tiers_data:
        vec = generate_embedding(t_item["text"])
        res = queries.brain_ask(TENANT_STRESS, question=f"Query {t_item['key']}", embedding=vec, k=1)
        assert res["grounded"] is True
        assert len(res["citations"]) == 1
        cit = res["citations"][0]

        # Verify exact citation fields
        assert cit["fact_id"] == t_item["id"], f"Expected fact_id {t_item['id']}, got {cit['fact_id']}"
        assert cit["quote"] == t_item["quote"], f"Expected quote '{t_item['quote']}', got '{cit['quote']}'"
        assert cit["author_tier"] == t_item["tier"], f"Expected tier '{t_item['tier']}', got '{cit['author_tier']}'"
        assert cit["source_title"] == t_item["src"]
        assert cit["owner_team"] == t_item["team"]
        assert cit["version"] == t_item["version"]
        assert cit["stale"] is False
        assert cit["similarity"] >= 0.99
        print(f"  [OK] Citation provenance verified for {t_item['id']}: tier={cit['author_tier']}, quote='{cit['quote'][:35]}...', team='{cit['owner_team']}'")


# =========================================================================
# TEST 5: Concurrency Stress Test: 100 Queries across 10 Threads under Active Fact Mutation & Supersede
# =========================================================================

def test_concurrent_100_queries_under_mutation():
    """
    Test 5:
    - Concurrently run 100 brain_ask queries across 10 threads while facts are being added and superseded.
    - Measure throughput, latencies (p50, p95, p99), errors, and invariant safety.
    - Verify zero corrupted citations, zero unhandled exceptions, and that superseded facts are NEVER cited as healthy.
    """
    clean_graph(TENANT_STRESS)
    g = queries.get_graph(TENANT_STRESS, "commons")

    # 1. Seed initial base corpus of 10 facts
    base_facts = []
    print("\n--- [TEST 5] Concurrency Stress Test: 100 Queries / 10 Threads during Fact Add & Supersede ---")
    for i in range(10):
        fid = f"fact_base_{i}"
        fkey = f"policy.base_{i}"
        ftext = f"Acme enterprise operational procedure {i}: compliance threshold established at level {i*10}."
        fquote = f"compliance threshold established at level {i*10}"
        vec = generate_embedding(ftext)
        vec_str = ", ".join(str(x) for x in vec)
        cypher = f"""
        CREATE (s:Source {{id: 'src_{fid}', title: 'Standard Operating Procedure {i}', url: 'https://kb/sop_{i}', kind: 'DOC', version: '1.0'}})
        CREATE (t:Team {{id: 'team_{fid}', name: 'Operations Team {i % 3}'}})
        CREATE (t)-[:OWNS]->(s)
        CREATE (f:Fact:MemoryNode {{
            id: '{fid}',
            key: '{fkey}',
            text: '{ftext}',
            value: '{i*10}',
            quote: '{fquote}',
            status: 'HEALTHY',
            stale: false,
            author_tier: 'established',
            embedding: vecf32([{vec_str}])
        }})
        CREATE (f)-[:FROM_SOURCE]->(s)
        """
        g.query(cypher)
        base_facts.append({"id": fid, "key": fkey, "text": ftext, "vec": vec})

    stop_mutation_event = threading.Event()
    mutation_stats = {"adds": 0, "supersedes": 0, "errors": []}

    # Background Mutator 1: Add new facts
    def fact_adder():
        add_counter = 0
        while not stop_mutation_event.is_set():
            add_counter += 1
            fid = f"fact_dynamic_add_{add_counter}_{int(time.time()*1000)%10000}"
            fkey = f"dynamic.key_{add_counter}"
            ftext = f"Dynamic policy update number {add_counter}: active verification required."
            vec = generate_embedding(ftext)
            vec_str = ", ".join(str(x) for x in vec)
            try:
                g.query(f"""
                CREATE (s:Source {{id: 'src_{fid}', title: 'Dynamic Source {add_counter}', url: 'https://kb/dyn', kind: 'DOC', version: '1.0'}})
                CREATE (f:Fact:MemoryNode {{
                    id: '{fid}',
                    key: '{fkey}',
                    text: '{ftext}',
                    value: '{add_counter}',
                    quote: 'active verification required',
                    status: 'HEALTHY',
                    stale: false,
                    author_tier: 'new',
                    embedding: vecf32([{vec_str}])
                }})
                CREATE (f)-[:FROM_SOURCE]->(s)
                """)
                mutation_stats["adds"] += 1
            except Exception as e:
                mutation_stats["errors"].append(f"Add error: {e}")
            time.sleep(0.01)

    # Background Mutator 2: Supersede existing facts
    superseded_old_ids = set()
    superseded_times: Dict[str, float] = {}
    supersede_lock = threading.Lock()

    def fact_superseder():
        sup_counter = 0
        while not stop_mutation_event.is_set():
            sup_counter += 1
            target_fact = base_facts[sup_counter % len(base_facts)]
            new_fid = f"fact_superseded_{target_fact['id']}_v{sup_counter}"
            new_text = f"SUPERSEDED: Acme policy {target_fact['key']} revised to updated level {sup_counter * 50}."
            try:
                sup_res = queries.supersede_with_review(
                    TENANT_STRESS,
                    old_fact_key=target_fact["key"],
                    new_fact_id=new_fid,
                    new_text=new_text,
                    new_value=str(sup_counter * 50),
                    author="compliance_officer"
                )
                if sup_res.get("success"):
                    t_sup = time.perf_counter()
                    with supersede_lock:
                        superseded_old_ids.add(target_fact["id"])
                        if target_fact["id"] not in superseded_times:
                            superseded_times[target_fact["id"]] = t_sup
                    mutation_stats["supersedes"] += 1
            except Exception as e:
                mutation_stats["errors"].append(f"Supersede error: {e}")
            time.sleep(0.02)

    # Start background mutators
    t_adder = threading.Thread(target=fact_adder, daemon=True)
    t_superseder = threading.Thread(target=fact_superseder, daemon=True)
    t_adder.start()
    t_superseder.start()

    # Query Worker: 100 queries across 10 threads
    total_queries = 100
    num_threads = 10
    latencies_ms: List[float] = []
    query_records: List[Dict[str, Any]] = []
    query_lock = threading.Lock()

    def run_query(query_idx: int):
        target = base_facts[query_idx % len(base_facts)]
        is_vector = (query_idx % 2 == 0)
        t0 = time.perf_counter()
        if is_vector:
            res = queries.brain_ask(
                TENANT_STRESS,
                question=f"Operational compliance query {query_idx}",
                embedding=target["vec"],
                k=3,
                threshold=0.0
            )
        else:
            res = queries.brain_ask(
                TENANT_STRESS,
                question=f"compliance threshold operational procedure {query_idx % 10}",
                embedding=None,
                k=3
            )
        t_finish = time.perf_counter()
        lat = (t_finish - t0) * 1000
        with query_lock:
            latencies_ms.append(lat)
            query_records.append({
                "query_idx": query_idx,
                "t_start": t0,
                "t_finish": t_finish,
                "res": res
            })

    t_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(run_query, i) for i in range(total_queries)]
        for f in as_completed(futures):
            f.result()
    total_duration = time.perf_counter() - t_start

    # Stop background mutators
    stop_mutation_event.set()
    t_adder.join(timeout=1.0)
    t_superseder.join(timeout=1.0)

    # 4. Verify invariants and assertions
    assert len(query_records) == total_queries, f"Expected {total_queries} results, got {len(query_records)}"

    stale_or_superseded_leaks = 0
    grounded_count = 0
    refused_count = 0
    valid_citation_count = 0

    for record in query_records:
        res = record["res"]
        t_qstart = record["t_start"]
        assert "refused" in res and "grounded" in res
        if res["grounded"]:
            grounded_count += 1
            for cit in res["citations"]:
                assert "fact_id" in cit
                assert "quote" in cit
                assert "author_tier" in cit
                assert "similarity" in cit
                assert cit["stale"] is False, f"Stale fact leaked in citation: {cit}"
                cid = cit["fact_id"]
                with supersede_lock:
                    if cid in superseded_times:
                        t_sup = superseded_times[cid]
                        # Only considered a leak if query started strictly after supersede finished
                        if t_qstart > t_sup:
                            stale_or_superseded_leaks += 1
                valid_citation_count += 1
        elif res["refused"]:
            refused_count += 1
            assert res["citations"] == []
            assert res["answer"] is None

    # Post-concurrency verification: ensure none of the superseded facts are cited now
    print(f"\n  [Post-Concurrency Check] Verifying {len(superseded_old_ids)} superseded facts are strictly excluded...")
    post_supersede_leaks = 0
    for old_id in superseded_old_ids:
        post_res = queries.brain_ask(TENANT_STRESS, question=f"compliance threshold {old_id}", k=5)
        if post_res["grounded"]:
            c_ids = [c["fact_id"] for c in post_res["citations"]]
            if old_id in c_ids:
                post_supersede_leaks += 1

    # Performance metrics
    lats_sorted = sorted(latencies_ms)
    p50 = statistics.median(lats_sorted)
    p95 = lats_sorted[int(len(lats_sorted) * 0.95)]
    p99 = lats_sorted[int(len(lats_sorted) * 0.99)]
    min_lat = min(lats_sorted)
    max_lat = max(lats_sorted)
    qps = total_queries / total_duration

    print("\n================================================================================")
    print("--- CONCURRENCY STRESS TEST RESULTS ---")
    print("================================================================================")
    print(f"  Total Queries Executed:       {total_queries} (across {num_threads} concurrent threads)")
    print(f"  Total Duration:               {total_duration:.3f} s")
    print(f"  Throughput (Queries/sec):     {qps:.2f} QPS")
    print(f"  Latency Min:                  {min_lat:.2f} ms")
    print(f"  Latency p50 (Median):         {p50:.2f} ms")
    print(f"  Latency p95:                  {p95:.2f} ms")
    print(f"  Latency p99:                  {p99:.2f} ms")
    print(f"  Latency Max:                  {max_lat:.2f} ms")
    print("--------------------------------------------------------------------------------")
    print(f"  Background Fact Additions:    {mutation_stats['adds']}")
    print(f"  Background Supersedes:        {mutation_stats['supersedes']}")
    print(f"  Background Mutator Errors:    {len(mutation_stats['errors'])}")
    print(f"  Grounded Queries:             {grounded_count}")
    print(f"  Refused Queries:              {refused_count}")
    print(f"  Valid Citations Verified:     {valid_citation_count}")
    print(f"  Race Condition Leaks:         {stale_or_superseded_leaks} (Expected: 0)")
    print(f"  Post-Concurrency Leaks:       {post_supersede_leaks} (Expected: 0)")
    print("================================================================================\n")

    assert stale_or_superseded_leaks == 0, f"Found {stale_or_superseded_leaks} race condition leaks in citations!"
    assert post_supersede_leaks == 0, f"Found {post_supersede_leaks} post-concurrency leaks in citations!"
    assert len(mutation_stats["errors"]) == 0, f"Mutator errors encountered: {mutation_stats['errors']}"
    assert grounded_count + refused_count == total_queries


def run_all_benchmarks() -> Dict[str, Any]:
    test_vector_and_keyword_retrieval()
    test_stale_facts_strictly_excluded()
    test_structured_refusal_and_threshold()
    test_citation_provenance_and_author_tiers()
    test_concurrent_100_queries_under_mutation()
    return {"status": "ALL_PASS"}


if __name__ == "__main__":
    import json
    import os

    test_vector_and_keyword_retrieval()
    test_stale_facts_strictly_excluded()
    test_structured_refusal_and_threshold()
    test_citation_provenance_and_author_tiers()
    test_concurrent_100_queries_under_mutation()

    summary = {
        "track": "T03-5",
        "name": "Answers with Clear Sources (brain_ask query & citation enforcement)",
        "timestamp_ms": int(time.time() * 1000),
        "status": "PASS",
        "tests": {
            "test_1_vector_and_keyword_retrieval": {"status": "PASS"},
            "test_2_stale_facts_strictly_excluded": {"status": "PASS"},
            "test_3_structured_refusal_and_threshold": {"status": "PASS"},
            "test_4_citation_provenance_and_author_tiers": {"status": "PASS"},
            "test_5_concurrency_under_mutation": {"status": "PASS", "queries": 100, "threads": 10}
        }
    }

    bench_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bench"))
    os.makedirs(bench_dir, exist_ok=True)
    out_path = os.path.join(bench_dir, "t03_5_brain_ask_stress_results.json")
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\n[Artifact Saved] Benchmark results written to {out_path}")
    print("\n>>> ALL 5 T03-5 STRESS TESTS COMPLETED SUCCESSFULLY! <<<")
