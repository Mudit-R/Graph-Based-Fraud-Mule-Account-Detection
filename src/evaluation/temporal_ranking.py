"""Binary ranking metrics for separately scored validation and test partitions."""

from __future__ import annotations

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score


def _binary_vectors(labels: np.ndarray, scores: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    labels = np.asarray(labels)
    scores = np.asarray(scores, dtype=np.float64)
    if labels.ndim != 1 or scores.shape != labels.shape or labels.size == 0:
        raise ValueError("labels and scores must be equal-length nonempty vectors")
    if not np.all((labels == 0) | (labels == 1)) or not np.all(np.isfinite(scores)):
        raise ValueError("labels must be binary and scores must be finite")
    positive = labels.astype(bool, copy=False)
    if not np.any(positive) or np.all(positive):
        raise ValueError("ranking evaluation requires both label classes")
    return positive, scores


def select_threshold(labels: np.ndarray, scores: np.ndarray) -> float:
    """Maximize F1 on validation scores, preferring the highest cutoff on ties.

    Classify a score equal to the cutoff as positive. Only consider boundaries
    after complete equal-score groups, since a threshold cannot split a tie.
    """
    positive, scores = _binary_vectors(labels, scores)
    order = np.argsort(-scores, kind="stable")
    ranked_scores = scores[order]
    true_positives = np.cumsum(positive[order], dtype=np.intp)
    ends = np.r_[np.flatnonzero(ranked_scores[:-1] != ranked_scores[1:]), scores.size - 1]
    denominator = true_positives[-1] + ends + 1
    f1_values = 2 * true_positives[ends] / denominator
    return float(ranked_scores[ends[np.argmax(f1_values)]])


def ranking_metrics(
    labels: np.ndarray,
    scores: np.ndarray,
    *,
    threshold: float,
) -> dict[str, int | float]:
    """Measure scores on a partition using a previously selected threshold."""
    positive, scores = _binary_vectors(labels, scores)
    if not np.isfinite(threshold):
        raise ValueError("threshold must be finite")

    positives = int(positive.sum())
    predicted = scores >= threshold
    predicted_count = int(predicted.sum())
    true_positive = int(np.count_nonzero(predicted & positive))
    result = {
        "sample_count": scores.size,
        "positive_count": positives,
        "threshold": float(threshold),
        "roc_auc": float(roc_auc_score(positive, scores)),
        "average_precision": float(average_precision_score(positive, scores)),
        "precision": true_positive / predicted_count if predicted_count else 0.0,
        "recall": true_positive / positives,
        "f1": 2 * true_positive / (positives + predicted_count),
    }

    # Stable descending sort keeps the original input order for exact ties.
    ranked = positive[np.argsort(-scores, kind="stable")[:1000]]
    for budget in (100, 500, 1000):
        reviewed = min(budget, scores.size)
        caught = int(ranked[:reviewed].sum())
        result[f"reviewed_at_{budget}"] = reviewed
        result[f"true_positives_at_{budget}"] = caught
        result[f"precision_at_{budget}"] = caught / reviewed
        result[f"recall_at_{budget}"] = caught / positives
    return result
