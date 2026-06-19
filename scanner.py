import os, time, threading, ccxt
from flask import Flask, render_template_string, request

SCAN_INTERVAL = 10
DEFAULT_MIN_SPREAD = -100
DEFAULT_MAX_SPREAD = 100
DEFAULT_MIN_LIQUIDITY = 0
SPREAD_SAIDA_ALVO = 0.30

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
    if base and (quote == "USDT" or settle == "USDT"):
        return f"{base}/USDT"
    return None

def is_spot(m):
    return m.get("spot") is True and m.get("quote") == "USDT"

def is_fut(m):
    return (m.get("swap") is True or m.get("future") is True or m.get("contract") is True) and (
        m.get("quote") == "USDT" or m.get("settle") == "USDT"
    )

def load_markets_map():
    market_map, counts = {}, {}
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

def liquidity_from_ticker(ticker):
    qv = ticker.get("quoteVolume")
    if qv:
        try:
            return float(qv)
        except:
            pass

    bv = ticker.get("baseVolume")
    last = ticker.get("last")
    try:
        if bv and last:
            return float(bv) * float(last)
    except:
        pass

    return 0

def get_funding_rate(exchange, symbol, market):
    if market != "FUT":
        return None

    try:
        fr = exchange.fetch_funding_rate(symbol)
        rate = fr.get("fundingRate")
        if rate is not None:
            return float(rate) * 100
    except:
        pass

    return None

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
            liq = liquidity_from_ticker(ticker)

            if price and price > 0:
                prices.setdefault(key, []).append({
                    "exchange": ex_name,
                    "market": "SPOT",
                    "label": f"{ex_name} SPOT",
                    "symbol": symbol,
                    "price": float(price),
                    "liquidity": liq,
                    "fr": None,
                })

        for key, symbol in market_map[ex_name]["FUT"].items():
            ticker = fut_tickers.get(symbol, {})
            price = ticker.get("last")
            liq = liquidity_from_ticker(ticker)

            if price and price > 0:
                prices.setdefault(key, []).append({
                    "exchange": ex_name,
                    "market": "FUT",
                    "label": f"{ex_name} FUT",
                    "symbol": symbol,
                    "price": float(price),
                    "liquidity": liq,
                    "fr": None,
                })

    return prices

def symbol_base(symbol):
    return symbol.replace(":USDT", "").replace("/", "_").replace("-", "_")

def symbol_dash(symbol):
    return symbol.replace(":USDT", "").replace("/", "-").replace("_", "-")

def make_link(exchange, market, symbol):
    s_under = symbol_base(symbol)
    s_dash = symbol_dash(symbol)

    if exchange == "Gate" and market == "SPOT":
        return f"https://www.gate.com/trade/{s_under}"
    if exchange == "Gate" and market == "FUT":
        return f"https://www.gate.com/futures/USDT/{s_under}"

    if exchange == "MEXC" and market == "SPOT":
        return f"https://www.mexc.com/exchange/{s_under}"
    if exchange == "MEXC" and market == "FUT":
        return f"https://www.mexc.com/futures/{s_under}"

    if exchange == "BingX" and market == "SPOT":
        return f"https://bingx.com/en/spot/{s_dash}"
    if exchange == "BingX" and market == "FUT":
        return f"https://bingx.com/en/perpetual/{s_dash}"

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
                        min_liq = min(buy.get("liquidity", 0), sell.get("liquidity", 0))

                        buy_fr = get_funding_rate(exchanges[buy["exchange"]], buy["symbol"], buy["market"])
                        sell_fr = get_funding_rate(exchanges[sell["exchange"]], sell["symbol"], sell["market"])

                        results.append({
                            "pair": pair,
                            "type": arb_type,
                            "buy": buy["label"],
                            "sell": sell["label"],
                            "buy_price": round(buy["price"], 8),
                            "sell_price": round(sell["price"], 8),
                            "spread_abertura": round(spread, 2),
                            "spread_saida": SPREAD_SAIDA_ALVO,
                            "liquidity": round(min_liq, 2),
                            "buy_fr": None if buy_fr is None else round(buy_fr, 4),
                            "sell_fr": None if sell_fr is None else round(sell_fr, 4),
                            "buy_link": make_link(buy["exchange"], buy["market"], buy["symbol"]),
                            "sell_link": make_link(sell["exchange"], sell["market"], sell["symbol"]),
                        })

            data = sorted(results, key=lambda x: x["spread_abertura"], reverse=True)
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
    <meta http-equiv="refresh" content="10">
    <style>
        body{margin:0;font-family:Arial;background:#0b0f19;color:#e5e7eb}
        .header{padding:20px;background:#111827;border-bottom:1px solid #1f2937}
        .title{font-size:24px;font-weight:bold;color:#00ff99}
        .subtitle{color:#9ca3af;margin-top:5px}
        .tabs{padding:15px 20px}
        .tabs a{padding:10px 14px;background:#111827;border:1px solid #374151;border-radius:8px;margin-right:8px;color:#fff;text-decoration:none}
        .tabs a.active{background:#00cc88;color:#001b12;font-weight:bold}
        .cards{display:flex;gap:15px;padding:10px 20px;flex-wrap:wrap}
        .card{background:#111827;border:1px solid #1f2937;border-radius:10px;padding:15px;min-width:160px}
        .card-title{color:#9ca3af;font-size:13px}
        .card-value{font-size:20px;margin-top:6px;font-weight:bold}
        .filters{padding:10px 20px 20px}
        select,input{background:#111827;color:white;border:1px solid #374151;padding:8px;border-radius:6px;margin:4px}
        button{background:#00cc88;border:none;color:#001b12;padding:8px 12px;border-radius:6px;font-weight:bold;cursor:pointer}
        table{width:calc(100% - 40px);margin:0 20px 30px;border-collapse:collapse;background:#111827;border-radius:10px;overflow:hidden}
        th{background:#1f2937;color:#00ff99;padding:12px;font-size:13px}
        td{padding:10px;border-bottom:1px solid #1f2937;text-align:center}
        tr:hover{background:#172033}
        .badge{padding:5px 8px;border-radius:20px;font-weight:bold;font-size:12px}
        .spotfut{background:#064e3b;color:#6ee7b7}
        .futfut{background:#312e81;color:#c4b5fd}
        .pos{color:#22c55e;font-weight:bold}
        .neg{color:#ef4444;font-weight:bold}
        .neutral{color:#fbbf24;font-weight:bold}
        .liq{color:#93c5fd;font-weight:bold}
        .openIcon{font-size:22px;text-decoration:none}
        .empty{margin:20px;padding:25px;background:#111827;border:1px solid #1f2937;border-radius:10px;color:#fbbf24}
        .error{margin:20px;color:#f87171}
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

    <div class="tabs">
        <a href="/?view=best" class="{% if view == 'best' %}active{% endif %}">⭐ Melhores oportunidades</a>
        <a href="/?view=all" class="{% if view == 'all' %}active{% endif %}">📊 Todas</a>
    </div>

    <div class="cards">
        <div class="card"><div class="card-title">Oportunidades</div><div class="card-value">{{filtered|length}}</div></div>
        <div class="card"><div class="card-title">Total bruto</div><div class="card-value">{{total_data}}</div></div>
        <div class="card"><div class="card-title">Última atualização</div><div class="card-value" style="font-size:15px;">{{status.last_update}}</div></div>
        <div class="card"><div class="card-title">Intervalo</div><div class="card-value">{{interval}}s</div></div>

        {% for ex, c in status.counts.items() %}
        <div class="card"><div class="card-title">{{ex}}</div><div class="card-value" style="font-size:15px;">Spot: {{c.spot}} | Fut: {{c.fut}}</div></div>
        {% endfor %}
    </div>

    <form class="filters" method="get">
        <input type="hidden" name="view" value="{{view}}">
        <input name="min_spread" type="number" step="0.01" placeholder="Spread mínimo %" value="{{min_spread}}">
        <input name="max_spread" type="number" step="0.01" placeholder="Spread máximo %" value="{{max_spread}}">
        <input name="min_liquidity" type="number" step="1" placeholder="Liquidez mínima USDT" value="{{min_liquidity}}">
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
        <input name="pair" placeholder="Filtrar par" value="{{selected_pair}}">
        <button type="submit">Aplicar filtros</button>
    </form>

    {% if status.errors %}
        <div class="error"><b>Erros:</b> {{status.errors}}</div>
    {% endif %}

    {% if filtered|length == 0 %}
        <div class="empty">Nenhuma oportunidade encontrada com estes filtros.</div>
    {% endif %}

    <table>
        <tr>
            <th>Par</th>
            <th>Tipo</th>
            <th>Entrada</th>
            <th>Saída</th>
            <th>Preço Entrada</th>
            <th>Preço Saída</th>
            <th>Spread Entrada</th>
            <th>Spread Saída</th>
            <th>FR Entrada</th>
            <th>FR Saída</th>
            <th>Liquidez</th>
            <th>Abrir</th>
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

            <td class="{% if r.spread_abertura >= 0 %}pos{% else %}neg{% endif %}">
                {{r.spread_abertura}}%
            </td>

            <td class="neutral">{{r.spread_saida}}%</td>

            <td class="{% if r.buy_fr is not none and r.buy_fr >= 0 %}pos{% else %}neg{% endif %}">
                {{ "--" if r.buy_fr is none else r.buy_fr ~ "%" }}
            </td>

            <td class="{% if r.sell_fr is not none and r.sell_fr >= 0 %}pos{% else %}neg{% endif %}">
                {{ "--" if r.sell_fr is none else r.sell_fr ~ "%" }}
            </td>

            <td class="liq">{{r.liquidity}}</td>

            <td>
                <a class="openIcon" href="javascript:void(0)" onclick="openBoth('{{r.buy_link}}','{{r.sell_link}}')">🔗</a>
            </td>
        </tr>
        {% endfor %}
    </table>
</body>
</html>
"""

def get_float_param(name, default):
    try:
        value = request.args.get(name, "")
        return float(default) if value == "" else float(value)
    except:
        return float(default)

@app.route("/")
def index():
    view = request.args.get("view", "best")
    min_spread = get_float_param("min_spread", DEFAULT_MIN_SPREAD)
    max_spread = get_float_param("max_spread", DEFAULT_MAX_SPREAD)
    min_liquidity = get_float_param("min_liquidity", DEFAULT_MIN_LIQUIDITY)

    selected_type = request.args.get("type", "")
    selected_exchange = request.args.get("exchange", "")
    selected_pair = request.args.get("pair", "").upper().strip()

    filtered = data

    filtered = [r for r in filtered if min_spread <= r["spread_abertura"] <= max_spread]
    filtered = [r for r in filtered if r["liquidity"] >= min_liquidity]

    if view == "best":
        filtered = [r for r in filtered if r["spread_abertura"] > 0]
        filtered = sorted(filtered, key=lambda x: x["spread_abertura"], reverse=True)[:50]

    if selected_type:
        filtered = [r for r in filtered if r["type"] == selected_type]

    if selected_exchange:
        filtered = [r for r in filtered if selected_exchange in r["buy"] or selected_exchange in r["sell"]]

    if selected_pair:
        filtered = [r for r in filtered if selected_pair in r["pair"].upper()]

    return render_template_string(
        HTML,
        filtered=filtered,
        total_data=len(data),
        status=status,
        interval=SCAN_INTERVAL,
        min_spread=min_spread,
        max_spread=max_spread,
        min_liquidity=min_liquidity,
        selected_type=selected_type,
        selected_exchange=selected_exchange,
        selected_pair=selected_pair,
        view=view
    )

threading.Thread(target=scanner, daemon=True).start()

port = int(os.environ.get("PORT", 5000))
app.run(host="0.0.0.0", port=port)
