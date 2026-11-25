# Point-in-time PaySim evaluation

This benchmark measures what historical transaction-graph features add to a
tabular fraud-ranking baseline. It uses PaySim's **simulated** payment events.
Both models are logistic regression. The existing GATv2 and XGBoost experiments
are separate from this evaluation.

## Reproduce

From the repository root, with the project's NumPy, pandas and scikit-learn
dependencies installed:

    python -m scripts.evaluate_paysim_history path/to/PS_20174392719_1491204439457_log.csv --rows 0
    python -m pytest -q tests/test_paysim_history.py

Run without **--rows 0** to evaluate the first 1,000,000 events. The script
outputs machine-readable partition counts, metrics and validation thresholds.

The tabular features use the amount, transaction category and balances before
the transaction. The graph feature set adds incoming and outgoing payment
counts for the two accounts over the previous 24 simulation steps. Each
step is scored before any of its edges are added to history. Step boundaries
are respected even when the CSV reader splits a step across chunks. The
decision threshold is selected using validation labels before the test metrics
are computed.

## Full-dataset result

Measured on 2026-11-25 with **6,362,620** events, including **8,213** labeled
fraud events. Training was restricted to steps 1–281 (3,820,599 events),
validation to steps 282–355 (1,293,285 events), and the test to steps 356–743
(1,248,736 events with 4,250 positive labels).

| Held-out metric | Tabular | Tabular + historical graph |
| --- | ---: | ---: |
| Average precision | 0.5234 | 0.5202 |
| ROC-AUC | 0.9722 | 0.9747 |
| F1 at validation-selected threshold | 0.4617 | 0.4767 |
| Precision@100 | 1.0000 | 1.0000 |

The graph signals improved thresholded F1 and ROC-AUC on this test and
slightly reduced average precision. Both models found 100 labeled fraud
payments in their top 100. These measurements describe the PaySim experiment.
They provide no estimate for real merchant payments.

## Ring traversal benchmark

    python -m scripts.benchmark_ring_traversal

On a separate 3,000-payment shared-device fixture, five runs per version
measured **294.42 ms → 5.78 ms** median traversal time (**51.0×**) against
commit **bf4837f**. Both versions preserved all 3,000 fixture payments.
The result measures connected-component traversal.
