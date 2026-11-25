"""
tests/test_triage_and_subgraph.py
──────────────────────────────────────────────────────────────────────────────
Integration tests for real-world AML fast-rules triage and 2-hop ego subgraph extractor.
"""
import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


class TestAMLTriageEndpoint:
    """Tests for deterministic AML compliance triage rules."""

    def test_clean_merchant_fast_pass(self, client):
        payload = {
            "account_id": "acc_clean_retail_001",
            "balance_drain_ratio": 0.10,
            "amount_spike_ratio": 1.1,
            "unique_dest_count": 3.0,
            "degree_ratio": 0.8,
            "night_tx_fraction": 0.05,
            "gnn_risk_score": 0.04
        }
        res = client.post("/triage/rules", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["triage_status"] == "FAST_PASS_CLEAN"
        assert data["risk_level"] == "LOW"
        assert len(data["violations"]) == 0
        assert data["composite_risk_score"] < 0.20

    def test_mule_account_critical_block(self, client):
        payload = {
            "account_id": "acc_mule_syndicate_991",
            "balance_drain_ratio": 0.98,      # >90% drain
            "amount_spike_ratio": 4.5,        # 450% surge
            "unique_dest_count": 35.0,        # extreme fan-out
            "degree_ratio": 8.0,
            "night_tx_fraction": 0.85,        # nocturnal bot
            "gnn_risk_score": 0.94
        }
        res = client.post("/triage/rules", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["triage_status"] == "BLOCK_IMMEDIATELY"
        assert data["risk_level"] == "CRITICAL"
        assert len(data["violations"]) >= 3
        rule_codes = [v["rule_code"] for v in data["violations"]]
        assert "FATF-RED-01" in rule_codes
        assert "FATF-RED-02" in rule_codes
        assert "Mule Account Liquidation" in data["regulatory_typology"]

    def test_borderline_manual_review(self, client):
        payload = {
            "account_id": "acc_borderline_002",
            "balance_drain_ratio": 0.50,
            "amount_spike_ratio": 3.2,        # Only surge triggers
            "unique_dest_count": 5.0,
            "degree_ratio": 1.0,
            "night_tx_fraction": 0.20,
            "gnn_risk_score": 0.35
        }
        res = client.post("/triage/rules", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["triage_status"] in ["MANUAL_AML_REVIEW", "BLOCK_IMMEDIATELY"]
        assert data["risk_level"] in ["ELEVATED", "CRITICAL"]


class TestEgoSubgraphEndpoint:
    """Tests for 2-hop ego network subgraph extraction."""

    def test_extract_subgraph_structure(self, client):
        account_id = "acc_merchant_xyz_8821"
        res = client.get(f"/graph/subgraph/{account_id}?hop_depth=2")
        assert res.status_code == 200
        data = res.json()
        assert data["focal_account_id"] == account_id
        assert data["total_nodes"] > 1
        assert data["total_edges"] > 0
        assert len(data["nodes"]) == data["total_nodes"]
        assert len(data["edges"]) == data["total_edges"]

        # Check focal node
        focal_nodes = [n for n in data["nodes"] if n["is_focal"]]
        assert len(focal_nodes) == 1
        assert focal_nodes[0]["id"] == account_id
        assert focal_nodes[0]["node_type"] == "FOCAL_TARGET"

        # Check edge structure
        for edge in data["edges"]:
            assert "source" in edge
            assert "target" in edge
            assert edge["tx_volume_inr"] > 0
