"""Versioned USD / million-token prices; update this file when rates change."""
from datetime import datetime, timezone
from decimal import Decimal

PRICING_VERSION = "deepseek-2026-09-13"
PRICING_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing/"
PRICES = {
    ("deepseek", "deepseek-flash"): {
        "peak": {"hit": "0.006", "miss": "0.3", "output": "1.2"},
        "off_peak": {"hit": "0.003", "miss": "0.15", "output": "0.6"},
        "peak_weekdays": [0, 1, 2, 3, 4],
        "peak_utc_hours": [(1, 4), (6, 10)],
    }
}


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
