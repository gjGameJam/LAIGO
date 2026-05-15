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
    FALLBACK_ORDERED  = "fallback_ordered"
    PAYMENT_CAPTURED  = "payment_captured"
    COMPENSATED       = "compensated"
    FAILED            = "failed"


# ── Exceptions ───────────────────────────────────────────────────────────────

class StockoutError(Exception):
    """Raised by brickowl_client.create_order when a piece is no longer available.
    Reserved for when order placement is implemented."""
    def __init__(self, element_id: str):
        self.element_id = element_id
        super().__init__(f"Stockout: element {element_id} is no longer available")


# ── Status response ───────────────────────────────────────────────────────────

class CheckoutStatusResponse(BaseModel):
    checkout_id: str
    saga_status: SagaStatus
    brickowl_order_ids: list[str]
    lego_order_id: Optional[str] = None
    stripe_payment_intent_id: Optional[str] = None
    total_charged_cents: Optional[int] = None
    error: Optional[str] = None
    completed_at: Optional[str] = None   # ISO 8601 timestamp
