"""Futu OpenD quote feed with graceful fallback when OpenD is offline."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_SYMBOL = "US.QQQ"
MOCK_QQQ_SNAPSHOT = {
    "code": DEFAULT_SYMBOL,
    "last_price": 450.0,
    "source": "mock",
}


@dataclass(frozen=True)
class QuoteSnapshot:
    code: str
    last_price: float
    source: str

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> QuoteSnapshot:
        return cls(
            code=str(data["code"]),
            last_price=float(data["last_price"]),
            source=str(data.get("source", "futu")),
        )


def _fetch_live_snapshot(host: str, port: int, symbol: str) -> QuoteSnapshot:
    from futu import RET_OK, OpenQuoteContext

    ctx = OpenQuoteContext(host=host, port=port)
    try:
        ret, data = ctx.get_market_snapshot([symbol])
        if ret != RET_OK or data is None or data.empty:
            raise ConnectionError(f"Futu snapshot failed for {symbol}: ret={ret}")

        row = data.iloc[0]
        last_price = row.get("last_price")
        if last_price is None or (hasattr(last_price, "__float__") and float(last_price) <= 0):
            raise ConnectionError(f"Invalid last_price for {symbol}")

        return QuoteSnapshot(code=symbol, last_price=float(last_price), source="futu")
    finally:
        ctx.close()


def get_qqq_snapshot(
    host: str = "127.0.0.1",
    port: int = 11111,
    symbol: str = DEFAULT_SYMBOL,
    *,
    allow_mock: bool = True,
) -> QuoteSnapshot:
    """Fetch US.QQQ snapshot from OpenD, or return mock data when unavailable."""
    try:
        return _fetch_live_snapshot(host, port, symbol)
    except ImportError:
        logger.warning("futu-api not installed; using mock QQQ snapshot")
    except Exception as exc:
        logger.warning("OpenD unavailable (%s); using mock QQQ snapshot", exc)

    if not allow_mock:
        raise ConnectionError("OpenD unavailable and mock fallback disabled")

    mock = dict(MOCK_QQQ_SNAPSHOT)
    mock["code"] = symbol
    return QuoteSnapshot.from_mapping(mock)
