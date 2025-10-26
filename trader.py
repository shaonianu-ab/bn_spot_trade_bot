# binance_bot/trader.py
import asyncio
from dataclasses import dataclass
from typing import Optional, Dict, List
from binance import AsyncClient
from binance.enums import *
from .utils import (
    get_symbol_filters, quantize_price, quantize_qty, max_buy_base_qty,
    get_balance, get_price, base_asset_from_symbol, need_pause_for_bnb_fee,
    commission_to_quote_usdt
)

@dataclass
class Stats:
    limit_sell_filled: int = 0
    forced_market_sell: int = 0
    pnl_usdt: float = 0.0

@dataclass
class Config:
    symbol: str
    order_usdt: float
    target_usdt: float
    deviation: float = 0.0005      # 0.05%
    poll_interval: float = 2.0     # seconds

class Trader:
    def __init__(self, client: AsyncClient, cfg: Config):
        self.client = client
        self.cfg = cfg
        self.filters = None
        self.base = base_asset_from_symbol(cfg.symbol)
        self.quote = "USDT" if cfg.symbol.endswith("USDT") else cfg.symbol[-4:]
        self.stats = Stats()
        self.session_filled_quote: float = 0.0  # 本次任务累计成交额(买+卖)
        self.open_base_qty: float = 0.0
        self.open_avg_buy_quote: float = 0.0
        self.open_limit_order_id: Optional[int] = None
        self.open_limit_price: Optional[float] = None
        # 记录本笔买单的手续费(USDT)
        self.open_buy_fee_usdt: float = 0.0

    async def init(self):
        self.filters = await get_symbol_filters(self.client, self.cfg.symbol)

    async def place_market(self, side: str, qty_base: float):
        return await self.client.create_order(
            symbol=self.cfg.symbol,
            side=SIDE_BY_MAP[side],
            type=ORDER_TYPE_MARKET,
            quantity=qty_base
        )

    async def place_limit_sell(self, qty_base: float, price: float):
        price_q = quantize_price(price, self.filters['price_tick'])
        qty_q = quantize_qty(qty_base, self.filters['lot_step'])
        if qty_q < self.filters['lot_min']:
            return None
        return await self.client.create_order(
            symbol=self.cfg.symbol,
            side=SIDE_SELL,
            type=ORDER_TYPE_LIMIT,
            timeInForce=TIME_IN_FORCE_GTC,
            price=f"{price_q:.10f}".rstrip('0').rstrip('.'),
            quantity=qty_q
        )

    async def cancel_order_silent(self, order_id: int):
        try:
            await self.client.cancel_order(symbol=self.cfg.symbol, orderId=order_id)
        except Exception:
            pass

    async def fetch_order(self, order_id: int) -> Optional[Dict]:
        try:
            return await self.client.get_order(symbol=self.cfg.symbol, orderId=order_id)
        except Exception:
            return None

    async def _sum_market_fills_fee_usdt(self, fills: List[Dict], avg_px: float) -> float:
        """把create_order(MARKET)返回的fills手续费统一折算为USDT"""
        total_fee = 0.0
        for f in fills or []:
            com_asset = f.get("commissionAsset")
            com_amt = float(f.get("commission", 0.0))
            if com_asset and com_amt > 0:
                total_fee += await commission_to_quote_usdt(
                    self.client, com_asset, com_amt, self.cfg.symbol, avg_px
                )
        return total_fee

    async def _sum_order_trades_fee_usdt(self, order_id: int, avg_px: float) -> float:
        """通过 get_my_trades(orderId=) 汇总该限价单成交的手续费（精确版）"""
        try:
            trades = await self.client.get_my_trades(symbol=self.cfg.symbol, orderId=order_id)
        except Exception:
            trades = []
        total_fee = 0.0
        for t in trades:
            com_asset = t.get("commissionAsset")
            com_amt = float(t.get("commission", 0.0))
            if com_asset and com_amt > 0:
                total_fee += await commission_to_quote_usdt(
                    self.client, com_asset, com_amt, self.cfg.symbol, avg_px
                )
        return total_fee

    async def maybe_pause_for_bnb(self) -> bool:
        return await need_pause_for_bnb_fee(self.client, self.cfg.order_usdt)

    async def total_position_value_for_symbol(self, last_price: float) -> float:
        base_free = await get_balance(self.client, self.base)
        return base_free * last_price

    async def should_pause_for_position(self, last_price: float) -> bool:
        base_value = await self.total_position_value_for_symbol(last_price)
        total_pos_quote = base_value + max(self.open_avg_buy_quote, 1e-9)
        return self.open_avg_buy_quote > 0.5 * total_pos_quote

    async def run_once_cycle(self, last_price: float):
        if await self.maybe_pause_for_bnb():
            print("Insufficient bnb")
            return
        if await self.should_pause_for_position(last_price):
            print("Too large posotion")
            return

        qty_base, price_now = await max_buy_base_qty(self.client, self.cfg.symbol, self.cfg.order_usdt)
        if qty_base <= 0:
            return

        # --- 市价买入 ---
        buy_ord = await self.client.create_order(
            symbol=self.cfg.symbol, side=SIDE_BUY, type=ORDER_TYPE_MARKET, quantity=qty_base
        )
        buy_cum_quote = float(buy_ord.get("cummulativeQuoteQty", 0.0))
        avg_buy_px = (buy_cum_quote / qty_base) if (buy_cum_quote > 0 and qty_base > 0) else price_now

        # 统计市价买入手续费
        buy_fee_usdt = await self._sum_market_fills_fee_usdt(buy_ord.get("fills", []), avg_buy_px)

        self.open_base_qty = qty_base
        self.open_avg_buy_quote = buy_cum_quote
        self.open_buy_fee_usdt = buy_fee_usdt
        self.session_filled_quote += buy_cum_quote

        # --- 以买入均价挂限价卖出 ---
        lim = await self.place_limit_sell(qty_base=self.open_base_qty, price=avg_buy_px)
        if not lim:
            # 限价挂不出去则直接市价卖
            sell_mkt = await self.client.create_order(
                symbol=self.cfg.symbol, side=SIDE_SELL, type=ORDER_TYPE_MARKET, quantity=self.open_base_qty
            )
            sell_quote = float(sell_mkt.get("cummulativeQuoteQty", 0.0))
            # 市价卖手续费
            sell_fee_usdt = await self._sum_market_fills_fee_usdt(sell_mkt.get("fills", []), avg_buy_px)

            self.stats.forced_market_sell += 1
            self.stats.pnl_usdt += (sell_quote - buy_cum_quote - buy_fee_usdt - sell_fee_usdt)
            self.session_filled_quote += sell_quote
            self._clear_open()
            return

        self.open_limit_order_id = lim["orderId"]
        self.open_limit_price = avg_buy_px

        # --- 监控成交/偏离 ---
        while True:
            await asyncio.sleep(self.cfg.poll_interval)

            ord_info = await self.fetch_order(self.open_limit_order_id)
            if ord_info and ord_info.get("status") in ("FILLED", "PARTIALLY_FILLED"):
                if ord_info["status"] == "FILLED":
                    executed_quote = float(ord_info.get("cummulativeQuoteQty", 0.0))
                    # 精确拉成交手续费
                    sell_fee_usdt = await self._sum_order_trades_fee_usdt(self.open_limit_order_id, self.open_limit_price)

                    self.stats.limit_sell_filled += 1
                    self.stats.pnl_usdt += (executed_quote - buy_cum_quote - self.open_buy_fee_usdt - sell_fee_usdt)
                    self.session_filled_quote += executed_quote
                    self._clear_open()
                    return
                # 部分成交则更新剩余数量
                executed_qty = float(ord_info.get("executedQty", 0.0))
                self.open_base_qty = max(self.open_base_qty - executed_qty, 0.0)

            # 偏离阈值触发强平
            px = await get_price(self.client, self.cfg.symbol)
            if self.open_limit_price and abs(px - self.open_limit_price) / self.open_limit_price > self.cfg.deviation:
                await self.cancel_order_silent(self.open_limit_order_id)

                if self.open_base_qty > 0:
                    sell_mkt = await self.client.create_order(
                        symbol=self.cfg.symbol, side=SIDE_SELL, type=ORDER_TYPE_MARKET, quantity=self.open_base_qty
                    )
                    sell_quote = float(sell_mkt.get("cummulativeQuoteQty", 0.0))
                    sell_fee_usdt = await self._sum_market_fills_fee_usdt(sell_mkt.get("fills", []), px)
                else:
                    sell_quote = 0.0
                    sell_fee_usdt = 0.0

                self.stats.forced_market_sell += 1
                self.stats.pnl_usdt += (sell_quote - buy_cum_quote - self.open_buy_fee_usdt - sell_fee_usdt)
                self.session_filled_quote += sell_quote
                self._clear_open()
                return

    def _clear_open(self):
        self.open_base_qty = 0.0
        self.open_avg_buy_quote = 0.0
        self.open_buy_fee_usdt = 0.0
        self.open_limit_order_id = None
        self.open_limit_price = None

    def summary(self) -> str:
        wear = abs((self.stats.pnl_usdt / self.session_filled_quote * 10000) if self.session_filled_quote != 0.0 else 0)
        return (f"[统计] 限价卖出成功: {self.stats.limit_sell_filled} 次 | "
                f"偏离阈值强平: {self.stats.forced_market_sell} 次 | "
                f"总体盈亏(含费): {self.stats.pnl_usdt:.4f} {self.quote} | "
                f"本次任务成交额: {self.session_filled_quote:.2f} {self.quote} | "
                f"磨损: 万{wear:.1f}")


# 简化：side字符串映射
SIDE_BY_MAP = {"BUY": SIDE_BUY, "SELL": SIDE_SELL}
