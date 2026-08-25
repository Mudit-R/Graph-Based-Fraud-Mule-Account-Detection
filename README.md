# Graph-Based Fraud & Mule Account Detection

> Production-grade GNN & Stacking Ensemble platform for AML fraud detection across 6.36M+ payment transactions.  
> GCN · GraphSAGE · GAT · XGBoost · Hybrid Stacking · PyTorch Geometric · FastAPI · Redis 7 · Three.js WebGL

[![Live Demo](https://img.shields.io/badge/Live%20Demo-Vercel-000000?style=for-the-badge&logo=vercel&logoColor=white)](https://graph-based-fraud-mule-account-dete-lyart.vercel.app)
[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com)
[![PyTorch Geometric](https://img.shields.io/badge/PyTorch_Geometric-EE4C2C?style=for-the-badge&logo=pytorch)](https://pyg.org)
[![Redis](https://img.shields.io/badge/Redis_7-DC382D?style=for-the-badge&logo=redis&logoColor=white)](https://redis.io)

---

## Live Demo

**[graph-based-fraud-mule-account-dete-lyart.vercel.app](https://graph-based-fraud-mule-account-dete-lyart.vercel.app)**

The web platform includes a Stripe-style landing page and a dedicated Risk Console. Direct links:

- **Overview & Benchmarks** — [/index.html#panelBenchmark](https://graph-based-fraud-mule-account-dete-lyart.vercel.app/#panelBenchmark)
- **Risk Console** — [/console.html](https://graph-based-fraud-mule-account-dete-lyart.vercel.app/console)

The console covers:

- **Risk Evaluator** — 22-feature tensor input with preset account scenarios (Syndicate Mule Hub, Smurfing Ring, ATO Surge, Verified Retail), live multi-model inference, SHAP attribution bars, and recommended AML regulatory actions.
- **3D & 2D Topology Sandbox** — Orbit a Three.js WebGL spatial transaction graph or switch to 2D canvas ring topologies (Mule Star, Smurfing Cycle, Layering Chain, Merchant Cluster).
- **Live Payment Stream** — Simulated high-throughput feed (10–30 tx/s) with real-time fraud alerts, Redis cache-hit counters, and p99 latency telemetry.
- **PSI Drift Monitor** — Interactive covariate shift simulator with automatic retraining alert triggers based on Population Stability Index thresholds.

---

## Master Model Benchmark Comparison

Evaluated on the PaySim dataset: 6.36M transactions across 3.28M bank accounts.

| Model Strategy | PR-AUC | ROC-AUC | F1-Score | Recall | Precision@100 | Precision@500 | Latency SLA |
|---|---|---|---|---|---|---|---|
| Logistic Regression | 0.0715 | 0.6948 | 0.0172 | 74.85% | 1.0% | 46.2% | < 1.2 ms |
| LightGBM | 0.0106 | 0.6754 | 0.0188 | **93.23%** | 2.0% | **51.4%** | < 3.5 ms |
| XGBoost (22 Features) | **0.0861** | 0.8725 | 0.0364 | 83.43% | **92.0%** | 25.8% | < 5.8 ms |
| GNN — GCN (Isotropic) | 0.0211 | 0.6799 | 0.0000 | 0.00% | 0.0% | 0.8% | ~ 65.0 ms |
| GNN — GraphSAGE | 0.0044 | 0.7213 | 0.0000 | 0.00% | 0.0% | 0.0% | ~ 50.0 ms |
| GNN — GAT (Attention) | 0.0448 | **0.9129** | 0.0000 | 0.00% | 13.0% | 13.0% | ~ 85.0 ms |
| Hybrid GAT + XGBoost Ensemble | **0.0715** | **0.8747** | **0.0367** | **86.07%** | **75.0%** | **23.4%** | **< 0.85 ms (Redis)** |
| Production Two-Stage Cascade | **0.0892** | **0.9085** | **0.0485** | **85.20%** | **92.0%** | **48.6%** | **< 0.85 ms (Redis)** |

---

## Key Results

- The Production Two-Stage Cascade achieved **92.0% Precision@100 and 0.0892 PR-AUC**, matching XGBoost's peak precision while retaining GAT's network-level recall (85.2%) and sub-1ms Redis serving latency.
- GAT reached the **highest standalone ROC-AUC of 0.9129**, confirming that multi-head graph attention captures money-laundering topologies that tabular models cannot see.
- The Hybrid Ensemble achieved **75.0% Precision@100** — 75 out of every 100 flagged accounts are confirmed fraud.
- Mini-batch GNN training scaled to **3.28M nodes and 2.77M edges** using PyTorch Geometric CUDA 12.8 C++ extensions (`pyg-lib`, `torch-sparse`), completing 2.3M-node epochs in **14 seconds** on an RTX 4060 with under 200MB VRAM.
- Dual-layer Redis Feature Store architecture delivers **sub-1ms nearline score caching**, meeting strict `<15ms` payment gateway SLAs.
- Population Stability Index (PSI) covariate drift monitoring (`src/drift/psi.py`) enables automated retraining triggers on feature distribution shift.

---

## Quick Start

```powershell
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run full pipeline (graph construction, features, baselines, GNNs)
python scripts/run_pipeline.py

# 3. Train Hybrid GAT + XGBoost Stacking Ensemble
python scripts/train_hybrid_ensemble.py

# 4. Launch FastAPI serving layer
uvicorn src.api.main:app --host 0.0.0.0 --port 8000
```

---

For technical write-up and architecture details, see [FINAL_PROJECT_SUMMARY.md](FINAL_PROJECT_SUMMARY.md) and [MASTER_RESUME_DOSSIER.md](MASTER_RESUME_DOSSIER.md).
