# binance_bot/utils.py
import math
from binance import AsyncClient
from binance.enums import *
from typing import Tuple, Dict

async def get_symbol_filters(client: AsyncClient, symbol: str) -> Dict:
    info = await client.get_symbol_info(symbol)
    if not info:
        raise ValueError(f"Symbol not found: {symbol}")
    filters = {f['filterType']: f for f in info['filters']}
    return {
        'lot_step': float(filters['LOT_SIZE']['stepSize']),
        'lot_min': float(filters['LOT_SIZE']['minQty']),
        'price_tick': float(filters['PRICE_FILTER']['tickSize']),
        'min_notional': float(filters.get('MIN_NOTIONAL', {}).get('minNotional', 0.0))
    }

def _precision_from_step(step: float) -> int:
    if step >= 1:
        return 0
    return int(round(-math.log(step, 10), 0))

def quantize_qty(qty: float, step: float) -> float:
    p = _precision_from_step(step)
    return math.floor(qty * (10 ** p)) / (10 ** p)

def quantize_price(px: float, tick: float) -> float:
    p = _precision_from_step(tick)
    return math.floor(px * (10 ** p)) / (10 ** p)

async def max_buy_base_qty(
    client: AsyncClient, symbol: str, order_usdt: float
) -> Tuple[float, float]:
    """返回(可买基础币数量, 最新价)"""
    ticker = await client.get_symbol_ticker(symbol=symbol)
    price = float(ticker["price"])
    filters = await get_symbol_filters(client, symbol)
    raw_qty = order_usdt / price
    qty = quantize_qty(raw_qty, filters['lot_step'])
    if qty < filters['lot_min']:
        return 0.0, price
    # 满足 MIN_NOTIONAL
    if order_usdt < filters['min_notional']:
        min_qty = quantize_qty(filters['min_notional'] / price, filters['lot_step'])
        return (min_qty if min_qty >= filters['lot_min'] else 0.0), price
    return qty, price

async def get_balance(client: AsyncClient, asset: str) -> float:
    acct = await client.get_account()
    for b in acct['balances']:
        if b['asset'] == asset:
            return float(b['free'])
    return 0.0

async def get_price(client: AsyncClient, symbol: str) -> float:
    t = await client.get_symbol_ticker(symbol=symbol)
    return float(t["price"])

async def _safe_price(client: AsyncClient, symbol: str) -> float:
    try:
        return await get_price(client, symbol)
    except Exception:
        return 0.0

async def commission_to_quote_usdt(
    client: AsyncClient,
    commission_asset: str,
    commission_amt: float,
    symbol: str,
    avg_trade_price: float
) -> float:
    """
    将手续费(可能是 BNB、USDT、base 或其他币)折算到USDT。
    - 若手续费资产==USDT(quote)：直接返回
    - 若手续费资产==BNB：用 BNBUSDT
    - 若手续费资产==base：用本单平均成交价 * 手续费数量
    - 其它：优先尝试 {asset}USDT，否则尝试 {asset}BUSD，再不行返回0(保守)
    """
    if commission_amt <= 0:
        return 0.0
    asset = commission_asset.upper()
    if asset == "USDT":
        return commission_amt

    # base from symbol
    base = base_asset_from_symbol(symbol)
    if asset == base:
        return commission_amt * float(avg_trade_price)

    if asset == "BNB":
        p = await _safe_price(client, "BNBUSDT")
        return commission_amt * p if p > 0 else 0.0

    # 尝试 ASSETUSDT
    p = await _safe_price(client, f"{asset}USDT")
    if p > 0:
        return commission_amt * p

    # 尝试 ASSETBUSD（有些老交易对）
    p = await _safe_price(client, f"{asset}BUSD")
    if p > 0:
        return commission_amt * p

    return 0.0

async def need_pause_for_bnb_fee(
    client: AsyncClient, order_usdt: float
) -> bool:
    """
    估算是否BNB不足以支付一次买+卖(2笔)手续费。
    以0.1%费率估算，按BNBUSDT市价换算。
    """
    bnb_free = await get_balance(client, "BNB")
    bnb_price = await get_price(client, "BNBUSDT")
    if bnb_price <= 0:
        return False  # 无法估算则不阻断
    bnb_needed = 2 * order_usdt * 0.001 / bnb_price
    return bnb_free < bnb_needed * 1.1

def base_asset_from_symbol(symbol: str) -> str:
    for quote in ("USDT","BUSD","USDC","FDUSD","TUSD","DAI"):
        if symbol.endswith(quote):
            return symbol[:-len(quote)]
    return symbol[:-4]
