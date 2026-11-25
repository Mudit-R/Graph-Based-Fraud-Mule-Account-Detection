"""Regression tests for typed graph links and deterministic ring summaries."""

import unittest

from src.graph.ring_detector import RingDetectionEngine


class RingIdentifierTests(unittest.TestCase):
    def setUp(self):
        self.engine = RingDetectionEngine()

    def test_identical_values_in_different_fields_do_not_connect(self):
        rings = self.engine.extract_rings_from_transactions(
            [
                {"transaction_id": "a", "device_id": "shared"},
                {"transaction_id": "b", "device_id": "shared"},
                {"transaction_id": "c", "ip_id": "shared"},
                {"transaction_id": "d", "ip_id": "shared"},
            ]
        )

        self.assertEqual([ring.transaction_ids for ring in rings], [["a", "b"], ["c", "d"]])
        self.assertEqual(rings[0].shared_devices, ["shared"])
        self.assertEqual(rings[0].shared_ips, [])
        self.assertEqual(rings[1].shared_ips, ["shared"])

    def test_transitive_links_and_report_order_are_deterministic(self):
        transactions = [
            {"transaction_id": "a", "customer_id": "z", "device_id": "device", "amount": 5.25},
            {"transaction_id": "b", "customer_id": "y", "device_id": "device", "ip_id": "ip", "amount": 15},
            {"transaction_id": "c", "customer_id": "x", "ip_id": "ip", "amount": 17.60},
            {"transaction_id": "d", "customer_id": "d", "promo_id": "promo"},
            {"transaction_id": "e", "customer_id": "e", "promo_id": "promo"},
        ]
        rings = self.engine.extract_rings_from_transactions(transactions)

        self.assertEqual(rings, self.engine.extract_rings_from_transactions(transactions[::-1]))
        self.assertEqual(rings[0].transaction_ids, ["a", "b", "c"])
        self.assertEqual(rings[0].member_accounts, ["x", "y", "z"])
        self.assertEqual(rings[0].total_exposure_inr, 37.85)
        self.assertEqual(rings[1].transaction_ids, ["d", "e"])

    def test_replayed_or_missing_ids_do_not_connect_unrelated_transactions(self):
        rings = self.engine.extract_rings_from_transactions(
            [
                {"transaction_id": "a", "device_id": "device"},
                {"transaction_id": "b", "device_id": "device"},
                {"transaction_id": "a", "bank_account_id": "bank"},
                {"transaction_id": "c", "bank_account_id": "bank"},
                {"device_id": "device", "bank_account_id": "bank"},
            ]
        )

        self.assertEqual([ring.transaction_ids for ring in rings], [["a", "b"]])

    def test_legacy_numeric_identifiers_can_coexist_with_strings(self):
        transactions = [
            {"transaction_id": 1, "customer_id": 2, "device_id": "shared"},
            {"transaction_id": "1", "customer_id": "2", "device_id": "shared"},
        ]

        [ring] = self.engine.extract_rings_from_transactions(transactions)

        self.assertEqual(ring.transaction_ids, [1, "1"])
        self.assertEqual(ring.member_accounts, [2, "2"])

    def test_high_degree_identifier_includes_every_unique_transaction(self):
        transactions = [
            {"transaction_id": f"{index:05d}", "device_id": "device"}
            for index in range(10_000)
        ]

        [ring] = self.engine.extract_rings_from_transactions(transactions)

        self.assertEqual(ring.transaction_count, 10_000)
        self.assertEqual(ring.transaction_ids, [item["transaction_id"] for item in transactions])


if __name__ == "__main__":
    unittest.main()
