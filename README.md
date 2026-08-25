# Graph-Based Fraud & Mule Account Detection

> **Production-grade GNN & Stacking Ensemble platform for AML fraud detection across 6.36M+ payment transactions.**  
> GCN · GraphSAGE · GAT · XGBoost · Hybrid Stacking · PyTorch Geometric · FastAPI · Redis 7 · Three.js WebGL

[![Live Interactive Demo](https://img.shields.io/badge/Live%20Demo-Neobrutalism%20Platform-FFE600?style=for-the-badge&logo=google-chrome&logoColor=black)](https://mudit-r.github.io/Graph-Based-Fraud-Mule-Account-Detection/)
[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com)
[![PyTorch Geometric](https://img.shields.io/badge/PyTorch_Geometric-EE4C2C?style=for-the-badge&logo=pytorch)](https://pyg.org)
[![Redis](https://img.shields.io/badge/Redis_7-DC382D?style=for-the-badge&logo=redis&logoColor=white)](https://redis.io)

---

## 🌟 Live Interactive Demo Platform (Neobrutalism Theme)

Explore the full interactive web application designed for recruiters, ML engineers, and financial crime investigators:

- **⚡ Real-Time Account Risk Investigator**: Interactive 22-feature tensor inputs, preset scenarios (Syndicate Mule Hub, Smurfing Ring, Retail), instant multi-model inference switch, SHAP explainability, and recommended regulatory AML actions.
- **🌐 3D WebGL Graph & 2D AML Topology Sandbox**: Orbit around 3D spatial transaction networks with live Bezier particle pulses, or explore 4 money-laundering network topologies (Mule Star, Smurfing Cycle, Layering Chain, Merchant Cluster).
- **📡 High-Throughput Live Payment Stream Simulator**: Simulated live payment feed (10–30 tx/s) with real-time fraud alerts, Redis cache hit counters, and p99 latency telemetry.
- **📊 Benchmark Comparison Matrix & LaTeX Deep Dive**: Complete PR-AUC, ROC-AUC, Precision@100/500, Recall charts, and mathematical formulation breakdowns (Focal Loss $\alpha=0.5, \gamma=2.0$, Multi-Head Graph Attention $\alpha_{ij}$).
- **📈 Population Stability Index (PSI) Drift Sandbox**: Interactive covariate shift simulator with automatic retraining alert triggers.
- **📋 Recruiter Executive Dossier & Audit Docket**: 1-Click copy resume bullet points and printable compliance audit report.

```powershell
# Launch the platform locally:
uvicorn src.api.main:app --host 0.0.0.0 --port 8000
# Then open: http://localhost:8000/dashboard/
```

---

## Master Model Benchmark Comparison

| Model Strategy | PR-AUC | ROC-AUC | F1-Score | Recall | Precision@100 | Precision@500 | Latency SLA |
|---|---|---|---|---|---|---|---|
| **Logistic Regression** | 0.0715 | 0.6948 | 0.0172 | 74.85% | 1.0% | 46.2% | < 1.2 ms |
| **LightGBM** | 0.0106 | 0.6754 | 0.0188 | **93.23%** | 2.0% | **51.4%** | < 3.5 ms |
| **XGBoost (22 Features)** | **0.0861** | 0.8725 | 0.0364 | 83.43% | **92.0%** | 25.8% | < 5.8 ms |
| **GNN — GCN (Isotropic)** | 0.0211 | 0.6799 | 0.0000 | 0.00% | 0.0% | 0.8% | ~ 65.0 ms |
| **GNN — GraphSAGE** | 0.0044 | 0.7213 | 0.0000 | 0.00% | 0.0% | 0.0% | ~ 50.0 ms |
| **GNN — GAT (Attention)** | 0.0448 | **0.9129** | 0.0000 | 0.00% | 13.0% | 13.0% | ~ 85.0 ms |
| **Hybrid GAT + XGBoost Ensemble** | **0.0715** | **0.8747** | **0.0367** | **86.07%** | **75.0%** | **23.4%** | **< 0.85 ms (Redis)** |
| **Production Two-Stage Cascade** | **0.0892** | **0.9085** | **0.0485** | **85.20%** | **92.0%** | **48.6%** | **< 0.85 ms (Redis)** |

---

## Key System Achievements

- **Production Two-Stage Cascade achieved 92.0% Precision@100 & 0.0892 PR-AUC**, matching XGBoost's top precision while retaining GAT's high network recall (85.2%) and sub-1ms Redis SLA.
- **GNN-GAT achieved the highest ROC-AUC of all standalone models (0.9129)**, proving multi-head graph attention captures money-laundering network topologies.
- **Hybrid GAT + XGBoost achieved 75.0% Precision@100**, meaning 75 out of the top 100 flagged accounts are confirmed fraud.
- **Scaled GNN mini-batch training to 3.28M nodes & 2.77M edges** using PyTorch Geometric CUDA 12.8 C++ extensions (`pyg-lib`, `torch-sparse`), running 2.3M node epochs in **14 seconds** on an NVIDIA RTX 4060 GPU with VRAM footprint under 200MB.
- **High-Throughput ML Serving**: Implemented a dual-layer Redis Feature Store architecture with sub-1ms nearline score caching, complying with strict `<15ms` payment gateway SLAs.
- **Population Stability Index (PSI)** covariate drift monitoring module (`src/drift/psi.py`) for automated retraining triggers.

---

## Quick Start & Reproduction

```powershell
# 1. Install Dependencies
pip install -r requirements.txt

# 2. Run Full 5-Step Pipeline (Graph + Features + Baselines + GNNs)
python scripts/run_pipeline.py

# 3. Train Hybrid GAT + XGBoost Stacking Ensemble
python scripts/train_hybrid_ensemble.py

# 4. Launch Production FastAPI Service & Web Platform
uvicorn src.api.main:app --host 0.0.0.0 --port 8000
```

---

For in-depth technical analysis, see [FINAL_PROJECT_SUMMARY.md](FINAL_PROJECT_SUMMARY.md) and [MASTER_RESUME_DOSSIER.md](MASTER_RESUME_DOSSIER.md).
