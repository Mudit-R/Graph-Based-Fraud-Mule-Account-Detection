"""Independent checks for temporal ranking and threshold metrics."""

import unittest

import numpy as np

from src.evaluation.temporal_ranking import ranking_metrics, select_threshold


class TemporalRankingTests(unittest.TestCase):
    def test_select_threshold_searches_every_distinct_score_group(self):
        labels = np.array([0, 1, 0, 1, 1])
        scores = np.array([0.93, 0.491, 0.587, 0.491, 0.687])
        self.assertEqual(select_threshold(labels, scores), 0.491)

    def test_equal_f1_uses_the_highest_cutoff(self):
        # One true positive at 0.9 and two true positives at 0.6 both yield F1 2/3.
        labels = np.array([1, 0, 0, 1])
        scores = np.array([0.9, 0.8, 0.7, 0.6])
        self.assertEqual(select_threshold(labels, scores), 0.9)

    def test_threshold_never_splits_a_tied_score(self):
        scores = np.array([0.9, 0.9, 0.2])
        first = np.array([1, 0, 1])
        second = np.array([0, 1, 1])
        self.assertEqual(select_threshold(first, scores), 0.2)
        self.assertEqual(select_threshold(second, scores), 0.2)

    def test_fixed_validation_threshold_is_never_retuned_on_test_scores(self):
        threshold = select_threshold(np.array([1, 0]), np.array([0.8, 0.4]))
        metrics = ranking_metrics(np.array([0, 1]), np.array([0.91, 0.75]), threshold=threshold)
        self.assertEqual(metrics["threshold"], 0.8)
        self.assertEqual(metrics["f1"], 0)
        self.assertEqual(metrics["precision"], 0)
        self.assertEqual(metrics["recall"], 0)
        self.assertEqual(metrics["roc_auc"], 0)
        self.assertEqual(metrics["average_precision"], 0.5)

    def test_probability_scores_and_review_counts_have_distinct_denominators(self):
        labels = np.array([1, 0, 1, 0])
        scores = np.array([0.9, 0.8, 0.7, 0.2])
        metrics = ranking_metrics(labels, scores, threshold=0.7)
        self.assertEqual(metrics["positive_count"], 2)
        self.assertEqual(metrics["sample_count"], 4)
        self.assertAlmostEqual(metrics["roc_auc"], 0.75)
        self.assertAlmostEqual(metrics["average_precision"], (1 + 2 / 3) / 2)
        self.assertAlmostEqual(metrics["f1"], 0.8)
        self.assertAlmostEqual(metrics["precision"], 2 / 3)
        self.assertEqual(metrics["recall"], 1)
        for budget in (100, 500, 1000):
            self.assertEqual(metrics[f"reviewed_at_{budget}"], 4)
            self.assertEqual(metrics[f"true_positives_at_{budget}"], 2)
            self.assertEqual(metrics[f"precision_at_{budget}"], 0.5)
            self.assertEqual(metrics[f"recall_at_{budget}"], 1)

    def test_review_budgets_use_input_order_to_break_score_ties(self):
        labels = np.zeros(1200, dtype=np.int8)
        labels[[10, 100, 500, 1000]] = 1
        metrics = ranking_metrics(labels, np.full(len(labels), 0.5), threshold=0.51)
        self.assertAlmostEqual(metrics["average_precision"], 4 / 1200)
        self.assertEqual(metrics["roc_auc"], 0.5)
        for budget, caught in ((100, 1), (500, 2), (1000, 3)):
            self.assertEqual(metrics[f"reviewed_at_{budget}"], budget)
            self.assertEqual(metrics[f"true_positives_at_{budget}"], caught)
            self.assertEqual(metrics[f"precision_at_{budget}"], caught / budget)
            self.assertEqual(metrics[f"recall_at_{budget}"], caught / 4)

    def test_rejects_invalid_input_instead_of_reporting_uninterpretable_scores(self):
        for labels, scores in (
            ([], []), ([0, 1], [0]), ([[0, 1]], [[0.2, 0.3]]), ([0, 2], [0, 1]),
            ([0, 1], [np.nan, 1]), ([0, 1], [np.inf, 1]), ([1, 1], [0, 1]),
        ):
            with self.subTest(labels=labels, scores=scores):
                with self.assertRaises(ValueError):
                    select_threshold(np.asarray(labels), np.asarray(scores))
                with self.assertRaises(ValueError):
                    ranking_metrics(np.asarray(labels), np.asarray(scores), threshold=0.5)
        with self.assertRaises(ValueError):
            ranking_metrics(np.array([0, 1]), np.array([0.1, 0.9]), threshold=np.nan)
