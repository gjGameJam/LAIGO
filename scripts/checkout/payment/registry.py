"""
Single-active payment provider registry.

The registry is the runtime source of truth for "which provider should the
Saga + gate use." It is populated EXACTLY ONCE per process, at lifespan
startup in Main.py. After that, reads are lock-free (dict reads are
GIL-atomic and no caller mutates the dict).

Concurrency model (v1):
  - One active provider at a time (single-active per L5 design decision H2).
  - register() replaces any existing provider — useful for tests, suspicious
    in prod. A warning is logged on replacement so unexpected re-registrations
    are loud.
  - Cross-process: each uvicorn worker has its own copy of this module's state.
    Workers re-run lifespan independently, so each registers its own provider.
    No shared-memory concerns.

Why no `unregister()` — once a payment is in flight the Saga holds a strong
reference to the provider instance (passed through create_hold → capture).
Removing it from the registry mid-saga is not meaningful. If you need to
disable a provider, restart the worker.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from .base import PaymentProvider, PaymentProviderUnavailable
from .._env import is_truthy

logger = logging.getLogger("laigo")

_active: Optional[PaymentProvider] = None

# B17: gate for legitimate replacement (test infrastructure). Production code
# never sets this; tests that intentionally swap providers do.
_REPLACE_ALLOWED_ENV = "LAIGO_ALLOW_REGISTRY_REPLACE"


def _is_replace_allowed() -> bool:
    return is_truthy(os.environ.get(_REPLACE_ALLOWED_ENV))


def register(provider: PaymentProvider) -> None:
    """Set the active provider. Production code calls this exactly once per
    process at lifespan startup.

    The provider instance must already be successfully constructed (its
    __init__ either succeeded or raised PaymentProviderUnavailable — this
    function sees only the success case).

    B17: refuses to replace an already-registered provider unless the env
    flag `LAIGO_ALLOW_REGISTRY_REPLACE` is truthy. This catches the test-leak
    case where a test forgets `_reset_for_tests()` between scenarios, which
    would silently swap the production-registered provider mid-process. Tests
    that intentionally swap should set the env flag; the previous WARNING-only
    behavior is preserved when the flag is set.

    Raises:
      RuntimeError: when a different provider is already registered and the
        replace-allowed env flag is not truthy.
    """
    global _active
    if _active is not None and _active is not provider:
        if not _is_replace_allowed():
            raise RuntimeError(
                f"Refusing to replace registered provider {_active.name!r} with "
                f"{provider.name!r}. This catches accidental test leaks. If "
                f"intentional, set {_REPLACE_ALLOWED_ENV}=1 (tests should call "
                "_reset_for_tests() first instead)."
            )
        logger.warning(
            "payment.registry.replaced previous=%s new=%s — "
            "%s=truthy permits replacement; ensure the previous provider isn't "
            "still referenced by an in-flight Saga.",
            _active.name,
            provider.name,
            _REPLACE_ALLOWED_ENV,
        )
    _active = provider
    logger.info(
        "payment.registry.registered provider=%s mode=%s",
        provider.name,
        provider.mode(),
    )


def get_active() -> PaymentProvider:
    """Return the registered provider, or raise.

    Raises PaymentProviderUnavailable when no provider has been registered.
    Callers (Saga) treat this as a terminal failure — equivalent to the gate
    being closed at runtime. In practice this should never fire if the gate
    is open, because the gate's own probe (is_configured) calls this module.
    """
    if _active is None:
        raise PaymentProviderUnavailable(
            "No payment provider is registered. "
            "Check boot logs for the registration failure reason."
        )
    return _active


def is_configured() -> bool:
    """True iff a provider has been registered.

    Cheap, safe to call from the gate per-request. Does not touch the
    provider's underlying service.
    """
    return _active is not None


def active_name() -> Optional[str]:
    """Provider name for surfacing in /checkout/gate body, or None.

    Returns the provider's `name` attribute without raising. Used by the
    gate's compute_decision() to populate GateDecision.payment_provider.
    """
    return _active.name if _active is not None else None


def active_mode() -> Optional[str]:
    """Provider mode ('test'/'live') without raising, or None if unregistered.

    Used by gate.py to classify CheckoutMode (TEST vs LIVE) without having
    to import the provider directly.
    """
    return _active.mode() if _active is not None else None


def _reset_for_tests() -> None:
    """Clear the active provider. Test-only. Production code must never call
    this — restart the process instead to ensure clean state."""
    global _active
    _active = None
