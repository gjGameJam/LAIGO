"""
Single source of truth for whether the checkout pipeline is safe to operate.

This module is Layer 0 of a defense-in-depth design that prevents the system
from placing real marketplace orders without holding the customer's payment.
The class of bug being defended against:

  * STRIPE_ENABLED=False in code AND a customer hits /confirm
      → today's bug: orders placed at $0 charged
  * Env drift: CHECKOUT_ENABLED=true but STRIPE_SECRET_KEY missing
  * Future bypass routes that invoke the Saga without going through /confirm
  * A new payment provider added later without going through this gate
  * Server resumes a stale Saga after restart with a "hold skipped" state

No single check covers all of those. Each defense layer reads from this gate;
each makes its own decision. Layers (in order of when they fire):

  L1 Main.py lifespan         — refuse to boot if env says enabled but reality disagrees
  L2 /health/checkout         — operational visibility
  L3 router /confirm           — return 503 if gate is not open
  L4 saga pre-flight           — refuse to advance before any external call
  L5 PaymentProvider registry  — no stub providers exist; only real ones can be obtained
  L6 audit log                 — every gate decision is recorded

Gate states:

  DISABLED — checkout is off. /quote may still be served (read-only).
             /confirm returns 503. The Saga refuses to run.
  TEST     — payment provider in test mode (sk_test_...). Orders may be placed
             at sandbox marketplaces. Real-money flows are forbidden.
  LIVE     — full production. All checks have passed; a Saga may charge a real card.

The gate is computed on demand. It does not cache state across calls — each
caller re-reads ground truth. A configuration change requires a restart, which
re-runs the L1 boot assertion.
"""

from dataclasses import dataclass
from enum import Enum
import logging
import os

logger = logging.getLogger("laigo")


class CheckoutMode(str, Enum):
    DISABLED = "disabled"
    TEST = "test"
    LIVE = "live"


@dataclass(frozen=True)
class GateDecision:
    """Snapshot of the gate's current state.

    `reasons` is a tuple of human-readable strings describing why the gate is
    in its current mode. Every reason that contributed to a DISABLED decision
    is listed, so an operator can fix all of them in one pass instead of
    chasing them one at a time.
    """

    mode: CheckoutMode
    reasons: tuple[str, ...]
    payment_provider: str | None
    marketplaces_live: tuple[str, ...]

    @property
    def is_live(self) -> bool:
        return self.mode == CheckoutMode.LIVE

    @property
    def is_open(self) -> bool:
        """Open means: irreversible actions may proceed (TEST or LIVE)."""
        return self.mode in (CheckoutMode.TEST, CheckoutMode.LIVE)


class GateClosedError(RuntimeError):
    """Raised when an irreversible action is attempted while the gate is closed.

    This exception is intentionally narrow. It does NOT inherit from
    NotImplementedError, ValueError, or any other commonly-caught type — so an
    upstream `except Exception:` cannot silently swallow it. The only legitimate
    catch site is the Saga's top-level error handler, where it converts to a
    FAILED state with the gate's reasons preserved.
    """


# Env keys consulted by the gate. Centralized so additions are visible in one place.
_ENV_MASTER_FLAG = "CHECKOUT_ENABLED"
_ENV_STRIPE_KEY = "STRIPE_SECRET_KEY"
_ENV_BRICKOWL_KEY = "BRICKOWL_API_KEY"
_ENV_LEGO_EMAIL = "LEGO_EMAIL"
_ENV_LEGO_PASSWORD = "LEGO_PASSWORD"
_ENV_RENDER = "RENDER"  # set by Render automatically in their environment


def is_truthy(value: str | None) -> bool:
    """Public so callers (e.g. Main.py's boot block) parse env flags identically."""
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


# Backwards-compatible alias used internally; remove when no external callers remain.
_is_truthy = is_truthy


def _stripe_key_mode(key: str) -> str | None:
    """Returns 'test' / 'live' / None based on the Stripe key prefix.

    None means the key is missing or malformed. We deliberately do NOT log or
    return the key itself — only its mode classification.
    """
    if key.startswith("sk_test_"):
        return "test"
    if key.startswith("sk_live_"):
        return "live"
    return None


def compute_decision() -> GateDecision:
    """Compute the current gate state by inspecting env + provider modules.

    Pure function: no side effects, no caching, no logging. Safe to call from
    any context (boot, /health, Saga pre-flight). Each call re-reads ground
    truth so a runtime env change is reflected immediately.
    """
    reasons: list[str] = []
    payment_provider: str | None = None
    marketplaces_live: list[str] = []

    # ── Master switch ────────────────────────────────────────────────────────
    master_enabled = is_truthy(os.environ.get(_ENV_MASTER_FLAG))
    if not master_enabled:
        reasons.append(f"{_ENV_MASTER_FLAG} is not set to true")

    # ── Payment provider probe ───────────────────────────────────────────────
    # Imported lazily so the gate has no hard dependency on stripe_client and
    # so import cycles can't form. If stripe_client itself fails to import,
    # we treat that as "no payment provider available" rather than crashing.
    stripe_module_enabled = False
    try:
        from . import stripe_client
        stripe_module_enabled = bool(stripe_client.STRIPE_ENABLED)
    except Exception as exc:
        reasons.append(f"stripe_client failed to import: {exc}")

    if not stripe_module_enabled:
        reasons.append("stripe_client.STRIPE_ENABLED is False")
    else:
        stripe_key = os.environ.get(_ENV_STRIPE_KEY, "")
        key_mode = _stripe_key_mode(stripe_key)
        if key_mode is None:
            reasons.append(
                f"{_ENV_STRIPE_KEY} is missing or malformed "
                "(must start with sk_test_ or sk_live_)"
            )
        elif key_mode == "live" and not os.environ.get(_ENV_RENDER):
            # Mirror of stripe_client._log_mode's safeguard, evaluated at the
            # gate so we fail at boot instead of at the first Stripe call.
            reasons.append(
                "Live Stripe key (sk_live_) detected outside the Render "
                "environment — refusing to enable checkout"
            )
        else:
            payment_provider = "stripe"

    # ── Marketplace probe ────────────────────────────────────────────────────
    # A marketplace is "live" if its credentials are present in env. The gate
    # does not validate that the credentials WORK — that's the responsibility
    # of each adapter's own health check. The gate only ensures we haven't
    # forgotten to configure anything.
    #
    # TODO(layer-5): when MarketplaceAdapter Protocol lands, replace this with
    # iteration over registered adapters and their declared is_configured()
    # classmethod. Today's hardcoded list duplicates the same hidden-coupling
    # antipattern the audit calls out for the Saga's seller_id routing.
    if os.environ.get(_ENV_BRICKOWL_KEY):
        marketplaces_live.append("brickowl")
    if os.environ.get(_ENV_LEGO_EMAIL) and os.environ.get(_ENV_LEGO_PASSWORD):
        marketplaces_live.append("lego_official")

    if not marketplaces_live:
        reasons.append(
            "No marketplace credentials configured "
            f"(need {_ENV_BRICKOWL_KEY} or {_ENV_LEGO_EMAIL}+{_ENV_LEGO_PASSWORD})"
        )

    # ── Decide mode ──────────────────────────────────────────────────────────
    # Any failure → DISABLED. The order of these checks does not matter for
    # correctness — all reasons are collected above so the operator sees the
    # full list.
    if (not master_enabled) or (payment_provider is None) or (not marketplaces_live):
        mode = CheckoutMode.DISABLED
    else:
        # Provider is configured and master switch is on. Test vs live is
        # determined by the Stripe key prefix.
        stripe_key = os.environ.get(_ENV_STRIPE_KEY, "")
        mode = CheckoutMode.LIVE if _stripe_key_mode(stripe_key) == "live" else CheckoutMode.TEST

    return GateDecision(
        mode=mode,
        reasons=tuple(reasons),
        payment_provider=payment_provider,
        marketplaces_live=tuple(marketplaces_live),
    )


def require_open() -> GateDecision:
    """Assert that the gate permits irreversible action; return the decision.

    Call this immediately before any code path that:
      - places a marketplace order
      - creates a Stripe payment hold
      - captures or cancels a Stripe payment

    Raises GateClosedError if the gate is DISABLED. The caller MUST NOT catch
    this exception except at the Saga's top-level error handler, where it
    converts to a FAILED state. No `except Exception` block in the codebase
    should silently swallow a GateClosedError — a CI grep guard enforces this.
    """
    decision = compute_decision()
    if not decision.is_open:
        logger.error(
            "checkout.gate.blocked mode=%s reasons=%s",
            decision.mode.value,
            list(decision.reasons),
        )
        raise GateClosedError(
            f"Checkout gate is {decision.mode.value}. "
            f"Reasons: {'; '.join(decision.reasons) or '(none reported)'}"
        )
    return decision


def require_live() -> GateDecision:
    """Stricter variant: assert LIVE mode (refuses TEST).

    Use this for code paths that MUST run against real money — e.g., a future
    reconciliation job that pulls real Stripe charges. The Saga itself uses
    require_open() so the same code path can be exercised in TEST mode.
    """
    decision = compute_decision()
    if not decision.is_live:
        logger.error(
            "checkout.gate.not_live mode=%s reasons=%s",
            decision.mode.value,
            list(decision.reasons),
        )
        raise GateClosedError(
            f"Checkout gate is {decision.mode.value}, LIVE required. "
            f"Reasons: {'; '.join(decision.reasons) or '(none reported)'}"
        )
    return decision
