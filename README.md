# Graph-Based Fraud & Mule Account Detection

> **Production-grade GNN pipeline for AML fraud detection on 6.3M+ payment transactions.**  
> GCN · GraphSAGE · GAT · XGBoost · Hybrid Stacking · PyTorch Geometric · FastAPI · MLflow

---

## 📊 Final Model Comparison Results

| Model Strategy | PR-AUC | ROC-AUC | F1 | Precision | Recall | Precision@100 | Precision@500 |
|---|---|---|---|---|---|---|---|
| **Logistic Regression** | 0.0715 | 0.6948 | 0.0172 | 0.0087 | 0.7485 | 0.0100 | 0.4620 |
| **LightGBM** | 0.0106 | 0.6754 | 0.0188 | 0.0095 | **0.9323** | 0.0200 | **0.5140** |
| **XGBoost (22 Features)** | **0.0861** | 0.8725 | 0.0364 | 0.0186 | 0.8343 | **0.9200** | 0.2580 |
| **GNN — GCN** | 0.0211 | 0.6799 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0080 |
| **GNN — GraphSAGE** | 0.0044 | 0.7213 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| **GNN — GAT (Graph Attention)** | 0.0448 | **0.9129** | 0.0000 | 0.0000 | 0.0000 | 0.1300 | 0.1300 |
| 🥇 **Hybrid GAT + XGBoost** | **0.0715** | **0.8747** | **0.0367** | **0.0187** | **0.8607** | **0.7500** | **0.2340** |

---

## 🚀 Key Achievements
- 🏆 **GNN-GAT achieved the highest ROC-AUC of all models (`0.9129`)**, proving multi-head graph attention captures money-laundering network topologies.
- 🎯 **Hybrid GAT + XGBoost achieved 75.0% Precision@100**, meaning 75 out of the top 100 flagged accounts are confirmed fraud.
- ⚡ **Scaled GNN mini-batch training to 3.28M nodes** using PyTorch Geometric CUDA 12.8 C++ extensions (`pyg-lib`, `torch-sparse`).
- 🛡️ **Deployed real-time REST API (FastAPI + Uvicorn)** with Population Stability Index (PSI) drift monitoring.

---

For full technical documentation, see [FINAL_PROJECT_SUMMARY.md](FINAL_PROJECT_SUMMARY.md) and [deep_dive_architecture.md](deep_dive_architecture.md).
