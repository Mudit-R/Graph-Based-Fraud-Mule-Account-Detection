"""Measure real Neo4j case projection and bounded search with isolated synthetic data."""
import argparse
import json
import math
import os
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter

from fastapi.testclient import TestClient

from src.api.case_neo4j import Neo4jCaseGraph
from src.api.case_review import create_app
from src.api.case_store import CaseStore


TOKEN = "isolated-neo4j-benchmark-token-only-used-by-test"
SHARED_DEVICE = "workload-device"


def fixture(number, payments):
    prefix = f"benchmark-{number:04}"
    connected = number < 12
    events = [{
        "transaction_id": f"payment-{index}",
        "customer_id": f"{prefix}-customer-{index}",
        "merchant_id": f"{prefix}-merchant-{index}",
        "device_id": SHARED_DEVICE if connected and index == 0 else f"{prefix}-device",
        "address_id": f"{prefix}-address",
        "amount": 10_000 + index * 100,
        "timestamp": 1_795_600_000 + number * 60 + index,
    } for index in range(payments)]
    return prefix, events


def measure(uri, case_count, payments, warm_runs):
    with TemporaryDirectory(prefix="cross-case-benchmark-") as directory:
        database = Path(directory) / "review.sqlite3"
        store = CaseStore(database)
        case_ids = []
        for number in range(case_count):
            batch, events = fixture(number, payments)
            case_id, = store.ingest(batch, events, {})["case_ids"]
            case_ids.append(case_id)

        graph = Neo4jCaseGraph(
            uri,
            os.getenv("FRAUD_TEST_NEO4J_USER", "neo4j"),
            os.environ["FRAUD_TEST_NEO4J_PASSWORD"],
            os.getenv("FRAUD_TEST_NEO4J_DATABASE", "neo4j"),
        )
        params = {"anchor_type": "device_id", "anchor_id": SHARED_DEVICE, "hops": 4, "limit": 200}
        path = f"/cases/{case_ids[0]}/related-graph"
        with TestClient(create_app(database, "benchmark-analyst", TOKEN, graph_backend=graph)) as client:
            cold_start = perf_counter()
            for attempt in range(1, 13):
                response = client.get(path, params=params, headers={"Authorization": "Bearer " + TOKEN})
                if response.is_success:
                    break
                if response.status_code != 503 or "catching up" not in response.text:
                    raise RuntimeError(f"Cross-case query failed: {response.status_code} {response.text}")
            else:
                raise RuntimeError("The graph projection did not catch up.")
            cold_ms = (perf_counter() - cold_start) * 1000
            result = response.json()
            actual_matches = {case["id"] for case in result["related_cases"]}
            if actual_matches != set(case_ids[1:12]):
                raise AssertionError(f"Unexpected result: {len(actual_matches)} related cases")
            durations = []
            for _ in range(warm_runs):
                start = perf_counter()
                response = client.get(path, params=params, headers={"Authorization": "Bearer " + TOKEN})
                durations.append((perf_counter() - start) * 1000)
                response.raise_for_status()
                if response.json() != result:
                    raise AssertionError("Repeated query returned different evidence.")

        return {
            "scope": "Synthetic test data; local FastAPI endpoint backed by a real Neo4j database",
            "indexed_cases": case_count,
            "indexed_payments": case_count * payments,
            "linked_cases_including_seed": 1 + len(actual_matches),
            "query_hops": result["hops"],
            "query_node_cap": params["limit"],
            "returned_nodes": len(result["nodes"]),
            "returned_edges": len(result["edges"]),
            "truncated": result["truncated"],
            "cold_projection_plus_search_ms": round(cold_ms, 2),
            "cold_requests": attempt,
            "warm_runs": warm_runs,
            "warm_p50_ms": round(median(durations), 2),
            "warm_p95_ms": round(sorted(durations)[math.ceil(.95 * warm_runs) - 1], 2),
            "warm_duration_ms": [round(item, 2) for item in durations],
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=int, default=240)
    parser.add_argument("--payments", type=int, default=6)
    parser.add_argument("--warm-runs", type=int, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 12 <= args.cases <= 500 or not 2 <= args.payments <= 8 or not 1 <= args.warm_runs <= 100:
        parser.error("Use 12–500 cases, 2–8 payments per case and 1–100 warm runs.")
    uri = os.getenv("FRAUD_TEST_NEO4J_URI")
    if not uri:
        parser.error("Set FRAUD_TEST_NEO4J_URI to a dedicated test instance.")
    result = json.dumps(measure(uri, args.cases, args.payments, args.warm_runs), indent=2) + "\n"
    if args.output:
        args.output.write_text(result, encoding="utf-8")
    print(result, end="")


if __name__ == "__main__":
    main()
