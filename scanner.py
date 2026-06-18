import os, time, threading, ccxt
from flask import Flask, render_template_string

MIN_SPREAD = 1
MAX_SPREAD = 100
SCAN_INTERVAL = 30

EXCHANGE_IDS = {
    "Gate": "gate",
    "MEXC": "mexc",
    "BingX": "bingx",
}

data = []
status = {"last_update": "A iniciar...", "errors": [], "counts": {}}

def create_exchange(exchange_id):
    cls = getattr(ccxt, exchange_id)
    return cls({"enableRateLimit": True, "timeout": 20000})

exchanges = {name: create_exchange(eid) for name, eid in EXCHANGE_IDS.items()}

def normalize_key(market):
    base = market.get("base")
    quote = market.get("quote")
    settle = market.get("settle")
    if not base:
        return None
    if quote == "USDT" or settle == "USDT":
        return f"{base}/USDT"
    return None

def is_spot(market):
    return market.get("spot") is True and market.get("quote") == "USDT"

def is_fut(market):
    return (market.get("swap") is True or market.get("future") is True or market.get("contract") is True) and (
        market.get("quote") == "USDT" or market.get("settle") == "USDT"
    )

def load_markets_map():
    market_map = {}
    counts = {}

    for ex_name, ex in exchanges.items():
        market_map[ex_name] = {"SPOT": {}, "FUT": {}}

        try:
            markets = ex.load_markets()
            for symbol, market in markets.items():
                key = normalize_key(market)
                if not key:
                    continue

                if is_spot(market):
                    market_map[ex_name]["SPOT"][key] = symbol

                if is_fut(market):
                    market_map[ex_name]["FUT"][key] = symbol

            counts[ex_name] = {
                "spot": len(market_map[ex_name]["SPOT"]),
                "fut": len(market_map[ex_name]["FUT"]),
            }

        except Exception as e:
            counts[ex_name] = {"spot": 0, "fut": 0}
            status["errors"].append(f"Erro mercados {ex_name}: {e}")

    status["counts"] = counts
    return market_map

market_map = load_markets_map()

def safe_fetch_tickers(exchange, symbols):
    try:
        return exchange.fetch_tickers(symbols)
    except Exception:
        try:
            return exchange.fetch_tickers()
        except Exception:
            return {}

def get_all_prices():
    prices = {}

    for ex_name, ex in exchanges.items():
        spot_symbols = list(market_map[ex_name]["SPOT"].values())
        fut_symbols = list(market_map[ex_name]["FUT"].values())

        spot_tickers = safe_fetch_tickers(ex, spot_symbols)
        fut_tickers = safe_fetch_tickers(ex, fut_symbols)

        for key, symbol in market_map[ex_name]["SPOT"].items():
            ticker = spot_tickers.get(symbol, {})
            price = ticker.get("last")
            if price and price > 0:
                prices.setdefault(key, []).append({
                    "exchange": ex_name,
                    "market": "SPOT",
                    "label": f"{ex_name} SPOT",
                    "symbol": symbol,
                    "price": float(price),
                })

        for key, symbol in market_map[ex_name]["FUT"].items():
            ticker = fut_tickers.get(symbol, {})
            price = ticker.get("last")
            if price and price > 0:
                prices.setdefault(key, []).append({
                    "exchange": ex_name,
                    "market": "FUT",
                    "label": f"{ex_name} FUT",
                    "symbol": symbol,
                    "price": float(price),
                })

    return prices

def clean_symbol(symbol):
    return symbol.replace("/", "_").replace(":USDT", "")

def make_link(exchange, market, symbol):
    s = clean_symbol(symbol)

    if exchange == "Gate" and market == "SPOT":
        return f"https://www.gate.io/trade/{s}"
    if exchange == "Gate" and market == "FUT":
        return f"https://www.gate.io/futures_trade/{s}"

    if exchange == "MEXC" and market == "SPOT":
        return f"https://www.mexc.com/exchange/{s}"
    if exchange == "MEXC" and market == "FUT":
        return f"https://www.mexc.com/futures/{s}"

    if exchange == "BingX" and market == "SPOT":
        return f"https://bingx.com/en-us/spot/{s}"
    if exchange == "BingX" and market == "FUT":
        return f"https://bingx.com/en-us/perpetual/{s}"

    return "#"

def scanner():
    global data

    while True:
        results = []
        status["errors"] = []

        try:
            all_prices = get_all_prices()

            for pair, items in all_prices.items():
                if len(items) < 2:
                    continue

                for buy in items:
                    for sell in items:
                        if buy["label"] == sell["label"]:
                            continue

                        # Só permite SPOT x FUT e FUT x FUT
                        if buy["market"] == "SPOT" and sell["market"] == "FUT":
                            arb_type = "SPOT x FUT"
                        elif buy["market"] == "FUT" and sell["market"] == "FUT":
                            arb_type = "FUT x FUT"
                        else:
                            continue

                        spread = (sell["price"] - buy["price"]) / buy["price"] * 100

                        if MIN_SPREAD <= spread <= MAX_SPREAD:
                            results.append({
                                "pair": pair,
                                "type": arb_type,
                                "buy": buy["label"],
                                "sell": sell["label"],
                                "buy_price": round(buy["price"], 8),
                                "sell_price": round(sell["price"], 8),
                                "spread": round(spread, 2),
                                "buy_link": make_link(buy["exchange"], buy["market"], buy["symbol"]),
                                "sell_link": make_link(sell["exchange"], sell["market"], sell["symbol"]),
                            })

            data = sorted(results, key=lambda x: x["spread"], reverse=True)
            status["last_update"] = time.strftime("%Y-%m-%d %H:%M:%S")

        except Exception as e:
            status["errors"].append(str(e))

        time.sleep(SCAN_INTERVAL)

app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Scanner Arbitragem</title>
    <meta http-equiv="refresh" content="30">
    <style>
        body { font-family: Arial; background:#111; color:white; padding:20px; }
        table { width:100%; border-collapse:collapse; background:#1b1b1b; }
        th,td { border:1px solid #333; padding:8px; text-align:center; }
        th { background:#222; color:#00ff99; }
        a, button { color:#00ccff; font-weight:bold; cursor:pointer; }
        button { background:#222; border:1px solid #00ccff; padding:5px 8px; }
        .spread { color:#ff4d4d; font-weight:bold; }
        .box { background:#1b1b1b; padding:10px; margin-bottom:15px; border:1px solid #333; }
    </style>
    <script>
        function openBoth(buy, sell) {
            window.open(buy, '_blank', 'width=900,height=900,left=0,top=0');
            window.open(sell, '_blank', 'width=900,height=900,left=920,top=0');
        }
    </script>
</head>
<body>
    <h2>Scanner Arbitragem — Spot x Futures / Futures x Futures</h2>

    <div class="box">
        <p><b>Última atualização:</b> {{status.last_update}}</p>
        <p><b>Spread:</b> {{min_spread}}% até {{max_spread}}% | <b>Intervalo:</b> {{interval}}s</p>
        <p><b>Oportunidades:</b> {{data|length}}</p>

        {% for ex, c in status.counts.items() %}
            <p>{{ex}} — Spot: {{c.spot}} | Futuros: {{c.fut}}</p>
        {% endfor %}

        {% if status.errors %}
            <p><b>Erros:</b> {{status.errors}}</p>
        {% endif %}
    </div>

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
            <td class="spread">{{r.spread}}%</td>
            <td>
                <a href="{{r.buy_link}}" target="_blank">BUY</a>
                |
                <a href="{{r.sell_link}}" target="_blank">SELL</a>
                |
                <button onclick="openBoth('{{r.buy_link}}','{{r.sell_link}}')">Lado a lado</button>
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
        status=status,
        min_spread=MIN_SPREAD,
        max_spread=MAX_SPREAD,
        interval=SCAN_INTERVAL
    )

threading.Thread(target=scanner, daemon=True).start()

port = int(os.environ.get("PORT", 5000))
app.run(host="0.0.0.0", port=port)
