"""Opt-in API integration with an isolated real Neo4j test instance."""
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from src.api.case_review import Intake, create_app
from src.api.case_store import CaseStore


TOKEN = "cross-case-smoke-token-only-used-with-test-app"
HEADERS = {"Authorization": "Bearer " + TOKEN}
FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "cross-case-review-batches.json"


@pytest.fixture
def graph_factory():
    uri = os.getenv("FRAUD_TEST_NEO4J_URI")
    if not uri:
        pytest.skip("Set FRAUD_TEST_NEO4J_URI to an isolated Neo4j instance.")
    from src.api.case_neo4j import Neo4jCaseGraph

    def create():
        return Neo4jCaseGraph(
            uri,
            os.getenv("FRAUD_TEST_NEO4J_USER", "neo4j"),
            os.environ["FRAUD_TEST_NEO4J_PASSWORD"],
            os.getenv("FRAUD_TEST_NEO4J_DATABASE", "neo4j"),
        )

    return create


def query(case_id, anchor, **options):
    return f"/cases/{case_id}/related-graph", {
        "anchor_type": anchor["type"], "anchor_id": anchor["id"],
        "hops": 4, "limit": 48, **options,
    }


def test_real_neo4j_cross_case_review_and_backfill(tmp_path, graph_factory):
    demo = json.loads(FIXTURE.read_text(encoding="utf-8"))
    seed, *remaining = demo["batches"]
    database = tmp_path / "case-review.sqlite3"
    # Legacy ingest predates connection of a graph backend. Normalization is
    # identical to HTTP intake so a later replay has the same batch digest.
    intake = Intake.model_validate(seed)
    store = CaseStore(database)
    seed_id, = store.ingest(
        intake.batch_id, [event.model_dump() for event in intake.transactions], intake.risk_scores
    )["case_ids"]
    path, params = query(seed_id, demo["anchor"])

    with TestClient(create_app(database, "reviewer-qa", TOKEN, graph_backend=graph_factory())) as client:
        for batch in remaining:
            response = client.post("/batches", json=batch, headers=HEADERS)
            assert response.status_code == 201, response.text

        assert client.get(path, params=params).status_code == 401
        response = client.get(path, params=params, headers=HEADERS)
        assert response.status_code == 200, response.text
        graph = response.json()
        assert {row["batch_id"] for row in graph["related_cases"]} == {
            "cross-case-demo-west", "cross-case-demo-south"
        }
        assert {node["batch_id"] for node in graph["nodes"] if node["type"] == "event"} == {
            "cross-case-demo-east", "cross-case-demo-west", "cross-case-demo-south"
        }
        same_raw_id = [node["id"] for node in graph["nodes"] if node["label"] == "demo-payment-1"]
        assert len(same_raw_id) == len(set(same_raw_id)) == 3
        assert len({(edge["source"], edge["target"], edge["type"]) for edge in graph["edges"]}) == len(graph["edges"])
        assert all(node["type"] != "ip_id" or node["label"] != demo["anchor"]["id"] for node in graph["nodes"])
        shallow = client.get(path, params={**params, "hops": 1, "limit": 2}, headers=HEADERS).json()
        assert len(shallow["nodes"]) == 2 and shallow["truncated"]
        assert client.get(path, params={**params, "anchor_type": "ip_id"}, headers=HEADERS).status_code == 404

        replay = client.post("/batches", json=seed, headers=HEADERS)
        assert replay.status_code == 200
        assert replay.json() == {"created": False, "case_ids": [seed_id]}
        local = client.get(f"/cases/{seed_id}/graph", params={"hops": 4}, headers=HEADERS).json()
        assert {node["label"] for node in local["nodes"] if node["type"] == "event"} == {
            "demo-payment-1", "demo-payment-2"
        }

        remote = graph["related_cases"][0]["id"]
        decision = client.post(f"/cases/{remote}/reviews", headers=HEADERS, json={
            "version": 0, "state": "in_review", "reason": "Found evidence shared across the linked batches.",
        })
        assert decision.status_code == 200, decision.text

    with TestClient(create_app(database, "reviewer-qa", TOKEN, graph_backend=graph_factory())) as client:
        assert client.get(path, params=params, headers=HEADERS).json() == graph
        detail = client.get(f"/cases/{remote}", headers=HEADERS).json()
        assert detail["history"][0]["analyst_id"] == "reviewer-qa"


def test_real_neo4j_isolates_sqlite_workspaces(tmp_path, graph_factory):
    demo = json.loads(FIXTURE.read_text(encoding="utf-8"))
    batch, second, *_ = demo["batches"]
    first_database = tmp_path / "workspace-one.sqlite3"
    other_database = tmp_path / "workspace-two.sqlite3"
    with TestClient(create_app(first_database, "qa", TOKEN, graph_backend=graph_factory())) as first:
        seed, = first.post("/batches", json=batch, headers=HEADERS).json()["case_ids"]
        first.post("/batches", json=second, headers=HEADERS).raise_for_status()
        path, params = query(seed, demo["anchor"])
        baseline = first.get(path, params=params, headers=HEADERS).json()
        assert len(baseline["related_cases"]) == 1

        with TestClient(create_app(other_database, "qa", TOKEN, graph_backend=graph_factory())) as other:
            duplicate_seed, = other.post("/batches", json=batch, headers=HEADERS).json()["case_ids"]
            assert duplicate_seed == seed
            response = other.get(path, params=params, headers=HEADERS)
            assert response.status_code == 200, response.text
            assert response.json()["related_cases"] == []

        assert first.get(path, params=params, headers=HEADERS).json() == baseline
