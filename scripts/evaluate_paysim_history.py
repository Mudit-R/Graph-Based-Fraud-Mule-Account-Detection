"""Compare tabular and historical graph features on chronologically split PaySim payments."""
import argparse
from collections import Counter, deque
from pathlib import Path
import json

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, f1_score, precision_recall_curve, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


COLUMNS = ["step", "type", "amount", "nameOrig", "oldbalanceOrg", "nameDest", "oldbalanceDest", "isFraud"]
KINDS = ("CASH_IN", "CASH_OUT", "DEBIT", "PAYMENT", "TRANSFER")


class GraphHistory:
    """Directed degree counts from prior steps. Score a whole step before updating."""

    def __init__(self, window):
        self.window = window
        self.outgoing = Counter()
        self.incoming = Counter()
        self.buckets = deque()

    def score_then_update(self, step, origins, destinations):
        while self.buckets and self.buckets[0][0] < step - self.window:
            _, old_outgoing, old_incoming = self.buckets.popleft()
            for count, expired in ((self.outgoing, old_outgoing), (self.incoming, old_incoming)):
                for account, amount in expired.items():
                    remaining = count[account] - amount
                    if remaining:
                        count[account] = remaining
                    else:
                        del count[account]

        features = np.column_stack([
            np.fromiter((counter.get(account, 0) for account in accounts), np.float32, len(accounts))
            for accounts in (origins, destinations)
            for counter in (self.outgoing, self.incoming)
        ])
        new_outgoing = Counter(origins)
        new_incoming = Counter(destinations)
        self.outgoing.update(new_outgoing)
        self.incoming.update(new_incoming)
        self.buckets.append((step, new_outgoing, new_incoming))
        return np.log1p(features)


def chronological_steps(source, limit, chunksize=100_000):
    pending = None
    for chunk in pd.read_csv(source, usecols=COLUMNS, chunksize=chunksize, nrows=limit):
        if chunk.empty:
            continue
        if pending is not None:
            chunk = pd.concat([pending, chunk], ignore_index=True)
        if not chunk["step"].is_monotonic_increasing:
            raise ValueError("PaySim rows must be sorted by simulation step.")
        final_step = chunk["step"].iat[-1]
        complete = chunk["step"] < final_step
        yield from chunk.loc[complete].groupby("step", sort=False)
        pending = chunk.loc[~complete]
    if pending is not None:
        yield int(pending["step"].iat[0]), pending


def load_features(source, limit, window):
    history = GraphHistory(window)
    meta, base_data, graph_data, labels = [], [], [], []
    for step, payments in chronological_steps(source, limit):
        amount = payments[["amount", "oldbalanceOrg", "oldbalanceDest"]].to_numpy(dtype=np.float32)
        kinds = payments["type"].to_numpy()
        base_data.append(np.column_stack([np.log1p(amount), *(kinds == kind for kind in KINDS)]))
        graph_data.append(history.score_then_update(
            int(step), payments["nameOrig"].to_numpy(), payments["nameDest"].to_numpy()
        ))
        meta.append(np.full(len(payments), step, dtype=np.int32))
        labels.append(payments["isFraud"].to_numpy(dtype=np.int8))
    if not labels:
        raise ValueError("No payment events in the input.")
    return tuple(np.concatenate(part) for part in (base_data, graph_data, labels, meta))


def temporal_split(steps):
    """Split whole simulation steps, using roughly 60/20/20 percent of rows."""
    distinct, counts = np.unique(steps, return_counts=True)
    if len(distinct) < 3:
        raise ValueError("Need at least three distinct payment steps.")
    cumulative = np.cumsum(counts)
    train_i = min(np.searchsorted(cumulative, len(steps) * .60), len(distinct) - 3)
    validation_i = min(max(train_i + 1, np.searchsorted(cumulative, len(steps) * .80)),
                       len(distinct) - 2)
    training_end, validation_end = distinct[[train_i, validation_i]]
    return steps <= training_end, (steps > training_end) & (steps <= validation_end), steps > validation_end


def evaluate(features, labels, masks):
    training, validation, test = masks
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=200, random_state=0))
    model.fit(features[training], labels[training])
    val_scores = model.predict_proba(features[validation])[:, 1]
    precision, recall, thresholds = precision_recall_curve(labels[validation], val_scores)
    val_f1 = 2 * precision[:-1] * recall[:-1] / np.maximum(precision[:-1] + recall[:-1], 1e-12)
    threshold = float(thresholds[np.argmax(val_f1)])
    scores = model.predict_proba(features[test])[:, 1]
    truth = labels[test]
    top = np.argsort(-scores, kind="stable")[:min(100, len(truth))]
    return {
        "test_average_precision": float(average_precision_score(truth, scores)),
        "test_roc_auc": float(roc_auc_score(truth, scores)),
        "test_precision_at_100": float(truth[top].mean()),
        "test_f1_at_validation_threshold": float(f1_score(truth, scores >= threshold, zero_division=0)),
        "validation_threshold": threshold,
    }


def benchmark(source, limit, window):
    tabular, graph, labels, steps = load_features(source, limit, window)
    masks = temporal_split(steps)
    partitions = {}
    for name, mask in zip(("train", "validation", "test"), masks):
        partition_labels = labels[mask]
        if set(np.unique(partition_labels)) != {0, 1}:
            raise ValueError(f"{name} must include legitimate and fraudulent payments.")
        partitions[name] = {
            "first_step": int(steps[mask].min()),
            "last_step": int(steps[mask].max()),
            "payments": int(mask.sum()),
            "fraud_payments": int(partition_labels.sum()),
        }
    return {
        "scope": "PaySim simulated payments. Graph degree features use strictly earlier steps.",
        "observations": len(labels),
        "lookback_steps": window,
        "partitions": partitions,
        "tabular_logistic": evaluate(tabular, labels, masks),
        "tabular_and_graph_history_logistic": evaluate(np.column_stack([tabular, graph]), labels, masks),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--rows", type=int, default=1_000_000, help="Chronological row cap, 0 for all.")
    parser.add_argument("--window", type=int, default=24, help="Prior simulation steps retained.")
    args = parser.parse_args()
    if args.rows < 0 or args.window < 1:
        parser.error("--rows must be non-negative and --window must be positive")
    print(json.dumps(benchmark(args.csv, args.rows or None, args.window), indent=2))


if __name__ == "__main__":
    main()
