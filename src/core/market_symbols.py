"""Symbol ↔ market helpers. Forbid cross-market orders (PROH-175)."""

from __future__ import annotations

import re
from typing import Literal

MarketId = Literal["US", "HK"]

# Futu-style codes accepted by Lane A/B document loaders (PROH-179).
_US_SYMBOL_RE = re.compile(r"^US\.[A-Z][A-Z0-9.\-]*$")
_HK_SYMBOL_RE = re.compile(r"^HK\.[0-9]{4,5}$")
_MARKET_SYMBOL_RE = re.compile(
    r"^(?:US\.[A-Z][A-Z0-9.\-]*|HK\.[0-9]{4,5})$"
)


class MarketSymbolError(ValueError):
    """Symbol does not belong to the expected market."""


def is_valid_market_symbol(symbol: str | None) -> bool:
    """True if ``symbol`` matches ``US.TICKER`` or ``HK.#####`` (4–5 digits)."""
    if not symbol:
        return False
    return bool(_MARKET_SYMBOL_RE.match(str(symbol).strip()))


def symbol_market(symbol: str | None) -> MarketId | None:
    """Infer market from Futu-style code prefix (``US.*`` / ``HK.*``)."""
    if not symbol:
        return None
    code = str(symbol).strip().upper()
    if code.startswith("US."):
        return "US"
    if code.startswith("HK."):
        return "HK"
    return None


def assert_symbol_matches_market(symbol: str | None, market: MarketId) -> None:
    """Raise if ``symbol`` is tagged for a different venue than ``market``."""
    inferred = symbol_market(symbol)
    if inferred is None:
        raise MarketSymbolError(
            f"symbol {symbol!r} has no US./HK. prefix; refuse for market={market}"
        )
    if inferred != market:
        raise MarketSymbolError(
            f"cross-market refuse: symbol {symbol!r} belongs to {inferred}, "
            f"session market={market}"
        )


def filter_symbols_for_market(
    symbols: list[str] | tuple[str, ...],
    market: MarketId,
) -> list[str]:
    """Keep only symbols whose prefix matches ``market``."""
    out: list[str] = []
    for sym in symbols:
        if symbol_market(sym) == market:
            out.append(sym)
    return out
