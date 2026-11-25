"""API and persistence coverage for fraud case review using synthetic payments."""
from concurrent.futures import ThreadPoolExecutor
import json

import pytest
from fastapi.testclient import TestClient

from src.api.case_review import create_app, create_app_from_env


TOKEN = "local-test-only-bearer-token-with-32-plus-characters"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def harness(tmp_path):
    database = tmp_path / "case-review.sqlite3"
    with TestClient(create_app(database, "analyst-demo", TOKEN)) as client:
        yield client, database


def batch_payload(batch_id="demo-batch"):
    return {
        "batch_id": batch_id,
        "transactions": [
            {"transaction_id": "tx-01", "customer_id": "buyer-1", "merchant_id": "merchant-1",
             "device_id": "shared-1", "amount": 100, "timestamp": 1000},
            {"transaction_id": "tx-02", "customer_id": "buyer-2", "merchant_id": "merchant-2",
             "device_id": "shared-1", "amount": 250, "timestamp": 1010},
            {"transaction_id": "tx-03", "customer_id": "buyer-3", "merchant_id": "merchant-3",
             "ip_id": "shared-1", "amount": 10, "timestamp": 1020},
        ],
        "risk_scores": {"buyer-1": 0.8, "buyer-2": 0.6},
    }


def test_ingest_review_and_restart(harness):
    client, database = harness
    created = client.post("/batches", json=batch_payload(), headers=AUTH)
    assert created.status_code == 201, created.text
    case_id, = created.json()["case_ids"]
    assert [row["id"] for row in client.get("/cases?state=open", headers=AUTH).json()["cases"]] == [case_id]

    url = f"/cases/{case_id}"
    case = client.get(url, headers=AUTH).json()
    assert case["state"] == "open"
    assert case["exposure"] == 350
    assert [row["event_id"] for row in case["transactions"]] == ["tx-01", "tx-02"]
    assert {(edge["event_id"], edge["entity_id"]) for edge in case["graph_edges"]
            if edge["kind"] == "device_id"} == {("tx-01", "shared-1"), ("tx-02", "shared-1")}

    review = client.post(f"{url}/reviews", json={
        "version": 0, "state": "in_review", "reason": "Check shared device and counterparties."
    }, headers=AUTH)
    assert review.status_code == 200
    assert review.json()["version"] == 1

    with TestClient(create_app(database, "analyst-demo", TOKEN)) as reopened:
        decision = reopened.post(f"{url}/reviews", json={
            "version": 1, "state": "escalated", "reason": "Escalate the linked payment pattern."
        }, headers=AUTH)
        assert decision.status_code == 200
        assert reopened.get("/cases?state=open", headers=AUTH).json() == {"cases": []}
        after = reopened.get(url, headers=AUTH).json()
        assert [(event["version"], event["analyst_id"], event["to_state"])
                for event in after["history"]] == [(1, "analyst-demo", "in_review"),
                                                   (2, "analyst-demo", "escalated")]


def test_ingestion_replay_and_conflict(harness):
    client, _ = harness
    payload = batch_payload()
    first = client.post("/batches", json=payload, headers=AUTH)
    payload["transactions"].reverse()
    replay = client.post("/batches", json=payload, headers=AUTH)
    assert replay.status_code == 200
    assert replay.json() == {"created": False, "case_ids": first.json()["case_ids"]}
    payload["transactions"][0]["amount"] += 1
    assert client.post("/batches", json=payload, headers=AUTH).status_code == 409
    assert len(client.get("/cases", headers=AUTH).json()["cases"]) == 1


def test_concurrent_analysts_cannot_overwrite_a_review(harness):
    client, _ = harness
    case_id, = client.post("/batches", json=batch_payload(), headers=AUTH).json()["case_ids"]
    review_url = f"/cases/{case_id}/reviews"

    def submit(reason):
        return client.post(review_url, json={
            "state": "in_review", "version": 0, "reason": reason
        }, headers=AUTH).status_code

    with ThreadPoolExecutor(max_workers=2) as workers:
        statuses = list(workers.map(submit, ["Review from the first client.", "Review from the second client."]))
    assert sorted(statuses) == [200, 409]
    assert len(client.get(f"/cases/{case_id}", headers=AUTH).json()["history"]) == 1


def test_concurrent_ingestions_create_one_batch(harness):
    client, _ = harness

    def submit(_):
        return client.post("/batches", json=batch_payload(), headers=AUTH)

    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(submit, range(2)))
    assert sorted(response.status_code for response in responses) == [200, 201]
    assert responses[0].json()["case_ids"] == responses[1].json()["case_ids"]


def test_case_state_and_validation(harness):
    client, _ = harness
    payload = batch_payload()
    payload["transactions"][1]["transaction_id"] = "tx-01"
    assert client.post("/batches", json=payload, headers=AUTH).status_code == 422
    case_id, = client.post("/batches", json=batch_payload(), headers=AUTH).json()["case_ids"]
    assert client.get("/cases/missing", headers=AUTH).status_code == 404
    premature = client.post(f"/cases/{case_id}/reviews", json={
        "state": "dismissed", "version": 0, "reason": "Cannot skip the review."}, headers=AUTH)
    assert premature.status_code == 409
    assert client.get(f"/cases/{case_id}", headers=AUTH).json()["history"] == []


def test_case_queue_pagination_and_auth(harness):
    client, _ = harness
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/cases").status_code == 401
    assert client.get("/cases", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/batches", json=batch_payload()).status_code == 401
    for key in ("batch-1", "batch-2"):
        assert client.post("/batches", json=batch_payload(key), headers=AUTH).status_code == 201
    first, = client.get("/cases?limit=1", headers=AUTH).json()["cases"]
    second, = client.get(f"/cases?limit=1&after={first['id']}", headers=AUTH).json()["cases"]
    assert first["id"] < second["id"]

def graph_fixture(client):
    payload = batch_payload("graph-batch")
    payload["transactions"][1]["address_id"] = "shared-address"
    payload["transactions"][2]["merchant_id"] = "merchant-1"
    payload["transactions"].extend([
        {"transaction_id": "tx-04", "customer_id": "buyer-4", "merchant_id": "merchant-4",
         "address_id": "shared-address", "amount": 50, "timestamp": 1030},
        {"transaction_id": "tx-05", "customer_id": "buyer-5", "merchant_id": "merchant-5",
         "ip_id": "shared-1", "amount": 25, "timestamp": 1040},
    ])
    result = client.post("/batches", json=payload, headers=AUTH)
    assert result.status_code == 201, result.text
    case_ids = result.json()["case_ids"]
    assert len(case_ids) == 2
    return next(
        case_id for case_id in case_ids
        if client.get(f"/cases/{case_id}", headers=AUTH).json()["transactions"][0]["event_id"] == "tx-01"
    )


def test_graph_traverses_multiple_typed_hops_within_one_case(harness):
    client, _ = harness
    case_id = graph_fixture(client)
    url = f"/cases/{case_id}/graph"

    two_hops = client.get(url, headers=AUTH).json()
    assert two_hops["anchor"] == {"type": "event", "id": "tx-01"}
    assert two_hops["truncated"] is True
    assert {node["label"] for node in two_hops["nodes"] if node["type"] == "event"} == {"tx-01", "tx-02"}

    four_hops = client.get(url, params={"hops": 4}, headers=AUTH).json()
    assert {node["label"] for node in four_hops["nodes"] if node["type"] == "event"} == {
        "tx-01", "tx-02", "tx-04"
    }
    assert ("ip_id", "shared-1") not in {(node["type"], node["label"]) for node in four_hops["nodes"]}
    assert any(
        edge["source"] == "event:tx-04" and edge["target"] == "address_id:shared-address"
        for edge in four_hops["edges"]
    )

    anchored = client.get(url, params={"anchor_type": "device_id", "anchor_id": "shared-1",
                                      "hops": 1}, headers=AUTH).json()
    assert anchored["anchor"] == {"type": "device_id", "id": "shared-1"}
    assert [node["id"] for node in anchored["nodes"]] == [
        "device_id:shared-1", "event:tx-01", "event:tx-02"
    ]
    for anchor_type, anchor_id in [("ip_id", "shared-1"), ("event", "tx-03"), ("event", "tx-05")]:
        assert client.get(url, params={"anchor_type": anchor_type, "anchor_id": anchor_id},
                          headers=AUTH).status_code == 404


def test_graph_does_not_read_duplicate_event_ids_from_other_batches(harness):
    client, _ = harness
    case_id = graph_fixture(client)
    payload = batch_payload("unrelated-batch")
    payload["transactions"][0]["bank_account_id"] = "other-batch-only"
    assert client.post("/batches", json=payload, headers=AUTH).status_code == 201

    url = f"/cases/{case_id}/graph"
    graph = client.get(url, params={"hops": 4}, headers=AUTH).json()
    assert all(node["label"] != "other-batch-only" for node in graph["nodes"])
    assert client.get(url, params={"anchor_type": "bank_account_id",
                                   "anchor_id": "other-batch-only"}, headers=AUTH).status_code == 404


def test_graph_depth_size_and_authentication(harness):
    client, _ = harness
    case_id = graph_fixture(client)
    url = f"/cases/{case_id}/graph"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/cases/no-such-case/graph", headers=AUTH).status_code == 404

    limited = client.get(url, params={"hops": 4, "limit": 3}, headers=AUTH).json()
    ids = {node["id"] for node in limited["nodes"]}
    assert len(ids) == 3
    assert limited["truncated"] is True
    assert all(edge["source"] in ids and edge["target"] in ids for edge in limited["edges"])
    assert client.get(url, params={"hops": 4, "limit": 3}, headers=AUTH).json() == limited

    anchor_only = client.get(url, params={"hops": 0}, headers=AUTH).json()
    assert len(anchor_only["nodes"]) == 1
    assert anchor_only["truncated"] is True
    assert anchor_only["edges"] == []

    for query in [
        {"hops": 5}, {"limit": 0}, {"limit": 201}, {"anchor_id": "tx-01"},
        {"anchor_type": "event"}, {"anchor_type": "not-a-type", "anchor_id": "tx-01"},
    ]:
        assert client.get(url, params=query, headers=AUTH).status_code == 422


@pytest.mark.parametrize(("field", "value"), [("amount", 1e308), ("timestamp", 1e308)])
def test_intake_rejects_out_of_range_finite_numbers(harness, field, value):
    client, _ = harness
    payload = batch_payload()
    for transaction in payload["transactions"]:
        transaction[field] = value
    assert client.post("/batches", json=payload, headers=AUTH).status_code == 422
    assert client.get("/cases", headers=AUTH).json() == {"cases": []}


def test_analyst_demo_batch_requires_same_bearer_token(harness):
    client, _ = harness
    assert client.get("/analyst/demo-batch").status_code == 401
    example = client.get("/analyst/demo-batch", headers=AUTH)
    assert example.status_code == 200
    assert client.post("/batches", json=example.json(), headers=AUTH).status_code == 201

def test_named_roles_control_every_case_action(tmp_path):
    capabilities = {
        "viewer": ["read_cases"],
        "reviewer": ["read_cases", "review_cases"],
        "ingest": ["ingest_batches"],
        "admin": ["ingest_batches", "read_cases", "review_cases"],
    }
    credentials = {f"{role}-operator": {"role": role, "token": f"{role}-{TOKEN}"}
                   for role in capabilities}
    headers = {role: {"Authorization": f"Bearer {role}-{TOKEN}"} for role in capabilities}
    database = tmp_path / "case-review.sqlite3"
    with TestClient(create_app(database, credentials=credentials)) as client:
        assert client.get("/analyst/session").status_code == 401
        for role, allowed in capabilities.items():
            session = client.get("/analyst/session", headers=headers[role])
            assert session.status_code == 200
            assert session.headers["Cache-Control"] == "no-store"
            assert session.json() == {
                "analyst_id": f"{role}-operator", "role": role, "capabilities": allowed,
            }
            status = client.get("/analyst/demo-batch", headers=headers[role]).status_code
            assert status == (200 if "ingest_batches" in allowed else 403)

        for role in ("viewer", "reviewer"):
            assert client.post("/batches", json=batch_payload(), headers=headers[role]).status_code == 403
        intake = client.post("/batches", json=batch_payload(), headers=headers["ingest"])
        assert intake.status_code == 201, intake.text
        case_id, = intake.json()["case_ids"]
        case_url = f"/cases/{case_id}"
        for role, allowed in capabilities.items():
            for route in ["/cases", case_url, f"{case_url}/graph"]:
                actual = client.get(route, headers=headers[role]).status_code
                assert actual == (200 if "read_cases" in allowed else 403)

        review = {"state": "in_review", "version": 0, "reason": "Review linked payments."}
        review_url = f"{case_url}/reviews"
        for role in ("viewer", "ingest"):
            assert client.post(review_url, json=review, headers=headers[role]).status_code == 403
        spoofed = {**review, "analyst_id": "admin-operator"}
        assert client.post(review_url, json=spoofed, headers=headers["reviewer"]).status_code == 422
        assert client.post(
            review_url, json=review, headers={**headers["reviewer"], "X-Analyst-Id": "admin-operator"},
        ).status_code == 200
        assert client.post(review_url, json={
            "state": "dismissed", "version": 1, "reason": "Record the second review.",
        }, headers=headers["admin"]).status_code == 200
        history = client.get(case_url, headers=headers["viewer"]).json()["history"]
        assert [entry["analyst_id"] for entry in history] == ["reviewer-operator", "admin-operator"]


def test_legacy_account_retains_full_session_access(harness):
    client, _ = harness
    assert client.get("/analyst/session", headers=AUTH).json() == {
        "analyst_id": "analyst-demo",
        "role": "admin",
        "capabilities": ["ingest_batches", "read_cases", "review_cases"],
    }


def test_public_demo_bootstraps_synthetic_cases_and_stays_read_only(tmp_path):
    database = tmp_path / "public-demo.sqlite3"
    with TestClient(create_app(database, public_demo=True, bootstrap_demo=True)) as client:
        session = client.get("/analyst/session")
        assert session.status_code == 200
        assert session.json() == {
            "analyst_id": "public-demo", "role": "viewer", "capabilities": ["read_cases"],
        }
        cases = client.get("/cases").json()["cases"]
        assert len(cases) >= 3
        assert client.post("/batches", json=batch_payload()).status_code == 403
        case_id = cases[0]["id"]
        assert client.post(f"/cases/{case_id}/reviews", json={
            "version": 0, "state": "in_review", "reason": "This public demo is read-only."
        }).status_code == 403


@pytest.mark.parametrize("invalid", [
    {},
    {"viewer-a": {"role": "unknown", "token": TOKEN}},
    {"viewer-a": {"role": "viewer", "token": "short"}},
    {"viewer-a": {"role": "viewer", "token": TOKEN}, "viewer-b": {"role": "viewer", "token": TOKEN}},
])
def test_invalid_role_configuration_is_rejected(tmp_path, invalid):
    with pytest.raises(ValueError) as error:
        create_app(tmp_path / "case-review.sqlite3", credentials=invalid)
    assert TOKEN not in str(error.value)


def test_launch_configuration_accepts_named_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAUD_CASE_DATABASE", str(tmp_path / "case-review.sqlite3"))
    monkeypatch.setenv("FRAUD_CASE_ANALYST", "legacy")
    monkeypatch.setenv("FRAUD_CASE_TOKEN", f"legacy-{TOKEN}")
    monkeypatch.setenv("FRAUD_CASE_CREDENTIALS_JSON", json.dumps({
        "configured-viewer": {"role": "viewer", "token": TOKEN},
    }))
    with TestClient(create_app_from_env()) as client:
        assert client.get("/analyst/session", headers=AUTH).json()["analyst_id"] == "configured-viewer"
        legacy = {"Authorization": f"Bearer legacy-{TOKEN}"}
        assert client.get("/analyst/session", headers=legacy).status_code == 401
    monkeypatch.delenv("FRAUD_CASE_CREDENTIALS_JSON")
    with TestClient(create_app_from_env()) as client:
        assert client.get("/analyst/session", headers=legacy).json()["role"] == "admin"
