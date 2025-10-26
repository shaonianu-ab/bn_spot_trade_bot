README.md（要点）

大陆建议上境外服务器，否则可能因网络问题无法正常启用该脚本。

先 pip install -r requirements.txt

设置环境变量或在 config.py 中填 API_KEY/SECRET

启动示例（实盘谨慎）：

python -m bn_spot_trade_bot.main --symbol WALUSDT --order-usdt 100 --target-usdt 1000 --deviation 0.0005 --poll-interval 2.0


参数：

--symbol 指定代币对（现货，如 BTCUSDT）

--order-usdt 单次购买 USDT 金额

--target-usdt 本次任务累计成交目标 USDT

--deviation 价格偏离阈值（0.0005 = 0.05%）

--poll-interval 检查/动作间隔（秒）

--use-testnet 使用币安现货 Testnet

统计指标输出与持续记录：成功限价卖出次数、偏离阈值触发市价卖出次数、总体盈亏（USDT）。

注意你的文件夹名必须为bn_spot_trade_bot，然后再bn_spot_trade_bot同级目录执行启动命令。
