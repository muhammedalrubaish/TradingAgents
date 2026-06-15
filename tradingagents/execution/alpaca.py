"""Alpaca Markets trade execution integration.

Translates TradingAgents portfolio decisions (Buy/Overweight/Hold/Underweight/Sell)
into real market orders via the Alpaca Trading API.

Environment variables required:
    ALPACA_API_KEY      - Alpaca API key ID
    ALPACA_SECRET_KEY   - Alpaca API secret key
    ALPACA_PAPER        - "true" (default) for paper trading, "false" for live
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from tradingagents.agents.schemas import PortfolioRating, TraderAction


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


@dataclass
class ExecutionResult:
    success: bool
    order_id: Optional[str]
    symbol: str
    side: Optional[str]
    qty: Optional[float]
    filled_price: Optional[float]
    status: str
    message: str


def _rating_to_side(rating: PortfolioRating) -> Optional[OrderSide]:
    """Map 5-tier portfolio rating to an order side (None = no action)."""
    if rating in (PortfolioRating.BUY, PortfolioRating.OVERWEIGHT):
        return OrderSide.BUY
    if rating in (PortfolioRating.SELL, PortfolioRating.UNDERWEIGHT):
        return OrderSide.SELL
    return None  # HOLD


def _trader_action_to_side(action: TraderAction) -> Optional[OrderSide]:
    """Map 3-tier trader action to an order side (None = no action)."""
    if action == TraderAction.BUY:
        return OrderSide.BUY
    if action == TraderAction.SELL:
        return OrderSide.SELL
    return None  # HOLD


def _get_alpaca_client():
    """Build and return an Alpaca TradingClient."""
    try:
        from alpaca.trading.client import TradingClient
    except ImportError as e:
        raise ImportError(
            "alpaca-py is not installed. Run: pip install alpaca-py"
        ) from e

    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    paper = os.environ.get("ALPACA_PAPER", "true").strip().lower() in ("true", "1", "yes")

    if not api_key or not secret_key:
        raise ValueError(
            "Missing Alpaca credentials. Set ALPACA_API_KEY and ALPACA_SECRET_KEY "
            "environment variables (or add them to your .env file)."
        )

    return TradingClient(api_key, secret_key, paper=paper)


def get_account_info() -> dict:
    """Return current Alpaca account status and buying power."""
    client = _get_alpaca_client()
    account = client.get_account()
    return {
        "status": account.status,
        "buying_power": float(account.buying_power),
        "portfolio_value": float(account.portfolio_value),
        "cash": float(account.cash),
        "paper": os.environ.get("ALPACA_PAPER", "true").strip().lower() in ("true", "1", "yes"),
    }


def get_position(symbol: str) -> Optional[dict]:
    """Return open position for symbol, or None if flat."""
    try:
        from alpaca.common.exceptions import APIError
    except ImportError:
        pass

    client = _get_alpaca_client()
    try:
        pos = client.get_open_position(symbol.upper())
        return {
            "symbol": pos.symbol,
            "qty": float(pos.qty),
            "side": pos.side,
            "market_value": float(pos.market_value),
            "unrealized_pl": float(pos.unrealized_pl),
        }
    except Exception:
        return None


def submit_market_order(
    symbol: str,
    side: OrderSide,
    notional: Optional[float] = None,
    qty: Optional[float] = None,
) -> ExecutionResult:
    """Submit a market order by notional dollar amount or share quantity.

    Exactly one of ``notional`` or ``qty`` must be supplied.
    """
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide as AlpacaSide, TimeInForce

    if (notional is None) == (qty is None):
        raise ValueError("Supply exactly one of 'notional' or 'qty'.")

    client = _get_alpaca_client()
    alpaca_side = AlpacaSide.BUY if side == OrderSide.BUY else AlpacaSide.SELL

    req = MarketOrderRequest(
        symbol=symbol.upper(),
        side=alpaca_side,
        time_in_force=TimeInForce.DAY,
        notional=notional,
        qty=qty,
    )

    try:
        order = client.submit_order(req)
        return ExecutionResult(
            success=True,
            order_id=str(order.id),
            symbol=symbol.upper(),
            side=side.value,
            qty=float(order.qty) if order.qty else None,
            filled_price=float(order.filled_avg_price) if order.filled_avg_price else None,
            status=str(order.status),
            message=f"Order submitted: {order.id}",
        )
    except Exception as exc:
        return ExecutionResult(
            success=False,
            order_id=None,
            symbol=symbol.upper(),
            side=side.value,
            qty=qty,
            filled_price=None,
            status="error",
            message=str(exc),
        )


def submit_limit_order(
    symbol: str,
    side: OrderSide,
    limit_price: float,
    qty: float,
) -> ExecutionResult:
    """Submit a limit order at a specific price."""
    from alpaca.trading.requests import LimitOrderRequest
    from alpaca.trading.enums import OrderSide as AlpacaSide, TimeInForce

    client = _get_alpaca_client()
    alpaca_side = AlpacaSide.BUY if side == OrderSide.BUY else AlpacaSide.SELL

    req = LimitOrderRequest(
        symbol=symbol.upper(),
        side=alpaca_side,
        time_in_force=TimeInForce.DAY,
        limit_price=limit_price,
        qty=qty,
    )

    try:
        order = client.submit_order(req)
        return ExecutionResult(
            success=True,
            order_id=str(order.id),
            symbol=symbol.upper(),
            side=side.value,
            qty=qty,
            filled_price=None,
            status=str(order.status),
            message=f"Limit order submitted: {order.id}",
        )
    except Exception as exc:
        return ExecutionResult(
            success=False,
            order_id=None,
            symbol=symbol.upper(),
            side=side.value,
            qty=qty,
            filled_price=None,
            status="error",
            message=str(exc),
        )


def execute_from_portfolio_decision(
    symbol: str,
    rating: PortfolioRating,
    notional: float,
    limit_price: Optional[float] = None,
) -> ExecutionResult:
    """Execute a trade based on the Portfolio Manager's rating.

    Args:
        symbol:      Ticker symbol (e.g. "NVDA")
        rating:      PortfolioRating from the Portfolio Manager agent
        notional:    Dollar amount to trade (e.g. 1000.0)
        limit_price: When supplied, submits a limit order instead of market

    Returns:
        ExecutionResult with order details or error info
    """
    side = _rating_to_side(rating)

    if side is None:
        return ExecutionResult(
            success=True,
            order_id=None,
            symbol=symbol.upper(),
            side="hold",
            qty=None,
            filled_price=None,
            status="skipped",
            message=f"Rating is HOLD — no order submitted for {symbol}.",
        )

    if limit_price is not None:
        qty = round(notional / limit_price, 6)
        return submit_limit_order(symbol, side, limit_price=limit_price, qty=qty)

    return submit_market_order(symbol, side, notional=notional)


def execute_from_trader_action(
    symbol: str,
    action: TraderAction,
    notional: float,
    limit_price: Optional[float] = None,
) -> ExecutionResult:
    """Execute a trade based on the Trader agent's action.

    Args:
        symbol:      Ticker symbol
        action:      TraderAction (BUY / HOLD / SELL)
        notional:    Dollar amount to trade
        limit_price: When supplied, submits a limit order instead of market

    Returns:
        ExecutionResult with order details or error info
    """
    side = _trader_action_to_side(action)

    if side is None:
        return ExecutionResult(
            success=True,
            order_id=None,
            symbol=symbol.upper(),
            side="hold",
            qty=None,
            filled_price=None,
            status="skipped",
            message=f"Action is HOLD — no order submitted for {symbol}.",
        )

    if limit_price is not None:
        qty = round(notional / limit_price, 6)
        return submit_limit_order(symbol, side, limit_price=limit_price, qty=qty)

    return submit_market_order(symbol, side, notional=notional)
