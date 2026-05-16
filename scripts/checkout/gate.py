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
  L2 /checkout/gate            — operational visibility
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

from .payment import registry as payment_registry

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


# is_truthy lives in ._env (dependency-free) so registry.py and
# payment/stripe_provider.py can use it without importing gate. Re-exported
# here for backward compatibility — `from .checkout.gate import is_truthy`
# continues to work.
from ._env import is_truthy

# Backwards-compatible alias used internally; remove when no external callers remain.
_is_truthy = is_truthy


# Stripe key validation lives in payment/key_format.py so gate.py and
# stripe_provider.py agree on what counts as a valid key (B6/H4). Before
# the consolidation, gate's check was lenient (any suffix length) and the
# provider's was strict (≥8 chars beyond prefix) — same env produced two
# different reason strings on `/checkout/gate`.
from .payment.key_format import key_mode as _stripe_key_mode


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
    # B13: distinguish "operator hasn't set the flag" from "operator set it to
    # a falsy value" (deliberate kill switch). Same gate outcome (DISABLED)
    # but the reason string is far less ambiguous for someone investigating
    # the kill switch state.
    master_raw = os.environ.get(_ENV_MASTER_FLAG)
    master_enabled = is_truthy(master_raw)
    if not master_enabled:
        if master_raw is None:
            reasons.append(f"{_ENV_MASTER_FLAG} is not set")
        else:
            reasons.append(f"{_ENV_MASTER_FLAG}={master_raw!r} is not truthy")

    # ── Payment provider probe ───────────────────────────────────────────────
    # Source of truth: payment.registry. The registry is populated at lifespan
    # startup by Main.py constructing a concrete PaymentProvider (today only
    # StripeProvider). The provider's __init__ atomically validates SDK + env
    # + safety rules — if it succeeded, the gate trusts that provider is
    # ready to serve real payments. See scripts/checkout/payment/base.py for
    # the Protocol contract and scripts/checkout/payment/stripe_provider.py
    # for the construction-time checks (L5 of the defense-in-depth design).
    if payment_registry.is_configured():
        payment_provider = payment_registry.active_name()
    else:
        reasons.append(
            "No payment provider registered "
            "(see boot logs for the construction failure reason)"
        )

    # Defense-in-depth — keep the "live key outside Render" rule visible at
    # the gate even when the registry path didn't surface it. StripeProvider's
    # __init__ already enforces this and refuses to construct; this duplicate
    # check exists so an operator who sees `mode: disabled` on /checkout/gate
    # gets a specific, actionable reason in addition to the generic "no
    # provider registered." Two independent enforcement sites is the whole
    # point of layered defense (see audit P3 — intentional duplication).
    stripe_key = os.environ.get(_ENV_STRIPE_KEY, "")
    stripe_key_mode = _stripe_key_mode(stripe_key)
    if stripe_key_mode == "live" and not is_truthy(os.environ.get(_ENV_RENDER)):
        # is_truthy (not raw truthiness): RENDER="false"/"0"/"no" must NOT
        # count as "we're on Render". See CHECKOUT_AUDIT.md §10 D1.
        reasons.append(
            "Live Stripe key (sk_live_) detected outside the Render "
            "environment — refusing to enable checkout"
        )
        payment_provider = None  # force DISABLED even if registry somehow has one

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
        # Provider is registered and the master switch is on. Test vs live
        # comes from the registered provider's own mode() — single source of
        # truth, no risk of disagreement between env key and what the SDK is
        # actually configured to use.
        provider_mode = payment_registry.active_mode()
        mode = CheckoutMode.LIVE if provider_mode == "live" else CheckoutMode.TEST

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
