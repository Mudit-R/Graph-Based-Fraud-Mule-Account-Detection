"""Signed alert outbox tests; all outbound HTTP is mocked."""
import hashlib
import hmac
import json
import time

import httpx
from fastapi import HTTPException, Response
import pytest

from src.api.case_review import Analyst, Intake, create_app
from src.api.case_store import CaseStore
from src.api.ring_webhook import AlertWebhookConfig, RingAlertDispatcher


TOKEN = "webhook-test-only-token-with-more-than-32-characters"
SECRET = "local-test-secret-with-more-than-32-characters"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def risky_batch(batch_id="ring-webhook-test"):
    return {
        "batch_id": batch_id,
        "transactions": [
            {"transaction_id": "event-1", "customer_id": "customer-a", "merchant_id": "merchant-a",
             "device_id": "shared-device", "amount": 120, "timestamp": 1000},
            {"transaction_id": "event-2", "customer_id": "customer-b", "merchant_id": "merchant-b",
             "device_id": "shared-device", "amount": 220, "timestamp": 1010},
        ],
        "risk_scores": {"customer-a": 0.9, "customer-b": 0.8},
    }


def outbox_row(database):
    with CaseStore(database).connect() as db:
        row = db.execute("SELECT * FROM webhook_outbox").fetchone()
        return dict(row) if row else None


def test_alerts_are_persisted_atomically_only_above_configured_threshold(tmp_path):
    store = CaseStore(tmp_path / "cases.sqlite3")
    created = store.ingest("above", risky_batch()["transactions"], {"customer-a": 0.9, "customer-b": 0.8},
                           alert_threshold=0.70)
    assert created["created"] is True
    with store.connect() as db:
        row = db.execute("SELECT * FROM webhook_outbox").fetchone()
        case = db.execute("SELECT fraud_score FROM review_cases WHERE id = ?", (created["case_ids"][0],)).fetchone()
    assert row["status"] == "pending"
    assert case["fraud_score"] == 0.85
    payload = json.loads(row["payload"])
    assert payload["event"] == "fraud.ring.high_risk"
    assert payload["text"].startswith("High-risk payment ring")
    assert payload["case_id"] == created["case_ids"][0]
    assert "customer-a" not in row["payload"]
    assert "shared-device" not in row["payload"]

    store.ingest("below", risky_batch()["transactions"], {"customer-a": 0.9, "customer-b": 0.8},
                 alert_threshold=0.90)
    with store.connect() as db:
        count = db.execute("SELECT COUNT(*) FROM webhook_outbox").fetchone()[0]
    assert count == 1


def test_api_queues_alert_when_configured_and_is_off_by_default(tmp_path):
    payload = risky_batch()
    configured = create_app(
        tmp_path / "configured.sqlite3", "analyst", TOKEN,
        alert_webhook=AlertWebhookConfig("https://alerts.example.test/hook", SECRET, 0.70),
        start_alert_worker=False,
    )
    disabled = create_app(tmp_path / "disabled.sqlite3", "analyst", TOKEN)

    configured_route = next(route.endpoint for route in configured.routes if getattr(route, "path", "") == "/batches")
    assert configured_route(Intake(**payload), Response())["created"] is True
    with configured.state.case_store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM webhook_outbox").fetchone()[0] == 1

    disabled_route = next(route.endpoint for route in disabled.routes if getattr(route, "path", "") == "/batches")
    assert disabled_route(Intake(**risky_batch("disabled")), Response())["created"] is True
    with disabled.state.case_store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM webhook_outbox").fetchone()[0] == 0


def test_signed_delivery_retries_then_marks_alert_delivered(tmp_path):
    store = CaseStore(tmp_path / "cases.sqlite3")
    result = store.ingest("delivery", risky_batch()["transactions"], {"customer-a": 0.9, "customer-b": 0.8},
                          alert_threshold=0.70)
    event_id = f"fraud.ring.high_risk:{result['case_ids'][0]}"
    calls = []

    def handler(request):
        calls.append(request)
        body = request.content
        expected = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        assert request.headers["X-Case-Signature"] == f"sha256={expected}"
        assert request.headers["Idempotency-Key"] == event_id
        assert request.headers["X-Case-Event-Id"] == event_id
        return httpx.Response(503 if len(calls) == 1 else 204)

    transport = httpx.MockTransport(handler)
    config = AlertWebhookConfig("https://alerts.example.test/hook", SECRET, 0.70, base_retry_seconds=1)
    dispatcher = RingAlertDispatcher(
        store, config, client_factory=lambda: httpx.Client(transport=transport),
    )
    first_now = time.time() + 1
    assert dispatcher.deliver_due_once(now=first_now) == 1
    failed = outbox_row(store.database)
    assert failed["status"] == "pending"
    assert failed["attempts"] == 1
    assert failed["next_attempt_at"] >= first_now + 1

    # A fresh store/dispatcher models a service restart between attempts.
    restarted_store = CaseStore(store.database)
    restarted = RingAlertDispatcher(
        restarted_store, config, client_factory=lambda: httpx.Client(transport=transport),
    )
    assert restarted.deliver_due_once(now=first_now + 2) == 1
    delivered = outbox_row(store.database)
    assert delivered["status"] == "delivered"
    assert delivered["attempts"] == 2
    assert delivered["delivered_at"] is not None
    assert len(calls) == 2


def test_repeated_delivery_failures_dead_letter_after_bounded_attempts(tmp_path):
    store = CaseStore(tmp_path / "cases.sqlite3")
    store.ingest("dead-letter", risky_batch()["transactions"],
                 {"customer-a": 0.9, "customer-b": 0.8}, alert_threshold=0.70)
    attempts = []

    def handler(_request):
        attempts.append(1)
        return httpx.Response(503)

    config = AlertWebhookConfig("https://alerts.example.test/hook", SECRET,
                                base_retry_seconds=1, max_retry_seconds=4)
    dispatcher = RingAlertDispatcher(
        store, config, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    now = time.time() + 1
    for expected_attempt in range(1, 7):
        assert dispatcher.deliver_due_once(now=now) == 1
        row = outbox_row(store.database)
        assert row["attempts"] == expected_attempt
        now = row["next_attempt_at"] + 0.01
    assert row["status"] == "dead"
    assert dispatcher.deliver_due_once(now=now + 100) == 0
    assert len(attempts) == 6


def test_expired_worker_cannot_modify_the_recovered_attempt(tmp_path):
    store = CaseStore(tmp_path / "cases.sqlite3")
    store.ingest("lease", risky_batch()["transactions"],
                 {"customer-a": 0.9, "customer-b": 0.8}, alert_threshold=0.70)
    now = time.time() + 1
    first, = store.claim_due_webhooks(now=now, lease_seconds=1)
    assert store.claim_due_webhooks(now=now + 0.5) == []
    second, = store.claim_due_webhooks(now=now + 2)
    assert first["id"] == second["id"]
    assert second["attempts"] == 2
    assert not store.reschedule_webhook(first["id"], first["attempts"], "timeout", now + 10)
    assert not store.mark_webhook_delivered(first["id"], first["attempts"], now=now + 2)
    assert store.mark_webhook_delivered(second["id"], second["attempts"], now=now + 3)
    assert outbox_row(store.database)["status"] == "delivered"


def test_outbox_summary_exposes_aggregate_state_only_to_admins(tmp_path):
    app = create_app(
        tmp_path / "cases.sqlite3", "analyst", TOKEN,
        alert_webhook=AlertWebhookConfig("https://alerts.example.test/hook", SECRET),
        start_alert_worker=False,
    )
    store = app.state.case_store
    store.ingest("ops", risky_batch()["transactions"],
                 {"customer-a": 0.9, "customer-b": 0.8}, alert_threshold=0.70)
    route = next(route.endpoint for route in app.routes if route.path == "/ops/webhook-delivery")
    with pytest.raises(HTTPException) as denied:
        route(Analyst("viewer", "viewer"))
    assert denied.value.status_code == 403
    summary = route(Analyst("operator", "admin"))
    assert summary == {
        "configured": True,
        "counts": {"pending": 1, "sending": 0, "delivered": 0, "dead": 0},
        "oldest_undelivered_at": outbox_row(store.database)["created_at"],
    }


def test_webhook_config_requires_signature_secret_and_secure_destination():
    assert AlertWebhookConfig.from_env({}) is None
    try:
        AlertWebhookConfig.from_env({"FRAUD_CASE_ALERT_WEBHOOK_URL": "https://hooks.example.test"})
    except ValueError as exc:
        assert "secret" in str(exc).lower()
    else:
        raise AssertionError("Configured endpoint without signing key must be rejected")
    try:
        AlertWebhookConfig("http://hooks.example.test", SECRET)
    except ValueError as exc:
        assert "HTTPS" in str(exc)
    else:
        raise AssertionError("Non-local webhook must require HTTPS")
