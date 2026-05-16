"""Stripe API key format validation — shared between gate.py and stripe_provider.py.

Single source of truth for "is this string a plausible Stripe API key?"
Before B6/H4 this lived as `_stripe_key_mode` in gate.py (lenient — any
suffix length) AND `_key_mode` in stripe_provider.py (strict — required ≥8
chars beyond prefix). The disagreement produced two different `/checkout/gate`
reason strings for the same bad-config case (e.g. `STRIPE_SECRET_KEY=sk_live_`
bare prefix).

This module is the strict-everywhere version. Both layers now agree.
"""

# Minimum suffix length beyond the `sk_test_` / `sk_live_` prefix. Stripe's
# real keys are far longer; 8 chars is conservative enough to reject the
# bare-prefix typo without false-rejecting any real key.
_MIN_SUFFIX_CHARS = 8

# Prefix → mode. Centralized so a future "sk_restricted_" or other key type
# can be added in one place.
_PREFIXES: dict[str, str] = {
    "sk_test_": "test",
    "sk_live_": "live",
}


def key_mode(key: str) -> str | None:
    """Classify a Stripe API key as 'test', 'live', or None (invalid).

    None means the key is missing, doesn't start with a known prefix, or has
    fewer than _MIN_SUFFIX_CHARS chars beyond the prefix. We deliberately do
    NOT log or return the key itself — only its mode classification — to
    avoid leaking the key into logs or error messages.

    Examples:
      key_mode("sk_test_abc12345")  → "test"
      key_mode("sk_live_xyz98765")  → "live"
      key_mode("sk_test_")          → None   (no suffix)
      key_mode("sk_test_short")     → None   (suffix < 8 chars)
      key_mode("pk_test_…")         → None   (publishable, not secret)
      key_mode("")                  → None
    """
    for prefix, mode in _PREFIXES.items():
        if key.startswith(prefix) and len(key) >= len(prefix) + _MIN_SUFFIX_CHARS:
            return mode
    return None
