import numpy as np
import torch

from scripts.train_temporal_payment_gnn import FEATURE_NAMES, PaymentTensors, fraud_inputs
from src.graph.temporal_payment_history import PaymentHistory


def fixture():
    return PaymentHistory(
        targets=np.arange(50, dtype=np.float32).reshape(5, 10),
        steps=np.array([1, 1, 2, 2, 5], dtype=np.int32),
        labels=np.array([0, 1, 0, 1, 0], dtype=np.int8),
        neighbor_ids=np.array([
            [[0], [0]], [[0], [0]], [[-2], [1]], [[1], [0]], [[3], [-4]]
        ], dtype=np.int32),
    )


def test_model_features_exclude_balances_and_use_completed_hours():
    history = fixture()
    original = fraud_inputs(history)
    history.targets[:, 1:5] = 5e9
    np.testing.assert_array_equal(fraud_inputs(history), original)
    assert original.shape == (5, len(FEATURE_NAMES))
    np.testing.assert_allclose(original[:, 8], np.log1p([0, 0, 2, 2, 0]))
    np.testing.assert_allclose(original[:, 9], np.log1p([0, 0, 2, 2, 2]))


def test_gather_preserves_direction_age_and_padding():
    history = fixture()
    tensors = PaymentTensors(history, np.arange(4), 24, torch.device("cpu"))

    def inspect(target, messages, mask):
        torch.testing.assert_close(target, tensors.features[[2, 3]])
        torch.testing.assert_close(mask, torch.tensor([[[True], [True]], [[True], [False]]]))
        torch.testing.assert_close(messages[0, 0, 0, :-2], tensors.features[1])
        torch.testing.assert_close(messages[0, 1, 0, :-2], tensors.features[0])
        torch.testing.assert_close(messages[0, :, 0, -1], torch.tensor([1.0, 0.0]))
        torch.testing.assert_close(messages[0, :, 0, -2], torch.full((2,), float(np.log(2) / np.log(25))))
        return torch.zeros(2)

    tensors.predict(inspect, np.array([2, 3]), True)
