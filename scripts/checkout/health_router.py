"""
Layer 2 of the checkout defense-in-depth: operational visibility.

This module exposes a public read-only health endpoint that reflects the gate
state at the time of the request. See scripts/checkout/gate.py for the layered
design and docs/CHECKOUT_AUDIT.md §10 for the full threat model.

Endpoint:
  GET /health/checkout
    Public, always 200, no caching.
    Body: {mode, is_open, payment_provider, marketplaces_live, reasons[], commit}

Intended consumers:
  - Operator: curl after a Render deploy to verify the deployed instance is in
    the expected mode (DISABLED / TEST / LIVE)
  - Future dashboard / status page
  - CI post-deploy smoke checks
  - Future monitoring (alert when `reasons` is non-empty or `mode` doesn't match
    deploy environment)

Design notes:
  - Always returns HTTP 200. The gate's state is in the body. Status-code-based
    monitors that point at this URL would otherwise mark the service unhealthy
    when checkout is intentionally disabled (e.g., kill switch flipped).
    The separate /health endpoint in Main.py is the liveness check.
  - No caching. The kill switch (unsetting CHECKOUT_ENABLED in Render) must
    propagate immediately so operators see the new state.
  - The `commit` field reads RENDER_GIT_COMMIT, set automatically by Render.
    Locally, this env var is absent and the field is null.
  - This endpoint does NOT validate that marketplace credentials actually work
    (no live BrickOwl/Stripe probe). That's a deeper "readiness" check that
    belongs in a separate /health/checkout/deep endpoint if ever needed.
    The gate only knows whether credentials are present, not whether they
    authenticate successfully.

Mount in Main.py:
  from .checkout.health_router import checkout_health_router
  app.include_router(checkout_health_router)
"""

import os
from typing import Optional

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from .gate import compute_decision

checkout_health_router = APIRouter(tags=["health"])


class CheckoutHealthResponse(BaseModel):
    """Public health snapshot of the checkout gate.

    None of these fields are secrets. They reflect configuration intent and
    runtime state. See gate.py for the full meaning of each field.
    """

    mode: str = Field(
        ...,
        description="One of 'disabled', 'test', 'live'. See CheckoutMode in gate.py.",
        examples=["disabled", "test", "live"],
    )
    is_open: bool = Field(
        ...,
        description="True if irreversible actions (Stripe holds, marketplace orders) "
                    "may proceed. Equivalent to mode in ('test', 'live').",
    )
    payment_provider: Optional[str] = Field(
        None,
        description="Active payment provider, or null if none is configured.",
        examples=["stripe", None],
    )
    marketplaces_live: list[str] = Field(
        default_factory=list,
        description="Marketplace adapters whose credentials are present in env. "
                    "Does NOT mean the credentials work — only that they exist.",
        examples=[["brickowl", "lego_official"]],
    )
    reasons: list[str] = Field(
        default_factory=list,
        description="Human-readable reasons for the current mode. Empty when "
                    "everything is configured. Operators should treat any non-empty "
                    "list as something to investigate.",
    )
    commit: Optional[str] = Field(
        None,
        description="Git commit SHA of the deployed instance, from RENDER_GIT_COMMIT. "
                    "Null in local dev where this env var is unset.",
    )


@checkout_health_router.get(
    "/health/checkout",
    response_model=CheckoutHealthResponse,
    summary="Checkout gate state",
    description="Returns the current configuration state of the checkout pipeline. "
                "Always responds 200; clients read the body to determine state.",
)
async def checkout_health(response: Response) -> CheckoutHealthResponse:
    decision = compute_decision()

    # No caching — kill-switch state must propagate immediately. Render's edge
    # and any intermediate proxies must serve fresh data on every request.
    response.headers["Cache-Control"] = "no-store"

    return CheckoutHealthResponse(
        mode=decision.mode.value,
        is_open=decision.is_open,
        payment_provider=decision.payment_provider,
        marketplaces_live=list(decision.marketplaces_live),
        reasons=list(decision.reasons),
        commit=os.environ.get("RENDER_GIT_COMMIT") or None,
    )
