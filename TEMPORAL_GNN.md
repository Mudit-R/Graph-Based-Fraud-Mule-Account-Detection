# Temporal GNN evaluation

The temporal payment GNN ranks individual PaySim payments using the target's
amount, type, hour of day and traffic from completed hours. The model applies
four-head attention to the origin and destination neighborhoods separately.
Each endpoint contributes up to eight earlier transactions from a 24-step
window. Messages include direction and age. Both target and message features
exclude balance fields and simulator fraud flags. The current hour enters the
neighborhood index after all predictions for that hour have been prepared.

PaySim generates synthetic mobile-money transactions. The implementation draws
on the temporal message-passing formulation in Rossi et al. [PaySim simulator
paper](https://www.inderscience.com/info/inarticle.php?artid=93756) ·
[Temporal Graph Networks](https://arxiv.org/abs/2006.10637)

## Holdout results

The run read all **6,362,620 payments**. It trained each model on the same
**183,193** sampled training rows, including all 3,193 training positives.
Checkpoint selection used a fixed validation subset. The decision threshold
came from the full validation window. The final test kept its original class
distribution with **1,248,736 payments and 4,250 positive labels**.

| Simulation steps | Payments | Fraud labels | Purpose |
| --- | ---: | ---: | --- |
| 1–281 | 3,820,599 | 3,193 | Training pool |
| 282–355 | 1,293,285 | 770 | Checkpoint and threshold selection |
| 356–743 | 1,248,736 | 4,250 | Held-out test |

| Held-out metric | Target-only neural model | Temporal GNN |
| --- | ---: | ---: |
| ROC-AUC | 0.9455 | **0.9518** |
| Average precision | 0.1590 | 0.1318 |
| F1 at the validation threshold | 0.0761 | **0.1472** |
| Precision at 100 reviews | 1.000 | **0.960** |
| Precision at 500 reviews | 0.714 | 0.532 |
| Precision at 1,000 reviews | 0.498 | 0.354 |

The graph model found **96 labeled frauds in its top 100 test payments**.
The 100-review budget covered 2.26% of all 4,250 test positives. The GNN
improved ROC-AUC and F1. The target-only model led on average precision and
fixed review budgets on this run. The results apply to this simulated dataset
and chronological split.

## Reproduce

Use Python with PyTorch, NumPy, pandas and scikit-learn. Place the [PaySim
CSV](https://www.kaggle.com/datasets/ealaxi/paysim1) at
data/raw/PS_20174392719_1491204439457_log.csv.

    python -m scripts.train_temporal_payment_gnn data/raw/PS_20174392719_1491204439457_log.csv --device cpu --report outputs/temporal_payment_gnn_metrics.json

The [raw metrics](outputs/temporal_payment_gnn_metrics.json) include the dataset
SHA-256, the exact split sizes, the seed, the validation curves and the complete
test metrics. The trainer writes both model checkpoints to
outputs/temporal_payment_gnn/. Verify the saved GNN against the reported holdout:

    python -m scripts.replay_temporal_payment_gnn data/raw/PS_20174392719_1491204439457_log.csv outputs/temporal_payment_gnn/temporal_gnn.pt outputs/temporal_payment_gnn_metrics.json

The [analyst review console](CASE_REVIEW.md) explores saved evidence graphs
and records decisions. Its ring-traversal benchmark is documented separately
in the [PaySim baseline report](PAYSIM_EVALUATION.md).
