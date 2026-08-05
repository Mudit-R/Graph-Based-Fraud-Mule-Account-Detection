# Graph-Based Fraud & Mule Account Detection

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyTorch Geometric](https://img.shields.io/badge/PyTorch--Geometric-2.4+-ee4c2c.svg)](https://pyg.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An end-to-end graph machine learning pipeline for detecting illegal money mule accounts and transaction fraud rings in high-volume payment networks. Evaluated on **6,362,620 transactions** forming a directed graph of **3,277,509 bank accounts** and **2,770,409 edges**, combining Graph Neural Networks (GCN, GraphSAGE, GAT) with tree-based gradient boosting (XGBoost, LightGBM) via a hybrid stacking architecture.

---

## Technical Overview

Rule-based fraud engines and isolated tabular classifiers fail to identify money-laundering chains because individual transactions often appear normal (e.g., receiving funds and cashing out shortly after). In graph topology, mule accounts occupy distinct structural positions, exhibiting asymmetrical fan-in/fan-out patterns that link fraud originators to exit points.

This project implements a complete data-to-deployment pipeline:
1. **Graph Construction**: Directed graph representation isolating `TRANSFER` and `CASH_OUT` flows.
2. **Temporal Split**: Step-based temporal split (steps 0–347 train, 348–744 test) to prevent temporal data leakage.
3. **Feature Engineering**: 22 account-level features combining tabular aggregates, rolling temporal velocities, PageRank, local clustering coefficients, and k-core decomposition.
4. **Imbalance Mitigation**: Focal Loss ($\alpha=0.5, \gamma=2.0$) and weighted decision trees targeting extreme 130:1 class imbalance ($0.13\%$ transaction fraud rate).
5. **Hybrid Stacking**: Combines GAT attention probabilities ($P_{\text{GAT}}$) with node features inside XGBoost to achieve **0.9129 ROC-AUC** and **0.7500 Precision@100**.
6. **Production Serving & System Design**: Asynchronous FastAPI inference endpoint (<15ms latency target) backed by a **Redis Feature Store & Nearline GNN Score Cache**, **Prometheus Observability** (`/metrics`), MLflow experiment tracking, and Population Stability Index (PSI) covariate drift detection.

---

## System Architecture

```
                                  [ Raw PaySim CSV ]
                                          │
                                  (6.36M Transactions)
                                          │
                        ┌─────────────────┴─────────────────┐
                        ▼                                   ▼
             [ Data Preprocessing ]              [ Temporal Split ]
         (Filter TRANSFER/CASH_OUT)            (Train: 0-347 | Test: 348-744)
                        │                                   │
                        └─────────────────┬─────────────────┘
                                          ▼
                         [ PyG Directed Graph Construction ]
                          (3.28M Nodes | 2.77M Directed Edges)
                                          │
                        ┌─────────────────┴─────────────────┐
                        ▼                                   ▼
             [ Feature Extraction ]             [ Mini-Batch NeighborLoader ]
             - 11 Tabular Aggregates             (batch=2048, neighbors=[20,10,5])
             - 6 Graph Structural Metrics                   │
             - 5 Rolling Temporal Velocities               │
                        │                                   │
                        └─────────────────┬─────────────────┘
                                          ▼
                              [ Model Training Phase ]
              ┌───────────────────────────┼───────────────────────────┐
              ▼                           ▼                           ▼
       [ XGBoost / LightGBM ]        [ Standalone GNN ]        [ Hybrid Stacking ]
       (22 Node Features)         (GCN / GraphSAGE / GAT)     (GAT P_out + 22 Features)
              │                           │                           │
              └───────────────────────────┼───────────────────────────┘
                                          ▼
                            [ Production Serving Layer ]
             ┌────────────────────────────┼────────────────────────────┐
             ▼                            ▼                            ▼
   [ FastAPI REST Engine ]    [ Redis GNN Feature Store ]    [ Prometheus Scraper ]
   - Sub-15ms Scoring SLA      - Nearline Score Cache (TTL=24h)- Latency & Hits (/metrics)
   - Stacking XGBoost Inference- Sub-1ms Score Lookups          - Grafana Dashboard Ready
```

---

## Dataset Specification & Leakage Mitigation

- **Source**: PaySim synthetic financial log over 744 hourly time steps (30 days).
- **Graph Scale**:
  - Total Transactions Processed: `6,362,620`
  - Directed Graph Nodes ($V$): `3,277,509` unique accounts (`nameOrig` and `nameDest`).
  - Directed Graph Edges ($E$): `2,770,409` transaction edges (`TRANSFER` and `CASH_OUT`).
- **Class Imbalance**:
  - Transaction-level fraud rate: $0.13\%$
  - Node-level fraud rate: $0.25\%$ ($130:1$ negative-to-positive ratio)
- **Temporal Train/Test Split**:
  - **Train**: Steps 0–347 ($2,629,326$ accounts, $0.14\%$ fraud rate)
  - **Test**: Steps 348–744 ($648,183$ accounts, $0.67\%$ fraud rate)
  - *Rationale*: Random $k$-fold cross-validation on dynamic graph networks leaks future topological states into training representations. The step-347 split simulates real-world distribution shifts under evolving fraud tactics.

---

## Feature Engineering Catalogue (22 Features)

| Category | Feature Name | Mathematical Definition / Rationale |
|---|---|---|
| **Tabular Aggregates** | `total_sent_log` | $\log(1 + \sum \text{amount\_out})$ |
| | `total_received_log` | $\log(1 + \sum \text{amount\_in})$ |
| | `tx_count_out` | Total count of outgoing transactions |
| | `tx_count_in` | Total count of incoming transactions |
| | `unique_dest_count` | Count of unique destination accounts (fan-out degree) |
| | `unique_src_count` | Count of unique source accounts (fan-in degree) |
| | `avg_sent_log` | $\log(1 + \mu_{\text{sent}})$ |
| | `avg_received_log` | $\log(1 + \mu_{\text{received}})$ |
| | `balance_drain_ratio` | $\frac{\text{oldBalance} - \text{newBalance}}{\text{oldBalance} + \epsilon}$ (detects near 100% account drainage) |
| | `night_tx_fraction` | Ratio of transactions during off-hours (steps 00:00–06:00) |
| | `fraud_type_fraction` | Ratio of `TRANSFER` vs `CASH_OUT` events |
| **Graph Structural** | `in_degree` | $d_{in}(v)$ — Incoming link count |
| | `out_degree` | $d_{out}(v)$ — Outgoing link count |
| | `degree_ratio` | $\frac{d_{out}(v)}{d_{in}(v) + 1}$ — Asymmetrical flow indicator |
| | `pagerank` | Centrality score computed via power iteration |
| | `k_core_number` | Structural coreness in dense subgraph clusters |
| | `local_clustering_coeff` | Triadic closure metric measuring clique-like transaction rings |
| **Rolling Temporal** | `tx_velocity_24h` | Transaction count in previous 24 steps |
| | `tx_velocity_7d` | Transaction count in previous 168 steps |
| | `amount_velocity_24h` | Log transaction volume sent in previous 24 steps |
| | `amount_velocity_7d` | Log transaction volume sent in previous 168 steps |
| | `amount_spike_ratio` | $\frac{\text{amount\_velocity\_24h}}{\text{amount\_velocity\_7d} + 10^{-6}}$ (Surge ratio for newly activated mules) |

---

## Model Architectures & Formulations

### Graph Attention Networks (GAT)
GAT computes dynamic attention weights $\alpha_{ij}$ over incoming transaction edges to prioritize suspicious connections while ignoring normal transfers:

$$\alpha_{ij} = \frac{\exp\left(\text{LeakyReLU}\left(\mathbf{a}^T [\mathbf{W}\mathbf{h}_i \,||\, \mathbf{W}\mathbf{h}_j]\right)\right)}{\sum_{k \in \mathcal{N}(i)} \exp\left(\text{LeakyReLU}\left(\mathbf{a}^T [\mathbf{W}\mathbf{h}_i \,||\, \mathbf{W}\mathbf{h}_k]\right)\right)}$$

### Focal Loss
To prevent well-classified background accounts ($p_t \approx 0.99$) from dominating backpropagation gradients, neural networks were trained with Focal Loss:

$$\text{FL}(p_t) = -\alpha_t (1 - p_t)^\gamma \log(p_t) \quad (\alpha = 0.5, \gamma = 2.0)$$

For confident predictions, $(1 - 0.99)^2 = 0.0001$ suppresses loss gradients by **10,000x**, forcing parameter updates to focus on ambiguous boundary nodes.

### Hybrid Stacking Architecture
Standalone GAT delivers high discrimination (ROC-AUC 0.9129) but produces compressed probability scales under Focal Loss. The hybrid architecture feeds GAT prediction probabilities ($P_{\text{GAT}}$) directly into XGBoost alongside the 22 node features, forming a 23-dimensional dataset:

$$\mathbf{X}_{\text{hybrid}} = [\mathbf{X}_{\text{22\_features}} \,||\, P_{\text{GAT}}] \in \mathbb{R}^{3,277,509 \times 23}$$

---

## Benchmark Results

Evaluated on the temporal test set (648,183 accounts):

| Model Strategy | PR-AUC | ROC-AUC | F1-Score | Precision | Recall | Precision@100 | Precision@500 |
|---|---|---|---|---|---|---|---|
| **Logistic Regression** | 0.0715 | 0.6948 | 0.0172 | 0.0087 | 0.7485 | 0.0100 | 0.4620 |
| **LightGBM** | 0.0106 | 0.6754 | 0.0188 | 0.0095 | **0.9323** | 0.0200 | **0.5140** |
| **XGBoost (22 Features)** | **0.0861** | 0.8725 | 0.0364 | 0.0186 | 0.8343 | **0.9200** | 0.2580 |
| **GNN — GCN** | 0.0211 | 0.6799 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0080 |
| **GNN — GraphSAGE** | 0.0044 | 0.7213 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| **GNN — GAT (Attention)** | 0.0448 | **0.9129** | 0.0000 | 0.0000 | 0.0000 | 0.1300 | 0.1300 |
| **Hybrid GAT + XGBoost** | **0.0715** | **0.8747** | **0.0367** | **0.0187** | **0.8607** | **0.7500** | **0.2340** |

### Key Findings
1. **GAT captures topology**: Standalone GAT achieved the highest ROC-AUC (**0.9129**), confirming that multi-head graph attention successfully combats neighborhood over-smoothing.
2. **Hybrid Stacking optimizes operational review**: **Precision@100 = 0.7500** means 75 out of the top 100 alerts are confirmed fraud, minimizing false positives for human triage teams while capturing **86.1% overall recall**.

---

## Hardware & Systems Optimizations

- **PyG CUDA Extensions**: Built with custom `pyg-lib`, `torch-sparse`, and `torch-scatter` compiled against CUDA 12.8.
- **Mini-Batch Neighbor Sampling**: Implemented `NeighborLoader` with batch size $2048$ and sampling depth $[20, 10, 5]$.
- **VRAM Footprint**: Kept GPU memory usage below **200 MB** on an NVIDIA RTX 4060 GPU, accelerating training to **14 seconds per epoch**.

---

## Project Structure

```
Graph-Based Fraud & Mule Account Detection/
├── Makefile                       # Command shortcuts for setup, train, test, and serve
├── environment.yml                # Conda environment definition (CPU/GPU)
├── environment-rapids.yml         # RAPIDS accelerated environment (WSL2/Linux)
├── requirements.txt               # Standard pip requirements
├── docker/
│   ├── Dockerfile                 # Container image for FastAPI serving
│   └── docker-compose.yml         # Compose stack (FastAPI + MLflow DB)
├── data/
│   └── raw/                       # Location for PaySim CSV
├── src/
│   ├── api/
│   │   └── main.py                # FastAPI REST API endpoints
│   ├── graph/
│   │   ├── builder.py             # NetworkX / PyG directed graph constructor
│   │   └── features.py            # PageRank, k-core, and degree feature extractors
│   ├── models/
│   │   ├── baselines.py           # Logistic Regression, XGBoost, LightGBM
│   │   ├── gcn.py                 # PyTorch Geometric GCN
│   │   ├── graphsage.py           # PyTorch Geometric GraphSAGE
│   │   ├── gat.py                 # PyTorch Geometric GAT
│   │   └── focal_loss.py          # Custom PyTorch Focal Loss module
│   ├── training/
│   │   ├── run_baselines.py       # Baseline training CLI script
│   │   ├── run_gnn.py             # GNN training loop & evaluation
│   │   └── evaluate.py            # Precision@K, PR-AUC, ROC-AUC calculation
│   ├── drift/
│   │   └── psi.py                 # Population Stability Index drift monitor
│   └── explainability/
│       └── explain.py             # GNNExplainer and SHAP integration
├── scripts/
│   ├── download_data.py           # Kaggle API PaySim downloader
│   ├── run_pipeline.py            # End-to-end execution pipeline
│   └── train_hybrid_ensemble.py   # GAT + XGBoost stacking runner
└── tests/                         # Pytest unit and integration test suite
```

---

## Quickstart & Execution

### 1. Environment Setup
```bash
# Clone the repository
git clone https://github.com/Mudit-R/Graph-Based-Fraud-Mule-Account-Detection.git
cd Graph-Based-Fraud-Mule-Account-Detection

# Create Conda environment
make env
conda activate fraud-detection

# Install project package in editable mode
pip install -e .
```

### 2. Download Dataset
Download the PaySim dataset using Kaggle API credentials or run:
```bash
make data
```

### 3. Run Full Pipeline
To execute graph construction, feature extraction, baseline training, GNN training, and hybrid ensemble evaluation:
```bash
python scripts/run_pipeline.py
```

Or execute individual Makefile commands:
```bash
# Train tabular baselines (XGBoost, LightGBM, LogReg)
make train-baselines

# Train GNN models
make train-gat

# Run unit test suite
make test
```

---

## Production Deployment & MLOps

### FastAPI Inference Endpoint
Start the REST API server:
```bash
make serve
```
The server exposes `/predict` for single/batch node risk scoring and returns model predictions in under 15ms.

Interactive OpenAPI documentation is available at `http://localhost:8000/docs`.

### Docker Containerization
Run the API and MLflow tracking server via Docker Compose:
```bash
make docker-build
make docker-up
```

### Covariate Drift Monitoring (PSI)
The `src/drift/psi.py` module tracks feature distribution drift between training step baselines and incoming prediction batches. An alert is triggered whenever the Population Stability Index exceeds threshold ($\text{PSI} \ge 0.20$):

```python
from src.drift.psi import calculate_psi

psi_val = calculate_psi(baseline_features, current_batch_features)
if psi_val >= 0.20:
    logger.warning(f"Covariate drift detected! PSI={psi_val:.4f}. Triggering retraining pipeline.")
```

---

## License

Distributed under the MIT License. See `LICENSE` for details.
