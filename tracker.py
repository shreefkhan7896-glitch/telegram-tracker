"""
24/7 Macro + On-Chain Telegram Tracker
No fixed price targets. Sends crisp alerts for:
- War / geopolitical news (bearish for crypto)
- Macro: DXY up, US10Y yields up, Crude Oil spike (bearish)
- Whales: $1M+ transfers on ETH (Etherscan, free key) and BTC (blockchain.info, no key);
  optional Whale Alert API ($10M+) if key provided
- Spot: unusual volume spikes on BTC/ETH/SOL

Env vars required:
  TELEGRAM_BOT_TOKEN - from BotFather
  TELEGRAM_CHAT_ID   - your chat id (get via @userinfobot)
Optional:
  WHALE_ALERT_API_KEY - free tier from whale-alert.io
  ETHERSCAN_API_KEY   - free key from etherscan.io (no card needed), enables ETH whale scan
  POLL_INTERVAL_SEC   - default 600 (10 min)

Whale scan notes (free-tier honest design):
  - ETH: latest block scanned every poll via Etherscan (small blocks, cheap).
    Sampled coverage - ETH makes a block every ~12s, so this catches whales
    landing in the sampled blocks, not every single transfer.
  - BTC: latest block scanned every ~3 hours via blockchain.info rawblock
    (blocks are ~8MB, so less frequent to respect free bandwidth limits).
  - Full real-time coverage of every transfer needs a paid API - this is the
    best free-tier setup.
"""

import os
import time
import json
import threading
import requests
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
WHALE_KEY = os.getenv("WHALE_ALERT_API_KEY", "")
ETHERSCAN_KEY = os.getenv("ETHERSCAN_API_KEY", "")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SEC", "600"))

WHALE_USD_THRESHOLD = 1_000_000  # $1M+ transfers count as whale moves

STATE_FILE = "/tmp/tracker_state.json"

COINS = ["bitcoin", "ethereum", "solana"]

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("[WARN] Telegram not configured, skipping send")
        print(text)
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=15)
        print(f"[TG] sent: {r.status_code}")
        return r.ok
    except Exception as e:
        print(f"[TG ERROR] {e}")
        return False

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {"last_alerts": {}, "prev_macro": {}}

def save_state(s):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(s, f)
    except Exception as e:
        print(f"[STATE ERROR] {e}")

def can_alert(state, key, cooldown_sec=3600):
    last = state["last_alerts"].get(key, 0)
    now = time.time()
    if now - last > cooldown_sec:
        state["last_alerts"][key] = now
        return True
    return False

def get_coingecko():
    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": ",".join(COINS), "vs_currencies": "usd", "include_24hr_change": "true", "include_24hr_vol": "true"}
        r = requests.get(url, params=params, timeout=15)
        return r.json()
    except Exception as e:
        print(f"[CG ERROR] {e}")
        return {}

def get_yahoo(symbol):
    # Free Yahoo chart API, no key
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {"interval": "1d", "range": "5d"}
        r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        j = r.json()
        closes = j["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c]
        if len(closes) >= 2:
            prev, last = closes[-2], closes[-1]
            chg = (last - prev) / prev * 100
            return {"price": last, "chg_24h": chg}
    except Exception as e:
        print(f"[YAHOO {symbol} ERROR] {e}")
    return {}

def check_macro(state):
    alerts = []
    dxy = get_yahoo("DX-Y.NYB")   # Dollar index
    tnx = get_yahoo("%5ETNX")       # US 10Y yield
    crude = get_yahoo("CL=F")       # WTI crude

    prev = state.get("prev_macro", {})

    # DXY up = bearish for crypto
    if dxy and dxy["chg_24h"] > 0.5:
        if can_alert(state, "macro_dxy_up", 7200):
            alerts.append(f"⚠️ *Macro Bearish:* DXY +{dxy['chg_24h']:.2f}% (dollar strong, crypto par pressure)")

    # Yields up = bearish
    if tnx and tnx["chg_24h"] > 2.0:
        if can_alert(state, "macro_yield_up", 7200):
            alerts.append(f"⚠️ *Macro Bearish:* US10Y Yield +{tnx['chg_24h']:.2f}% (yields up, risk-off)")

    # Crude spike + war context = bearish
    if crude and crude["chg_24h"] > 3.0:
        if can_alert(state, "macro_crude_spike", 7200):
            alerts.append(f"🛢️ *Crude Spike:* Oil +{crude['chg_24h']:.2f}% — geopolitical tension possible, crypto ke liye bearish")

    state["prev_macro"] = {"dxy": dxy, "tnx": tnx, "crude": crude}
    return alerts

def check_war_news(state):
    # GDELT free API for conflict news, last 1 hour
    alerts = []
    try:
        q = 'war OR "missile strike" OR "military escalation" OR "geopolitical escalation"'
        url = "https://api.gdeltproject.org/api/v2/doc/doc"
        params = {"query": q, "mode": "ArtList", "maxrecords": 5, "format": "json", "timespan": "1h"}
        r = requests.get(url, params=params, timeout=20)
        j = r.json()
        articles = j.get("articles", [])
        if articles and can_alert(state, "war_news", 10800):
            top = articles[0]
            title = top.get("title", "War-related news")[:120]
            alerts.append(f"🌍 *Geopolitical Alert:* {title}\nCrypto ke liye risk-off, caution rakho.")
    except Exception as e:
        print(f"[GDELT ERROR] {e}")
    return alerts

def check_whales(state):
    alerts = []
    if not WHALE_KEY:
        return alerts
    try:
        url = "https://api.whale-alert.io/v1/transactions"
        params = {"api_key": WHALE_KEY, "min_value": 10000000}  # $10M+
        r = requests.get(url, params=params, timeout=20)
        j = r.json()
        for tx in j.get("transactions", [])[:3]:
            txid = tx.get("id", "")
            key = f"whale_{txid}"
            if txid and can_alert(state, key, 86400):
                amt = tx.get("amount_usd", 0)
                sym = tx.get("symbol", "?").upper()
                frm = tx.get("from", {}).get("owner_type", "unknown")
                to = tx.get("to", {}).get("owner_type", "unknown")
                alerts.append(f"🐋 *Whale Move:* ${amt:,.0f} {sym} | {frm} → {to}")
    except Exception as e:
        print(f"[WHALE ERROR] {e}")
    return alerts

def check_eth_whales(state, eth_price):
    """Scan latest ETH block for $1M+ transfers. Etherscan free key, no card."""
    alerts = []
    if not ETHERSCAN_KEY or not eth_price:
        return alerts
    try:
        url = "https://api.etherscan.io/api"
        params = {"module": "proxy", "action": "eth_getBlockByNumber",
                  "tag": "latest", "boolean": "true", "apikey": ETHERSCAN_KEY}
        r = requests.get(url, params=params, timeout=30)
        block = r.json().get("result")
        if not isinstance(block, dict):
            return alerts  # e.g. Etherscan error string like "Invalid API Key"
        bnum = block.get("number", "")
        if not bnum or state.get("eth_last_block") == bnum:
            return alerts  # already scanned this block
        state["eth_last_block"] = bnum
        for tx in block.get("transactions", []):
            try:
                val_eth = int(tx.get("value", "0x0"), 16) / 1e18
            except (ValueError, TypeError):
                continue
            usd = val_eth * eth_price
            if usd >= WHALE_USD_THRESHOLD:
                h = tx.get("hash", "")
                if h and can_alert(state, f"ethw_{h}", 86400):
                    frm = (tx.get("from") or "?")[:12]
                    to = (tx.get("to") or "contract")[:12]
                    alerts.append(
                        f"🐋 *ETH Whale:* {val_eth:,.0f} ETH (${usd:,.0f})\n`{frm}...` → `{to}...`")
    except Exception as e:
        print(f"[ETH WHALE ERROR] {e}")
    return alerts


def check_btc_whales(state, btc_price):
    """Scan latest BTC block for $1M+ transfers. blockchain.info, no key needed."""
    alerts = []
    if not btc_price:
        return alerts
    try:
        tip = requests.get("https://blockchain.info/latestblock", timeout=15).json()
        h = tip.get("hash", "")
        if not h or state.get("btc_last_block") == h:
            return alerts  # already scanned this block
        block = None
        for _ in range(2):  # retry once on transient failure
            r = requests.get(f"https://blockchain.info/rawblock/{h}?format=json", timeout=60)
            j = r.json()
            if isinstance(j, dict) and "tx" in j:
                block = j
                break
            time.sleep(5)
        if not block:
            print("[BTC WHALE] rawblock fetch failed, will retry next run")
            return alerts
        state["btc_last_block"] = h  # mark scanned only after successful fetch
        for tx in block.get("tx", []):
            total_sat = sum(o.get("value", 0) for o in tx.get("out", []))
            usd = total_sat / 1e8 * btc_price
            if usd >= WHALE_USD_THRESHOLD:
                th = tx.get("hash", "")
                if th and can_alert(state, f"btcw_{th}", 86400):
                    btc_amt = total_sat / 1e8
                    alerts.append(f"🐋 *BTC Whale:* {btc_amt:,.1f} BTC (${usd:,.0f})")
    except Exception as e:
        print(f"[BTC WHALE ERROR] {e}")
    return alerts


def check_volume_spike(state, data):
    alerts = []
    for coin in COINS:
        info = data.get(coin, {})
        chg = info.get("usd_24h_change", 0)
        # Large 24h move without fixed target: flag unusual momentum
        if abs(chg) > 8:
            key = f"vol_{coin}_{int(chg)}"
            if can_alert(state, key, 7200):
                direction = "up" if chg > 0 else "down"
                alerts.append(f"📊 *{coin.upper()} Momentum:* {chg:+.1f}% in 24h — bada move, whales/exchange flow check karo.")
    return alerts

class HealthHandler(BaseHTTPRequestHandler):
    # Render Free Web Service + UptimeRobot keep-alive endpoint
    def do_GET(self):
        if self.path in ("/", "/health"):
            body = b"OK - tracker alive"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass  # keep logs clean


def start_health_server():
    port = int(os.getenv("PORT", "10000"))  # Render sets PORT automatically
    try:
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"[HEALTH] listening on port {port}")
    except Exception as e:
        print(f"[HEALTH ERROR] {e}")


def main_loop():
    start_health_server()  # keep Render free service awake via UptimeRobot pings
    print(f"[START] Tracker running, poll every {POLL_INTERVAL}s")
    send_telegram("✅ *Tracker Online*\nMacro + Whale alerts 24/7 shuru. No fixed price targets, sirf smart alerts.")
    state = load_state()
    poll_count = state.get("poll_count", 0)
    while True:
        try:
            poll_count += 1
            state["poll_count"] = poll_count
            alerts = []
            prices = get_coingecko()  # fetched once per loop, shared by checks
            eth_price = prices.get("ethereum", {}).get("usd", 0)
            btc_price = prices.get("bitcoin", {}).get("usd", 0)

            alerts += check_macro(state)
            alerts += check_war_news(state)
            alerts += check_whales(state)
            alerts += check_volume_spike(state, prices)
            alerts += check_eth_whales(state, eth_price)      # every poll (~10 min)
            if poll_count % 18 == 1:                           # every ~3 hours
                alerts += check_btc_whales(state, btc_price)   # BTC blocks are ~8MB

            for a in alerts:
                send_telegram(a)

            save_state(state)
            print(f"[{datetime.now(timezone.utc).isoformat()}] checked, {len(alerts)} alerts")
        except Exception as e:
            print(f"[LOOP ERROR] {e}")
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main_loop()
