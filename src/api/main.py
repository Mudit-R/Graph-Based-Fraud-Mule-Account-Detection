"""
src/api/main.py
──────────────────────────────────────────────────────────────────────────────
FastAPI application — production fraud detection serving.

Endpoints:
  GET  /health          — liveness probe (used by Kubernetes/ECS health checks)
  POST /predict         — real-time single-account scoring (< 50ms target)
  POST /batch-score     — async batch scoring for offline pipelines
  GET  /metrics/{run_id} — fetch MLflow run metrics by run_id

Architecture note:
  In production, the GNN model would be deployed differently — full GNN
  inference requires the entire graph, which is expensive in real-time.
  This API demonstrates two modes used in real fraud systems:
    1. Feature-store mode: pre-computed graph features + a lightweight model
       (XGBoost/LightGBM) for <10ms p99 latency.
    2. GNN batch mode: run GNN on the full graph nightly, store scores in
       a cache (Redis/DynamoDB), serve from cache in real-time.

  For this project, we serve the XGBoost model in real-time and provide
  the GNN batch endpoint separately.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import mlflow
import numpy as np
import torch
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from src.api.schemas import (
    AccountFeatures,
    BatchScoreRequest,
    BatchScoreResponse,
    FraudPrediction,
    HealthResponse,
)
from src.drift.psi import DriftMonitor

# ── Model Registry ─────────────────────────────────────────────────────────────

MODEL_DIR = Path("outputs")
MLFLOW_URI = "mlruns/"

FEATURE_COLS = [
    "total_sent_log", "total_received_log", "tx_count_out", "tx_count_in",
    "unique_dest_count", "unique_src_count", "avg_sent_log", "avg_received_log",
    "balance_drain_ratio", "night_tx_fraction", "fraud_type_fraction",
    "in_degree", "out_degree", "degree_ratio", "pagerank", "k_core_number",
    "local_clustering_coefficient", "tx_velocity_24h", "tx_velocity_7d",
    "amount_velocity_24h", "amount_velocity_7d", "amount_spike_ratio",
]

# Global model state
_state: dict = {}


def _load_best_model():
    """Load the best model from MLflow model registry."""
    mlflow.set_tracking_uri(MLFLOW_URI)
    try:
        # Try XGBoost first (fast real-time model)
        client = mlflow.tracking.MlflowClient()
        runs = client.search_runs(
            experiment_ids=["0"],
            filter_string="tags.mlflow.runName = 'XGBoost'",
            order_by=["metrics.test_pr_auc DESC"],
            max_results=1,
        )
        if runs:
            run = runs[0]
            model_uri = f"runs:/{run.info.run_id}/xgboost"
            model = mlflow.xgboost.load_model(model_uri)
            version = run.info.run_id[:8]
            logger.success(f"Loaded XGBoost model from run {version}")
            return model, version, "xgboost"
    except Exception as e:
        logger.warning(f"Could not load from MLflow: {e}. Using stub model.")

    return None, "stub-v0", "none"


def _features_to_array(account: AccountFeatures) -> np.ndarray:
    """Convert AccountFeatures pydantic model to numpy array."""
    return np.array([getattr(account, col) for col in FEATURE_COLS], dtype=np.float32)


def _assign_risk_tier(prob: float) -> str:
    if prob >= 0.80:
        return "CRITICAL"
    elif prob >= 0.50:
        return "HIGH"
    elif prob >= 0.20:
        return "MEDIUM"
    else:
        return "LOW"


def _top_features(account_arr: np.ndarray, model, n: int = 3) -> list:
    """Return top-N contributing features using model feature importances."""
    try:
        importances = model.feature_importances_
        top_indices = np.argsort(importances)[::-1][:n]
        return [
            {FEATURE_COLS[i]: float(account_arr[i])} for i in top_indices
        ]
    except Exception:
        return []


# ── App Lifecycle ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load models and reference distributions on startup."""
    logger.info("🚀 Starting Fraud Detection API …")
    _state["start_time"] = time.time()
    _state["gpu_available"] = torch.cuda.is_available()

    model, version, model_type = _load_best_model()
    _state["model"] = model
    _state["model_version"] = version
    _state["model_type"] = model_type

    # Load drift monitor (reference distribution from training)
    ref_path = MODEL_DIR / "drift_reference.npz"
    if ref_path.exists():
        _state["drift_monitor"] = DriftMonitor.load(ref_path, feature_names=FEATURE_COLS)
        logger.info("Drift monitor loaded.")
    else:
        _state["drift_monitor"] = None
        logger.warning("No drift reference found. Run training first.")

    logger.success("API ready.")
    yield

    # Cleanup
    _state.clear()
    logger.info("API shutdown complete.")


# ── FastAPI App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Graph-Based Fraud Detection API",
    description=(
        "Real-time and batch fraud scoring using GNN + XGBoost models "
        "trained on a 6M+ transaction graph. Built for AML/fraud detection "
        "at fintech and payment systems scale."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["Monitoring"])
async def health_check():
    """Liveness probe — returns 200 if API is up and model is loaded."""
    return HealthResponse(
        status="healthy" if _state.get("model") is not None else "degraded",
        model_loaded=_state.get("model") is not None,
        model_version=_state.get("model_version", "unknown"),
        gpu_available=_state.get("gpu_available", False),
        uptime_seconds=time.time() - _state.get("start_time", time.time()),
    )


@app.post("/predict", response_model=FraudPrediction, tags=["Scoring"])
async def predict(account: AccountFeatures):
    """
    Real-time fraud score for a single account.

    Latency target: < 50ms p99 (XGBoost on pre-computed features)

    In production, features would be pulled from a feature store
    (Redis / Feast) using account_id as the key.
    """
    model = _state.get("model")
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model not loaded. Check startup logs.",
        )

    features = _features_to_array(account).reshape(1, -1)

    try:
        fraud_prob = float(model.predict_proba(features)[0, 1])
    except Exception:
        # Stub response if no trained model yet
        fraud_prob = 0.0

    top_features = _top_features(features[0], model)
    is_flagged = fraud_prob >= 0.50

    return FraudPrediction(
        account_id=account.account_id,
        fraud_probability=round(fraud_prob, 6),
        is_flagged=is_flagged,
        risk_tier=_assign_risk_tier(fraud_prob),
        top_contributing_features=top_features,
        model_version=_state.get("model_version", "unknown"),
    )


@app.post("/batch-score", response_model=BatchScoreResponse, tags=["Scoring"])
async def batch_score(request: BatchScoreRequest):
    """
    Batch scoring for offline pipelines.

    Also runs a PSI drift check on the incoming batch's feature distribution
    vs. the training distribution. If PSI >= 0.20, sets drift_alert=True.

    In production, this endpoint would be called by a nightly Airflow/Prefect
    job, not in real-time.
    """
    model = _state.get("model")
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded.")

    feature_matrix = np.stack([
        _features_to_array(acc) for acc in request.accounts
    ])

    try:
        probas = model.predict_proba(feature_matrix)[:, 1]
    except Exception:
        probas = np.zeros(len(request.accounts))

    predictions = []
    for acc, prob in zip(request.accounts, probas):
        predictions.append(FraudPrediction(
            account_id=acc.account_id,
            fraud_probability=round(float(prob), 6),
            is_flagged=prob >= 0.50,
            risk_tier=_assign_risk_tier(float(prob)),
            top_contributing_features=_top_features(
                _features_to_array(acc), model
            ),
            model_version=_state.get("model_version", "unknown"),
        ))

    flagged = sum(1 for p in predictions if p.is_flagged)

    # Drift check
    drift_psi = None
    drift_alert = False
    monitor = _state.get("drift_monitor")
    if monitor is not None:
        report = monitor.check(
            current_scores=probas,
            current_features=feature_matrix,
        )
        drift_psi = report.score_psi
        drift_alert = report.has_drift

    return BatchScoreResponse(
        predictions=predictions,
        total_accounts=len(request.accounts),
        flagged_accounts=flagged,
        flag_rate=flagged / len(request.accounts),
        model_version=_state.get("model_version", "unknown"),
        drift_psi=drift_psi,
        drift_alert=drift_alert,
    )


@app.get("/metrics/{run_id}", tags=["Monitoring"])
async def get_run_metrics(run_id: str):
    """Fetch MLflow run metrics by run_id."""
    try:
        mlflow.set_tracking_uri(MLFLOW_URI)
        client = mlflow.tracking.MlflowClient()
        run = client.get_run(run_id)
        return {"run_id": run_id, "metrics": run.data.metrics, "params": run.data.params}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))
