"""Local-dev TLS: route Python's SSL verification through the OS trust store.

Why
===
Corporate networks that intercept TLS (MITM proxies) present server certs
signed by a corporate root CA. Python's bundled `certifi` doesn't trust that
CA, so outbound HTTPS from httpx (LEGO / BrickOwl / Stripe) fails with
``CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate``. The
operating system's trust store DOES trust the corporate CA (it's why browsers
work); the `truststore` package makes Python verify against that store.

Scope
=====
Gated on the ``RENDER`` env var: only active for LOCAL dev. On Render
(``RENDER`` is set in its runtime) this is a no-op, so production keeps the
default certifi behavior unchanged — no SSL-path change ships to prod.

The import is defensive: if `truststore` isn't installed, log and continue.
Outside a TLS-interception proxy the default certifi bundle works fine, so a
missing dependency must not break startup.

Call ``maybe_enable_os_trust()`` once, as early as possible at process start,
before any httpx client builds an SSL context.
"""

import logging
import os

logger = logging.getLogger("laigo.local_tls")


def maybe_enable_os_trust() -> None:
    """Inject the OS trust store into Python's SSL for local dev only."""
    if os.environ.get("RENDER"):
        return  # production: leave the default certifi behavior untouched
    try:
        import truststore
    except ImportError:
        logger.debug(
            "[local_tls] truststore not installed; using default certifi "
            "(fine unless this machine is behind a TLS-interception proxy — "
            "then: pip install truststore)."
        )
        return
    truststore.inject_into_ssl()
    logger.info(
        "[local_tls] RENDER unset — injected OS trust store via truststore "
        "(local dev; supports corporate TLS-interception proxies)."
    )
