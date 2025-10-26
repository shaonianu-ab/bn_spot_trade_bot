# binance_bot/ws.py
import asyncio
from binance import AsyncClient, BinanceSocketManager

class PriceStream:
    def __init__(self, client: AsyncClient, symbol: str):
        self.client = client
        self.symbol = symbol
        self._q = asyncio.Queue(maxsize=1)  # 只保留最新
        self._task = None
        self._stop = asyncio.Event()

    async def start(self):
        bm = BinanceSocketManager(self.client)
        # 使用单品种ticker（! 现货）
        ts = bm.symbol_ticker_socket(self.symbol)
        async with ts as stream:
            while not self._stop.is_set():
                msg = await stream.recv()
                price = float(msg.get("c") or msg.get("lastPrice") or 0.0)
                if price <= 0:
                    continue
                if self._q.full():
                    _ = self._q.get_nowait()
                await self._q.put(price)

    async def latest_price(self) -> float:
        # 若队列为空，主动拉一次
        if self._q.empty():
            return None
        return await self._q.get()

    def run_forever(self):
        self._task = asyncio.create_task(self.start())

    async def stop(self):
        self._stop.set()
        if self._task:
            await asyncio.wait([self._task], timeout=2)
