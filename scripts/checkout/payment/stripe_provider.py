"""
Stripe PaymentIntent provider — L5 implementation.

The single concrete PaymentProvider in v1. Subsumes everything the legacy
`stripe_client.py` did, with three meaningful differences:

  1. Construction is ATOMIC. The constructor either returns a provider with a
     proven-valid `stripe` SDK + env config + safety checks, or raises
     PaymentProviderUnavailable. There is no `STRIPE_ENABLED=False but the
     module imports anyway` state.

  2. Stripe error classes are translated to PaymentRetryableError /
     PaymentPermanentError at the boundary. The Saga doesn't need to know
     about stripe.error.* types or which ones are recoverable.

  3. Sync Stripe SDK calls are wrapped in `asyncio.to_thread` so they don't
     block the event loop. Adopting `stripe.AsyncStripeClient` would be a
     drop-in replacement here later if Stripe deprecates the sync interface.

─────────────────────────────────────────────────────────────────────────────
Operator enable signal — read before flipping anything
─────────────────────────────────────────────────────────────────────────────

`STRIPE_ENABLED` (this module's top-level constant) must be `True` AS WELL AS:

  * `STRIPE_SECRET_KEY` set in env, prefix `sk_test_` or `sk_live_`
  * For `sk_live_`: `RENDER` env truthy (production deploy only)
  * `stripe` SDK installed

Two deliberate operator actions are required to enable real payment calls:
flip the code constant AND set the env. This is intentional defense-in-depth
— flipping creds alone (e.g., copy-pasting a key into .env.secrets) is NOT
sufficient. Matches the existing "flip when ready" pattern operators already
know from the pre-L5 codebase.

─────────────────────────────────────────────────────────────────────────────
Error classification table
─────────────────────────────────────────────────────────────────────────────

  Stripe error                         | Mapped to                  | Saga behavior
  -------------------------------------|----------------------------|-----------------
  APIConnectionError                   | PaymentRetryableError      | retry with backoff
  RateLimitError                       | PaymentRetryableError      | retry with backoff
  APIError (5xx from Stripe)           | PaymentRetryableError      | retry with backoff
  CardError (declined, expired)        | PaymentPermanentError      | fail / MANUAL_REVIEW
  AuthenticationError (bad key)        | PaymentPermanentError      | fail / MANUAL_REVIEW
  InvalidRequestError                  | PaymentPermanentError      | fail / MANUAL_REVIEW
  PermissionError                      | PaymentPermanentError      | fail / MANUAL_REVIEW
  IdempotencyError                     | PaymentPermanentError      | fail / MANUAL_REVIEW
  SignatureVerificationError           | PaymentPermanentError      | fail / MANUAL_REVIEW
  (any other StripeError)              | PaymentPermanentError      | fail / MANUAL_REVIEW

Hold-time CardError → Saga FAILED (no orders placed yet, no money moved).
Capture-time CardError → Saga MANUAL_REVIEW (orders placed; operator picks
the recovery path: refund the marketplace orders, or ask customer for new
payment method).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Final

from .base import (
    PaymentHold,
    PaymentPermanentError,
    PaymentProviderUnavailable,
    PaymentRetryableError,
)

logger = logging.getLogger("laigo")


# ─────────────────────────────────────────────────────────────────────────────
# Operator-controlled enable signal.
#
# Flip to True AFTER:
#   1. STRIPE_SECRET_KEY is set in .env.secrets
#   2. The key has been tested against Stripe's API (a single test charge)
#   3. The frontend's Stripe.js integration produces a valid pm_... token
#
# DO NOT flip this in a separate commit from those preconditions — the gate
# will refuse to enable checkout if env is missing anyway, but flipping the
# flag without env hides the misconfiguration behind a different reason
# string and slows diagnosis.
# ─────────────────────────────────────────────────────────────────────────────
STRIPE_ENABLED: bool = True


_ENV_STRIPE_KEY: Final = "STRIPE_SECRET_KEY"
_ENV_RENDER: Final = "RENDER"
_ENV_STRIPE_API_VERSION: Final = "STRIPE_API_VERSION"
_ENV_STRIPE_CURRENCY: Final = "STRIPE_CURRENCY"

# B7/H5: currency is locked at construction time, not read fresh on every
# hold call. The allowlist exists so a typo'd `STRIPE_CURRENCY=zzz` surfaces
# in /checkout/gate reasons immediately instead of failing at the first
# Stripe API call (after the hold attempt is already in flight). Add new
# currencies here only when LAIGO ships in a market that needs them.
_ALLOWED_CURRENCIES: Final = frozenset({"usd", "eur", "gbp", "cad"})


from .._env import is_truthy
# Key validation lives in .key_format so gate.py and this module agree on
# what counts as a valid key (B6/H4). The strict version (≥8 chars beyond
# prefix) is the contract everywhere.
from .key_format import key_mode as _key_mode


class StripeProvider:
    """Concrete PaymentProvider for Stripe.

    Construction order is significant. Each check is fail-fast so the operator
    gets one specific reason at boot:
      1. STRIPE_ENABLED flag (operator code-flip)
      2. stripe SDK importable
      3. STRIPE_SECRET_KEY present + well-formed
      4. Live key only allowed on Render
      5. STRIPE_CURRENCY in allowlist (B7/H5)
      6. SDK accepts the key (sets stripe.api_key)
    """

    name: Final = "stripe"

    def __init__(self) -> None:
        if not STRIPE_ENABLED:
            raise PaymentProviderUnavailable(
                "STRIPE_ENABLED is False in stripe_provider.py. "
                "Flip it to True only after Stripe credentials are tested. "
                "See scripts/checkout/payment/stripe_provider.py docstring."
            )

        try:
            import stripe  # noqa: F401 — import for side-effect (SDK validation)
        except ImportError as exc:
            raise PaymentProviderUnavailable(
                f"Stripe SDK not installed: {exc}. "
                "Run: pip install stripe~=10.0"
            ) from exc

        key = os.environ.get(_ENV_STRIPE_KEY, "").strip()
        key_mode = _key_mode(key)
        if key_mode is None:
            raise PaymentProviderUnavailable(
                f"{_ENV_STRIPE_KEY} is missing or malformed. "
                "Must start with sk_test_ or sk_live_ followed by at least 8 chars."
            )

        # Live key outside Render is a hard refusal — defense against
        # accidentally committing a live key to a dev .env.secrets file.
        # is_truthy (not raw truthiness) is critical here: RENDER="false"
        # must NOT count as "we're on Render". See CHECKOUT_AUDIT.md §10 D1.
        if key_mode == "live" and not is_truthy(os.environ.get(_ENV_RENDER)):
            raise PaymentProviderUnavailable(
                "Live Stripe key (sk_live_) detected outside the Render "
                "environment. Use sk_test_... for local development."
            )

        # B7/H5: validate STRIPE_CURRENCY against an allowlist at construction
        # time. Locks the currency for the lifetime of this provider instance
        # so the Saga can't read a different value mid-flight (env-mutation
        # after restart-while-saga-in-flight would otherwise risk a hold and
        # capture in different currencies). Surfaces in /checkout/gate reasons.
        currency = os.environ.get(_ENV_STRIPE_CURRENCY, "usd").strip().lower()
        if currency not in _ALLOWED_CURRENCIES:
            raise PaymentProviderUnavailable(
                f"{_ENV_STRIPE_CURRENCY}={currency!r} is not in the allowlist "
                f"{sorted(_ALLOWED_CURRENCIES)}. Add it to _ALLOWED_CURRENCIES "
                "in stripe_provider.py if LAIGO ships in a new market."
            )
        self.currency: Final = currency

        # B15: stripe.api_key AND stripe.api_version are MODULE-GLOBAL on
        # stripe-python (not per-client). Single-active enforcement in
        # payment.registry.register() is what keeps this safe today. Instantiating
        # StripeProvider outside the lifespan path (or registering a second
        # instance) silently overwrites the key for any in-flight saga that
        # captured the OLD provider reference. If we ever need multi-active
        # (e.g. test + live providers in the same process for staging tooling),
        # migrate to `stripe.StripeClient(api_key=key)` — see PRE_RELEASE
        # §4 B15 for the migration sketch.
        import stripe
        stripe.api_key = key

        # Pin API version if operator specified one — helps catch breaking
        # changes in Stripe's API rather than silently inheriting "latest".
        # B15: stripe.api_version is ALSO module-global; same constraint.
        api_version = os.environ.get(_ENV_STRIPE_API_VERSION, "").strip()
        if api_version:
            stripe.api_version = api_version

        self._mode: Final = key_mode
        self._stripe = stripe
        logger.info(
            "payment.stripe_provider.constructed mode=%s api_version=%s",
            key_mode,
            api_version or "(default)",
        )

    def mode(self) -> str:
        return self._mode

    # ─────────────────────────────────────────────────────────────────────────
    # Hold
    # ─────────────────────────────────────────────────────────────────────────

    async def create_hold(
        self,
        *,
        amount_cents: int,
        currency: str,
        payment_method_id: str,
        idempotency_key: str,
    ) -> PaymentHold:
        try:
            intent = await asyncio.to_thread(
                self._stripe.PaymentIntent.create,
                amount=amount_cents,
                currency=currency,
                payment_method=payment_method_id,
                capture_method="manual",
                confirm=True,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            self._raise_classified(exc, op="create_hold")
            raise  # _raise_classified always raises; this keeps type checkers happy

        # PaymentIntent statuses that mean "hold succeeded, ready to capture":
        #   requires_capture
        # Anything else at this point (requires_action, requires_payment_method,
        # processing, succeeded, canceled) is unexpected for capture_method=manual
        # with confirm=True. Treat as permanent — operator investigates.
        if intent.status != "requires_capture":
            raise PaymentPermanentError(
                f"Stripe hold returned unexpected status '{intent.status}' "
                f"(expected 'requires_capture'). PaymentIntent ID: {intent.id}"
            )

        return PaymentHold(
            hold_id=intent.id,
            amount_authorized_cents=int(intent.amount),
            currency=str(intent.currency).lower(),
            provider=self.name,
            mode=self._mode,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Capture
    # ─────────────────────────────────────────────────────────────────────────

    async def capture(
        self,
        *,
        hold_id: str,
        amount_cents: int,
        idempotency_key: str,
    ) -> None:
        try:
            intent = await asyncio.to_thread(
                self._stripe.PaymentIntent.capture,
                hold_id,
                amount_to_capture=amount_cents,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            # Treat "already captured" as success — happens on retry when the
            # first attempt succeeded but the response was lost to a network
            # error. Stripe's idempotency cache covers most cases; this is
            # the belt-and-suspenders fallback for the rare case where the
            # idempotency window expired between attempts.
            if self._is_already_captured(exc, hold_id):
                logger.warning(
                    "payment.stripe.capture.already_captured hold_id=%s — "
                    "treating as success (retry of previously-succeeded capture)",
                    hold_id,
                )
                return
            self._raise_classified(exc, op="capture")
            raise

        if intent.status != "succeeded":
            raise PaymentPermanentError(
                f"Stripe capture returned unexpected status '{intent.status}' "
                f"(expected 'succeeded'). PaymentIntent ID: {hold_id}"
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Cancel
    # ─────────────────────────────────────────────────────────────────────────

    async def cancel(
        self,
        *,
        hold_id: str,
        idempotency_key: str,
    ) -> None:
        try:
            await asyncio.to_thread(
                self._stripe.PaymentIntent.cancel,
                hold_id,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            # "Already cancelled" or "already captured" on cancel = idempotent
            # success from the Saga's perspective. The Saga calls cancel during
            # compensation; if the hold was never created or already released,
            # compensation should not fail.
            if self._is_already_terminal_state(exc):
                logger.warning(
                    "payment.stripe.cancel.no_op hold_id=%s — hold was "
                    "already in a terminal state (cancelled or captured)",
                    hold_id,
                )
                return
            self._raise_classified(exc, op="cancel")
            raise

    # ─────────────────────────────────────────────────────────────────────────
    # Charge (immediate capture) — pay-what-you-want digital build pack.
    #
    # Unlike create_hold (capture_method="manual"), this captures in a single
    # call. There is no marketplace fulfillment behind it, so there is nothing
    # to compensate — the charge either succeeds, needs 3DS, or fails. Used by
    # scripts/pay_router.py; NOT part of the (shelved) hold→capture saga.
    # ─────────────────────────────────────────────────────────────────────────

    async def charge(
        self,
        *,
        amount_cents: int,
        payment_method_id: str,
        idempotency_key: str,
    ) -> dict:
        """Create + confirm an immediate-capture PaymentIntent.

        Returns a dict:
          {"status": "succeeded" | "requires_action",
           "payment_intent_id": str,
           "amount_cents": int,
           "client_secret": str | None}

        `requires_action` means the card needs 3DS/SCA — the caller returns
        `client_secret` to the frontend to finish authentication. Any other
        non-succeeded status is treated as a permanent failure.

        Raises PaymentRetryableError / PaymentPermanentError (classified from
        Stripe's error types by `_raise_classified`).
        """
        try:
            intent = await asyncio.to_thread(
                self._stripe.PaymentIntent.create,
                amount=amount_cents,
                currency=self.currency,
                payment_method=payment_method_id,
                capture_method="automatic",
                confirm=True,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            self._raise_classified(exc, op="charge")
            raise  # _raise_classified always raises; keeps type checkers happy

        if intent.status == "succeeded":
            return {
                "status": "succeeded",
                "payment_intent_id": intent.id,
                "amount_cents": int(intent.amount),
                "client_secret": None,
            }
        if intent.status == "requires_action":
            return {
                "status": "requires_action",
                "payment_intent_id": intent.id,
                "amount_cents": int(intent.amount),
                "client_secret": intent.client_secret,
            }
        raise PaymentPermanentError(
            f"Stripe charge returned unexpected status '{intent.status}' "
            f"(expected 'succeeded' or 'requires_action'). "
            f"PaymentIntent ID: {intent.id}"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Status read (used by reconcile_orphan_holds)
    # ─────────────────────────────────────────────────────────────────────────

    # Stripe PaymentIntent statuses, mapped to our normalized 4-value set.
    # Reference: https://stripe.com/docs/payments/intents#intent-statuses
    # Any PI status not in this map yields "unknown" and the reconciler logs
    # a warning. Adding a new mapping is a code-only change.
    _STRIPE_STATUS_MAP = {
        # Active authorization — the auth-and-capture flow we use puts holds
        # here after create_hold returns successfully.
        "requires_capture":          "requires_capture",
        # Terminal — captured.
        "succeeded":                 "succeeded",
        # Terminal — cancelled. Stripe uses the American spelling.
        "canceled":                  "canceled",
        # Pre-capture states that should never persist long enough to be
        # reconciled, but we map them defensively in case they do.
        "processing":                "requires_capture",
        # Anything below means our auth-and-capture flow broke — these
        # statuses indicate the hold never reached requires_capture, which
        # shouldn't happen for a row we recorded. Map to "unknown" so the
        # reconciler logs + stops re-querying.
        "requires_payment_method":   "unknown",
        "requires_confirmation":     "unknown",
        "requires_action":           "unknown",
    }

    async def get_hold_status(self, hold_id: str) -> str:
        """Return Stripe's current view of `hold_id`'s lifecycle state.

        Maps `PaymentIntent.status` to our normalized 4-value set. See
        `_STRIPE_STATUS_MAP` for the conversion table.

        Read-only — no idempotency key needed; Stripe's `retrieve` is a GET.
        Network/5xx/rate-limit → `PaymentRetryableError`. "No such payment
        intent" + auth misconfig + other terminal Stripe errors →
        `PaymentPermanentError`. See `_raise_classified` for the full rules.
        """
        try:
            intent = await asyncio.to_thread(
                self._stripe.PaymentIntent.retrieve,
                hold_id,
            )
        except Exception as exc:
            self._raise_classified(exc, op="get_hold_status")
            raise  # _raise_classified raises on every branch; this is unreachable

        # Stripe's response is a dict-like object; `.get("status")` is the
        # documented field and is always present on a successfully retrieved
        # PaymentIntent.
        raw_status = intent.get("status")
        mapped = self._STRIPE_STATUS_MAP.get(raw_status, "unknown")
        if mapped == "unknown":
            logger.warning(
                "payment.stripe.get_hold_status.unmapped hold_id=%s "
                "stripe_status=%r — treating as 'unknown'. If this fires "
                "regularly, extend _STRIPE_STATUS_MAP in stripe_provider.py.",
                hold_id, raw_status,
            )
        return mapped

    # ─────────────────────────────────────────────────────────────────────────
    # Error classification helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _raise_classified(self, exc: Exception, *, op: str) -> None:
        """Translate Stripe-specific errors into PaymentRetryable/Permanent.

        Conservative retry policy: only network, rate-limit, and Stripe 5xx
        are retryable. Card errors, auth errors, and request errors will not
        recover on retry — wasting backoff time on them is harmful.
        """
        stripe_err = self._stripe.error

        if isinstance(exc, (stripe_err.APIConnectionError, stripe_err.RateLimitError)):
            raise PaymentRetryableError(
                f"Stripe {op} transient error ({type(exc).__name__}): {exc}",
                underlying=exc,
            ) from exc

        if isinstance(exc, stripe_err.APIError):
            # APIError is Stripe's generic 5xx — server-side issue, retry safe.
            raise PaymentRetryableError(
                f"Stripe {op} server error: {exc}",
                underlying=exc,
            ) from exc

        if isinstance(exc, stripe_err.CardError):
            raise PaymentPermanentError(
                f"Card declined during {op}: {exc.user_message or exc}",
                underlying=exc,
            ) from exc

        if isinstance(exc, stripe_err.StripeError):
            # Catches AuthenticationError, InvalidRequestError, PermissionError,
            # IdempotencyError, SignatureVerificationError, and any future
            # Stripe error class we haven't seen.
            raise PaymentPermanentError(
                f"Stripe {op} failed permanently ({type(exc).__name__}): {exc}",
                underlying=exc,
            ) from exc

        # Non-Stripe exception bubbling out — e.g., a programming error in
        # asyncio.to_thread. Re-raise so generic except Exception in the Saga
        # catches it with full traceback.
        raise

    def _is_already_captured(self, exc: Exception, hold_id: str) -> bool:
        """Detect 'PaymentIntent has already been captured' on retry.

        Stripe raises InvalidRequestError with a specific message. We check
        message substring rather than an opaque error code because the code
        ('payment_intent_unexpected_state') also covers other states (canceled,
        requires_action) where treating as success would be wrong.
        """
        if not isinstance(exc, self._stripe.error.InvalidRequestError):
            return False
        message = str(exc).lower()
        return "has already been captured" in message or "is already captured" in message

    def _is_already_terminal_state(self, exc: Exception) -> bool:
        """Detect 'cancel called on already-terminal PaymentIntent'."""
        if not isinstance(exc, self._stripe.error.InvalidRequestError):
            return False
        message = str(exc).lower()
        # Covers: "already been canceled", "cannot cancel a captured", etc.
        return (
            "already been canceled" in message
            or "already been cancelled" in message
            or "cannot cancel" in message
            or "has already been captured" in message
        )
