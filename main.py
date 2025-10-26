# binance_bot/main.py
import os
import sys
import asyncio
import argparse
from binance import AsyncClient
from .config import settings
from .trader import Trader, Config as TraderConfig
from .ws import PriceStream

def _apply_win_loop_policy():
    # 解决 Windows 下 websockets + Proactor 的退出异常
    if sys.platform.startswith("win"):
        try:
            import asyncio
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass

async def run_bot(args):
    client = await AsyncClient.create(
        api_key=os.getenv("BINANCE_API_KEY", settings.API_KEY),
        api_secret=os.getenv("BINANCE_API_SECRET", settings.API_SECRET),
        testnet=args.use_testnet
    )

    ps = None
    try:
        tcfg = TraderConfig(
            symbol=args.symbol,
            order_usdt=args.order_usdt,
            target_usdt=args.target_usdt,
            deviation=args.deviation,
            poll_interval=args.poll_interval
        )
        trader = Trader(client, tcfg)
        await trader.init()

        # WebSocket 实时价格
        ps = PriceStream(client, args.symbol)
        ps.run_forever()

        print(f"▶️ 启动：{args.symbol} | 单次买入 {args.order_usdt} USDT | 目标成交 {args.target_usdt} USDT | 偏离阈值 {args.deviation*100:.3f}% | 每 {args.poll_interval}s 检查")
        while trader.session_filled_quote < args.target_usdt:
            last_price = await ps.latest_price()
            if last_price is None:
                await asyncio.sleep(0.2)
                continue

            await trader.run_once_cycle(last_price)
            print(trader.summary())
            await asyncio.sleep(max(0.05, args.poll_interval))

        print("✅ 目标已达成")
        # print(trader.summary())

    finally:
        # 优雅关闭：先停WS，再关HTTP客户端
        try:
            if ps:
                await ps.stop()
        except Exception:
            pass
        try:
            await client.close_connection()
        except Exception:
            pass

def parse_args():
    p = argparse.ArgumentParser(description="Binance Spot Auto Trader")
    p.add_argument("--symbol", required=True, help="交易对，例如 BTCUSDT")
    p.add_argument("--order-usdt", type=float, required=True, help="单次市价买入的USDT金额")
    p.add_argument("--target-usdt", type=float, required=True, help="本次任务希望达到的总成交额(USDT)")
    p.add_argument("--deviation", type=float, default=0.0005, help="价格偏离阈值(比例)，默认0.0005=0.05%")
    p.add_argument("--poll-interval", type=float, default=2.0, help="检查间隔秒")
    p.add_argument("--use-testnet", action="store_true", help="使用币安Spot Testnet")
    return p.parse_args()

if __name__ == "__main__":
    _apply_win_loop_policy()
    args = parse_args()
    asyncio.run(run_bot(args))
