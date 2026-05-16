"""
Payment provider Protocol + value types + exception hierarchy.

This module is dependency-free (no SDK imports) so the Saga and gate can import
it without dragging in `stripe` or any other provider library. Concrete
implementations live in sibling modules (stripe_provider.py, etc.).

─────────────────────────────────────────────────────────────────────────────
Exception taxonomy — read this before catching anything from a provider
─────────────────────────────────────────────────────────────────────────────

  PaymentProviderUnavailable — construction-time failure. The provider could
      not validate its config / SDK / safety rules. Caller (lifespan
      registration) catches at construction; runtime callers never see this
      because get_active() raises if no provider was registered.

  PaymentRetryableError      — runtime, transient. The same call replayed
      seconds-to-minutes later may succeed. Network errors, rate-limits,
      provider 5xx. The Saga's capture retry loop handles this class.

  PaymentPermanentError      — runtime, terminal. Card declined, auth
      misconfigured, request invalid, already-captured. Retrying will NOT
      help. Saga skips retry and either fails the hold (no orders placed
      yet) or escalates to MANUAL_REVIEW (orders already placed).

None of these inherit from NotImplementedError, ValueError, or KeyError, so
generic `except Exception:` blocks in the Saga still catch them but a
selective `except (PaymentRetryableError, PaymentPermanentError):` lets the
retry policy distinguish recoverable from terminal failures.

─────────────────────────────────────────────────────────────────────────────
Why a frozen dataclass for PaymentHold (not a bare hold_id string)
─────────────────────────────────────────────────────────────────────────────

The Saga needs more than the provider's hold ID to operate safely:
  * `amount_authorized_cents` so the capture step can verify it isn't trying
    to capture more than was held (closes the +5% buffer's "allocation drift
    > buffer" case → escalate to MANUAL_REVIEW).
  * `mode` for audit-log emission ("test" vs "live" gets recorded at every
    payment event).
  * `provider` name so a future multi-provider world can route refunds back
    to the same provider that took the hold.

Frozen so the Saga can't accidentally mutate a hold record mid-flight, and
hashable so it can be stored in dict keys / sets if needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


# ─────────────────────────────────────────────────────────────────────────────
# Value types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PaymentHold:
    """Result of a successful payment authorization.

    The provider has authorized `amount_authorized_cents` against the
    customer's payment method. Capture (or cancel) must happen within the
    provider's authorization window (Stripe: 7 days for cards).
    """

    hold_id: str
    """Provider's identifier for the hold. For Stripe: `pi_...` (PaymentIntent ID)."""

    amount_authorized_cents: int
    """Actual amount authorized. May exceed the order total if the caller
    applied a buffer (e.g., 5%). Capture amount must be <= this value."""

    currency: str
    """ISO 4217 lowercase. Provider does not enforce uppercase; we don't either."""

    provider: str
    """Provider name. Matches the value returned by `PaymentProvider.name`."""

    mode: str
    """'test' or 'live'. Surfaced in audit events so the audit log can
    distinguish sandbox from real-money transactions at every step."""


# ─────────────────────────────────────────────────────────────────────────────
# Exception hierarchy
# ─────────────────────────────────────────────────────────────────────────────


class PaymentProviderUnavailable(RuntimeError):
    """Provider cannot be constructed — config, SDK, or safety check failed.

    Raised from provider __init__ only. Lifespan registration catches this and
    logs a warning; the gate then reports DISABLED with a reason. Operators
    fix the underlying issue (install SDK, set env, etc.) and redeploy.
    """


class PaymentRetryableError(RuntimeError):
    """Transient runtime failure — the same call may succeed if retried.

    Wraps the underlying provider exception so the Saga doesn't need to
    import provider SDKs to classify errors. The Saga's capture retry loop
    catches this specifically.
    """

    def __init__(self, message: str, *, underlying: Exception | None = None):
        super().__init__(message)
        self.underlying = underlying


class PaymentPermanentError(RuntimeError):
    """Terminal runtime failure — retrying will not help.

    Card declined, authentication bug, invalid request (e.g., already
    captured), permission denied. The Saga skips retry; routing to FAILED
    vs MANUAL_REVIEW depends on whether marketplace orders were already
    placed at the time of the error.
    """

    def __init__(self, message: str, *, underlying: Exception | None = None):
        super().__init__(message)
        self.underlying = underlying


# ─────────────────────────────────────────────────────────────────────────────
# Protocol
# ─────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class PaymentProvider(Protocol):
    """Interface every payment provider must implement.

    Construction must be ATOMIC: __init__ either returns a fully-validated
    provider ready to serve real calls, or raises PaymentProviderUnavailable.
    There is no half-initialized state. This is the core L5 invariant — once
    the registry holds a provider, every method on it is guaranteed to
    actually call the upstream service (no NotImplementedError stubs).

    All methods are `async`. Sync providers should wrap their calls with
    `asyncio.to_thread` rather than blocking the event loop.

    Idempotency keys are REQUIRED on every mutating call. The Saga generates
    keys deterministically from `checkout_id` so retries within the provider's
    cache window (Stripe: 24h) return the original response, not a duplicate.
    """

    name: str
    """Stable string identifier. Matches `gate.payment_provider`, audit event
    `data.provider`, and the registry key. Must not change between versions
    once a provider has been used in production (audit-trail stability)."""

    currency: str
    """Lowercase ISO 4217 currency code locked at construction time (B7/H5).
    The Saga reads this instead of an env var so the hold and capture cannot
    drift across a mid-saga env mutation. Providers MUST validate against
    their own allowlist in __init__ and refuse to construct on unsupported
    currencies."""

    def mode(self) -> str:
        """Returns 'test' or 'live'. Read by the gate to classify CheckoutMode
        and by audit emitters to tag each event. Must not raise."""
        ...

    async def create_hold(
        self,
        *,
        amount_cents: int,
        currency: str,
        payment_method_id: str,
        idempotency_key: str,
    ) -> PaymentHold:
        """Authorize `amount_cents` against `payment_method_id` without capturing.

        The caller (Saga) chooses `amount_cents` — pre-buffered or not. The
        provider does not apply additional buffers.

        Raises PaymentRetryableError for transient failures (network, 5xx,
        rate limit) and PaymentPermanentError for terminal failures (card
        declined, auth bug, invalid request)."""
        ...

    async def capture(
        self,
        *,
        hold_id: str,
        amount_cents: int,
        idempotency_key: str,
    ) -> None:
        """Capture `amount_cents` of a previously-created hold.

        `amount_cents` may be less than or equal to the authorized amount
        (Stripe allows partial capture; the rest of the auth releases
        automatically). Greater-than-authorized is a caller bug — providers
        will raise PaymentPermanentError.

        Idempotency: callers should pass a STABLE key across retry attempts
        (e.g., `f"capture-{checkout_id}"`, NOT per-attempt). Stripe's
        idempotency cache returns the same response for the same key within
        24h — which is exactly what we want for transient-failure retries."""
        ...

    async def cancel(
        self,
        *,
        hold_id: str,
        idempotency_key: str,
    ) -> None:
        """Void an uncaptured hold. Safe to call on already-cancelled holds
        (provider treats as no-op)."""
        ...
