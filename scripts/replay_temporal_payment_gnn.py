"""Recompute the held-out ranking metrics from a saved temporal model checkpoint."""
import argparse
from hashlib import file_digest
import json
import math
from pathlib import Path

import numpy as np
import torch

from scripts.evaluate_paysim_history import temporal_split
from scripts.train_temporal_payment_gnn import FEATURE_NAMES, PaymentTensors, keep_positives, score
from src.evaluation.temporal_ranking import ranking_metrics
from src.graph.temporal_payment_history import load_payment_history
from src.models.temporal_payment_gnn import TemporalPaymentGNN


def verify(csv, checkpoint_path, report_path, threads):
    torch.set_num_threads(threads)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    with csv.open("rb") as source:
        if file_digest(source, "sha256").hexdigest() != report["dataset_sha256"]:
            raise ValueError("CSV does not match the dataset recorded in the training report.")

    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    config = checkpoint["config"]
    if config != report["config"] or tuple(checkpoint["target_features"]) != FEATURE_NAMES:
        raise ValueError("Checkpoint configuration does not match the training report.")
    history = load_payment_history(
        csv, rows=report["payments"], neighbors=config["neighbors"], window=config["window"]
    )
    train_all, _, test = [np.flatnonzero(mask) for mask in temporal_split(history.steps)]
    train = keep_positives(
        train_all, history.labels, config["train_negatives"], np.random.default_rng(config["seed"])
    )
    device = torch.device("cpu")
    tensors = PaymentTensors(history, train, config["window"], device)
    torch.testing.assert_close(tensors.center, checkpoint["feature_center"])
    torch.testing.assert_close(tensors.scale, checkpoint["feature_scale"])
    model = TemporalPaymentGNN(
        len(FEATURE_NAMES), len(FEATURE_NAMES) + 2, config["width"], config["heads"],
        checkpoint["use_history"],
    ).to(device)
    model.load_state_dict(checkpoint["model"])
    predictions = score(model, tensors, test, checkpoint["use_history"], config["score_batch_size"])
    result = ranking_metrics(history.labels[test], predictions, threshold=checkpoint["threshold"])
    name = "temporal_gnn" if checkpoint["use_history"] else "target_only"
    expected = report["models"][name]["test"]
    mismatched = [key for key in expected if not math.isclose(expected[key], result[key], abs_tol=1e-5)]
    if mismatched:
        raise ValueError(f"Checkpoint results differ for: {', '.join(mismatched)}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--threads", type=int, default=6)
    args = parser.parse_args()
    if args.threads < 1:
        parser.error("--threads must be positive.")
    print(json.dumps(verify(args.csv, args.checkpoint, args.report, args.threads), indent=2))
    print("Checkpoint matched the reported held-out metrics.")


if __name__ == "__main__":
    main()
