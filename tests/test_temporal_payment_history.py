"""Exercise the temporal graph builder on explicitly ordered payments."""

from functools import partial
from pathlib import Path
import csv
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from scripts.evaluate_paysim_history import COLUMNS, chronological_steps
from src.graph.temporal_payment_history import RecentIncidence, load_payment_history


ROWS = [
    (1, "CASH_IN", 24, "A", 99, "B", 3, 0),
    (1, "CASH_OUT", 7, "B", 12, "C", 4, 1),
    (1, "DEBIT", 3, "D", 20, "A", 65, 0),
    (2, "PAYMENT", 5, "A", 4, "B", 10, 0),
    (2, "TRANSFER", 10, "B", 50, "A", 40, 1),
    (3, "CASH_OUT", 2, "A", 21, "C", 15, 0),
    (4, "CASH_OUT", 7, "B", 10, "A", 30, 0),
]


class TemporalPaymentHistoryTests(unittest.TestCase):
    def write_csv(self, rows):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "payments.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(COLUMNS + ["newbalanceOrig", "newbalanceDest", "isFlaggedFraud"])
            writer.writerows((*row, 999999999, 200, 1) for row in rows)
        return path

    def test_complete_steps_and_recency_across_csv_chunks(self):
        path = self.write_csv(ROWS)
        with patch("src.graph.temporal_payment_history.chronological_steps",
                   partial(chronological_steps, chunksize=2)):
            history = load_payment_history(path, neighbors=3, window=1)

        expected = [
            [[0, 0, 0], [0, 0, 0]],
            [[0, 0, 0], [0, 0, 0]],
            [[0, 0, 0], [0, 0, 0]],
            [[-3, 1, 0], [2, -1, 0]],
            [[2, -1, 0], [-3, 1, 0]],
            [[-5, 4, 0], [0, 0, 0]],
            [[0, 0, 0], [6, 0, 0]],
        ]
        np.testing.assert_array_equal(history.neighbor_ids, expected)
        np.testing.assert_array_equal(history.steps, [1, 1, 1, 2, 2, 3, 4])
        np.testing.assert_array_equal(history.labels, [0, 1, 0, 0, 1, 0, 0])
        self.assertEqual(history.targets.dtype, np.float32)
        self.assertEqual(history.steps.dtype, np.int32)
        self.assertEqual(history.labels.dtype, np.int8)
        self.assertEqual(history.neighbor_ids.dtype, np.int32)

    def test_row_limit_and_feature_layout(self):
        history = load_payment_history(self.write_csv(ROWS), rows=4, neighbors=1)
        expected = np.log1p([24, 99, 3])
        np.testing.assert_allclose(
            history.targets[0, :5],
            [*expected, expected[0] - expected[1], expected[0] - expected[2]],
            rtol=1e-6,
        )
        np.testing.assert_array_equal(history.targets[:4, 5:], np.eye(5, dtype=np.float32)[:4])
        np.testing.assert_array_equal(history.neighbor_ids[-1, :, 0], [-3, 2])
        self.assertEqual(history.targets.shape, (4, 10))

    def test_final_record_without_line_break_is_retained(self):
        path = self.write_csv(ROWS)
        path.write_bytes(path.read_bytes().rstrip(b"\r\n"))
        history = load_payment_history(path, rows=1_000_000, neighbors=1)
        self.assertEqual(len(history.steps), len(ROWS))
        self.assertEqual(history.steps[-1], 4)

    def test_self_transfer_records_incoming_and_outgoing_roles(self):
        history = load_payment_history(self.write_csv([
            (1, "PAYMENT", 5, "A", 10, "A", 10, 0),
            (2, "PAYMENT", 5, "B", 10, "A", 10, 0),
        ]), neighbors=2)
        np.testing.assert_array_equal(history.neighbor_ids[1], [[0, 0], [-1, 1]])

    def test_inactive_accounts_are_pruned_before_next_step(self):
        graph = RecentIncidence(neighbors=2)
        graph.commit_step(1, ["old"], ["shared"], first_id=1)
        graph.commit_step(3, ["shared"], ["new"], first_id=2)
        graph.expire(2)
        self.assertEqual(list(graph.accounts), ["shared", "new"])
        graph.expire(4)
        self.assertEqual(len(graph.accounts), 0)

    def test_rejects_payload_misalignment(self):
        with self.assertRaisesRegex(ValueError, "sorted"):
            load_payment_history(self.write_csv([ROWS[3], ROWS[0]]))
        with self.assertRaisesRegex(ValueError, "fraud labels"):
            malformed = (*ROWS[0][:-1], 2)
            load_payment_history(self.write_csv([malformed]))
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            load_payment_history(self.write_csv([(1, "DEBIT", -1, "A", 2, "B", 2, 0)]))
        with self.assertRaisesRegex(ValueError, "positive"):
            load_payment_history(self.write_csv(ROWS), neighbors=0)


if __name__ == "__main__":
    unittest.main()
