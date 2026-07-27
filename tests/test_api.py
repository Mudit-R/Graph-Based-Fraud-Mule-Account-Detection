"""
tests/test_api.py
──────────────────────────────────────────────────────────────────────────────
Integration tests for the FastAPI fraud detection endpoints.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture(scope="module")
def client():
    """Create a test client with the app's lifespan managed."""
    with TestClient(app) as c:
        yield c


EXAMPLE_ACCOUNT = {
    "account_id": "C_TEST_001",
    "total_sent_log": 12.5,
    "total_received_log": 10.2,
    "tx_count_out": 45.0,
    "tx_count_in": 3.0,
    "unique_dest_count": 40.0,
    "unique_src_count": 3.0,
    "avg_sent_log": 8.3,
    "avg_received_log": 9.1,
    "balance_drain_ratio": 0.95,
    "night_tx_fraction": 0.8,
    "fraud_type_fraction": 1.0,
    "in_degree": 3.0,
    "out_degree": 45.0,
    "degree_ratio": 15.0,
    "pagerank": 0.0023,
    "k_core_number": 5.0,
    "local_clustering_coefficient": 0.02,
    "tx_velocity_24h": 12.0,
    "tx_velocity_7d": 45.0,
    "amount_velocity_24h": 10.5,
    "amount_velocity_7d": 12.5,
    "amount_spike_ratio": 2.3,
}


class TestHealthEndpoint:

    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_has_required_fields(self, client):
        data = client.get("/health").json()
        assert "status" in data
        assert "model_loaded" in data
        assert "model_version" in data
        assert "gpu_available" in data
        assert "uptime_seconds" in data

    def test_health_uptime_positive(self, client):
        data = client.get("/health").json()
        assert data["uptime_seconds"] >= 0


class TestPredictEndpoint:

    def test_predict_returns_200(self, client):
        response = client.post("/predict", json=EXAMPLE_ACCOUNT)
        assert response.status_code == 200

    def test_predict_response_schema(self, client):
        data = client.post("/predict", json=EXAMPLE_ACCOUNT).json()
        assert "account_id" in data
        assert "fraud_probability" in data
        assert "is_flagged" in data
        assert "risk_tier" in data
        assert "top_contributing_features" in data
        assert "model_version" in data

    def test_predict_probability_range(self, client):
        data = client.post("/predict", json=EXAMPLE_ACCOUNT).json()
        prob = data["fraud_probability"]
        assert 0.0 <= prob <= 1.0, f"fraud_probability out of range: {prob}"

    def test_predict_risk_tier_valid(self, client):
        data = client.post("/predict", json=EXAMPLE_ACCOUNT).json()
        assert data["risk_tier"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

    def test_predict_account_id_echoed(self, client):
        data = client.post("/predict", json=EXAMPLE_ACCOUNT).json()
        assert data["account_id"] == EXAMPLE_ACCOUNT["account_id"]

    def test_predict_flagged_consistent(self, client):
        data = client.post("/predict", json=EXAMPLE_ACCOUNT).json()
        prob = data["fraud_probability"]
        is_flagged = data["is_flagged"]
        # is_flagged should be True iff prob >= 0.5
        assert is_flagged == (prob >= 0.5)


class TestBatchScoreEndpoint:

    def test_batch_score_returns_200(self, client):
        payload = {"accounts": [EXAMPLE_ACCOUNT, EXAMPLE_ACCOUNT]}
        response = client.post("/batch-score", json=payload)
        assert response.status_code == 200

    def test_batch_score_count(self, client):
        n = 3
        payload = {"accounts": [EXAMPLE_ACCOUNT] * n}
        data = client.post("/batch-score", json=payload).json()
        assert data["total_accounts"] == n
        assert len(data["predictions"]) == n

    def test_batch_score_empty_raises(self, client):
        """Empty batch should return validation error."""
        response = client.post("/batch-score", json={"accounts": []})
        assert response.status_code == 422

    def test_batch_score_flag_rate(self, client):
        payload = {"accounts": [EXAMPLE_ACCOUNT] * 4}
        data = client.post("/batch-score", json=payload).json()
        flagged = data["flagged_accounts"]
        total = data["total_accounts"]
        assert abs(data["flag_rate"] - flagged / total) < 1e-6
