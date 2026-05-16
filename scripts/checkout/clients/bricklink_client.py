"""
BrickLink API client (STUB — pending setup).

BrickLink (bricklink.com) is the largest LEGO parts marketplace. Their API v3 uses
OAuth 1.0a authentication and is primarily a seller/store management API.

Current status:
  - API keys not yet configured
  - BrickLink seller account required to obtain API credentials
  - Endpoint verification against live API not yet done

What this client will provide once set up:
  - Price guide data for cost estimation (GET /catalog/item/{type}/{item_no}/price)
  - Want List creation for buyer-side order tracking
  - Individual seller lot data is NOT available via the public API — the price guide
    returns aggregated lot data without store identity. This limits shipping
    consolidation optimization to LEGO.com + BrickOwl only.

Setup steps:
  1. Create a BrickLink seller account at https://www.bricklink.com/register.asp
  2. Register an API app at https://www.bricklink.com/v3/api.page
  3. Generate a consumer key + consumer secret + token + token secret
  4. Add to .env.secrets:
       BRICKLINK_CONSUMER_KEY=
       BRICKLINK_CONSUMER_SECRET=
       BRICKLINK_TOKEN=
       BRICKLINK_TOKEN_SECRET=
  5. Verify endpoint responses against live API before activating this client
  6. Set BRICKLINK_ENABLED=true in .env

Architecture note:
  BrickLink will plug into the same optimizer interface as BrickOwl and LEGO.com:
    get_all_listings(order_items, shipping_country, shipping_zip) -> dict[str, list[SellerListing]]
  Since BrickLink's price guide doesn't expose store identity, each price guide
  lot will be assigned a synthetic seller_id ("bricklink_lot_{index}") with
  shipping_cost_cents set to a configurable estimate (BRICKLINK_EST_SHIPPING_CENTS).
  This prevents shipping consolidation across BrickLink lots but still allows
  price comparison against BrickOwl and LEGO.com.
"""

import logging
import os
from typing import Optional

from ..models import SellerListing

logger = logging.getLogger("laigo")

SELLER_ID_PREFIX = "bricklink_"
_ENABLED = os.environ.get("BRICKLINK_ENABLED", "false").lower() == "true"


async def get_all_listings(
    order_items: list[dict],
    shipping_country: str,
    shipping_zip: str,
    cache_ttl: int = 3600,
) -> dict[str, list[SellerListing]]:
    """
    Return BrickLink listings for all order items.
    Currently returns empty dict (client not yet configured).
    """
    if not _ENABLED:
        logger.debug("BrickLink client disabled (BRICKLINK_ENABLED not set)")
        return {item["elementId"]: [] for item in order_items}

    raise NotImplementedError(
        "BrickLink client is not yet implemented. "
        "Complete the setup steps in clients/bricklink_client.py."
    )


# ── Cache invalidation ────────────────────────────────────────────────────────
# Stub today (BrickLink listings aren't cached because get_all_listings either
# returns empty or raises NotImplementedError). Kept here so the Saga's
# parallel-invalidate gather pattern (B9/B10/H8) has a uniform contract across
# all three marketplaces. Wire up real cache key deletion when BrickLink
# ordering ships.

async def invalidate_listing(element_id: str) -> None:
    """No-op stub. BrickLink doesn't cache listings yet (see module docstring)."""
    return None
