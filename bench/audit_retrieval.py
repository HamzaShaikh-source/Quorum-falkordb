"""Reproducible audit probe; writes only the explicitly selected output artifact.

Use --repo for a clean baseline checkout. Measures the actual full MCP retrieval
service and REST context-preview path with real FalkorDB persistence separately.
"""
import argparse
import json
import os
import platform
import statistics
import sys
import time
import uuid
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--size", type=int, default=2000)
    args = parser.parse_args()
    sys.path[:0] = [str(args.repo / "backend"), str(args.repo)]
    os.environ.update(QUORUM_ENV="development", QUORUM_DEMO="0", QUORUM_PERSISTENCE="falkordb",
                      QUORUM_RATE_LIMIT_HTTP_PER_MIN="0", QUORUM_RATE_LIMIT_TOOLS_PER_MIN="0")
    from fastapi.testclient import TestClient
    from app import db
    from app.graph.queries import get_graph_store
    from app.main import app
    from app.models.common import NodeKind, Status
    from app.models.graph import GraphNode
    from app.services.mcp_read import retrieve_context

    tenant = "auditbench" + uuid.uuid4().hex[:8]
    token = f"token_{tenant}_human"
    store = get_graph_store(tenant)
    healthy = max(1, args.size // 20)
    try:
        for i in range(args.size):
            store.add_node(GraphNode(id=f"fact_{i:05}", kind=NodeKind.FACT, label=f"Refund policy {i}",
                                     text=f"Refund window is {30 + i % 60} days for product {i}",
                                     status=Status.HEALTHY if i < healthy else Status.QUARANTINED))
        start = time.perf_counter()
        store.commit()
        initial_persistence_ms = (time.perf_counter() - start) * 1000

        def measure(call, samples=15):
            start = time.perf_counter()
            first = call()
            cold = (time.perf_counter() - start) * 1000
            elapsed = []
            for _ in range(samples):
                start = time.perf_counter()
                call()
                elapsed.append((time.perf_counter() - start) * 1000)
            return {"cold_ms": cold, "warm_p50_ms": statistics.median(elapsed),
                    "warm_p95_ms": sorted(elapsed)[int(len(elapsed) * .95)], "samples": samples}, first

        service, result = measure(lambda: retrieve_context("refund window", token=token))
        client = TestClient(app)

        def preview():
            response = client.get("/api/context/preview", params={"query": "refund window"},
                                  headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 200, response.text
            return response.json()

        rest, _ = measure(preview, samples=5)
        report = {"engine": "FalkorDB persisted API graph + in-process ranking", "dataset_nodes": args.size,
                  "healthy_candidates": healthy, "concurrency": 1, "ranking": "default hybrid/local embeddings",
                  "hardware": {"platform": platform.platform(), "processor": platform.processor(), "python": platform.python_version(), "cpus": os.cpu_count()},
                  "initial_persistence_ms": initial_persistence_ms,
                  "full_retrieval_service": service, "rest_context_preview": rest,
                  "boundaries": {"service": "full retrieval service, ranking, baseline evidence and delivery tracking; excludes HTTP and commit",
                                 "rest": "FastAPI TestClient request through real middleware, auth, context comparison and diff sync; excludes TCP and external actions",
                                 "initial_persistence": "one initial commit only; separate from retrieval", "external_call_ms": "not applicable"},
                  "selected_ids": [row["id"] for row in result["context"]]}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        db.graph_for(tenant).delete()


if __name__ == "__main__":
    main()
