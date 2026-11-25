"""Build chronological, bounded transaction neighborhoods for the PaySim GNN."""

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from scripts.evaluate_paysim_history import COLUMNS, KINDS, chronological_steps


@dataclass(frozen=True)
class PaymentHistory:
    """Aligned event arrays, indexed by original CSV row order."""

    targets: np.ndarray
    steps: np.ndarray
    labels: np.ndarray
    neighbor_ids: np.ndarray


class RecentIncidence:
    """Keep the newest K incident events for accounts active in a short window."""

    def __init__(self, neighbors: int):
        self.neighbors = neighbors
        self.accounts = OrderedDict()

    def expire(self, earliest: int) -> None:
        while self.accounts:
            incidents = next(iter(self.accounts.values()))
            if incidents[-1][0] >= earliest:
                break
            self.accounts.popitem(last=False)

    def write_neighbors(self, origins, destinations, earliest, output) -> None:
        for endpoint, accounts in enumerate((origins, destinations)):
            for row, account in enumerate(accounts):
                incidents = self.accounts.get(account)
                if not incidents:
                    continue
                for column, (step, event_id) in enumerate(reversed(incidents)):
                    if step < earliest:
                        break
                    output[row, endpoint, column] = event_id

    def _append(self, account, step, event_id) -> None:
        incidents = self.accounts.get(account)
        if incidents is None:
            self.accounts[account] = [(step, event_id)]
            return

        incidents.append((step, event_id))
        if len(incidents) > self.neighbors:
            del incidents[0]
        self.accounts.move_to_end(account)

    def commit_step(self, step, origins, destinations, first_id) -> None:
        for event_id, (origin, destination) in enumerate(zip(origins, destinations), start=first_id):
            self._append(origin, step, event_id)
            self._append(destination, step, -event_id)


def _count_data_lines(source: Path) -> int:
    """Count a capacity bound for a plain CSV without materializing its rows."""
    line_breaks = 0
    last_byte = b""
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            line_breaks += block.count(b"\n")
            last_byte = block[-1:]
    return max(0, line_breaks + int(bool(last_byte) and last_byte != b"\n") - 1)


def _write_targets(payments, output: np.ndarray) -> None:
    numeric = payments[["amount", "oldbalanceOrg", "oldbalanceDest"]].to_numpy(dtype=np.float32)
    if not np.isfinite(numeric).all() or (numeric < 0).any():
        raise ValueError("Amounts and pre-event balances must be finite and nonnegative.")

    np.log1p(numeric, out=output[:, :3])
    output[:, 3:5] = output[:, :1] - output[:, 1:3]

    kinds = payments["type"].to_numpy()
    one_hot = np.column_stack([kinds == kind for kind in KINDS])
    if not one_hot.any(axis=1).all():
        raise ValueError("Unknown PaySim transaction type.")
    output[:, 5:] = one_hot


def load_payment_history(
    csv: Path,
    *,
    rows: int | None = None,
    neighbors: int = 8,
    window: int = 24,
) -> PaymentHistory:
    """Index earlier events using signed, 1-based IDs and zero padding.

    Endpoint 0 is the origin and 1 is the destination. Each endpoint's
    neighbors are ordered by recency, with later CSV rows first within an
    earlier step. Both directions of a self-transfer are incidences.

    Targets store log amount, log pre-event origin and destination balances,
    the two log amount-to-balance differences, and five transaction-type flags.
    An event's label and post-event balances are never used as features.
    """
    if (rows is not None and rows < 1) or neighbors < 1 or window < 1:
        raise ValueError("Rows, neighbors and window must be positive when specified.")

    path = Path(csv)
    capacity = _count_data_lines(path)
    if rows is not None:
        capacity = min(rows, capacity)
    max_index = np.iinfo(np.int32).max
    if capacity > max_index:
        raise ValueError("PaySim row count exceeds the range of signed int32 event IDs.")
    if capacity == 0:
        raise ValueError("No payment events in the input.")

    targets = np.empty((capacity, 5 + len(KINDS)), dtype=np.float32)
    steps = np.empty(capacity, dtype=np.int32)
    labels = np.empty(capacity, dtype=np.int8)
    neighbor_ids = np.zeros((capacity, 2, neighbors), dtype=np.int32)
    history = RecentIncidence(neighbors)
    start = 0

    with path.open(encoding="utf-8", newline="") as source:
        for step, payments in chronological_steps(source, rows):
            if not set(COLUMNS).issubset(payments.columns):
                raise ValueError("PaySim input must include all required columns.")
            step = int(step)
            if step < 0 or step > max_index or not (payments["step"] == step).all():
                raise ValueError("PaySim steps must be nonnegative int32 values.")
            stop = start + len(payments)
            if payments["nameOrig"].hasnans or payments["nameDest"].hasnans:
                raise ValueError("Payment endpoints must be present.")
            label_values = payments["isFraud"].to_numpy()
            if not np.isin(label_values, [0, 1]).all():
                raise ValueError("PaySim fraud labels must be 0 or 1.")

            _write_targets(payments, targets[start:stop])
            steps[start:stop] = step
            labels[start:stop] = label_values
            origins = payments["nameOrig"].to_numpy()
            destinations = payments["nameDest"].to_numpy()
            earliest = step - window

            history.expire(earliest)
            history.write_neighbors(origins, destinations, earliest, neighbor_ids[start:stop])
            history.commit_step(step, origins, destinations, first_id=start + 1)

            start = stop

    if start == 0:
        raise ValueError("No payment events in the input.")
    return PaymentHistory(targets[:start], steps[:start], labels[:start], neighbor_ids[:start])
