"""Compare ring traversal with the checked-in baseline on synthetic payments.

From the repository root, run: python -m scripts.benchmark_ring_traversal
Requires NumPy and a Git checkout containing the baseline commit.

Recorded local run (2026-11-25, Windows, Python 3.11.9):
    bf4837f [286.60, 284.39, 305.39, 294.42, 299.28] ms, median 294.42 ms
    current [5.93, 5.78, 6.77, 5.69, 5.71] ms, median 5.78 ms (51.0x faster)

Both runs preserved all 3,000 payments. This workload measures detector
execution on one high-degree synthetic component, outside the API and model.
"""

from pathlib import Path
from platform import python_version, system
from statistics import median
import subprocess
import sys
from time import perf_counter
from types import ModuleType

from src.graph.ring_detector import RingDetectionEngine


BASELINE_COMMIT = "bf4837fa520b386e360b4ed1897ee92cce5bd89b"
DETECTOR_PATH = "src/graph/ring_detector.py"
EVENT_COUNT = 3_000
REPEATS = 5


def load_baseline():
    """Load the original implementation from a fixed project commit."""
    repository = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "show", f"{BASELINE_COMMIT}:{DETECTOR_PATH}"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    baseline = ModuleType("_ring_traversal_baseline")
    sys.modules[baseline.__name__] = baseline
    exec(compile(result.stdout, f"{BASELINE_COMMIT}:{DETECTOR_PATH}", "exec"), vars(baseline))
    return baseline.RingDetectionEngine()


def main():
    transactions = [
        {
            "transaction_id": f"payment-{index:05d}",
            "customer_id": f"customer-{index:05d}",
            "device_id": "shared-device",
            "amount": 1,
            "timestamp": index,
        }
        for index in range(EVENT_COUNT)
    ]
    expected_customers = {transaction["customer_id"] for transaction in transactions}
    expected_payments = [transaction["transaction_id"] for transaction in transactions]
    baseline = load_baseline()
    current = RingDetectionEngine()
    measurements = {"baseline": [], "current": []}

    for _ in range(REPEATS):
        for name, detector in [("baseline", baseline), ("current", current)]:
            start = perf_counter()
            [ring] = detector.extract_rings_from_transactions(transactions)
            measurements[name].append(1000 * (perf_counter() - start))
            if (
                ring.transaction_count != EVENT_COUNT
                or set(ring.member_accounts) != expected_customers
                or ring.total_exposure_inr != EVENT_COUNT
                or (name == "current" and ring.transaction_ids != expected_payments)
            ):
                raise RuntimeError(f"{name} failed to preserve the complete synthetic ring.")

    print(f"{EVENT_COUNT:,} synthetic payments, one shared device, {REPEATS} runs per version")
    print(f"{system()}, Python {python_version()}, baseline {BASELINE_COMMIT[:7]}")
    for name, samples in measurements.items():
        print(f"{name}: samples_ms={[round(value, 2) for value in samples]}")
        print(f"{name}: median_ms={median(samples):.2f}")
    print(f"median_speedup={median(measurements['baseline']) / median(measurements['current']):.1f}x")
    print(f"Both implementations preserved all {EVENT_COUNT:,} payments.")


if __name__ == "__main__":
    main()
