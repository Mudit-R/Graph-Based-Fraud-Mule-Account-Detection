"""Focused tests for causal-neighborhood pooling and the no-history ablation."""

import pytest
import torch

from src.models.temporal_payment_gnn import TemporalPaymentGNN


def test_masked_nan_padding_does_not_affect_outputs_or_gradients():
    torch.manual_seed(37)
    model = TemporalPaymentGNN(target_dim=5, message_dim=3, width=16, heads=4).double()
    target = torch.randn(2, 5, dtype=torch.float64)
    history = torch.randn(2, 2, 4, 3, dtype=torch.float64)
    mask = torch.tensor([
        [[True, False, True, False], [False, False, False, False]],
        [[False, False, False, False], [False, False, False, False]],
    ])
    padded = history.masked_fill(~mask.unsqueeze(-1), float("nan")).requires_grad_()

    scores = model(target, padded, mask)
    torch.testing.assert_close(scores, model(target, history, mask))
    torch.testing.assert_close(scores[1:], model(target[1:]))
    assert scores.dtype == torch.float64
    assert scores.shape == (2,)

    scores.sum().backward()
    assert torch.isfinite(padded.grad).all()
    assert torch.count_nonzero(padded.grad[~mask]) == 0
    assert torch.count_nonzero(padded.grad[mask]) > 0
    assert torch.count_nonzero(model.message_encoder[0].weight.grad) > 0
    assert torch.isfinite(model.query.weight.grad).all()


def test_history_pooling_preserves_roles_and_ignores_neighbor_order():
    torch.manual_seed(42)
    model = TemporalPaymentGNN(4, 2)
    target = torch.randn(1, 4)
    messages = torch.randn(1, 2, 5, 2)
    mask = torch.tensor([[[True, True, True, True, False], [True, False, True, False, False]]])
    order = torch.tensor([2, 3, 0, 4, 1])

    scores = model(target, messages, mask)
    torch.testing.assert_close(scores, model(target, messages[:, :, order], mask[:, :, order]))
    assert scores.shape == (1,)
    assert not torch.allclose(scores, model(target, messages.flip(1), mask.flip(1)))


def test_zero_neighbors_equal_omitted_history():
    model = TemporalPaymentGNN(4, 2)
    target = torch.ones(1, 4)
    messages = torch.empty(1, 2, 0, 2)
    mask = torch.empty(1, 2, 0, dtype=torch.bool)
    torch.testing.assert_close(model(target, messages, mask), model(target))


def test_no_history_ablation_ignores_supplied_neighborhood():
    model = TemporalPaymentGNN(4, 2, use_history=False)
    target = torch.ones(3, 4)
    messages = torch.full((3, 2, 4, 2), float("nan"))
    mask = torch.ones(3, 2, 4, dtype=torch.bool)
    torch.testing.assert_close(model(target, messages, mask), model(target))


def test_rejects_malformed_history():
    model = TemporalPaymentGNN(4, 2)
    target = torch.ones(1, 4)
    messages = torch.ones(1, 2, 3, 2)
    with pytest.raises(ValueError, match="together"):
        model(target, messages)
    with pytest.raises(ValueError, match="messages must have shape"):
        model(target, messages[:, :1], torch.ones(1, 1, 3, dtype=torch.bool))
    with pytest.raises(ValueError, match="mask must be boolean"):
        model(target, messages, torch.ones(1, 2, 3))
