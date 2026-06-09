from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


# ── Request models ──────────────────────────────────────────────────────────

class QuoteRequest(BaseModel):
    shipping_country: str = Field(..., min_length=2, max_length=2,
                                  description="ISO 3166-1 alpha-2 country code (e.g. 'US')")
    shipping_zip: str = Field(..., min_length=1, max_length=20,
                               description="ZIP/postal code for shipping cost estimation")
    customer_email: str = Field(..., description="Used for order confirmation emails")


class ConfirmRequest(BaseModel):
    checkout_id: str = Field(..., description="checkout_id returned by /quote")
    stripe_payment_method_id: str = Field(
        ..., description="pm_... token produced by Stripe.js on the frontend"
    )


# ── Internal data models ─────────────────────────────────────────────────────

class SellerListing(BaseModel):
    seller_id: str
    seller_name: str
    price_per_cent: int       # price per single piece in US cents
    available_qty: int
    shipping_cost_cents: int  # flat shipping cost to the requested destination
    lot_id: str = ""          # BrickOwl lot ID — needed for cart/order placement


class AllocationEntry(BaseModel):
    seller_id: str
    seller_name: str
    items: dict[str, int]     # elementId → quantity assigned to this seller
    piece_cost_cents: int
    shipping_cost_cents: int
    subtotal_cents: int       # piece_cost + shipping


class AllocationResult(BaseModel):
    seller_allocations: list[AllocationEntry]
    lego_fallback_items: list[dict]    # [{elementId: str, quantity: int}]
    lego_fallback_cost_cents: int      # placeholder 0 until LEGO.com pricing integrated
    total_piece_cost_cents: int
    total_shipping_cents: int
    grand_total_cents: int             # pieces + shipping (before LAIGO fee)
    laigo_fee_cents: int               # max($3, 5% of grand_total)
    customer_total_cents: int          # grand_total + laigo_fee


# ── Response models ──────────────────────────────────────────────────────────

class SellerAllocationResponse(BaseModel):
    seller_id: str
    seller_name: str
    pieces_count: int
    piece_cost_cents: int
    shipping_cost_cents: int
    subtotal_cents: int


class QuoteResponse(BaseModel):
    checkout_id: str
    expires_at: int                      # Unix timestamp; quote valid 600 seconds
    pieces_total: int
    sellers: list[SellerAllocationResponse]
    lego_fallback_items: list[dict]      # pieces routed to LEGO.com (confirmed in stock)
    lego_fallback_cost_cents: int
    unsourceable_items: list[dict]       # pieces unavailable on both BrickOwl AND LEGO.com
    can_proceed: bool                    # False when unsourceable_items is non-empty
    total_cost_cents: int
    laigo_service_fee_cents: int
    grand_total_cents: int


class ConfirmResponse(BaseModel):
    checkout_id: str
    saga_status: str
    poll_url: str


class SagaStatus(str, Enum):
    INITIATED         = "initiated"
    STRIPE_HELD       = "stripe_held"
    ORDERS_PLACED     = "orders_placed"
    # RESERVED — see docs/ORDER_OPTIMIZER.md §8. Not written by any current
    # saga path. Reserved for forward compatibility: when BrickOwl supplies
    # some pieces and LEGO.com handles overflow as the literal fallback, this
    # status marks the transition between "primary orders placed" and
    # "fallback (LEGO) order placed." Keep until that flow ships; removing
    # without coordinated frontend update breaks the SagaStatus enum contract.
    FALLBACK_ORDERED  = "fallback_ordered"
    PAYMENT_CAPTURED  = "payment_captured"
    COMPENSATED       = "compensated"
    FAILED            = "failed"
    # Terminal state distinct from FAILED. Reached when orders WERE placed
    # but Stripe capture did not succeed after the retry budget OR a
    # permanent error class. The customer's marketplace orders are real;
    # operator action is required to either capture out-of-band, refund
    # the placed orders, or charge a different payment method. The
    # `manual_review_reason` field in checkout_state.json describes what
    # the operator needs to do.
    MANUAL_REVIEW     = "manual_review"


class HoldDisposition(str, Enum):
    """B55 — describes how the orphan-hold reconciler should treat a Stripe
    hold whose saga has reached MANUAL_REVIEW.

    Set by every saga write site that transitions to MANUAL_REVIEW with a
    still-authorized hold. Read by `reconcile_orphan_holds` to branch between
    auto-cancel and hands-off.

    CANCEL_SAFE: the saga's MANUAL_REVIEW runbook tells the operator to cancel
        manually in the Stripe dashboard (because automated cancel already
        failed, or because no further capture is possible). The reconciler
        may safely auto-cancel as a safety net within its normal cadence.

    OPERATOR_DECIDES: the saga's MANUAL_REVIEW runbook offers the operator a
        choice between capture and cancel (capture-exhausted-retries with
        orders placed, post-placement drift, stockout-retry cancel failed
        with some BrickOwl orders potentially live). The reconciler MUST NOT
        touch Stripe; auto-cancellation would defeat the operator's capture
        option.

    NULL (no value): MANUAL_REVIEW was reached without a known hold, OR the
        write site is from pre-B55 code. Reconciler treats NULL conservatively
        — same as OPERATOR_DECIDES (don't touch Stripe).
    """
    CANCEL_SAFE       = "cancel_safe"
    OPERATOR_DECIDES  = "operator_decides"


# ── Customer-facing error translation (B12 / H1) ─────────────────────────────
# Every saga write that sets `error: ...` MUST also set `customer_message: ...`
# using a key from this table. `error` is operator-internal (raw exception
# text, Stripe IDs, PaymentIntent IDs); `customer_message` is the only string
# safe to surface to /status response consumers.
#
# Adding a new category here is a coordinated change — saga.py picks the key,
# the frontend may want to render it differently. Don't change existing
# strings without coordinating with the frontend.

ERROR_MESSAGES: dict[str, str] = {
    "payment_permanent":   "Your payment method was declined. Please use a different card.",
    "payment_transient":   "Our payment system is temporarily unavailable. Please retry shortly.",
    "marketplace_failure": "We couldn't complete one of your orders. Your card was not charged.",
    "manual_review":       "Your order is being reviewed by our team. We'll email you within 24 hours.",
    "drift_buffer":        "The price of your order changed. Please request a new quote.",
    "gate_closed":         "Checkout is temporarily unavailable. Please try again shortly.",
    "timeout":             "Your order took longer than expected. Our team is reviewing — no action required.",
    # LEGO.com Playwright session expired or never seeded. Saga routes to
    # MANUAL_REVIEW; an operator re-seeds via scripts/seed_lego_session.py.
    # Customer-facing wording is intentionally vague — we don't want to expose
    # "our automation broke" to the customer; the order WILL ship, just on a
    # slightly delayed manual track.
    "lego_session_expired": "Finalizing your order. You will get an email soon.",
}


# ── Exceptions ───────────────────────────────────────────────────────────────

class StockoutError(Exception):
    """Raised by brickowl_client.create_order when a piece is no longer available.
    Reserved for when order placement is implemented."""
    def __init__(self, element_id: str):
        self.element_id = element_id
        super().__init__(f"Stockout: element {element_id} is no longer available")


class LegoSessionExpiredError(Exception):
    """Raised by lego_client.order_from_lego when the cached Playwright
    storage_state is missing or no longer valid for LAIGO's LEGO.com account.

    Caught by the saga's LEGO ordering section and routed to MANUAL_REVIEW
    with manual_review_reason='lego_session_expired'. An operator then
    re-seeds the session by running `python -m scripts.seed_lego_session`
    locally and completing Google SSO in the headed browser.

    `reason` is one of:
      'not_seeded'     — no row exists in external_sessions for provider='lego'
                         (initial deploy, or someone deleted the row)
      'cookie_expired' — Playwright loaded the cached state but the LEGO.com
                         redirected us to /profile/login on the first
                         logged-in-only navigation, meaning the cookies are
                         no longer accepted
    """
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"LEGO session unavailable: {reason}")


# ── Status response ───────────────────────────────────────────────────────────

class CheckoutStatusResponse(BaseModel):
    checkout_id: str
    saga_status: SagaStatus
    brickowl_order_ids: list[str]
    lego_order_id: Optional[str] = None
    # Provider-agnostic name (renamed from `stripe_payment_intent_id` in L5).
    # For Stripe this is the PaymentIntent ID (pi_...). For future providers
    # it is whatever opaque identifier they return from create_hold().
    payment_hold_id: Optional[str] = None
    # Amount actually authorized at hold time. May exceed `total_charged_cents`
    # if a buffer was applied (today: 1.05x the quote total).
    payment_authorized_cents: Optional[int] = None
    total_charged_cents: Optional[int] = None
    # Operator-facing error text. May contain Stripe IDs, exception class
    # names, internal request IDs. Frontend MUST NOT render this verbatim —
    # render `customer_message` instead.
    error: Optional[str] = None
    # Customer-facing translated message (B12 / H1). One of the strings from
    # ERROR_MESSAGES above, or None when no error has occurred. This is the
    # field /status consumers should surface to the customer.
    customer_message: Optional[str] = None
    # Populated when saga_status == MANUAL_REVIEW. Human-readable explanation
    # of what went wrong and what an operator needs to do. Frontend should
    # display the `customer_message` instead, but the field is included so
    # support staff can read the operator runbook directly when investigating.
    manual_review_reason: Optional[str] = None
    completed_at: Optional[str] = None   # ISO 8601 timestamp
