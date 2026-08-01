# 📄 Master Project Dossier: Graph-Based Fraud & Mule Account Detection

---

## 1. Executive Summary & Business Context

- **Domain**: Financial Technology / Anti-Money Laundering (AML) / Real-Time Fraud Prevention.
- **Core Objective**: Detect illegal **mule accounts** (accounts used by criminal networks to launder stolen funds) within a massive financial transaction network before cash-out occurs.
- **The Core Problem**: Traditional rule-based engines and tabular ML models evaluate transactions in isolation. Mule accounts look completely legitimate on single transactions ($1,000 transferred in, $950 cashed out). However, in the **graph topology**, they sit at the center of fan-in/fan-out network structures connecting fraud origins to exit accounts.
- **Scale**: Processed **6,362,620 payment transactions** representing **3,277,509 unique bank account nodes** and **2,770,409 directed edges**.

---

## 2. Dataset & Problem Specification

- **Dataset Source**: PaySim (synthetic financial log derived from 30 days / 744 steps of African mobile money logs).
- **Class Imbalance**: Extreme **130:1 imbalance ratio** ($0.13\%$ transaction fraud rate, $0.25\%$ node-level fraud rate).
- **Temporal Alignment**: Transactions ordered across 744 time steps (1 step = 1 hour).
- **Data Leakage Prevention**: Split at step 347 into **Train Set** ($2,629,326$ nodes, steps 0–347) and **Test Set** ($648,183$ nodes, steps 348–744). The test period simulates real-world drift where fraud concentration increases to $0.67\%$.

---

## 3. Graph Schema & Structural Rationale

- **Node Definition**: Unique bank accounts (`nameOrig` and `nameDest`).
- **Edge Definition**: Directed transactions (`nameOrig` $\rightarrow$ `nameDest`).
- **Edge Filtering Logic**: Filtered exclusively to `TRANSFER` and `CASH_OUT` events. `PAYMENT`, `DEBIT`, and `CASH_IN` events carry no fraud signal in PaySim and would create $3.5\text{M}$ noise edges.
- **Why Directed Edges?** Mule accounts exhibit strong asymmetric flow:
  - **High In-Degree** ($d_{in}$): Receiving stolen funds from multiple origin accounts.
  - **Low Out-Degree** ($d_{out}$): Cashing out to a few exit points.
  - An undirected graph collapses this asymmetry, destroying the primary structural fraud signal.

---

## 4. Feature Engineering Catalogue (22 Features Per Account)

### A. Tabular Aggregates (11 Features)
1. `total_sent_log`: $\log(1 + \sum \text{amount\_out})$
2. `total_received_log`: $\log(1 + \sum \text{amount\_in})$
3. `tx_count_out`: Number of outgoing transfers (fan-out)
4. `tx_count_in`: Number of incoming transfers (fan-in)
5. `unique_dest_count`: Count of unique target accounts
6. `unique_src_count`: Count of unique source accounts
7. `avg_sent_log`: $\log(1 + \mu_{\text{sent}})$
8. `avg_received_log`: $\log(1 + \mu_{\text{received}})$
9. `balance_drain_ratio`: $\frac{\text{oldBalance} - \text{newBalance}}{\text{oldBalance} + \epsilon}$ (Mule accounts drain $\approx 100\%$ of incoming funds)
10. `night_tx_fraction`: Ratio of transactions occurring between steps 00:00–06:00
11. `fraud_type_fraction`: Ratio of `TRANSFER` vs `CASH_OUT` events

### B. Graph Structural Features (6 Features)
12. `in_degree`: $d_{in}(v)$
13. `out_degree`: $d_{out}(v)$
14. `degree_ratio`: $\frac{d_{out}(v)}{d_{in}(v) + 1}$
15. `pagerank`: Importance score computed via power iteration:
    $$PR(v) = \frac{1-d}{N} + d \sum_{u \in \mathcal{N}_{in}(v)} \frac{PR(u)}{d_{out}(u)}$$
16. `k_core_number`: Embeddedness in dense graph cores ($k$-core decomposition).
17. `local_clustering_coefficient`: Measures clique-like transaction rings:
    $$C(v) = \frac{2 e(v)}{d(v)(d(v)-1)}$$

### C. Rolling Temporal Features (5 Features)
18. `tx_velocity_24h`: Transaction count in last 24 steps
19. `tx_velocity_7d`: Transaction count in last 168 steps
20. `amount_velocity_24h`: Log amount sent in last 24 steps
21. `amount_velocity_7d`: Log amount sent in last 168 steps
22. `amount_spike_ratio`: $\frac{\text{amount\_velocity\_24h}}{\text{amount\_velocity\_7d} + 10^{-6}}$ (Surge ratio catching newly activated mules)

---

## 5. Model Suite & Mathematical Formulations

### 1. Logistic Regression
$$P(y=1|\mathbf{x}) = \frac{1}{1 + e^{-(\mathbf{w}^T \mathbf{x} + b)}}$$

### 2. XGBoost (Gradient Boosted Trees)
Ensemble of $500$ decision trees with `scale_pos_weight = 680.9` (balancing negative/positive sample weights):
$$\mathcal{L}^{(t)} = \sum_{i=1}^n l(y_i, \hat{y}_i^{(t-1)} + f_t(\mathbf{x}_i)) + \Omega(f_t)$$

### 3. LightGBM (Histogram-Based Gradient Boosting)
Uses histogram binning of continuous features for fast tree splits.

### 4. GCN (Graph Convolutional Network)
Spectral graph convolution averaging neighbor representations:
$$H^{(l+1)} = \sigma \left( \tilde{D}^{-\frac{1}{2}} \tilde{A} \tilde{D}^{-\frac{1}{2}} H^{(l)} W^{(l)} \right)$$

### 5. GraphSAGE (Sample and Aggregate)
Inductive neighborhood sampling with concatenation:
$$\mathbf{h}_{\mathcal{N}(v)}^{(k)} = \text{AGGREGATE}_k \left( \{ \mathbf{h}_u^{(k-1)}, \forall u \in \mathcal{N}(v) \} \right)$$
$$\mathbf{h}_v^{(k)} = \sigma \left( W^{(k)} \cdot \left[ \mathbf{h}_v^{(k-1)} \,||\, \mathbf{h}_{\mathcal{N}(v)}^{(k)} \right] \right)$$

### 6. GAT (Graph Attention Network)
Uses multi-head self-attention ($\alpha_{ij}$) to assign dynamic weights to edges:
$$\alpha_{ij} = \frac{\exp\left(\text{LeakyReLU}\left(\mathbf{a}^T [W\mathbf{h}_i \,||\, W\mathbf{h}_j]\right)\right)}{\sum_{k \in \mathcal{N}(i)} \exp\left(\text{LeakyReLU}\left(\mathbf{a}^T [W\mathbf{h}_i \,||\, W\mathbf{h}_k]\right)\right)}$$

### 7. Hybrid GAT + XGBoost Stacking Ensemble
Extracts GAT's $0.9129$ ROC-AUC prediction probability $P_{\text{GAT}}$ and appends it as feature #23 to the 22 node features, training XGBoost on the stacked representation:
$$\mathbf{X}_{\text{hybrid}} = [\mathbf{X}_{\text{22\_features}} \,||\, P_{\text{GAT}}] \in \mathbb{R}^{3,277,509 \times 23}$$

---

## 6. Class Imbalance Mitigation: Focal Loss

Standard Cross-Entropy is dominated by the 99.87% normal accounts. **Focal Loss** adds a modulating factor $(1 - p_t)^\gamma$:

$$\text{FL}(p_t) = -\alpha_t (1 - p_t)^\gamma \log(p_t)$$

- **Parameters**: $\alpha = 0.5$, $\gamma = 2.0$
- **Effect**: For an easy normal account ($p_t = 0.99$), $(1 - 0.99)^2 = 0.0001$ $\rightarrow$ **Loss is suppressed by 10,000x**, forcing gradient updates to focus on rare fraud cases.

---

## 7. Performance & Optimization Metrics

- **NVIDIA GeForce RTX 4060 Laptop GPU** (7 GB VRAM).
- **PyG CUDA Extensions**: Built with `pyg-lib-0.8.0+pt211cu128`, `torch_sparse-0.6.18+pt211cu128`, and `torch_scatter-2.1.2+pt211cu128`.
- **Mini-Batch Neighbor Sampling**: `NeighborLoader` with batch size $2,048$ and sampling sizes $[20, 10, 5]$.
- **Speed**: Reduced 2.3M node epoch training time from 3+ minutes on CPU to **14 seconds on GPU**.
- **Memory Optimization**: Implemented mini-batch evaluation (`batch_size=1024`), keeping VRAM footprint under **200 MB** during inference.

---

## 8. Final Master Benchmark Results

| Model Strategy | PR-AUC | ROC-AUC | F1-Score | Precision | Recall | Precision@100 | Precision@500 |
|---|---|---|---|---|---|---|---|
| **Logistic Regression** | 0.0715 | 0.6948 | 0.0172 | 0.0087 | 0.7485 | 0.0100 | 0.4620 |
| **LightGBM** | 0.0106 | 0.6754 | 0.0188 | 0.0095 | **0.9323** 🏆 | 0.0200 | **0.5140** 🏆 |
| **XGBoost (22 Feat)** | **0.0861** 🏆 | 0.8725 | 0.0364 | 0.0186 | 0.8343 | **0.9200** 🏆 | 0.2580 |
| **GNN — GCN** | 0.0211 | 0.6799 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0080 |
| **GNN — GraphSAGE** | 0.0044 | 0.7213 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| **GNN — GAT (Attention)** | 0.0448 | **0.9129** 🏆 | 0.0000 | 0.0000 | 0.0000 | 0.1300 | 0.1300 |
| 🥇 **Hybrid GAT + XGBoost** | **0.0715** | **0.8747** | **0.0367** 🏆 | **0.0187** 🏆 | **0.8607** | **0.7500** | **0.2340** |

---

## 9. Production Architecture & Deployment

1. **FastAPI Serving Engine (`src/api/main.py`)**:
   - `/predict`: Low-latency account scoring ($<15\text{ms}$).
   - `/batch-score`: Async batch prediction pipeline.
2. **MLflow Experiment Tracking**:
   - SQLite-backed registry (`sqlite:///mlflow.db`) tracking parameters, loss curves, and model binaries.
3. **Drift Monitoring (`src/drift/psi.py`)**:
   - Population Stability Index (PSI) monitoring feature distribution shifts:
     $$\text{PSI} = \sum_{k=1}^K (Actual_k - Expected_k) \times \ln\left(\frac{Actual_k}{Expected_k}\right)$$
   - Triggers automated retraining alerts when $\text{PSI} \ge 0.20$.
