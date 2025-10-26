# binance_bot/config.py
import os

class Settings:
    API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    API_SECRET: str = os.getenv("BINANCE_API_SECRET", "")
    USE_TESTNET: bool = os.getenv("BINANCE_USE_TESTNET", "false").lower() == "true"

    # 超时/重试
    REQUEST_TIMEOUT = 10
    WEBSOCKET_RECONNECT_SECS = 3

settings = Settings()
