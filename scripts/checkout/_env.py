"""Tiny dependency-free env-parsing utilities for the checkout package.

Importable from anywhere in the codebase without dragging in heavy deps
(no SDK imports, no FastAPI, no asyncpg). Used by gate.py, payment/registry.py,
payment/stripe_provider.py, and Main.py to parse boolean-shaped env flags
identically — eliminates the H3 drift risk where the truthy set was
duplicated in four places.

If you add another env-shape helper here, keep this module dependency-free.
"""

# The accepted truthy literals. Single source of truth across the codebase.
# Touching this set affects every env-flag the app reads; coordinate carefully.
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def is_truthy(value: str | None) -> bool:
    """Parse a boolean-shaped env flag.

    Empty string, None, whitespace-only, and any value outside _TRUTHY return
    False. Case-insensitive after stripping.

    Examples:
      is_truthy("true")  → True
      is_truthy("YES")   → True
      is_truthy(" 1 ")   → True
      is_truthy("false") → False
      is_truthy("")      → False
      is_truthy(None)    → False
    """
    return (value or "").strip().lower() in _TRUTHY
