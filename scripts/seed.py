"""
Quorum Synthetic Seed Data Generator (scripts/seed.py)

Author: Janani (Person 1 - Graph & FalkorDB)
Generates:
1. Acme Support Scenario:
   - Root 30-day refund policy fact
   - Poisoned webhook 90-day fact
   - 3 refund tickets/claims
   - Agents across all 3 tiers (root, established, new)
   - Procedures with seeded scores (for adaptive ranking demo)
2. Company Brain Corpus (Track 03):
   - 25 items across Sources (DOC, TICKET, CHAT)
   - 12 People, 4 Teams, 3 Projects, 3 Systems
   - Ownership, authorship, and topic resolution links
3. Precomputed 384-dim normalized embeddings so 'docker compose up' requires zero external downloads!
"""

import os
import sys

_current_dir = os.path.dirname(os.path.abspath(__file__))
_root_dir = os.path.abspath(os.path.join(_current_dir, ".."))
if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)

import hashlib
import numpy as np
from typing import List
from graph import queries


def generate_embedding(text: str) -> List[float]:
    """
    Deterministic 384-dim normalized pseudo-embedding derived from SHA-256 hash.
    Ensures identical vectors across runs without downloading heavy weights.
    """
    rng = np.random.RandomState(int(hashlib.sha256(text.encode()).hexdigest()[:8], 16))
    vec = rng.randn(384)
    norm = np.linalg.norm(vec)
    return (vec / norm).tolist()


def seed_acme_commons(tenant_id: str = "acme"):
    print(f"\n[Seed] Generating Acme Commons graph for tenant '{tenant_id}'...")
    g = queries.get_graph(tenant_id, "commons")
    try:
        g.delete()
    except Exception:
        pass
    queries.ensure_indexes(tenant_id)

    # 1. Agents with Trust Tiers
    agents = [
        {"id": "agent_root_auditor", "name": "Compliance Root Auditor", "role": "Auditor", "tier": "root", "tier_weight": 10.0},
        {"id": "agent_scout", "name": "Ingest Scout", "role": "Scout", "tier": "established", "tier_weight": 3.0},
        {"id": "agent_analyst", "name": "Policy Analyst", "role": "Analyst", "tier": "established", "tier_weight": 3.0},
        {"id": "agent_refunder", "name": "Refund Dispatcher", "role": "Refunder", "tier": "established", "tier_weight": 3.0},
        {"id": "agent_comms", "name": "Communications Agent", "role": "Comms", "tier": "new", "tier_weight": 0.5},
        {"id": "agent_webhook", "name": "External Webhook Ingest", "role": "Webhook", "tier": "new", "tier_weight": 0.5},
    ]
    for a in agents:
        g.query(
            "CREATE (:Agent {id: $id, name: $name, role: $role, tier: $tier, tier_weight: $tier_weight})",
            a
        )

    # 2. Procedures with Seeded Scores (Adaptive Ranking Demo)
    procedures = [
        {
            "id": "proc_standard_refund",
            "task_type": "REFUND",
            "steps": ["verify_receipt", "check_30d_window", "dispatch_payment"],
            "uses": 24, "successes": 23, "failures": 1
        },
        {
            "id": "proc_extended_refund",
            "task_type": "REFUND",
            "steps": ["accept_unverified_claim", "dispatch_payment"],
            "uses": 8, "successes": 1, "failures": 7
        },
    ]
    for p in procedures:
        g.query(
            "CREATE (:Procedure {id: $id, task_type: $task_type, steps: $steps, "
            "uses: $uses, successes: $successes, failures: $failures})",
            p
        )

    # 3. Track 03: Company Brain Corpus
    # 4 Teams
    teams = [
        ("team_support", "Customer Support Operations"),
        ("team_compliance", "Legal & Compliance"),
        ("team_eng", "Core Platform Engineering"),
        ("team_finance", "Finance & Accounting"),
    ]
    for tid, tname in teams:
        g.query("CREATE (:Team {id: $id, name: $name})", {"id": tid, "name": tname})

    # 12 People across 4 Teams
    people = [
        ("p_alice", "Alice Walker", "alice@acme.corp", "team_support"),
        ("p_bob", "Bob Chen", "bob@acme.corp", "team_support"),
        ("p_charlie", "Charlie Miller", "charlie@acme.corp", "team_support"),
        ("p_dana", "Dana Vance", "dana@acme.corp", "team_compliance"),
        ("p_elena", "Elena Rostova", "elena@acme.corp", "team_compliance"),
        ("p_frank", "Frank Gomez", "frank@acme.corp", "team_compliance"),
        ("p_grace", "Grace Hopper", "grace@acme.corp", "team_eng"),
        ("p_henry", "Henry Ford", "henry@acme.corp", "team_eng"),
        ("p_ivy", "Ivy Brooks", "ivy@acme.corp", "team_eng"),
        ("p_jack", "Jack Ryan", "jack@acme.corp", "team_finance"),
        ("p_karen", "Karen White", "karen@acme.corp", "team_finance"),
        ("p_leo", "Leo Tolstoy", "leo@acme.corp", "team_finance"),
    ]
    for pid, pname, pmail, ptid in people:
        g.query(
            "CREATE (p:Person {id: $id, name: $name, email: $email}) "
            "WITH p MATCH (t:Team {id: $tid}) CREATE (p)-[:MEMBER_OF]->(t)",
            {"id": pid, "name": pname, "email": pmail, "tid": ptid}
        )

    # 3 Topics
    topics = [
        ("top_refunds", "Refund & Cancellation Policies"),
        ("top_fraud", "Fraud Detection & Risk"),
        ("top_payments", "Payment Gateway Architecture"),
    ]
    for top_id, top_name in topics:
        g.query("CREATE (:Topic {id: $id, name: $name})", {"id": top_id, "name": top_name})

    # 3 Projects
    projects = [
        ("proj_portal", "Customer Self-Service Portal", "ACTIVE", "Self-service returns, order status, and customer dispute resolution interface."),
        ("proj_gateway", "Next-Gen Payment Gateway", "ACTIVE", "High-throughput fault-tolerant transaction processing and refund orchestration engine."),
        ("proj_compliance_vault", "Regulatory Compliance Vault", "ACTIVE", "Immutable audit logging, AML tracking, and compliance certification repository."),
    ]
    for prid, prname, prstatus, prdesc in projects:
        g.query(
            "CREATE (:Project {id: $id, name: $name, status: $status, description: $desc, created_at: timestamp()})",
            {"id": prid, "name": prname, "status": prstatus, "desc": prdesc}
        )

    # Team ownership of Projects (OWNS)
    proj_owners = [
        ("team_support", "proj_portal", "sponsorship"),
        ("team_eng", "proj_gateway", "technical_ownership"),
        ("team_compliance", "proj_compliance_vault", "governance"),
    ]
    for tid, prid, lvl in proj_owners:
        g.query(
            "MATCH (t:Team {id: $tid}), (pr:Project {id: $prid}) "
            "CREATE (t)-[:OWNS {since: timestamp(), level: $lvl}]->(pr)",
            {"tid": tid, "prid": prid, "lvl": lvl}
        )

    # 3 Systems
    systems = [
        ("sys_zendesk", "Zendesk Helpdesk Suite", "HELPDESK", "CRITICAL", "Primary customer inquiry and escalation ticketing system."),
        ("sys_stripe", "Stripe Payment Engine", "PAYMENT_GATEWAY", "CRITICAL", "Card processing, refund disbursement, and payout infrastructure."),
        ("sys_slack", "Enterprise Slack Collaboration", "MESSAGING", "INTERNAL", "Real-time incident response, cross-functional channels, and alerting."),
    ]
    for sys_id, sys_name, sys_kind, sys_tier, sys_desc in systems:
        g.query(
            "CREATE (:System {id: $id, name: $name, kind: $kind, tier: $tier, description: $desc, created_at: timestamp()})",
            {"id": sys_id, "name": sys_name, "kind": sys_kind, "tier": sys_tier, "desc": sys_desc}
        )

    # Team ownership of Systems (OWNS)
    sys_owners = [
        ("team_support", "sys_zendesk", "operational"),
        ("team_eng", "sys_stripe", "operational"),
        ("team_eng", "sys_slack", "administrative"),
    ]
    for tid, sys_id, lvl in sys_owners:
        g.query(
            "MATCH (t:Team {id: $tid}), (sys:System {id: $sys_id}) "
            "CREATE (t)-[:OWNS {since: timestamp(), level: $lvl}]->(sys)",
            {"tid": tid, "sys_id": sys_id, "lvl": lvl}
        )

    # Systems support Projects (SUPPORTS)
    sys_supports = [
        ("sys_zendesk", "proj_portal", "HIGH", "99.9%"),
        ("sys_stripe", "proj_gateway", "CRITICAL", "99.99%"),
        ("sys_slack", "proj_compliance_vault", "STANDARD", "99.5%"),
    ]
    for sys_id, prid, crit, sla in sys_supports:
        g.query(
            "MATCH (sys:System {id: $sys_id}), (pr:Project {id: $prid}) "
            "CREATE (sys)-[:SUPPORTS {criticality: $crit, uptime_sla: $sla, since: timestamp()}]->(pr)",
            {"sys_id": sys_id, "prid": prid, "crit": crit, "sla": sla}
        )

    # 4. 25 Synthetic Corpus Items across Sources (9 DOC, 8 TICKET, 8 CHAT)
    # Format: (id, kind, title, author, team, topic, resolver_if_ticket, fact_key, fact_val)
    corpus_items = [
        # --- DOCS (9) ---
        ("src_doc_refund_policy_v1", "DOC", "Global Customer Refund Policy", "p_dana", "team_compliance", "top_refunds", None, "refund.window_days", "30"),
        ("src_doc_301", "DOC", "Payment Gateway Runbook", "p_ivy", "team_eng", "top_payments", None, "payment.retry_limit", "3"),
        ("src_doc_302", "DOC", "Fraud Evaluation Guidelines", "p_dana", "team_compliance", "top_fraud", None, "fraud.risk_score_max", "85"),
        ("src_doc_303", "DOC", "Customer Support Escalation Matrix", "p_bob", "team_support", "top_refunds", None, "support.escalation_sla_hrs", "4"),
        ("src_doc_304", "DOC", "Accounting Reconciliation SOP", "p_leo", "team_finance", "top_payments", None, "reconciliation.frequency", "daily"),
        ("src_doc_305", "DOC", "SLA & Customer Rights Charter", "p_elena", "team_compliance", "top_refunds", None, "sla.guaranteed_uptime_pct", "99.9"),
        ("src_doc_306", "DOC", "Dispute & Chargeback Resolution Guide", "p_frank", "team_compliance", "top_fraud", None, "dispute.evidence_window_days", "7"),
        ("src_doc_307", "DOC", "API Rate Limiting & Webhook Security Architecture", "p_grace", "team_eng", "top_payments", None, "gateway.rate_limit_rps", "500"),
        ("src_doc_308", "DOC", "Annual Financial & Compliance Audit Manual", "p_karen", "team_finance", "top_refunds", None, "audit.retention_years", "7"),

        # --- TICKETS (8) ---
        ("src_ticket_101", "TICKET", "Refund inquiry for order #8841", "p_alice", "team_support", "top_refunds", "p_alice", "ticket.order_8841.status", "processed"),
        ("src_ticket_102", "TICKET", "Chargeback dispute charge #9921", "p_bob", "team_support", "top_fraud", "p_bob", "ticket.charge_9921.status", "contested"),
        ("src_ticket_103", "TICKET", "Duplicate payment on invoice #112", "p_jack", "team_finance", "top_payments", "p_jack", "ticket.invoice_112.refund", "issued"),
        ("src_ticket_104", "TICKET", "Refund window exception requested", "p_charlie", "team_support", "top_refunds", "p_alice", "ticket.exception_window.granted", "true"),
        ("src_ticket_105", "TICKET", "API timeout during gateway batch", "p_grace", "team_eng", "top_payments", "p_grace", "ticket.api_timeout.mitigated", "true"),
        ("src_ticket_106", "TICKET", "International payment settlement failure #3412", "p_jack", "team_finance", "top_payments", "p_karen", "ticket.intl_settle_3412.resolved", "true"),
        ("src_ticket_107", "TICKET", "Suspected carding attack on checkout session #4490", "p_frank", "team_compliance", "top_fraud", "p_bob", "ticket.carding_4490.blocked", "true"),
        ("src_ticket_108", "TICKET", "Overdue customer payout claim for return #7719", "p_alice", "team_support", "top_refunds", "p_alice", "ticket.payout_7719.dispatched", "true"),

        # --- CHATS (8) ---
        ("src_chat_201", "CHAT", "Slack #ops: Gateway retry parameters", "p_henry", "team_eng", "top_payments", None, "chat.ops.retry_param", "exponential_backoff"),
        ("src_chat_202", "CHAT", "Slack #legal: 30-day compliance review", "p_elena", "team_compliance", "top_refunds", None, "chat.legal.review_status", "compliant"),
        ("src_chat_203", "CHAT", "Slack #support: Handling customer delays", "p_alice", "team_support", "top_refunds", None, "chat.support.delay_guidance", "proactive_voucher"),
        ("src_chat_204", "CHAT", "Slack #finance: Monthly refund totals", "p_karen", "team_finance", "top_refunds", None, "chat.finance.refund_mtd", "$42,300"),
        ("src_chat_205", "CHAT", "Slack #security: Anomaly in return logs", "p_frank", "team_compliance", "top_fraud", None, "chat.security.anomaly_rate", "0.02%"),
        ("src_chat_206", "CHAT", "Slack #eng: Webhook latency spikes and circuit breakers", "p_grace", "team_eng", "top_payments", None, "chat.eng.circuit_breaker_threshold", "5_failures"),
        ("src_chat_207", "CHAT", "Slack #leadership: Policy alignment on exception approvals", "p_dana", "team_compliance", "top_refunds", None, "chat.lead.approval_tier", "manager_only"),
        ("src_chat_208", "CHAT", "Slack #billing: Daily reconciliation variance triage", "p_leo", "team_finance", "top_payments", None, "chat.billing.variance_cents", "0"),
    ]

    for sid, kind, title, author, team, topic, resolver, fkey, fval in corpus_items:
        vec = generate_embedding(title)
        fid = f"fact_{sid}" if sid != "src_doc_refund_policy_v1" else "fact_refund_policy_30d"

        # Create Source & Fact with normalized 384-dim embeddings
        g.query(
            """
            CREATE (s:Source {
                id: $id, kind: $kind, title: $title, url: 'https://internal/' + $id,
                version: '1.0', origin: 'internal_wiki', embedding: vecf32($vec),
                created_at: timestamp()
            })
            CREATE (f:Fact:MemoryNode {
                id: $fid, key: $fkey, value: $fval, text: $title,
                status: 'HEALTHY', embedding: vecf32($vec),
                valid_from: timestamp(), valid_to: null, stale: false,
                source_ref: $id, quote: $title
            })
            CREATE (f)-[:FROM_SOURCE]->(s)
            """,
            {"id": sid, "kind": kind, "title": title, "vec": vec, "fid": fid, "fkey": fkey, "fval": fval}
        )

        # Connect Authorship (AUTHORS & AUTHORED), Ownership (OWNS), and Topic (ABOUT)
        g.query(
            """
            MATCH (s:Source {id: $sid}), (p:Person {id: $author}), (t:Team {id: $team}), (top:Topic {id: $topic})
            CREATE (p)-[:AUTHORS {role: 'author', created_at: timestamp()}]->(s)
            CREATE (p)-[:AUTHORED {role: 'author', created_at: timestamp()}]->(s)
            CREATE (t)-[:OWNS {since: timestamp(), level: 'primary'}]->(s)
            CREATE (s)-[:ABOUT]->(top)
            """,
            {"sid": sid, "author": author, "team": team, "topic": topic}
        )

        # Connect Ticket Resolution (RESOLVES & RESOLVED)
        if resolver:
            g.query(
                """
                MATCH (s:Source {id: $sid}), (p:Person {id: $resolver})
                CREATE (p)-[:RESOLVES {resolution: 'RESOLVED', resolved_at: timestamp()}]->(s)
                CREATE (p)-[:RESOLVED {resolution: 'RESOLVED', resolved_at: timestamp()}]->(s)
                """,
                {"sid": sid, "resolver": resolver}
            )

    # Key Source documents support Projects (SUPPORTS)
    src_proj_supports = [
        ("src_doc_refund_policy_v1", "proj_portal", "FOUNDATIONAL"),
        ("src_doc_301", "proj_gateway", "ARCHITECTURE"),
        ("src_doc_302", "proj_compliance_vault", "REGULATORY"),
    ]
    for sid, prid, rel in src_proj_supports:
        g.query(
            """
            MATCH (s:Source {id: $sid}), (pr:Project {id: $prid})
            CREATE (s)-[:SUPPORTS {relevance: $rel, linked_at: timestamp()}]->(pr)
            """,
            {"sid": sid, "prid": prid, "rel": rel}
        )

    # 5. Connect Decisions and Actions for Provenance and SUPPORTS verification
    dec_text = "Acme standard return threshold decision confirmed at 30 days."
    g.query(
        """
        MATCH (f:Fact {id: 'fact_refund_policy_30d'})
        CREATE (dec:Conclusion:Decision:MemoryNode {
            id: 'dec_standard_window_30d', text: $dec_text, status: 'HEALTHY',
            stale: false, owner_team: 'Customer Support Operations', created_at: timestamp()
        })
        CREATE (act:Action:MemoryNode {
            id: 'act_demo_refund_verify', type: 'REFUND_VERIFICATION', status: 'STAGED',
            gate: 'PASSED', reversibility: 'COMPENSABLE', executed: false, created_at: timestamp()
        })
        CREATE (f)-[:LEADS_TO]->(dec)
        CREATE (dec)-[:LEADS_TO]->(act)
        CREATE (f)-[:SUPPORTS {confidence: 1.0, weight: 1.0, timestamp: timestamp()}]->(dec)
        CREATE (f)-[:SUPPORTS {confidence: 1.0, weight: 1.0, timestamp: timestamp()}]->(act)
        """,
        {"dec_text": dec_text}
    )

    # 6. Seed Action #1 (Irreversible Customer Email for Damage Control Demo)
    g.query("""
    MATCH (dec:Conclusion {id: 'dec_standard_window_30d'})
    CREATE (act:Action:MemoryNode {
        id: 'act_email_001',
        label: 'Send customer refund approval notice',
        type: 'SEND_CUSTOMER_EMAIL',
        reversibility: 'IRREVERSIBLE',
        executed: true,
        confirmed: true,
        gate: 'PASSED',
        status: 'HEALTHY',
        text: 'Your refund of $150 has been approved under our new 90-day policy.',
        created_at: timestamp()
    })
    CREATE (dec)-[:LEADS_TO]->(act)
    """)
    print(f"[Seed] Successfully seeded Acme Commons graph for tenant '{tenant_id}'!")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Quorum Synthetic Seed Data Generator")
    parser.add_argument("--tenant", "-t", default="acme", help="Tenant ID (default: acme)")
    args = parser.parse_args()
    seed_acme_commons(args.tenant)

