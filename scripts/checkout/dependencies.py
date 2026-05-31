"""
Shared FastAPI dependencies for the checkout package.

This module hosts dependency functions used across router endpoints to enforce
cross-cutting concerns at the HTTP boundary. It is intentionally small — keep
business logic out; keep policy enforcement in.

Currently houses:
  - require_checkout_gate_open: Layer 3 of the checkout defense-in-depth.
    Rejects requests with HTTP 503 when the gate is DISABLED. Applied to
    write-side endpoints that initiate Sagas (today: only /confirm; future:
    any new endpoint that places marketplace orders or holds payments).

─────────────────────────────────────────────────────────────────────────────
Design contract — read before changing anything in this file
─────────────────────────────────────────────────────────────────────────────

The dependencies in this module are the HTTP-framework-aware bridge between
the pure gate.compute_decision() function and FastAPI's HTTPException
machinery. gate.py stays HTTP-agnostic; this module is the only place that
imports both fastapi and gate.

Rules for adding a new dependency:
  1. Keep it pure of business logic. It should make a policy decision (allow /
     reject) based on stable inputs, then either return a value or raise
     HTTPException.
  2. Document the response body shape in the docstring, exactly as it appears
     in the wire response. Frontend code matches on these shapes — they are
     part of the API contract.
  3. If the dependency rejects a request, emit a structured log line at
     WARNING. This is the only signal we have until Layer 6 (audit log) ships.
  4. Don't catch exceptions other than the specific known cases. Letting
     unhandled errors bubble up surfaces real bugs instead of hiding them.

Rules for using these dependencies on endpoints:
  - Apply via FastAPI's Depends() in the endpoint signature, NOT in the
    decorator's `dependencies=[...]` list. The signature form is more
    discoverable in code review and lets the endpoint use the returned value
    if needed (e.g. logging the mode in which the Saga was started).
  - Any new write-side endpoint (one that places orders, holds money, or
    mutates marketplace state) MUST include require_checkout_gate_open.
    Read-side endpoints (quotes, status polls, debug introspection) may opt
    out — they don't move money.
"""

import logging
from fastapi import Depends, HTTPException, Request

from . import audit
from .gate import compute_decision, GateDecision

logger = logging.getLogger("laigo")


async def require_checkout_gate_open(request: Request) -> GateDecision:
    """FastAPI dependency: Layer 3 of the checkout gate.

    Computes the gate state on every request via gate.compute_decision()
    (pure, fast — env reads + a few string ops). If the gate is DISABLED,
    raises HTTPException(503) with a wire body of:

        {
          "detail": {
            "error": "Checkout is currently unavailable. Please try again later or contact support.",
            "code": "CHECKOUT_GATE_CLOSED",
            "mode": "disabled"
          }
        }

    Field-by-field contract (treat as load-bearing; frontend matches on these):
      - error: customer-facing English. Identical regardless of why the gate
               is closed. NEVER include operator-facing reasons here — those
               leak deployment details to customers. Operators get the full
               picture from /checkout/gate.
      - code:  stable machine-readable identifier. Frontend matches exactly
               on "CHECKOUT_GATE_CLOSED" to render its "checkout unavailable"
               UI. Renaming this string is a breaking change to the frontend.
      - mode:  gate's current mode value. Today always "disabled" when this
               branch fires (because is_open is False ↔ mode == DISABLED).
               The field is forward-looking — when the gate gains additional
               non-open modes (e.g. "maintenance", "degraded"), the same
               shape carries them without protocol change.

    No Retry-After header is set. The kill switch's recovery time is
    operator-determined and unpredictable; an inaccurate Retry-After would
    cause clients to either hammer the endpoint (too short) or back off
    longer than necessary (too long).

    ─────────────────────────────────────────────────────────────────────
    AUDIT LOG GAP — TODO(L6)
    ─────────────────────────────────────────────────────────────────────
    Today this dependency emits a single WARNING line per rejection, which
    lands in FastAPI's stdout log. That is the ONLY signal we have that a
    customer was rejected by L3.

    When Layer 6 (structured audit log) ships, this function MUST emit:

        audit.emit(
            "gate.confirm_rejected",
            mode=decision.mode.value,
            reasons=list(decision.reasons),
            # plus any request-correlation id available at that point
        )

    Three reasons that event matters when L6 is real:
      1. Detects a frontend bug: a UI that didn't poll /checkout/gate
         before showing the "Confirm" button. Symptom: a flood of
         "gate.confirm_rejected" events during a kill-switch window.
      2. Detects probing / attack patterns: someone POSTing /confirm
         repeatedly even though /checkout/gate says disabled.
      3. Confirms the kill switch actually blocked traffic — proves the
         operational action had the intended effect (compliance / audit).

    Until L6 ships, set an alert in Render's log search if you want
    visibility:
        log line contains "checkout.gate.l3_rejected" → notify

    See docs/CHECKOUT_AUDIT.md §10 "Known L3 limitations" for the full
    rationale and the migration steps when L6 lands.
    ─────────────────────────────────────────────────────────────────────
    """
    decision = compute_decision()
    if decision.is_open:
        return decision

    # L6 audit event (Phase E step 3, shipped 2026-05-19).
    # `audit.emit` is async, never raises (logs CRITICAL on failure), and
    # no-ops when DB_BACKEND != postgres. Per §2.2 vocabulary, this is the
    # `gate.confirm_rejected` event.
    #
    # actor.ip is populated by the `real_ip_middleware` in Main.py from
    # X-Forwarded-For (B60). Falls back to the immediate client host when
    # the middleware was skipped (test stubs, direct connections with no
    # XFF). The single-trusted-proxy assumption is documented at the
    # middleware definition — do not bypass it here.
    state_ip = getattr(getattr(request, "state", None), "real_ip", None)
    actor_ip = state_ip or (request.client.host if request.client else None)
    actor_user_agent = request.headers.get("user-agent")
    # Harvest job_id from the URL path for per-job audit correlation. The
    # `audit_events_job_ts_idx` index supports the operator query "show me
    # every rejected /confirm for this customer's job." path_params is
    # populated by FastAPI's router; the dependency runs after path matching.
    # path_params.get returns None if the route doesn't have a {job_id}
    # parameter (defensive — today /confirm is the only call site, but the
    # dependency may be applied to future routes without job_id).
    job_id = request.path_params.get("job_id")
    await audit.emit(
        "gate.confirm_rejected",
        subject={"job_id": job_id} if job_id else None,
        actor={
            "type": "customer",
            "ip": actor_ip,
            "user_agent": actor_user_agent,
        },
        data={
            "mode": decision.mode.value,
            "reasons": list(decision.reasons),
        },
    )

    # Keep the WARNING line alongside the audit emit per §2.6 cross-check
    # window — operators can verify that every audit event has a matching
    # log line and vice versa. The line is scheduled for removal one month
    # after L6 ships if no completeness gap is detected.
    logger.warning(
        "checkout.gate.l3_rejected mode=%s reasons=%s",
        decision.mode.value,
        list(decision.reasons),
    )

    raise HTTPException(
        status_code=503,
        detail={
            "error": "Checkout is currently unavailable. Please try again later or contact support.",
            "code": "CHECKOUT_GATE_CLOSED",
            "mode": decision.mode.value,
        },
    )
