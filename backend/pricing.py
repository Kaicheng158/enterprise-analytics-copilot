"""Versioned USD / million-token prices; update this file when rates change."""
from datetime import datetime, timezone
from decimal import Decimal

from backend.config import PRICING_VERSION, PRICING_SOURCE, PRICES


def estimate_cost(provider, model, usage, started_at: datetime):
    config = PRICES.get((provider, model))
    if config is None:
        return None, None
    utc = started_at.astimezone(timezone.utc)
    peak = utc.weekday() in config["peak_weekdays"] and any(
        start <= utc.hour < end for start, end in config["peak_utc_hours"]
    )
    tier = "peak" if peak else "off_peak"
    hit, miss = usage.prompt_cache_hit_tokens, usage.prompt_cache_miss_tokens
    if hit is None or miss is None or hit + miss != usage.prompt_tokens:
        return None, tier
    rates = config[tier]
    amount = (hit * Decimal(rates["hit"]) + miss * Decimal(rates["miss"])
              + usage.completion_tokens * Decimal(rates["output"])) / Decimal(1000000)
    return format(amount, "f"), tier
