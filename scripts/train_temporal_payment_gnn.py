"""Train and evaluate a causal payment-neighborhood GNN on PaySim."""
import argparse
from dataclasses import asdict, dataclass
from hashlib import file_digest
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from sklearn.metrics import average_precision_score
import torch
from torch import nn

from scripts.evaluate_paysim_history import temporal_split
from src.evaluation.temporal_ranking import ranking_metrics, select_threshold
from src.graph.temporal_payment_history import load_payment_history
from src.models.temporal_payment_gnn import TemporalPaymentGNN


FEATURE_NAMES = (
    "log_amount", "cash_in", "cash_out", "debit", "payment", "transfer",
    "hour_sin", "hour_cos", "log_prior_hour_volume", "log_prior_3h_volume",
    "log_prior_24h_volume",
)


@dataclass(frozen=True)
class TrainingConfig:
    seed: int = 2026
    neighbors: int = 8
    window: int = 24
    width: int = 32
    heads: int = 4
    train_negatives: int = 180_000
    selection_negatives: int = 90_000
    batch_size: int = 2048
    score_batch_size: int = 8192
    epochs: int = 10
    patience: int = 4
    learning_rate: float = 0.0015
    positive_weight: float = 2.0


def fraud_inputs(history):
    """Use amount, type, clock and completed-hour traffic. Balances stay excluded."""
    steps = history.steps
    counts = np.bincount(steps)
    completed = np.concatenate(([0], np.cumsum(counts)))
    hours = steps * (2 * np.pi / 24)
    previous_counts = [
        np.log1p(completed[steps] - completed[np.maximum(steps - lookback, 0)])
        for lookback in (1, 3, 24)
    ]
    features = np.empty((len(steps), len(FEATURE_NAMES)), dtype=np.float32)
    features[:, 0] = history.targets[:, 0]
    features[:, 1:6] = history.targets[:, 5:10]
    features[:, 6] = np.sin(hours)
    features[:, 7] = np.cos(hours)
    for column, activity in enumerate(previous_counts, start=8):
        features[:, column] = activity
    return features


class PaymentTensors:
    """Gather target and prior incident event tensors without materializing all messages."""

    def __init__(self, history, train_ids, window, device):
        self.device = device
        self.features = torch.from_numpy(fraud_inputs(history)).to(device)
        train_features = self.features[torch.as_tensor(train_ids, device=device)]
        self.center = train_features.mean(0)
        self.scale = train_features.std(0, unbiased=False).clamp(min=0.01)
        self.features.sub_(self.center).div_(self.scale).clamp_(-12, 12)
        self.steps = torch.from_numpy(history.steps).to(device)
        self.neighbor_ids = torch.from_numpy(history.neighbor_ids).to(device)
        self.labels = torch.from_numpy(history.labels).to(device, dtype=torch.float32)
        self.age_scale = float(np.log1p(window))

    def predict(self, model, rows, use_history):
        index = torch.as_tensor(rows, dtype=torch.long, device=self.device)
        target = self.features[index]
        if not use_history:
            return model(target)

        signed = self.neighbor_ids[index]
        mask = signed.ne(0)
        history_index = signed.abs().long().sub_(1).clamp_(min=0)
        age = (self.steps[index, None, None] - self.steps[history_index]).clamp_(min=0)
        age = age.to(torch.float32).log1p_().div_(self.age_scale)
        messages = torch.cat(
            (self.features[history_index], age.unsqueeze(-1), signed.lt(0).unsqueeze(-1)),
            dim=-1,
        )
        return model(target, messages, mask)


def keep_positives(ids, labels, negative_cap, rng):
    """Subsample negatives only within training or checkpoint-selection windows."""
    subset = labels[ids]
    positives = ids[subset == 1]
    negatives = ids[subset == 0]
    if len(negatives) > negative_cap:
        negatives = rng.choice(negatives, size=negative_cap, replace=False)
    return np.sort(np.concatenate((positives, negatives)))


@torch.inference_mode()
def score(model, tensors, ids, use_history, batch_size):
    model.eval()
    scores = np.empty(len(ids), dtype=np.float32)
    for start in range(0, len(ids), batch_size):
        stop = min(start + batch_size, len(ids))
        logits = tensors.predict(model, ids[start:stop], use_history)
        scores[start:stop] = torch.sigmoid(logits).cpu().numpy()
    return scores


def train_model(tensors, labels, train_ids, selection_ids, config, use_history):
    torch.manual_seed(config.seed)
    rng = np.random.default_rng(config.seed)
    device = tensors.device
    model = TemporalPaymentGNN(
        len(FEATURE_NAMES), len(FEATURE_NAMES) + 2, config.width, config.heads, use_history
    ).to(device)
    name = "temporal_gnn" if use_history else "target_only"
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate, weight_decay=1e-4)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(config.positive_weight, device=device))
    best_ap = -1.0
    best_epoch = 0
    best_state = None
    selection_trace = []
    for epoch in range(1, config.epochs + 1):
        model.train()
        shuffled = rng.permutation(train_ids)
        for start in range(0, len(shuffled), config.batch_size):
            rows = shuffled[start:start + config.batch_size]
            optimizer.zero_grad(set_to_none=True)
            logits = tensors.predict(model, rows, use_history)
            targets = tensors.labels[torch.as_tensor(rows, device=device)]
            loss_fn(logits, targets).backward()
            optimizer.step()
        selection_scores = score(model, tensors, selection_ids, use_history, config.score_batch_size)
        selection_ap = float(average_precision_score(labels[selection_ids], selection_scores))
        print(f"{name} epoch {epoch}: selection AP {selection_ap:.6f}", flush=True)
        selection_trace.append(selection_ap)
        if selection_ap > best_ap:
            best_ap = selection_ap
            best_epoch = epoch
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
        if epoch - best_epoch >= config.patience:
            break
    model.load_state_dict(best_state)
    return model, best_epoch, selection_trace


def split_report(history, split_ids):
    partitions = {}
    for name, ids in zip(("train", "validation", "test"), split_ids):
        labels = history.labels[ids]
        fraud = int(labels.sum())
        if not 0 < fraud < len(ids) or not np.isin(labels, [0, 1]).all():
            raise ValueError(f"{name} must contain valid binary labels and both classes.")
        steps = history.steps[ids]
        partitions[name] = {
            "first_step": int(steps[0]),
            "last_step": int(steps[-1]),
            "payments": len(ids),
            "fraud_payments": fraud,
        }
    return partitions


def run(csv, rows, output, report_file, config, device):
    start = perf_counter()
    with csv.open("rb") as dataset_file:
        sha256 = file_digest(dataset_file, "sha256").hexdigest()
    history = load_payment_history(csv, rows=rows, neighbors=config.neighbors, window=config.window)
    dataset_count = len(history.labels)
    train_all, validation_ids, test_ids = tuple(np.flatnonzero(mask) for mask in temporal_split(history.steps))
    partitions = split_report(history, (train_all, validation_ids, test_ids))
    rng = np.random.default_rng(config.seed)
    train_ids = keep_positives(train_all, history.labels, config.train_negatives, rng)
    selection_ids = keep_positives(validation_ids, history.labels, config.selection_negatives, rng)
    print(f"Prepared {dataset_count:,} payments in {perf_counter() - start:.1f}s. "
          f"Fitting on {len(train_ids):,}. Test has {len(test_ids):,} rows.", flush=True)
    labels = history.labels
    tensors = PaymentTensors(history, train_ids, config.window, device)
    del history
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "scope": "PaySim simulated transactions, chronological train/validation/test split",
        "dataset_file": csv.name,
        "dataset_sha256": sha256,
        "payments": dataset_count,
        "target_features": list(FEATURE_NAMES),
        "message_features_added": ["normalized_log_age", "incoming_at_endpoint"],
        "partitions": partitions,
        "training_rows": len(train_ids),
        "training_fraud_rows": int(labels[train_ids].sum()),
        "selection_rows": len(selection_ids),
        "config": asdict(config),
        "device": str(device),
        "torch_version": torch.__version__,
        "models": {},
    }
    for name, use_history in (("temporal_gnn", True), ("target_only", False)):
        model, epoch, trace = train_model(tensors, labels, train_ids, selection_ids, config, use_history)
        validation_scores = score(model, tensors, validation_ids, use_history, config.score_batch_size)
        threshold = select_threshold(labels[validation_ids], validation_scores)
        test_scores = score(model, tensors, test_ids, use_history, config.score_batch_size)
        metrics = ranking_metrics(labels[test_ids], test_scores, threshold=threshold)
        report["models"][name] = {
            "selected_epoch": epoch,
            "validation_checkpoint_selection_ap": trace,
            "test": metrics,
        }
        print(f"{name} test AUC {metrics['roc_auc']:.6f}, AP {metrics['average_precision']:.6f}", flush=True)
        torch.save({
            "model": {key: value.cpu() for key, value in model.state_dict().items()},
            "target_features": list(FEATURE_NAMES),
            "feature_center": tensors.center.cpu(),
            "feature_scale": tensors.scale.cpu(),
            "threshold": threshold,
            "config": asdict(config),
            "use_history": use_history,
        }, output / f"{name}.pt")
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_text = json.dumps(report, indent=2) + "\n"
    report_file.write_text(report_text, encoding="utf-8")
    print(report_text, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--rows", type=int, default=0, help="Optional chronological row cap. Zero uses the full file.")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--epochs", type=int, default=TrainingConfig.epochs)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--train-negatives", type=int, default=TrainingConfig.train_negatives)
    parser.add_argument("--output", type=Path, default=Path("outputs/temporal_payment_gnn"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.rows < 0 or min(args.epochs, args.threads, args.train_negatives) < 1:
        parser.error("Use a nonnegative row cap and positive training sizes.")
    if args.device == "cuda" and not torch.cuda.is_available():
        parser.error("This PyTorch installation has no available CUDA device.")
    device_name = ("cuda" if torch.cuda.is_available() else "cpu") if args.device == "auto" else args.device
    device = torch.device(device_name)
    torch.set_num_threads(args.threads)
    config = TrainingConfig(epochs=args.epochs, train_negatives=args.train_negatives)
    run(args.csv, args.rows or None, args.output, args.report or args.output / "metrics.json", config, device)


if __name__ == "__main__":
    main()
