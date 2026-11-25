"""Projection and authorization tests with a controllable graph backend."""

from collections import defaultdict
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from src.api import case_review
from src.api.case_review import create_app
from src.api.case_store import CaseStore
from tests.test_case_review import AUTH, TOKEN, batch_payload


class FakeCaseGraph:
    def __init__(self):
        self.snapshots = defaultdict(dict)
        self.projected_pages = []
        self.queries = []
        self.failure = None

    def get_cursor(self, workspace_id):
        if self.failure == "offline":
            raise OSError("private connection metadata")
        return max(self.snapshots[workspace_id], default=0)

    def project(self, workspace_id, snapshots):
        self.projected_pages.append((workspace_id, [snapshot["seq"] for snapshot in snapshots]))
        if self.failure == "stuck":
            return
        self.snapshots[workspace_id].update({snapshot["seq"]: snapshot for snapshot in snapshots})
        if self.failure == "lost_response":
            raise OSError("write committed before receiving response")

    def related_graph(self, workspace_id, case_id, anchor_type, anchor_id, hops, limit):
        self.queries.append((workspace_id, case_id, anchor_type, anchor_id, hops, limit))
        return {
            "anchor": {"type": anchor_type, "id": anchor_id},
            "related_cases": [
                {"id": case["case_id"], "batch_id": case["batch_id"]}
                for case in self.snapshots[workspace_id].values()
                if case["case_id"] != case_id
            ],
            "nodes": [],
            "edges": [],
            "truncated": False,
        }


def intake(client, batch_id, headers=AUTH):
    response = client.post("/batches", json=batch_payload(batch_id), headers=headers)
    assert response.status_code == 201, response.text
    case_id, = response.json()["case_ids"]
    return case_id


def endpoint(case_id):
    return f"/cases/{case_id}/related-graph"


def test_backfills_legacy_cases_and_replays_without_duplicates(tmp_path):
    path = tmp_path / "review.sqlite3"
    with TestClient(create_app(path, "analyst", TOKEN)) as legacy:
        first = intake(legacy, "legacy-a")
        second = intake(legacy, "legacy-b")

    # Simulate a persisted case database generated before the graph migration.
    with CaseStore(path).connect() as db:
        db.execute("DROP TABLE case_projection")
        db.execute("DROP TABLE graph_workspace")

    graph = FakeCaseGraph()
    with TestClient(create_app(path, "analyst", TOKEN, graph_backend=graph)) as client:
        store = CaseStore(path)
        assert UUID(store.workspace_id).version == 4
        assert graph.projected_pages == []
        assert store.latest_projection_seq() == 2
        replay = client.post("/batches", json=batch_payload("legacy-a"), headers=AUTH)
        assert replay.status_code == 200
        assert replay.json()["case_ids"] == [first]
        assert store.latest_projection_seq() == 2

        result = client.get(endpoint(first), headers=AUTH)
        assert result.status_code == 200, result.text
        assert result.json()["anchor"] == {"type": "event", "id": "tx-01"}
        assert result.json()["related_cases"] == [{"id": second, "batch_id": "legacy-b"}]
        page, = graph.projected_pages
        assert page == (store.workspace_id, [1, 2])
        first_case = graph.snapshots[store.workspace_id][1]
        assert first_case == {
            "seq": 1, "case_id": first, "batch_id": "legacy-a",
            "events": [
                {"event_id": "tx-01", "entities": [
                    {"type": "customer_id", "id": "buyer-1"},
                    {"type": "device_id", "id": "shared-1"},
                    {"type": "merchant_id", "id": "merchant-1"},
                ]},
                {"event_id": "tx-02", "entities": [
                    {"type": "customer_id", "id": "buyer-2"},
                    {"type": "device_id", "id": "shared-1"},
                    {"type": "merchant_id", "id": "merchant-2"},
                ]},
            ],
        }

        assert client.get(endpoint(first), headers=AUTH).status_code == 200
        assert graph.projected_pages == [page]
        assert CaseStore(path).workspace_id == store.workspace_id


def test_roles_and_anchor_validation_precede_projection(tmp_path):
    graph = FakeCaseGraph()
    credentials = {role: {"role": role, "token": f"{role}-{TOKEN}"}
                   for role in ("viewer", "reviewer", "ingest", "admin")}
    headers = {role: {"Authorization": f"Bearer {role}-{TOKEN}"} for role in credentials}
    with TestClient(create_app(tmp_path / "review.sqlite3", credentials=credentials,
                               graph_backend=graph)) as client:
        case_id = intake(client, "protected", headers["ingest"])
        url = endpoint(case_id)
        assert client.get(url).status_code == 401
        assert client.get(url, headers=headers["ingest"]).status_code == 403
        assert client.get(endpoint("missing"), headers=headers["viewer"]).status_code == 404
        for query in (
            {"anchor_type": "event"},
            {"anchor_id": "tx-01"},
            {"hops": 5},
            {"limit": 0},
        ):
            assert client.get(url, params=query, headers=headers["viewer"]).status_code == 422
        assert client.get(url, params={"anchor_type": "ip_id", "anchor_id": "shared-1"},
                          headers=headers["viewer"]).status_code == 404
        assert graph.projected_pages == []
        assert graph.queries == []

        for role in ("viewer", "reviewer", "admin"):
            session = client.get("/analyst/session", headers=headers[role]).json()
            assert session["cross_case_available"] is True
        assert "cross_case_available" not in client.get(
            "/analyst/session", headers=headers["ingest"]
        ).json()

        query = {"anchor_type": "device_id", "anchor_id": "shared-1", "hops": 4, "limit": 25}
        result = client.get(url, params=query, headers=headers["viewer"])
        assert result.status_code == 200
        assert graph.queries[0][1:] == (case_id, "device_id", "shared-1", 4, 25)


def test_neo4j_outage_leaves_intake_and_review_authoritative(tmp_path):
    graph = FakeCaseGraph()
    graph.failure = "offline"
    path = tmp_path / "review.sqlite3"
    with TestClient(create_app(path, "analyst", TOKEN, graph_backend=graph)) as client:
        first = intake(client, "outage-a")
        second = intake(client, "outage-b")
        assert client.get(f"/cases/{second}", headers=AUTH).status_code == 200
        result = client.get(endpoint(first), headers=AUTH)
        assert result.status_code == 503
        assert "private" not in result.text
        assert graph.queries == []
        assert CaseStore(path).latest_projection_seq() == 2

        update = client.post(
            f"/cases/{first}/reviews",
            headers=AUTH,
            json={"version": 0, "state": "in_review", "reason": "Review new linked evidence."},
        )
        assert update.status_code == 200
        assert CaseStore(path).latest_projection_seq() == 2
        graph.failure = "lost_response"
        assert client.get(endpoint(first), headers=AUTH).status_code == 503
        assert len(graph.projected_pages) == 1
        assert graph.queries == []

        graph.failure = None
        result = client.get(endpoint(first), headers=AUTH)
        assert result.status_code == 200
        assert result.json()["related_cases"] == [{"id": second, "batch_id": "outage-b"}]
        assert len(graph.projected_pages) == 1
        assert client.get(f"/cases/{first}", headers=AUTH).json()["version"] == 1


def test_catchup_is_bounded_and_never_serves_partial_results(tmp_path, monkeypatch):
    monkeypatch.setattr(case_review, "MAX_PROJECTION_PAGES", 1)
    monkeypatch.setattr(case_review, "PROJECTION_PAGE_SIZE", 1)
    graph = FakeCaseGraph()
    path = tmp_path / "review.sqlite3"
    with TestClient(create_app(path, "analyst", TOKEN, graph_backend=graph)) as client:
        first = intake(client, "queued-a")
        intake(client, "queued-b")

        graph.failure = "stuck"
        stuck = client.get(endpoint(first), headers=AUTH)
        assert stuck.status_code == 503
        assert "catching up" in stuck.text
        assert graph.queries == []

        graph.failure = None
        assert client.get(endpoint(first), headers=AUTH).status_code == 503
        assert graph.queries == []
        assert client.get(endpoint(first), headers=AUTH).status_code == 200
        assert len(graph.queries) == 1
        assert [seq for _, seq in graph.projected_pages] == [[1], [1], [2]]


def test_workspace_and_sequence_are_independent_for_two_sqlite_databases(tmp_path):
    graph = FakeCaseGraph()
    first_path = tmp_path / "first.sqlite3"
    second_path = tmp_path / "second.sqlite3"
    with TestClient(create_app(first_path, "analyst", TOKEN, graph_backend=graph)) as first:
        case = intake(first, "same-batch")
        assert first.get(endpoint(case), headers=AUTH).status_code == 200
    with TestClient(create_app(second_path, "analyst", TOKEN, graph_backend=graph)) as second:
        assert intake(second, "same-batch") == case
        assert second.get(endpoint(case), headers=AUTH).status_code == 200
    workspaces = [workspace for workspace, _ in graph.projected_pages]
    assert len(set(workspaces)) == 2
    assert [seq for _, seq in graph.projected_pages] == [[1], [1]]


def test_disabled_backend_preserves_legacy_session_shape(tmp_path):
    with TestClient(create_app(tmp_path / "review.sqlite3", "analyst", TOKEN)) as client:
        case_id = intake(client, "sqlite-only")
        response = client.get(endpoint(case_id), headers=AUTH)
        assert response.status_code == 503
        assert "not configured" in response.text
        assert "cross_case_available" not in client.get("/analyst/session", headers=AUTH).json()


def test_graph_ahead_of_restored_sqlite_snapshot_is_unavailable(tmp_path):
    graph = FakeCaseGraph()
    path = tmp_path / "review.sqlite3"
    with TestClient(create_app(path, "analyst", TOKEN, graph_backend=graph)) as client:
        case_id = intake(client, "restore-older")
        graph.snapshots[CaseStore(path).workspace_id][10] = {"seq": 10}
        response = client.get(endpoint(case_id), headers=AUTH)
        assert response.status_code == 503
        assert "ahead of the source" in response.text
        assert graph.queries == []

def test_demo_related_batches_seeds_current_case_anchor(tmp_path):
    credentials = {
        "viewer": {"role": "viewer", "token": f"viewer-{TOKEN}"},
        "admin": {"role": "admin", "token": TOKEN},
    }
    graph = FakeCaseGraph()
    with TestClient(create_app(tmp_path / "review.sqlite3", credentials=credentials,
                               graph_backend=graph)) as client:
        url = "/analyst/demo-related-batches"
        assert client.get(url).status_code == 401
        assert client.get(url, headers={"Authorization": f"Bearer viewer-{TOKEN}"}).status_code == 403
        example = client.get(url, headers=AUTH)
        assert example.status_code == 200
        data = example.json()
        assert data["anchor"] == {"type": "device_id", "id": "demo-cross-shared-device"}
        assert len(data["batches"]) >= 2

        case_ids = []
        for batch in data["batches"]:
            result = client.post("/batches", json=batch, headers=AUTH)
            assert result.status_code == 201, result.text
            case_ids.extend(result.json()["case_ids"])
        assert len(case_ids) >= 2
        result = client.get(endpoint(case_ids[0]), headers=AUTH, params={
            "anchor_type": data["anchor"]["type"], "anchor_id": data["anchor"]["id"],
        })
        assert result.status_code == 200, result.text


def test_env_config_passes_named_connection_settings(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    import sys

    graph = FakeCaseGraph()
    constructor = Mock(return_value=graph)
    monkeypatch.setitem(sys.modules, "src.api.case_neo4j", SimpleNamespace(Neo4jCaseGraph=constructor))
    monkeypatch.setenv("FRAUD_CASE_NEO4J_URI", "bolt://fixture.local:7687")
    monkeypatch.setenv("FRAUD_CASE_NEO4J_USER", "fixture-user")
    monkeypatch.setenv("FRAUD_CASE_NEO4J_PASSWORD", "fixture-password")
    monkeypatch.setenv("FRAUD_CASE_NEO4J_DATABASE", "fixture-db")
    monkeypatch.setenv("FRAUD_CASE_DATABASE", str(tmp_path / "review.sqlite3"))
    monkeypatch.setenv("FRAUD_CASE_ANALYST", "analyst")
    monkeypatch.setenv("FRAUD_CASE_TOKEN", TOKEN)
    monkeypatch.delenv("FRAUD_CASE_CREDENTIALS_JSON", raising=False)
    with TestClient(case_review.create_app_from_env()) as client:
        assert client.get("/analyst/session", headers=AUTH).json()["cross_case_available"] is True
    constructor.assert_called_once_with("bolt://fixture.local:7687", "fixture-user",
                                        "fixture-password", "fixture-db")

    monkeypatch.delenv("FRAUD_CASE_NEO4J_PASSWORD")
    with pytest.raises(ValueError, match="FRAUD_CASE_NEO4J_PASSWORD"):
        case_review.create_app_from_env()
