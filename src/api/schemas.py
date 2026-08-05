"""
src/api/schemas.py
──────────────────────────────────────────────────────────────────────────────
Pydantic v2 request/response models for the fraud detection API.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# ── Request Models ────────────────────────────────────────────────────────────

class AccountFeatures(BaseModel):
    """
    Features for a single account, used in real-time scoring.

    In production, these would be pulled from a feature store
    (e.g., Redis or Feast) keyed by account_id.
    """
    account_id: str = Field(..., description="Account identifier (e.g. 'C123456789')")

    # Tabular aggregates (pre-computed in the feature store)
    total_sent_log: float = Field(0.0, ge=0.0)
    total_received_log: float = Field(0.0, ge=0.0)
    tx_count_out: float = Field(0.0, ge=0.0)
    tx_count_in: float = Field(0.0, ge=0.0)
    unique_dest_count: float = Field(0.0, ge=0.0)
    unique_src_count: float = Field(0.0, ge=0.0)
    avg_sent_log: float = Field(0.0, ge=0.0)
    avg_received_log: float = Field(0.0, ge=0.0)
    balance_drain_ratio: float = Field(0.0)
    night_tx_fraction: float = Field(0.0, ge=0.0, le=1.0)
    fraud_type_fraction: float = Field(0.0, ge=0.0, le=1.0)

    # Graph-structural features (pre-computed offline, loaded at startup)
    in_degree: float = Field(0.0, ge=0.0)
    out_degree: float = Field(0.0, ge=0.0)
    degree_ratio: float = Field(0.0, ge=0.0)
    pagerank: float = Field(0.0, ge=0.0)
    k_core_number: float = Field(0.0, ge=0.0)
    local_clustering_coefficient: float = Field(0.0, ge=0.0, le=1.0)

    # Temporal rolling features
    tx_velocity_24h: float = Field(0.0, ge=0.0)
    tx_velocity_7d: float = Field(0.0, ge=0.0)
    amount_velocity_24h: float = Field(0.0, ge=0.0)
    amount_velocity_7d: float = Field(0.0, ge=0.0)
    amount_spike_ratio: float = Field(0.0, ge=0.0)

    class Config:
        json_schema_extra = {
            "example": {
                "account_id": "C1234567890",
                "total_sent_log": 12.5,
                "total_received_log": 10.2,
                "tx_count_out": 45,
                "tx_count_in": 3,
                "unique_dest_count": 40,
                "unique_src_count": 3,
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
        }


class BatchScoreRequest(BaseModel):
    """Batch scoring request — list of AccountFeatures."""
    accounts: List[AccountFeatures] = Field(..., min_length=1, max_length=10_000)


# ── Response Models ───────────────────────────────────────────────────────────

class FraudPrediction(BaseModel):
    """Single account fraud prediction result."""
    account_id: str
    fraud_probability: float = Field(..., ge=0.0, le=1.0)
    is_flagged: bool
    risk_tier: str  # "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
    top_contributing_features: List[Dict[str, float]]
    model_version: str
    cache_hit: bool = Field(False, description="True if GNN score was retrieved from nearline Redis cache")
    gnn_nearline_score: Optional[float] = Field(None, description="Pre-computed nearline GNN risk score if available")
    scoring_latency_ms: float = Field(0.0, description="Exact inference & scoring latency in milliseconds")


class BatchScoreResponse(BaseModel):
    """Response for batch scoring."""
    predictions: List[FraudPrediction]
    total_accounts: int
    flagged_accounts: int
    flag_rate: float
    model_version: str
    drift_psi: Optional[float] = None
    drift_alert: bool = False
    cache_hits: int = Field(0, description="Total nearline GNN cache hits in batch")


class HealthResponse(BaseModel):
    """API health check response."""
    status: str
    model_loaded: bool
    model_version: str
    gpu_available: bool
    redis_connected: bool = Field(False, description="Status of Redis feature store connection")
    uptime_seconds: float


class CacheSeedRequest(BaseModel):
    """Request model for batch seeding GNN risk scores into Redis."""
    scores: Dict[str, float] = Field(..., description="Map of account_id to pre-computed GNN risk score")
    ttl_seconds: int = Field(86400, ge=1, description="Cache Time-To-Live in seconds (default 24h)")


class CacheSeedResponse(BaseModel):
    """Response model for GNN score caching pipeline."""
    seeded_count: int
    redis_connected: bool
    ttl_seconds: int

