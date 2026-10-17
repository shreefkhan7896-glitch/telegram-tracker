"""
24/7 Macro + On-Chain Telegram Tracker
No fixed price targets. Sends crisp alerts for:
- War / geopolitical news (bearish for crypto)
- Macro: DXY up, US10Y yields up, Crude Oil spike (bearish)
- Whales: large exchange flows, whale wallet movements (via Whale Alert if key provided)
- Spot: unusual volume spikes on BTC/ETH/SOL

Env vars required:
  TELEGRAM_BOT_TOKEN - from BotFather
  TELEGRAM_CHAT_ID   - your chat id (get via @userinfobot)
Optional:
  WHALE_ALERT_API_KEY - free tier from whale-alert.io
  POLL_INTERVAL_SEC   - default 600 (10 min)
"""

import os
import time
import json
import requests
from datetime import datetime, timezone

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
WHALE_KEY = os.getenv("WHALE_ALERT_API_KEY", "")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SEC", "600"))

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

def check_volume_spike(state):
    alerts = []
    data = get_coingecko()
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

def main_loop():
    print(f"[START] Tracker running, poll every {POLL_INTERVAL}s")
    send_telegram("✅ *Tracker Online*\nMacro + Whale alerts 24/7 shuru. No fixed price targets, sirf smart alerts.")
    state = load_state()
    while True:
        try:
            alerts = []
            alerts += check_macro(state)
            alerts += check_war_news(state)
            alerts += check_whales(state)
            alerts += check_volume_spike(state)

            for a in alerts:
                send_telegram(a)

            save_state(state)
            print(f"[{datetime.now(timezone.utc).isoformat()}] checked, {len(alerts)} alerts")
        except Exception as e:
            print(f"[LOOP ERROR] {e}")
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main_loop()
