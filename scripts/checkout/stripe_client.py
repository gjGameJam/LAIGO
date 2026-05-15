"""
Stripe PaymentIntent wrapper.

Currently stubbed — set STRIPE_ENABLED = True and implement the commented code
once Stripe is ready to go live. See docs/ORDER_OPTIMIZER.md → Stripe Integration
for the full integration plan.

Pattern: capture_method="manual" (hold then capture)
  1. create_payment_hold  → hold funds, get PaymentIntent ID
  2. [orders placed successfully]
  3. capture_payment      → charge the card
  If orders fail → cancel_payment_hold → card never charged

Test payment method ID for development: pm_card_visa
"""

import os
import logging

logger = logging.getLogger("laigo")

# Flip to True once STRIPE_SECRET_KEY is verified and tested end-to-end.
STRIPE_ENABLED = False


def _check_enabled() -> None:
    if not STRIPE_ENABLED:
        raise NotImplementedError(
            "Stripe payment is not yet configured. "
            "Set STRIPE_ENABLED = True in stripe_client.py after completing setup. "
            "See docs/ORDER_OPTIMIZER.md for the full integration plan."
        )
    _log_mode()


async def create_payment_hold(
    amount_cents: int,
    currency: str,
    payment_method_id: str,
    idempotency_key: str,
) -> str:
    """
    Create a Stripe PaymentIntent with capture_method='manual'.
    Funds are held but not charged until capture_payment() is called.
    Returns the PaymentIntent ID (pi_...).

    idempotency_key: use f"hold-{checkout_id}" to make retries safe.
    """
    _check_enabled()

    # Full implementation (uncomment when STRIPE_ENABLED = True):
    # import stripe
    # stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    # intent = stripe.PaymentIntent.create(
    #     amount=amount_cents,
    #     currency=currency,
    #     payment_method=payment_method_id,
    #     capture_method="manual",
    #     confirm=True,
    #     idempotency_key=idempotency_key,
    # )
    # _log_mode()
    # return intent.id
    return ""   # unreachable while stubbed


async def capture_payment(payment_intent_id: str) -> None:
    """Capture a previously held PaymentIntent. Call after all orders succeed."""
    _check_enabled()
    # stripe.PaymentIntent.capture(payment_intent_id)


async def cancel_payment_hold(payment_intent_id: str) -> None:
    """
    Cancel a held PaymentIntent without charging the card.
    Idempotent — safe to call multiple times.
    """
    _check_enabled()
    # stripe.PaymentIntent.cancel(payment_intent_id)


def _log_mode() -> None:
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if key.startswith("sk_live_") and not os.environ.get("RENDER"):
        raise RuntimeError(
            "Refusing to use a Stripe LIVE key outside of the Render deployment. "
            "Use sk_test_... for local development."
        )
    mode = "LIVE" if key.startswith("sk_live_") else "TEST"
    logger.info(f"Stripe mode: {mode}")
