"""Signed, retryable outbox delivery for high-risk ring review events."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import logging
import math
import os
import threading
import time
from urllib.parse import urlsplit

import httpx


logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 6


@dataclass(frozen=True)
class AlertWebhookConfig:
    url: str
    secret: str
    threshold: float = 0.70
    poll_seconds: float = 2.0
    timeout_seconds: float = 5.0
    base_retry_seconds: float = 1.0
    max_retry_seconds: float = 300.0

    def __post_init__(self):
        parts = urlsplit(self.url)
        local_http = parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost", "::1"}
        if parts.scheme != "https" and not local_http:
            raise ValueError("Alert webhook URL must use HTTPS (HTTP is allowed for loopback tests).")
        if not parts.hostname or parts.username or parts.password:
            raise ValueError("Alert webhook URL must be an absolute URL without embedded credentials.")
        if not isinstance(self.secret, str) or len(self.secret) < 32 or not self.secret.isascii():
            raise ValueError("Alert webhook signing secret must be ASCII and at least 32 characters.")
        if not math.isfinite(self.threshold) or not 0 <= self.threshold <= 1:
            raise ValueError("Alert score threshold must be between 0 and 1.")
        intervals = (self.poll_seconds, self.timeout_seconds,
                     self.base_retry_seconds, self.max_retry_seconds)
        if any(not math.isfinite(value) or value <= 0 for value in intervals):
            raise ValueError("Alert poll, timeout and retry intervals must be finite and positive.")
        if self.max_retry_seconds < self.base_retry_seconds:
            raise ValueError("Maximum retry delay must be at least the base retry delay.")

    @classmethod
    def from_env(cls, environ=None) -> "AlertWebhookConfig | None":
        env = os.environ if environ is None else environ
        url = env.get("FRAUD_CASE_ALERT_WEBHOOK_URL", "").strip()
        if not url:
            return None
        secret = env.get("FRAUD_CASE_ALERT_WEBHOOK_SECRET", "")
        try:
            threshold = float(env.get("FRAUD_CASE_ALERT_THRESHOLD", "0.70"))
        except ValueError:
            raise ValueError("FRAUD_CASE_ALERT_THRESHOLD must be a number between 0 and 1.") from None
        return cls(
            url=url,
            secret=secret,
            threshold=threshold,
            poll_seconds=float(env.get("FRAUD_CASE_ALERT_POLL_SECONDS", "2")),
            timeout_seconds=float(env.get("FRAUD_CASE_ALERT_TIMEOUT_SECONDS", "5")),
            base_retry_seconds=float(env.get("FRAUD_CASE_ALERT_RETRY_BASE_SECONDS", "1")),
            max_retry_seconds=float(env.get("FRAUD_CASE_ALERT_RETRY_MAX_SECONDS", "300")),
        )


class RingAlertDispatcher:
    """Deliver the durable SQLite outbox with signed, idempotent HTTP requests."""

    def __init__(self, store, config: AlertWebhookConfig, client_factory=None):
        self.store = store
        self.config = config
        self.client_factory = client_factory or (
            lambda: httpx.Client(timeout=config.timeout_seconds, follow_redirects=False)
        )

    def signed_headers(self, event_id: str, body: bytes) -> dict[str, str]:
        signature = hmac.new(self.config.secret.encode("ascii"), body, hashlib.sha256).hexdigest()
        return {
            "Content-Type": "application/json",
            "X-Case-Event-Id": event_id,
            "Idempotency-Key": event_id,
            "X-Case-Signature": f"sha256={signature}",
        }

    def deliver_due_once(self, now=None) -> int:
        now = time.time() if now is None else now
        alerts = self.store.claim_due_webhooks(now=now, max_attempts=MAX_ATTEMPTS)
        if not alerts:
            return 0
        with self.client_factory() as client:
            for alert in alerts:
                body = alert["payload"].encode("utf-8")
                try:
                    response = client.post(
                        self.config.url,
                        content=body,
                        headers=self.signed_headers(alert["id"], body),
                    )
                    response.raise_for_status()
                    self.store.mark_webhook_delivered(alert["id"], now=now)
                except Exception as exc:  # Retry transport errors and non-2xx responses.
                    delay = min(self.config.max_retry_seconds,
                                self.config.base_retry_seconds * (2 ** (alert["attempts"] - 1)))
                    self.store.reschedule_webhook(
                        alert["id"], alert["attempts"], type(exc).__name__, now + delay, MAX_ATTEMPTS,
                    )
                    logger.warning("High-risk case webhook delivery failed (%s)", type(exc).__name__)
        return len(alerts)

    def run(self, stop: threading.Event):
        while not stop.is_set():
            try:
                self.deliver_due_once()
            except Exception as exc:
                # Keep the service alive; durable outbox records can be retried after restart.
                logger.warning("High-risk case webhook worker iteration failed (%s)", type(exc).__name__)
            stop.wait(self.config.poll_seconds)
