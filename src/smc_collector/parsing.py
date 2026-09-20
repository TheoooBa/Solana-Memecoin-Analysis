"""Interprétation des réponses JSON:API de GeckoTerminal.

Défensif par construction : un champ absent ou d'une forme inattendue devient
`None` (jamais une valeur inventée), et un avertissement est journalisé. Rien
ici ne doit jamais lever d'exception sur une réponse simplement incomplète.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def index_included(raw: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """Indexe le bloc `included` (tokens, dex) par (type, id) pour lookup O(1)."""
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for item in raw.get("included") or []:
        item_type = item.get("type")
        item_id = item.get("id")
        if item_type and item_id:
            index[(item_type, item_id)] = item
    return index


class ParsedPool:
    """Vue normalisée d'un objet "pool" JSON:API, indépendante de l'endpoint
    qui l'a produit (trending, new_pools, multi)."""

    __slots__ = (
        "pool_address",
        "dex",
        "base_token_address",
        "base_token_symbol",
        "quote_token_address",
        "quote_token_symbol",
        "pool_created_at",
        "price_usd",
        "fdv_usd",
        "market_cap_usd",
        "reserve_usd",
        "volume_usd_m5",
        "volume_usd_h1",
        "volume_usd_h24",
        "buys_m5",
        "sells_m5",
        "buyers_m5",
        "sellers_m5",
        "buys_h1",
        "sells_h1",
        "buyers_h1",
        "sellers_h1",
        "buys_h24",
        "sells_h24",
        "buyers_h24",
        "sellers_h24",
    )


def parse_pool_entry(entry: dict[str, Any], included_index: dict[tuple[str, str], dict[str, Any]]) -> ParsedPool:
    attrs = entry.get("attributes") or {}
    relationships = entry.get("relationships") or {}

    parsed = ParsedPool()
    parsed.pool_address = attrs.get("address") or entry.get("id", "").split("_", 1)[-1]

    dex_rel = (relationships.get("dex") or {}).get("data") or {}
    dex_item = included_index.get((dex_rel.get("type"), dex_rel.get("id"))) if dex_rel else None
    parsed.dex = (dex_item or {}).get("attributes", {}).get("name") or dex_rel.get("id")

    base_rel = (relationships.get("base_token") or {}).get("data") or {}
    base_item = included_index.get((base_rel.get("type"), base_rel.get("id"))) if base_rel else None
    base_attrs = (base_item or {}).get("attributes", {})
    parsed.base_token_address = base_attrs.get("address")
    parsed.base_token_symbol = base_attrs.get("symbol")

    quote_rel = (relationships.get("quote_token") or {}).get("data") or {}
    quote_item = included_index.get((quote_rel.get("type"), quote_rel.get("id"))) if quote_rel else None
    quote_attrs = (quote_item or {}).get("attributes", {})
    parsed.quote_token_address = quote_attrs.get("address")
    parsed.quote_token_symbol = quote_attrs.get("symbol")

    parsed.pool_created_at = attrs.get("pool_created_at")
    parsed.price_usd = _to_float(attrs.get("base_token_price_usd"))
    parsed.fdv_usd = _to_float(attrs.get("fdv_usd"))
    parsed.market_cap_usd = _to_float(attrs.get("market_cap_usd"))
    parsed.reserve_usd = _to_float(attrs.get("reserve_in_usd"))

    volume = attrs.get("volume_usd") or {}
    parsed.volume_usd_m5 = _to_float(volume.get("m5"))
    parsed.volume_usd_h1 = _to_float(volume.get("h1"))
    parsed.volume_usd_h24 = _to_float(volume.get("h24"))

    tx = attrs.get("transactions") or {}
    for period in ("m5", "h1", "h24"):
        period_tx = tx.get(period) or {}
        setattr(parsed, f"buys_{period}", _to_int(period_tx.get("buys")))
        setattr(parsed, f"sells_{period}", _to_int(period_tx.get("sells")))
        setattr(parsed, f"buyers_{period}", _to_int(period_tx.get("buyers")))
        setattr(parsed, f"sellers_{period}", _to_int(period_tx.get("sellers")))

    return parsed


def parse_pool_list(raw: dict[str, Any]) -> list[ParsedPool]:
    """Pour les endpoints qui renvoient une liste : trending_pools, new_pools, multi."""
    included_index = index_included(raw)
    data = raw.get("data")
    if data is None:
        logger.warning("Réponse sans clé 'data' : %s", list(raw.keys()))
        return []
    if isinstance(data, dict):
        data = [data]
    return [parse_pool_entry(entry, included_index) for entry in data]


def parse_ohlcv_candles(raw: dict[str, Any]) -> list[tuple[int, float, float, float, float, float]]:
    """Retourne une liste de tuples (timestamp_unix, open, high, low, close, volume)."""
    try:
        candle_list = raw["data"]["attributes"]["ohlcv_list"]
    except (KeyError, TypeError):
        logger.warning("Réponse OHLCV de forme inattendue : %s", list(raw.keys()))
        return []
    candles = []
    for row in candle_list:
        if len(row) != 6:
            continue
        ts, o, h, l, c, v = row
        candles.append((int(ts), float(o), float(h), float(l), float(c), float(v)))
    return candles
