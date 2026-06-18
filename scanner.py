import os
import time
import threading
import ccxt
from flask import Flask, render_template_string

MIN_SPREAD = 1
MAX_SPREAD = 100
SCAN_INTERVAL = 15

gate = ccxt.gateio({"enableRateLimit": True})
mexc = ccxt.mexc({"enableRateLimit": True})
bingx = ccxt.bingx({"enableRateLimit": True})

data = []

def load_pairs():
    print("Carregando mercados...")

    exchanges = {
        "Gate": gate,
        "MEXC": mexc,
        "BingX": bingx
    }

    spot = {}
    fut = {}

    for name, ex in exchanges.items():
        try:
            markets = ex.load_markets()
            spot[name] = [
                s for s, m in markets.items()
                if "/USDT" in s and m.get("spot") is True
            ]
            fut[name] = [
                s for s, m in markets.items()
                if "/USDT" in s and (m.get("swap") is True or m.get("future") is True)
            ]
            print(name, "spot:", len(spot[name]), "futuros:", len(fut[name]))
        except Exception as e:
            print("Erro ao carregar", name, e)
            spot[name] = []
            fut[name] = []

    all_pairs = list(set(
        spot["Gate"] + spot["MEXC"] + spot["BingX"] +
        fut["Gate"] + fut["MEXC"] + fut["BingX"]
    ))

    return exchanges, spot, fut, all_pairs

exchanges, spot_pairs, fut_pairs, all_pairs = load_pairs()

def get_price(exchange, pair):
    try:
        ticker = exchange.fetch_ticker(pair)
        price = ticker.get("last")
        if price is None or price <= 0:
            return None
        return float(price)
    except:
        return None

def make_link(exchange_name, market_type, pair):
    symbol = pair.replace("/", "_").replace(":USDT", "")

    if exchange_name == "Gate" and market_type == "SPOT":
        return f"https://www.gate.io/trade/{symbol}"

    if exchange_name == "Gate" and market_type == "FUT":
        return f"https://www.gate.io/futures_trade/{symbol}"

    if exchange_name == "MEXC" and market_type == "SPOT":
        return f"https://www.mexc.com/exchange/{symbol}"

    if exchange_name == "MEXC" and market_type == "FUT":
        return f"https://www.mexc.com/futures/{symbol}"

    if exchange_name == "BingX" and market_type == "SPOT":
        return f"https://bingx.com/en-us/spot/{symbol}"

    if exchange_name == "BingX" and market_type == "FUT":
        return f"https://bingx.com/en-us/perpetual/{symbol}"

    return "#"

def scanner():
    global data

    while True:
        results = []

        for pair in all_pairs:
            prices = {}

            for name, ex in exchanges.items():
                if pair in spot_pairs[name]:
                    price = get_price(ex, pair)
                    if price:
                        prices[f"{name} SPOT"] = price

                if pair in fut_pairs[name]:
                    price = get_price(ex, pair)
                    if price:
                        prices[f"{name} FUT"] = price

            valid_prices = {
                k: v for k, v in prices.items()
                if v is not None and v > 0
            }

            if len(valid_prices) < 2:
                continue

            buy_ex = min(valid_prices, key=valid_prices.get)
            sell_ex = max(valid_prices, key=valid_prices.get)

            buy_price = valid_prices[buy_ex]
            sell_price = valid_prices[sell_ex]

            spread = (sell_price - buy_price) / buy_price * 100

            if not (MIN_SPREAD <= spread <= MAX_SPREAD):
                continue

            if "SPOT" in buy_ex and "FUT" in sell_ex:
                arb_type = "SPOT x FUT"

            elif "FUT" in buy_ex and "FUT" in sell_ex:
                arb_type = "FUT x FUT"

            else:
                continue

            buy_name, buy_market = buy_ex.split()
            sell_name, sell_market = sell_ex.split()

            results.append({
                "pair": pair,
                "type": arb_type,
                "buy": buy_ex,
                "sell": sell_ex,
                "buy_price": round(buy_price, 8),
                "sell_price": round(sell_price, 8),
                "spread": round(spread, 2),
                "buy_link": make_link(buy_name, buy_market, pair),
                "sell_link": make_link(sell_name, sell_market, pair),
            })

        results = sorted(results, key=lambda x: x["spread"], reverse=True)
        data = results
        time.sleep(SCAN_INTERVAL)

app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Scanner Arbitragem</title>
    <meta http-equiv="refresh" content="15">
    <style>
        body { font-family: Arial; padding: 20px; background: #111; color: white; }
        table { width: 100%; border-collapse: collapse; background: #1c1c1c; }
        th, td { border: 1px solid #333; padding: 8px; text-align: center; }
        th { background: #222; color: #00ff99; }
        a { color: #00ccff; font-weight: bold; }
        .high { color: #ff4d4d; font-weight: bold; }
    </style>
</head>
<body>
    <h2>Scanner Arbitragem — Spot x Futures / Futures x Futures</h2>
    <p>Spread: {{min_spread}}% até {{max_spread}}% | Atualiza a cada {{interval}} segundos</p>
    <p>Oportunidades encontradas: {{data|length}}</p>

    <table>
        <tr>
            <th>Par</th>
            <th>Tipo</th>
            <th>Comprar</th>
            <th>Vender</th>
            <th>Preço Compra</th>
            <th>Preço Venda</th>
            <th>Spread</th>
            <th>Abrir</th>
        </tr>

        {% for r in data %}
        <tr>
            <td>{{r.pair}}</td>
            <td>{{r.type}}</td>
            <td>{{r.buy}}</td>
            <td>{{r.sell}}</td>
            <td>{{r.buy_price}}</td>
            <td>{{r.sell_price}}</td>
            <td class="high">{{r.spread}}%</td>
            <td>
                <a href="{{r.buy_link}}" target="_blank">BUY</a>
                |
                <a href="{{r.sell_link}}" target="_blank">SELL</a>
            </td>
        </tr>
        {% endfor %}
    </table>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(
        HTML,
        data=data,
        min_spread=MIN_SPREAD,
        max_spread=MAX_SPREAD,
        interval=SCAN_INTERVAL
    )

threading.Thread(target=scanner, daemon=True).start()

port = int(os.environ.get("PORT", 5000))
app.run(host="0.0.0.0", port=port)