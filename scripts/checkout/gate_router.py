"""
Layer 2 of the checkout defense-in-depth: operational visibility.

This module exposes a public read-only gate endpoint that reflects the gate
state at the time of the request.

Path note: this is intentionally NOT under `/health/...`. The endpoint always
returns 200 (see "Always 200." rule below), so a path like `/health/checkout`
tempted operators to point Render's healthcheck setting at it — which would
keep the service marked healthy even when checkout is intentionally disabled.
Moving the route out of the `/health/...` namespace removes that foot-gun.
`/health` remains the pure liveness endpoint; this one reports gate state. See scripts/checkout/gate.py for the layered
design and docs/CHECKOUT_AUDIT.md §10 for the full threat model + interaction
contract with L0/L1.

Endpoint:
  GET /checkout/gate
    Public, always 200, no caching.
    Body: {mode, is_open, payment_provider, marketplaces_live, reasons[], commit}

─────────────────────────────────────────────────────────────────────────────
Intended consumers
─────────────────────────────────────────────────────────────────────────────
  - Operator: curl after a Render deploy to verify mode matches intent.
  - Future dashboard / status page (must be added to CORS allow_origins in
    Main.py if served from a different origin and consumed via browser fetch).
  - CI post-deploy smoke checks.
  - Future monitoring (alert when `reasons` is non-empty or `mode` doesn't
    match deploy environment).

─────────────────────────────────────────────────────────────────────────────
Design contract — read before changing anything in this file
─────────────────────────────────────────────────────────────────────────────

  Always 200.
    Status code in body, not in HTTP status. Some operator might point an
    uptime monitor (Pingdom / Render healthcheck / UptimeRobot) at this URL
    by mistake. If we returned 503 on DISABLED, the kill switch (intentional
    operator action) would mark the whole service unhealthy and Render would
    stop routing traffic to it. Always-200 makes that misconfiguration safe.
    The separate /health endpoint in Main.py is the liveness check Render
    should be pointed at.

  No caching.
    `Cache-Control: no-store` on every response. The kill switch (unsetting
    CHECKOUT_ENABLED in Render → restart) must show `mode: "disabled"`
    immediately, not after some edge cache TTL.

  Pure read.
    The endpoint does not mutate anything (no logs per request, no metric
    increments, no DB writes). compute_decision() is itself a pure function.
    This keeps polling safe and cheap. If polling rates require throttling
    in the future, do it in FastAPI middleware so this module stays pure.

  Stateless across layers.
    L2 does not read any state set by L1 (the boot assertion). Both call
    compute_decision() independently. This means L2 reflects current env
    truth, even if env mutated after boot — that drift detection is a
    feature, not a bug. See CHECKOUT_AUDIT.md §10 "Layer interaction
    contract" for the full table.

  No deep readiness probe.
    The gate only knows whether marketplace + payment credentials are
    PRESENT in env, not whether they AUTHENTICATE. A reason of
    "BRICKOWL_API_KEY is set" does NOT mean BrickOwl will accept it.
    A deeper endpoint (/checkout/gate/deep that hits real APIs) is
    future work and would belong in a separate file with rate limiting.

─────────────────────────────────────────────────────────────────────────────
Field semantics (subtleties that bit me; document them so they don't bite again)
─────────────────────────────────────────────────────────────────────────────

  `is_open` means "the Saga code path will execute", NOT "real money is
  moving." TEST mode produces `is_open: true` because we want the full Saga
  flow to be exercisable against sandbox endpoints. Alerting rules that care
  about real-money exposure should match `mode == "live"`, not `is_open`.

  `mode` derives from STRIPE_SECRET_KEY prefix: `sk_test_` → TEST,
  `sk_live_` → LIVE, anything else → DISABLED with a reason.

  `payment_provider` is currently always "stripe" or null. When new providers
  are added, it stays a single string (only one provider active per request).

  `marketplaces_live` is sorted alphabetically. Clients may rely on the order
  for equality checks. Adding a new marketplace appends it then resorts.

  `reasons` is a list of operator-facing English strings. NOT a stable
  programmatic interface. Substring-matching is fragile; reword carefully.
  When alerting matures, refactor to {code, message} pairs.

  `commit` comes from RENDER_GIT_COMMIT (Render sets it automatically). Null
  in local dev where the env var is absent. Useful to verify the running
  instance matches your intended deploy.

─────────────────────────────────────────────────────────────────────────────
Mount in Main.py
─────────────────────────────────────────────────────────────────────────────
  from .checkout.gate_router import checkout_gate_router
  app.include_router(checkout_gate_router)

The endpoint inherits CORS middleware from Main.py. Today's CORS allow_origins
is restricted to the LAIGO frontend + localhost:5173. Browser-side fetch from
any other origin will fail preflight. Server-side curl / monitoring agents
are unaffected.
"""

import os
from typing import Optional

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from .gate import compute_decision

checkout_gate_router = APIRouter(tags=["checkout"])


class CheckoutGateResponse(BaseModel):
    """Public snapshot of the checkout gate.

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
        description="True if the Saga code path is permitted to run. Equivalent to "
                    "mode in ('test', 'live'). NOTE: True in TEST mode does NOT mean "
                    "real money is moving — TEST uses sandbox payment + marketplaces. "
                    "For real-money alerting, match `mode == 'live'`, not `is_open`.",
    )
    payment_provider: Optional[str] = Field(
        None,
        description="Active payment provider, or null if none is configured.",
        examples=["stripe", None],
    )
    marketplaces_live: list[str] = Field(
        default_factory=list,
        description="Marketplace adapters whose credentials are present in env. "
                    "Sorted alphabetically for stable client-side equality checks. "
                    "Does NOT mean the credentials authenticate — only that they exist.",
        examples=[["brickowl", "lego_official"]],
    )
    reasons: list[str] = Field(
        default_factory=list,
        description="Human-readable, operator-facing English strings. Empty when "
                    "everything is configured. NOT a stable programmatic interface — "
                    "do not substring-match for production alerting; reason wording "
                    "may change between releases.",
    )
    commit: Optional[str] = Field(
        None,
        description="Git commit SHA of the deployed instance, from RENDER_GIT_COMMIT. "
                    "Null in local dev where this env var is unset.",
    )


@checkout_gate_router.get(
    "/checkout/gate",
    response_model=CheckoutGateResponse,
    summary="Checkout gate state",
    description="Returns the current configuration state of the checkout pipeline. "
                "Always responds 200; clients read the body to determine state.",
)
async def checkout_gate(response: Response) -> CheckoutGateResponse:
    decision = compute_decision()

    # No caching — kill-switch state must propagate immediately. Render's edge
    # and any intermediate proxies must serve fresh data on every request.
    response.headers["Cache-Control"] = "no-store"

    return CheckoutGateResponse(
        mode=decision.mode.value,
        is_open=decision.is_open,
        payment_provider=decision.payment_provider,
        # Sort for stable client-side equality. Insertion order in gate.py today
        # already produces alphabetical, but sorting here makes the contract
        # explicit and survives reordering of the gate's probe code.
        marketplaces_live=sorted(decision.marketplaces_live),
        reasons=list(decision.reasons),
        commit=os.environ.get("RENDER_GIT_COMMIT") or None,
    )
