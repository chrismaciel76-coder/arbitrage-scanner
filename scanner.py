import os, time, threading, ccxt
from flask import Flask, render_template_string, request

MIN_SPREAD = 1
MAX_SPREAD = 100
SCAN_INTERVAL = 30

EXCHANGE_IDS = {"Gate": "gate", "MEXC": "mexc", "BingX": "bingx"}

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
    return (
        market.get("swap") is True
        or market.get("future") is True
        or market.get("contract") is True
    ) and (market.get("quote") == "USDT" or market.get("settle") == "USDT")

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
            status["errors"].append(f"{ex_name}: {e}")
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
    <title>Arbitrage Scanner</title>
    <meta http-equiv="refresh" content="30">
    <style>
        body {
            margin: 0;
            font-family: Arial, sans-serif;
            background: #0b0f19;
            color: #e5e7eb;
        }
        .header {
            padding: 20px;
            background: #111827;
            border-bottom: 1px solid #1f2937;
        }
        .title {
            font-size: 24px;
            font-weight: bold;
            color: #00ff99;
        }
        .subtitle {
            color: #9ca3af;
            margin-top: 5px;
        }
        .cards {
            display: flex;
            gap: 15px;
            padding: 20px;
            flex-wrap: wrap;
        }
        .card {
            background: #111827;
            border: 1px solid #1f2937;
            border-radius: 10px;
            padding: 15px;
            min-width: 180px;
        }
        .card-title {
            color: #9ca3af;
            font-size: 13px;
        }
        .card-value {
            font-size: 22px;
            margin-top: 6px;
            font-weight: bold;
        }
        .filters {
            padding: 0 20px 20px 20px;
        }
        select, input {
            background: #111827;
            color: white;
            border: 1px solid #374151;
            padding: 8px;
            border-radius: 6px;
            margin-right: 8px;
        }
        table {
            width: calc(100% - 40px);
            margin: 0 20px 30px 20px;
            border-collapse: collapse;
            background: #111827;
            border-radius: 10px;
            overflow: hidden;
        }
        th {
            background: #1f2937;
            color: #00ff99;
            padding: 12px;
            font-size: 13px;
        }
        td {
            padding: 10px;
            border-bottom: 1px solid #1f2937;
            text-align: center;
        }
        tr:hover {
            background: #172033;
        }
        .badge {
            padding: 5px 8px;
            border-radius: 20px;
            font-weight: bold;
            font-size: 12px;
        }
        .spotfut {
            background: #064e3b;
            color: #6ee7b7;
        }
        .futfut {
            background: #312e81;
            color: #c4b5fd;
        }
        .spread {
            color: #f87171;
            font-weight: bold;
            font-size: 16px;
        }
        a {
            color: #38bdf8;
            text-decoration: none;
            font-weight: bold;
        }
        button {
            background: #00cc88;
            border: none;
            color: #001b12;
            padding: 7px 10px;
            border-radius: 6px;
            font-weight: bold;
            cursor: pointer;
        }
        .empty {
            margin: 20px;
            padding: 25px;
            background: #111827;
            border: 1px solid #1f2937;
            border-radius: 10px;
            color: #fbbf24;
        }
        .error {
            margin: 20px;
            color: #f87171;
        }
    </style>
    <script>
        function openBoth(buy, sell) {
            window.open(buy, '_blank', 'width=900,height=900,left=0,top=0');
            window.open(sell, '_blank', 'width=900,height=900,left=920,top=0');
        }
    </script>
</head>
<body>
    <div class="header">
        <div class="title">Arbitrage Scanner</div>
        <div class="subtitle">Gate.io · MEXC · BingX | Spot x Futures / Futures x Futures</div>
    </div>

    <div class="cards">
        <div class="card">
            <div class="card-title">Oportunidades</div>
            <div class="card-value">{{filtered|length}}</div>
        </div>

        <div class="card">
            <div class="card-title">Última atualização</div>
            <div class="card-value" style="font-size:15px;">{{status.last_update}}</div>
        </div>

        <div class="card">
            <div class="card-title">Spread</div>
            <div class="card-value">{{min_spread}}% - {{max_spread}}%</div>
        </div>

        {% for ex, c in status.counts.items() %}
        <div class="card">
            <div class="card-title">{{ex}}</div>
            <div class="card-value" style="font-size:15px;">Spot: {{c.spot}} | Fut: {{c.fut}}</div>
        </div>
        {% endfor %}
    </div>

    <form class="filters" method="get">
        <select name="type">
            <option value="">Todos os tipos</option>
            <option value="SPOT x FUT" {% if selected_type == "SPOT x FUT" %}selected{% endif %}>SPOT x FUT</option>
            <option value="FUT x FUT" {% if selected_type == "FUT x FUT" %}selected{% endif %}>FUT x FUT</option>
        </select>

        <select name="exchange">
            <option value="">Todas exchanges</option>
            <option value="Gate" {% if selected_exchange == "Gate" %}selected{% endif %}>Gate</option>
            <option value="MEXC" {% if selected_exchange == "MEXC" %}selected{% endif %}>MEXC</option>
            <option value="BingX" {% if selected_exchange == "BingX" %}selected{% endif %}>BingX</option>
        </select>

        <input name="pair" placeholder="Filtrar par ex: BTC" value="{{selected_pair}}">
        <button type="submit">Filtrar</button>
    </form>

    {% if status.errors %}
        <div class="error"><b>Erros:</b> {{status.errors}}</div>
    {% endif %}

    {% if filtered|length == 0 %}
        <div class="empty">
            Nenhuma oportunidade encontrada neste momento. O scanner está ativo e continuará atualizando.
        </div>
    {% endif %}

    <table>
        <tr>
            <th>Par</th>
            <th>Tipo</th>
            <th>Comprar</th>
            <th>Vender</th>
            <th>Preço Compra</th>
            <th>Preço Venda</th>
            <th>Spread</th>
            <th>Ações</th>
        </tr>

        {% for r in filtered %}
        <tr>
            <td><b>{{r.pair}}</b></td>
            <td>
                {% if r.type == "SPOT x FUT" %}
                    <span class="badge spotfut">{{r.type}}</span>
                {% else %}
                    <span class="badge futfut">{{r.type}}</span>
                {% endif %}
            </td>
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
                <button onclick="openBoth('{{r.buy_link}}','{{r.sell_link}}')" type="button">Lado a lado</button>
            </td>
        </tr>
        {% endfor %}
    </table>
</body>
</html>
"""

@app.route("/")
def index():
    selected_type = request.args.get("type", "")
    selected_exchange = request.args.get("exchange", "")
    selected_pair = request.args.get("pair", "").upper().strip()

    filtered = data

    if selected_type:
        filtered = [r for r in filtered if r["type"] == selected_type]

    if selected_exchange:
        filtered = [
            r for r in filtered
            if selected_exchange in r["buy"] or selected_exchange in r["sell"]
        ]

    if selected_pair:
        filtered = [r for r in filtered if selected_pair in r["pair"].upper()]

    return render_template_string(
        HTML,
        filtered=filtered,
        status=status,
        min_spread=MIN_SPREAD,
        max_spread=MAX_SPREAD,
        selected_type=selected_type,
        selected_exchange=selected_exchange,
        selected_pair=selected_pair
    )

threading.Thread(target=scanner, daemon=True).start()

port = int(os.environ.get("PORT", 5000))
app.run(host="0.0.0.0", port=port)
