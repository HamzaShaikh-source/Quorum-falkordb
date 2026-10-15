"""
tests/test_new_features.py

Verification tests for T02-1, T02-2, T03-4, T03-5 (in-memory via WSL FalkorDB).
Run: pytest tests/test_new_features.py -v
"""
import sys
import os
import time
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

# These tests require a live FalkorDB instance.
falkordb_available = False
try:
    from graph import queries
    db = queries.get_db()
    db.select_graph("probe_features").query("RETURN 1")
    db.select_graph("probe_features").delete()
    falkordb_available = True
except Exception:
    pass

skip_no_db = pytest.mark.skipif(
    not falkordb_available,
    reason="FalkorDB not running on localhost:6379"
)

TENANT = "test_features"


def setup_module(_module):
    if not falkordb_available:
        return
    teardown_module(_module)


def teardown_module(_module):
    if not falkordb_available:
        return
    for kind in ["commons", "agent_agent1:raw", "agent_agent2:raw"]:
        try:
            g = queries.get_db().select_graph(f"tenant_{TENANT}:{kind}")
            g.delete()
        except Exception:
            pass


# =========================================================================
# T02-1: Episodic, Semantic & Procedural Memory
# =========================================================================

class TestProceduralMemory:

    @skip_no_db
    def test_upsert_procedure(self):
        result = queries.upsert_procedure(
            TENANT,
            procedure_id="proc_refund_standard",
            name="Standard Refund Procedure",
            description="Handle refund requests within policy window",
            steps=["verify_purchase", "check_window", "issue_refund"],
            domain="support",
        )
        assert result["id"] == "proc_refund_standard"
        assert result["rank"] == pytest.approx(0.5, abs=0.01)

    @skip_no_db
    def test_rank_changes_after_success(self):
        """Rank climbs after success outcome (Laplace: 2/3 after 1 success)."""
        queries.upsert_procedure(
            TENANT,
            procedure_id="proc_rank_test",
            name="Rank Test Procedure",
            description="Test rank changes",
            steps=["step1", "step2"],
            domain="test",
        )
        initial = queries.rank_procedures(TENANT, domain="test")
        init_rank = next(
            (p["rank"] for p in initial if p["id"] == "proc_rank_test"), 0.5
        )
        assert 0.4 <= init_rank <= 0.6

        result = queries.score_procedure(TENANT, "proc_rank_test", "success")
        assert result["success"] is True
        assert result["rank"] > init_rank, "Rank should increase after success"
        assert result["rank"] == pytest.approx(2 / 3, abs=0.01)

    @skip_no_db
    def test_rank_drops_after_failure(self):
        """Rank drops after failure (Laplace: 1/3 after 1 failure)."""
        queries.upsert_procedure(
            TENANT,
            procedure_id="proc_fail_test",
            name="Failure Test",
            description="Test failure rank drop",
            steps=["step1"],
            domain="test",
        )
        result = queries.score_procedure(TENANT, "proc_fail_test", "failure")
        assert result["success"] is True
        assert result["rank"] == pytest.approx(1 / 3, abs=0.01)

    @skip_no_db
    def test_rank_procedures_ordering(self):
        """rank_procedures returns list sorted by rank desc."""
        ranked = queries.rank_procedures(TENANT)
        assert isinstance(ranked, list)
        if len(ranked) >= 2:
            ranks = [p["rank"] for p in ranked]
            assert ranks == sorted(ranks, reverse=True)

    @skip_no_db
    def test_record_episode(self):
        """Records messages into agent raw graph."""
        result = queries.record_episode(
            TENANT, agent_id="agent1", session_id="sess_001",
            message_id="msg_001", role="user",
            content="What is the refund policy?",
        )
        assert result["success"] is True
        queries.record_episode(
            TENANT, agent_id="agent1", session_id="sess_001",
            message_id="msg_002", role="assistant",
            content="Refunds are allowed within 30 days.",
        )


# =========================================================================
# T02-2: Persistent Conversation History
# =========================================================================

class TestPersistentHistory:

    @skip_no_db
    def test_close_session_writes_summary(self):
        """close_session writes summary Fact into commons graph."""
        for i in range(3):
            queries.record_episode(
                TENANT, agent_id="agent2", session_id="sess_resume_001",
                message_id=f"msg_r_{i}",
                role="user" if i % 2 == 0 else "assistant",
                content=f"Message {i} content here",
                ts_ms=int(time.time() * 1000) + i * 100,
            )
        result = queries.close_session(TENANT, "agent2", "sess_resume_001")
        assert result["success"] is True
        assert result["msg_count"] == 3
        assert "sess_resume_001" in result["summary_fact_id"]

    @skip_no_db
    def test_resume_agent_2_query_flow(self):
        """resume_agent returns both raw messages and commons summaries."""
        result = queries.resume_agent(TENANT, "agent2")
        assert result["resumed"] is True
        assert "recent_messages" in result
        assert "session_summaries" in result
        assert len(result["session_summaries"]) >= 1

    @skip_no_db
    def test_session_history_ordered(self):
        """session_history returns messages in chronological order."""
        history = queries.session_history(TENANT, "agent2", "sess_resume_001")
        assert isinstance(history, list)
        assert len(history) == 3
        timestamps = [m["ts"] for m in history]
        assert timestamps == sorted(timestamps)


# =========================================================================
# T03-4: Keeping Knowledge Current
# =========================================================================

class TestKeepingKnowledgeCurrent:

    @skip_no_db
    def test_supersede_propagates_stale(self):
        """Policy update marks old fact SUPERSEDED and propagates stale=true."""
        g = queries.get_graph(TENANT, "commons")
        g.query("""
        MERGE (:Fact:MemoryNode {
            id: 'f_policy_v3', key: 'refund_window_days',
            text: 'Refund policy: 30 day window', value: '30', status: 'HEALTHY'
        })
        """)
        g.query("""
        MERGE (:Decision {
            id: 'dec_30day_standard',
            text: 'Standard 30-day refund window decision',
            status: 'HEALTHY', stale: false
        })
        WITH 1 AS dummy
        MATCH (f:Fact {id: 'f_policy_v3'}), (d:Decision {id: 'dec_30day_standard'})
        MERGE (f)-[:LEADS_TO]->(d)
        """)

        result = queries.supersede_with_review(
            TENANT,
            old_fact_key="refund_window_days",
            new_fact_id="f_policy_v4",
            new_text="Refund policy: 45 day window (updated)",
            new_value="45",
            author="policy-bot",
        )
        assert result["success"] is True
        assert result["stale_propagated"] >= 1

    @skip_no_db
    def test_superseded_fact_status(self):
        g = queries.get_graph(TENANT, "commons")
        res = g.query(
            "MATCH (f:Fact {id: 'f_policy_v3'}) RETURN f.status"
        )
        if res.result_set:
            assert res.result_set[0][0] == "SUPERSEDED"

    @skip_no_db
    def test_new_fact_is_healthy_with_updated_value(self):
        g = queries.get_graph(TENANT, "commons")
        res = g.query("MATCH (f:Fact {id: 'f_policy_v4'}) RETURN f.status, f.value")
        assert res.result_set, "New fact v4 should exist"
        assert res.result_set[0][0] == "HEALTHY"
        assert res.result_set[0][1] == "45"


# =========================================================================
# T03-5: Answers with Clear Sources
# =========================================================================

class TestAnswersWithClearSources:

    @skip_no_db
    def test_brain_ask_refusal_on_no_match(self):
        """brain_ask refuses when no matching facts exist."""
        result = queries.brain_ask(
            TENANT,
            question="xyzzy completely unrelated nonsense",
        )
        assert result["refused"] is True
        assert result["grounded"] is False
        assert result["answer"] is None
        assert result["citations"] == []

    @skip_no_db
    def test_brain_ask_returns_grounded_answer(self):
        """brain_ask returns grounded answer with citations."""
        g = queries.get_graph(TENANT, "commons")
        g.query("""
        MERGE (:Fact:MemoryNode {
            id: 'f_refund_kb',
            key: 'refund_policy_text',
            text: 'Customers may request a refund within 45 days of purchase.',
            value: '45',
            status: 'HEALTHY',
            stale: false
        })
        """)
        result = queries.brain_ask(TENANT, question="What is the refund policy?")
        assert result["grounded"] is True
        assert result["refused"] is False
        assert result["answer"] is not None
        assert "refund" in result["answer"].lower()
        assert len(result["citations"]) >= 1

    @skip_no_db
    def test_brain_ask_stale_facts_excluded(self):
        """Stale facts must never appear in brain_ask citations."""
        g = queries.get_graph(TENANT, "commons")
        g.query("""
        MERGE (:Fact:MemoryNode {
            id: 'f_stale_only',
            key: 'stale_unique_xyz',
            text: 'stale_unique_xyz old obsolete policy.',
            status: 'HEALTHY',
            stale: true
        })
        """)
        result = queries.brain_ask(TENANT, question="stale_unique_xyz old obsolete policy")
        if result["grounded"]:
            cited_ids = [c["fact_id"] for c in result["citations"]]
            assert "f_stale_only" not in cited_ids
