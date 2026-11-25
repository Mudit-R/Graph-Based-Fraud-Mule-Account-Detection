"""Temporal message passing for fraud prediction on payment edges."""

import torch
from torch import Tensor, nn


class TemporalPaymentGNN(nn.Module):
    """Pool earlier incident payments into separate source and destination states.

    The caller supplies the causal, as-of-payment neighborhood and includes
    any age and direction features in each message. The graph layer shares its
    message encoder across endpoints and learns a separate query for each role.
    """

    def __init__(
        self,
        target_dim: int,
        message_dim: int,
        width: int = 48,
        heads: int = 4,
        use_history: bool = True,
    ):
        super().__init__()
        if min(target_dim, message_dim, width, heads) < 1 or width % heads:
            raise ValueError("Dimensions and heads must be positive, with width divisible by heads.")

        self.target_dim = target_dim
        self.message_dim = message_dim
        self.width = width
        self.heads = heads
        self.head_dim = width // heads
        self.use_history = use_history

        self.target_encoder = nn.Sequential(nn.Linear(target_dim, width), nn.SiLU())
        if use_history:
            self.message_encoder = nn.Sequential(nn.Linear(message_dim, width), nn.SiLU())
            self.query = nn.Linear(width, 2 * width)
            self.key_value = nn.Linear(width, 2 * width)
        self.classifier = nn.Sequential(
            nn.Linear(3 * width if use_history else width, width),
            nn.SiLU(),
            nn.Linear(width, 1),
        )

    def forward(
        self,
        target: Tensor,
        messages: Tensor | None = None,
        mask: Tensor | None = None,
    ) -> Tensor:
        """Return one logit per target payment, with True marking valid messages."""
        if target.ndim != 2 or target.shape[-1] != self.target_dim:
            raise ValueError("target must have shape [batch, target_dim].")

        payment = self.target_encoder(target)
        if not self.use_history:
            return self.classifier(payment).squeeze(-1)

        batch = target.shape[0]
        if messages is None and mask is None:
            context = payment.new_zeros((batch, 2 * self.width))
        else:
            if messages is None or mask is None:
                raise ValueError("Supply messages and mask together.")
            if (messages.ndim != 4 or messages.shape[:2] != (batch, 2)
                    or messages.shape[-1] != self.message_dim):
                raise ValueError("messages must have shape [batch, 2, neighbors, message_dim].")
            if mask.dtype != torch.bool or mask.shape != messages.shape[:-1]:
                raise ValueError("mask must be boolean with shape [batch, 2, neighbors].")
            context = self._pool_history(payment, messages, mask)

        return self.classifier(torch.cat((payment, context), dim=-1)).squeeze(-1)

    def _pool_history(self, payment: Tensor, messages: Tensor, mask: Tensor) -> Tensor:
        batch, _, neighbors, _ = messages.shape
        # Remove padding before any learned projection so even NaN padding has
        # no influence on the result or on the gradients.
        valid = mask.unsqueeze(-1)
        encoded = self.message_encoder(messages.masked_fill(~valid, 0))
        keys, values = self.key_value(encoded).chunk(2, dim=-1)
        shape = (batch, 2, neighbors, self.heads, self.head_dim)
        keys, values = keys.reshape(shape), values.reshape(shape)
        query = self.query(payment).reshape(batch, 2, 1, self.heads, self.head_dim)
        scores = (query * keys).sum(dim=-1) * self.head_dim ** -0.5

        # A finite floor keeps softmax defined for a fully masked endpoint.
        scores = scores.masked_fill(~valid, torch.finfo(scores.dtype).min)
        weights = scores.softmax(dim=2).masked_fill(~valid, 0)
        context = (weights.unsqueeze(-1) * values).sum(dim=2)
        return context.reshape(batch, 2 * self.width)
