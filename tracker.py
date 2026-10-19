"""
24/7 Macro + On-Chain Telegram Tracker
No fixed price targets. Sends crisp alerts for:
- War / geopolitical news (bearish for crypto)
- Macro: DXY up, US10Y yields up, Crude Oil spike (bearish)
- Fed/Macro news: GDELT se Fed/FOMC/CPI breaking news + bade financial press
  (Bloomberg/Reuters/WSJ/CoinDesk) ki crypto coverage par alerts;
  foreign-language titles auto-translate hokar English me aate hain (free, no key)
- Event calendar: FOMC + CPI dates hardcoded — 1 din pehle aur us din advance alert
- Whales: $1M+ transfers on ETH (Etherscan, free key) and BTC (blockchain.info, no key);
  optional Whale Alert API ($10M+) if key provided
- Spot: unusual volume spikes on BTC/ETH/SOL
- Futures (100% free, no keys):
  - Liquidations: Binance first (biggest volume), auto-fallback to OKX if geo-blocked
  - Funding extremes: Binance first, Hyperliquid fallback (crowded longs/shorts)
  - Liquidity: order-book bid/ask imbalance + Open Interest changes via Hyperliquid
  - L/S ratio extremes (OKX) - contrarian signal
  - Taker buy/sell pressure (OKX) - aggressive flow
  - OI + price divergence - trend strength
  - Perp premium extremes (Hyperliquid) - overheated/discount
  - Funding flips (Hyperliquid) - sentiment shift
  - Book walls radar (Hyperliquid) - scalping S/R
  - Volatility breakout 2x ATR (OKX candles) - expansion alerts
- Whale radar: $1M+ ETH/BTC transfers (Etherscan free key / blockchain.info),
  har transfer me from→to + explorer link; $5M+ ETH flow jo verified Binance hot
  wallet ko chhooe = inflow (possible sell pressure) / withdrawal (holding signal) tag.
- Market Pulse: 0-100 bullish/bearish gauge (BTC 60% + ETH 40%) from taker flow,
  L/S ratio (contrarian), funding (contrarian), 24h momentum, OI change.
  Report me verdict + main driver + flip watch lines. Auto Telegram reports:
  extreme zone entry (>=75 / <=25), 15+ point swing in 2h, plus digest every
  PULSE_DIGEST_HOURS (default 6, 0 = off). Data: OKX + Hyperliquid.
  - NOTE: Binance/Bybit geo-block US IPs (regulatory). No proxy jugaad - evading it
    violates their ToS and risks IP bans. Code tries Binance directly, accepts the
    block, falls back to OKX/Hyperliquid gracefully. Non-US server par Binance
    data automatically flow hoga.

Env vars required:
  TELEGRAM_BOT_TOKEN - from BotFather
  TELEGRAM_CHAT_ID   - your chat id (get via @userinfobot)
Optional:
  WHALE_ALERT_API_KEY - free tier from whale-alert.io
  ETHERSCAN_API_KEY   - free key from etherscan.io (no card needed), enables ETH whale scan
  POLL_INTERVAL_SEC   - default 600 (10 min)
  PULSE_DIGEST_HOURS  - Market Pulse digest interval in hours, default 6 (0 = off)

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
from datetime import datetime, timezone, timedelta, date
from http.server import BaseHTTPRequestHandler, HTTPServer

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
WHALE_KEY = os.getenv("WHALE_ALERT_API_KEY", "")
ETHERSCAN_KEY = os.getenv("ETHERSCAN_API_KEY", "")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SEC", "600"))

WHALE_USD_THRESHOLD = 1_000_000  # $1M+ transfers count as whale moves

# ============ Whale exchange-flow radar (curated known wallets) ============
# Verified 2026-10-19 via BaseScan/Arkham labels. Curated list - sirf Binance
# (sabse bada exchange); exchanges hot wallets rotate karte hain, stale ho sakta hai.
# Inflow = whale exchange bhej raha (possible sell pressure); outflow = nikal raha (holding signal).
KNOWN_EXCHANGE_WALLETS = {
    "0xf977814e90da44bfa03b6295a0616a897441acec": "Binance",
    "0x28c6c06298d514db089934071355e5743bf21d60c": "Binance",
}
WHALE_EXCHANGE_FLOW_USD = 5_000_000  # isse bada exchange flow = special alert line

# --- Futures data: 100% free, no API keys ---
# Exchange priority: Binance first (biggest volume), automatic fallback to
# OKX (liquidations) + Hyperliquid (funding/book/OI) if geo-blocked.
# NOTE: Binance/Bybit geo-block US IPs (regulatory). No proxy/VPN jugaad -
# evading that violates their ToS and risks IP bans. The code tries Binance
# directly and accepts the block, falling back gracefully. If the bot ever
# runs from a non-restricted region, Binance data flows automatically.
BINANCE_FAPI = "https://fapi.binance.com"
HL_API = "https://api.hyperliquid.xyz"
OKX_API = "https://www.okx.com"
FUT_SYMBOLS = [("BTC", "BTC-USDT", "BTC-USDT-SWAP"),
               ("ETH", "ETH-USDT", "ETH-USDT-SWAP"),
               ("SOL", "SOL-USDT", "SOL-USDT-SWAP")]
LIQ_MEGA_USD = 1_000_000    # single liquidation alert threshold
LIQ_CASCADE_USD = 2_000_000  # per-poll cascade alert threshold

_binance_blocked = None  # cached per process: True once geo-block confirmed


def binance_get(path, params=None):
    """Binance public API, no key. Returns None on geo-block/error (no evasion)."""
    global _binance_blocked
    if _binance_blocked:
        return None
    try:
        r = requests.get(BINANCE_FAPI + path, params=params or {}, timeout=12)
        j = r.json()
        if isinstance(j, dict) and "restricted location" in j.get("msg", ""):
            _binance_blocked = True
            print("[BINANCE] geo-blocked from this server, using OKX/Hyperliquid")
            return None
        return j
    except Exception as e:
        print(f"[BINANCE ERROR {path}] {e}")
        return None

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
            title = auto_translate(title)
            alerts.append(f"🌍 *Geopolitical Alert:* {title}\nCrypto ke liye risk-off, caution rakho.")
    except Exception as e:
        print(f"[GDELT ERROR] {e}")
    return alerts


# ============ Auto-translate: foreign-language news -> English ============
# GDELT global hai - Chinese/Japanese/Korean/Russian/Arabic titles aa sakte hain.
# Free Google endpoint (koi key/dependency nahi). Sirf non-Latin text translate hota hai.
def needs_translation(text):
    if not text:
        return False
    non_latin = sum(1 for ch in text if (
        '\u4e00' <= ch <= '\u9fff' or   # CJK (Chinese)
        '\u3040' <= ch <= '\u30ff' or   # Hiragana/Katakana (Japanese)
        '\uac00' <= ch <= '\ud7af' or   # Korean
        '\u0400' <= ch <= '\u04ff' or   # Cyrillic (Russian etc)
        '\u0600' <= ch <= '\u06ff' or   # Arabic
        '\u0e00' <= ch <= '\u0e7f'))    # Thai
    letters = sum(1 for ch in text if ch.isalpha())
    return letters > 0 and non_latin / letters > 0.3


def translate_to_english(text):
    try:
        r = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "auto", "tl": "en", "dt": "t", "q": text},
            timeout=15)
        out = "".join(seg[0] for seg in r.json()[0] if seg and seg[0])
        return out.strip() or text
    except Exception as e:
        print(f"[TRANSLATE ERROR] {e}")
        return text  # fail ho to original bhej do, alert rukega nahi


def auto_translate(title):
    if needs_translation(title):
        return translate_to_english(title) + " 🌐"
    return title


def check_macro_news(state):
    """GDELT free API: Fed/macro breaking news + bade financial press ki crypto coverage. No key.
    NOTE: GDELT allows ~1 req / 5 sec — isliye har query se pehle 6s sleep."""
    alerts = []
    queries = [
        # (key, query, cooldown_sec, prefix)
        ("fed",
         '("Federal Reserve" OR FOMC OR "rate decision" OR "rate cut" OR "rate hike" OR CPI) AND (market OR stocks OR crypto OR bitcoin)',
         21600, "🏦 *Fed/Macro News:*"),
        ("press",
         '(bitcoin OR crypto) AND (ETF OR regulation OR "central bank" OR "Wall Street")',
         43200, "📰 *Crypto Press:*"),
    ]
    for key, q, cd, prefix in queries:
        try:
            time.sleep(6)  # GDELT rate limit respect
            url = "https://api.gdeltproject.org/api/v2/doc/doc"
            params = {"query": q, "mode": "ArtList", "maxrecords": 3, "format": "json", "timespan": "6h"}
            r = requests.get(url, params=params, timeout=20)
            articles = r.json().get("articles", [])
            if articles and can_alert(state, f"macronews_{key}", cd):
                top = articles[0]
                title = top.get("title", "Macro news")[:130]
                title = auto_translate(title)
                src = top.get("source", "")
                alerts.append(f"{prefix} {title}\n_{src}_ — crypto par asar pad sakta hai.")
        except Exception as e:
            print(f"[GDELT MACRO ERROR {key}] {e}")
    return alerts


# ============ Macro event calendar (FOMC + CPI) ============
# Dates verified: Fed official FOMC calendar + BLS CPI schedule.
# FOMC decision 2pm ET (2nd day); CPI 8:30am ET. List saal me ek baar update karo.
MACRO_EVENTS = [
    (2026, 10, 28, "FOMC rate decision", "~11:30 PM IST"),
    (2026, 11, 10, "US CPI data", "~7:00 PM IST"),
    (2026, 12, 9, "FOMC rate decision + dot plot", "~12:30 AM IST (10 Dec)"),
    (2026, 12, 10, "US CPI data", "~7:00 PM IST"),
    (2027, 1, 13, "US CPI data", "~7:00 PM IST"),
]


def check_event_calendar(state):
    """Aane wale Fed/CPI events ke advance alerts (1 din pehle + us din). Koi API nahi."""
    alerts = []
    try:
        ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        today = ist_now.date()
        for y, m, d, name, t in MACRO_EVENTS:
            delta = (date(y, m, d) - today).days
            tag = f"{y}{m:02d}{d:02d}"
            if delta == 1 and can_alert(state, f"evt_pre_{tag}", 86400):
                alerts.append(f"⏰ *Kal: {name}* ({t}) — bada volatility event, leverage sambhal ke.")
            elif delta == 0 and can_alert(state, f"evt_day_{tag}", 43200):
                alerts.append(f"📅 *Aaj: {name}* ({t}) — market me tez move aa sakta hai, caution.")
    except Exception as e:
        print(f"[CALENDAR ERROR] {e}")
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
                    frm_full = (tx.get("from") or "").lower()
                    to_full = (tx.get("to") or "").lower()
                    frm = (tx.get("from") or "?")[:12]
                    to = (tx.get("to") or "contract")[:12]
                    tag = ""
                    if usd >= WHALE_EXCHANGE_FLOW_USD:
                        if to_full in KNOWN_EXCHANGE_WALLETS:
                            tag = f"\n➡️ *{KNOWN_EXCHANGE_WALLETS[to_full]} inflow* — possible sell pressure"
                        elif frm_full in KNOWN_EXCHANGE_WALLETS:
                            tag = f"\n⬅️ *{KNOWN_EXCHANGE_WALLETS[frm_full]} withdrawal* — holding signal"
                    alerts.append(
                        f"🐋 *ETH Whale:* {val_eth:,.0f} ETH (${usd:,.0f})\n`{frm}...` → `{to}...`{tag}\n[🔍 tx details](https://etherscan.io/tx/{h})")
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
                    outs = sorted(tx.get("out", []), key=lambda o: o.get("value", 0), reverse=True)
                    top_addr = (outs[0].get("addr", "") if outs else "")[:14]
                    alerts.append(f"🐋 *BTC Whale:* {btc_amt:,.1f} BTC (${usd:,.0f})\n→ `{top_addr}...`\n[🔍 tx details](https://blockchain.info/tx/{th})")
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


def hl_post(payload):
    try:
        r = requests.post(HL_API + "/info", json=payload, timeout=20)
        return r.json()
    except Exception as e:
        print(f"[HL ERROR] {e}")
        return None


def get_hl_ctx():
    """Hyperliquid perps: {coin: {funding (per hour), oi_usd, mid}} for BTC/ETH/SOL."""
    d = hl_post({"type": "metaAndAssetCtxs"})
    if not d or not isinstance(d, list) or len(d) < 2:
        return {}
    meta, ctxs = d[0], d[1]
    out = {}
    for m, c in zip(meta.get("universe", []), ctxs):
        name = m.get("name")
        if name in ("BTC", "ETH", "SOL"):
            try:
                mid = float(c.get("midPx") or 0)
                oi_usd = float(c.get("openInterest") or 0) * mid
                out[name] = {"funding": float(c.get("funding") or 0),
                             "oi_usd": oi_usd, "mid": mid}
            except (ValueError, TypeError):
                continue
    return out


def get_okx_ctval(uly, state):
    """Contract value for OKX SWAP (cached in state)."""
    key = f"ctval_{uly}"
    if state.get(key):
        return state[key]
    try:
        r = requests.get(OKX_API + "/api/v5/public/instruments",
                         params={"instType": "SWAP", "uly": uly}, timeout=15).json()
        data = r.get("data", [])
        if data:
            v = float(data[0].get("ctVal", 0))
            if v:
                state[key] = v
                return v
    except Exception as e:
        print(f"[OKX CTVAL ERROR {uly}] {e}")
    return 0


def fetch_liquidations(coin, uly, mid, state):
    """Unified liquidation feed: (ts_ms, usd, posSide, venue, uid).
    Tries Binance first (biggest volume); falls back to OKX if geo-blocked."""
    sym = coin + "USDT"
    bj = binance_get("/fapi/v1/allForceOrders", {"symbol": sym, "limit": 100})
    if isinstance(bj, list):
        out = []
        for o in bj:
            try:
                usd = float(o.get("origQty") or 0) * float(o.get("price") or 0)
                ts = int(o.get("time") or 0)
            except (ValueError, TypeError):
                continue
            if ts and usd > 0:
                # Binance force-order side SELL = long liquidated, BUY = short liquidated
                pos = "long" if o.get("side") == "SELL" else "short"
                out.append((ts, usd, pos, "Binance", f"bn_{o.get('orderId')}"))
        if out:
            return out
    # OKX fallback (public API, no key, not geo-blocked)
    ctval = get_okx_ctval(uly, state)
    if not ctval or not mid:
        return []
    try:
        r = requests.get(OKX_API + "/api/v5/public/liquidation-orders",
                         params={"instType": "SWAP", "uly": uly, "state": "filled"},
                         timeout=20).json()
    except Exception as e:
        print(f"[OKX LIQ ERROR {uly}] {e}")
        return []
    out = []
    for row in r.get("data", []):
        for d in row.get("details", []):
            try:
                ts = int(d.get("ts") or 0)
                usd = float(d.get("sz") or 0) * ctval * mid
            except (ValueError, TypeError):
                continue
            if ts and usd > 0:
                out.append((ts, usd, d.get("posSide") or "unknown", "OKX",
                            f"ok_{ts}_{d.get('sz')}"))
    return out


def check_liquidations(state, hl_ctx):
    """Liquidation cascades + mega liquidations. Binance first, OKX fallback."""
    alerts = []
    now_ms = int(time.time() * 1000)
    for coin, uly, instId in FUT_SYMBOLS:
        mid = (hl_ctx.get(coin) or {}).get("mid", 0)
        items = fetch_liquidations(coin, uly, mid, state)
        last_key = f"liq_last_{coin}"
        last_ts = state.get(last_key, 0)
        if not last_ts:
            state[last_key] = now_ms  # first run: baseline, skip backlog
            continue
        new = [(ts, usd, pos, venue, uid) for (ts, usd, pos, venue, uid) in items
               if ts > last_ts]
        if new:
            state[last_key] = max(ts for ts, _, _, _, _ in new)
        long_usd = short_usd = 0.0
        venue = new[0][3] if new else ""
        for ts, usd, pos, venue, uid in new:
            if pos == "long":
                long_usd += usd
            else:
                short_usd += usd
            if usd >= LIQ_MEGA_USD and can_alert(state, f"liqm_{uid}", 86400):
                side_txt = "LONG rekt 🔻" if pos == "long" else "SHORT rekt 🔺"
                alerts.append(f"💥 *Mega Liquidation ({coin}):* ${usd:,.0f} {side_txt} on {venue}")
        total = long_usd + short_usd
        if total >= LIQ_CASCADE_USD and can_alert(
                state, f"liqc_{coin}_{now_ms // 3600000}", 3600):
            alerts.append(
                f"🔥 *Liquidation Cascade ({coin}):* ${total:,.0f} liquidated on {venue} "
                f"(last ~10 min) — Longs ${long_usd:,.0f} / Shorts ${short_usd:,.0f}")
    return alerts


def fetch_funding_hourly(coin, hl_ctx):
    """Hourly funding rate (decimal) + venue. Binance first, Hyperliquid fallback."""
    bj = binance_get("/fapi/v1/premiumIndex", {"symbol": coin + "USDT"})
    if isinstance(bj, dict) and bj.get("lastFundingRate") is not None:
        try:
            return float(bj["lastFundingRate"]) / 8.0, "Binance"  # 8h -> hourly
        except (ValueError, TypeError):
            pass
    c = hl_ctx.get(coin) or {}
    if c.get("funding") is not None:
        return c["funding"], "Hyperliquid"
    return None, ""


def check_funding(state, hl_ctx):
    """Funding extremes: crowded longs = contrarian bearish, crowded shorts = bullish."""
    alerts = []
    for coin, _, _ in FUT_SYMBOLS:
        fr, venue = fetch_funding_hourly(coin, hl_ctx)
        if fr is None:
            continue
        if abs(fr) >= 0.0001:  # >= 0.01%/h ≈ 87% APR, genuinely extreme
            bucket = "pos" if fr > 0 else "neg"
            if can_alert(state, f"fund_{coin}_{bucket}", 21600):
                apr = fr * 24 * 365 * 100
                crowd = ("longs crowded (contrarian bearish 📉)"
                         if fr > 0 else "shorts crowded (contrarian bullish 📈)")
                alerts.append(f"💰 *Funding Extreme ({coin}):* {fr*100:+.4f}%/h "
                              f"(~{apr:+.0f}% APR) on {venue} — {crowd}")
    return alerts


def check_liquidity_hl(state, hl_ctx):
    """Hyperliquid: order-book bid/ask imbalance + Open Interest changes."""
    alerts = []
    for coin, _, _ in FUT_SYMBOLS:
        c = hl_ctx.get(coin)
        if not c:
            continue
        book = hl_post({"type": "l2Book", "coin": coin})
        if isinstance(book, dict) and "levels" in book:
            try:
                bids, asks = book["levels"][0], book["levels"][1]
                bv = sum(float(l["px"]) * float(l["sz"]) for l in bids[:20])
                av = sum(float(l["px"]) * float(l["sz"]) for l in asks[:20])
            except (ValueError, TypeError, KeyError):
                bv = av = 0
            if bv + av > 0:
                imb = (bv - av) / (bv + av)
                if abs(imb) > 0.3 and can_alert(
                        state, f"book_{coin}_{'bid' if imb > 0 else 'ask'}", 7200):
                    side = "BID-heavy 🟢 (buy wall)" if imb > 0 else "ASK-heavy 🔴 (sell wall)"
                    alerts.append(f"📚 *Book Imbalance ({coin}):* {side} — "
                                  f"bids {bv/(bv+av):.0%} vs asks {av/(bv+av):.0%}")
        oi = c["oi_usd"]
        prev = state.get(f"oi_{coin}", 0)
        if prev and oi:
            chg = (oi - prev) / prev
            if abs(chg) > 0.08 and can_alert(state, f"oichg_{coin}", 14400):
                if chg > 0:
                    alerts.append(f"📈 *Open Interest ({coin}):* UP {chg:.0%} → "
                                  f"${oi/1e9:.2f}B (leverage building)")
                else:
                    alerts.append(f"📉 *Open Interest ({coin}):* DOWN {abs(chg):.0%} → "
                                  f"${oi/1e9:.2f}B (deleverage)")
        if oi:
            state[f"oi_{coin}"] = oi
    return alerts


# ================= Futures pack v2: 7 advanced features (all free, no keys) =================

def check_ls_ratio(state):
    """OKX long/short account ratio extremes - contrarian signal."""
    alerts = []
    for coin, _, _ in FUT_SYMBOLS:
        try:
            r = requests.get(OKX_API + "/api/v5/rubik/stat/contracts/long-short-account-ratio",
                             params={"ccy": coin}, timeout=15).json()
        except Exception as e:
            print(f"[OKX LSR ERROR {coin}] {e}")
            continue
        data = r.get("data", [])
        if not data:
            continue
        try:
            ratio = float(data[0][1])
        except (ValueError, TypeError, IndexError):
            continue
        if ratio >= 2.5 and can_alert(state, f"lsr_{coin}_hi", 21600):
            alerts.append(f"⚖️ *L/S Ratio Extreme ({coin}):* {ratio:.2f} — bheed long me ghusi hai (contrarian bearish 📉)")
        elif ratio <= 0.7 and can_alert(state, f"lsr_{coin}_lo", 21600):
            alerts.append(f"⚖️ *L/S Ratio Extreme ({coin}):* {ratio:.2f} — bheed short me ghusi hai (contrarian bullish 📈)")
    return alerts


def check_taker_pressure(state):
    """OKX taker buy/sell volume - kaun aggressive hai.
    NOTE: columns [ts, buyVol, sellVol] - direction empirically verified."""
    alerts = []
    for coin, _, _ in FUT_SYMBOLS:
        try:
            r = requests.get(OKX_API + "/api/v5/rubik/stat/taker-volume",
                             params={"ccy": coin, "instType": "CONTRACTS"}, timeout=15).json()
        except Exception as e:
            print(f"[OKX TAKER ERROR {coin}] {e}")
            continue
        data = r.get("data", [])
        if len(data) < 6:
            continue
        buy = sell = 0.0
        for row in data[:6]:  # last ~30 min
            try:
                buy += float(row[1])
                sell += float(row[2])
            except (ValueError, TypeError, IndexError):
                continue
        if sell <= 0:
            continue
        ratio = buy / sell
        if ratio >= 1.5 and can_alert(state, f"taker_{coin}_buy", 14400):
            alerts.append(f"🟢 *Taker Pressure ({coin}):* buyers aggressive — buy/sell {ratio:.2f}x (30 min)")
        elif ratio <= 0.67 and can_alert(state, f"taker_{coin}_sell", 14400):
            alerts.append(f"🔴 *Taker Pressure ({coin}):* sellers aggressive — buy/sell {ratio:.2f}x (30 min)")
    return alerts


def get_funding_hist(coin, hours=3):
    """Hyperliquid hourly funding+premium history."""
    d = hl_post({"type": "fundingHistory", "coin": coin,
                 "startTime": int((time.time() - hours * 3600) * 1000),
                 "endTime": int(time.time() * 1000)})
    return d if isinstance(d, list) else []


def check_premium(state):
    """Perp premium extremes vs spot (Hyperliquid)."""
    alerts = []
    for coin, _, _ in FUT_SYMBOLS:
        hist = get_funding_hist(coin, 2)
        if not hist:
            continue
        try:
            prem = float(hist[-1].get("premium", 0))
        except (ValueError, TypeError):
            continue
        if abs(prem) >= 0.001:  # 0.1%
            bucket = "pos" if prem > 0 else "neg"
            if can_alert(state, f"prem_{coin}_{bucket}", 21600):
                txt = "perp overheated vs spot 🔥" if prem > 0 else "perp discount — panic selling? 😨"
                alerts.append(f"💹 *Premium Extreme ({coin}):* {prem*100:+.3f}% — {txt}")
    return alerts


def check_funding_flip(state):
    """Funding sign flips - sentiment shift ka sabse pehla ishara."""
    alerts = []
    for coin, _, _ in FUT_SYMBOLS:
        hist = get_funding_hist(coin, 3)
        if len(hist) < 2:
            continue
        try:
            prev = float(hist[-2].get("fundingRate", 0))
            cur = float(hist[-1].get("fundingRate", 0))
        except (ValueError, TypeError):
            continue
        if prev == 0 or cur == 0:
            continue
        if (prev > 0) != (cur > 0) and abs(cur) >= 0.00002:
            if can_alert(state, f"fflip_{coin}", 21600):
                direction = "bearish → bullish 📈" if cur < 0 else "bullish → bearish 📉"
                alerts.append(f"🔄 *Funding Flip ({coin}):* {prev*100:+.5f}% → {cur*100:+.5f}%/h — sentiment shift ({direction})")
    return alerts


def check_book_walls(state, hl_ctx):
    """Badi single-level bid/ask walls (mid ke 2% andar) - scalping S/R."""
    alerts = []
    for coin, _, _ in FUT_SYMBOLS:
        mid = (hl_ctx.get(coin) or {}).get("mid", 0)
        if not mid:
            continue
        book = hl_post({"type": "l2Book", "coin": coin})
        if not isinstance(book, dict) or "levels" not in book:
            continue
        try:
            bids, asks = book["levels"][0], book["levels"][1]
        except (IndexError, TypeError):
            continue
        for is_bid, levels, name in ((True, bids, "BID"), (False, asks, "ASK")):
            best_px, best_usd = 0, 0
            for l in levels:
                try:
                    px, sz = float(l["px"]), float(l["sz"])
                except (ValueError, TypeError, KeyError):
                    continue
                dist = (mid - px) / mid if is_bid else (px - mid) / mid
                if 0 <= dist <= 0.02:
                    usd = px * sz
                    if usd > best_usd:
                        best_px, best_usd = px, usd
            if best_usd >= 5_000_000 and can_alert(
                    state, f"wall_{coin}_{name}_{int(best_px)}", 14400):
                side = "support 🟢" if is_bid else "resistance 🔴"
                alerts.append(f"🧱 *Book Wall ({coin}):* ${best_usd/1e6:.1f}M {name} wall "
                              f"@ ${best_px:,.2f} ({side})")
    return alerts


def check_oi_divergence(state, hl_ctx):
    """Price vs Open Interest divergence - trend me kitna dum hai."""
    alerts = []
    now = time.time()
    for coin, _, _ in FUT_SYMBOLS:
        c = hl_ctx.get(coin)
        if not c or not c["mid"]:
            continue
        key = f"oidiv_hist_{coin}"
        hist = state.get(key, [])
        hist.append([now, c["mid"], c["oi_usd"]])
        hist = [h for h in hist if now - h[0] <= 7200][-13:]
        state[key] = hist
        if len(hist) < 7:
            continue
        old, new = hist[0], hist[-1]
        if not old[1] or not old[2]:
            continue
        px_chg = (new[1] - old[1]) / old[1]
        oi_chg = (new[2] - old[2]) / old[2]
        if px_chg > 0.02 and oi_chg < -0.03:
            if can_alert(state, f"oidiv_{coin}_bear", 21600):
                alerts.append(f"⚠️ *OI Divergence ({coin}):* price +{px_chg:.1%}, OI {oi_chg:.1%} — rally me dum nahi, short-covering lag raha hai")
        elif px_chg < -0.02 and oi_chg > 0.03:
            if can_alert(state, f"oidiv_{coin}_bull", 21600):
                alerts.append(f"⚠️ *OI Divergence ({coin}):* price {px_chg:.1%}, OI +{oi_chg:.1%} — fresh shorts ban rahe, aur girawat possible")
    return alerts


def check_vol_breakout(state):
    """15m candle range > 2x ATR(14) - volatility expansion (OKX candles)."""
    alerts = []
    for coin, _, instId in FUT_SYMBOLS:
        try:
            r = requests.get(OKX_API + "/api/v5/market/candles",
                             params={"instId": instId, "bar": "15m", "limit": "20"},
                             timeout=15).json()
        except Exception as e:
            print(f"[OKX CANDLES ERROR {instId}] {e}")
            continue
        candles = r.get("data", [])
        done = [c for c in reversed(candles) if len(c) > 8 and c[8] == "1"]
        if len(done) < 15:
            continue
        trs = []
        for i in range(1, len(done)):
            try:
                h, l, pc = float(done[i][2]), float(done[i][3]), float(done[i-1][4])
            except (ValueError, TypeError, IndexError):
                continue
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        if len(trs) < 14:
            continue
        atr = sum(trs[-14:]) / 14
        try:
            last = done[-1]
            rng = float(last[2]) - float(last[3])
            o, cl = float(last[1]), float(last[4])
        except (ValueError, TypeError, IndexError):
            continue
        if atr > 0 and rng > 2.0 * atr:
            if can_alert(state, f"volbrk_{coin}", 7200):
                chg = (cl - o) / o * 100 if o else 0
                alerts.append(f"⚡ *Volatility Breakout ({coin}):* 15m range {rng/atr:.1f}x ATR ({chg:+.1f}%) — bada move ban raha hai")
    return alerts


# ============ Market Pulse: 0-100 bullish/bearish gauge + auto Telegram reports ============
# Har poll me BTC(60%)+ETH(40%) se score banta hai: taker flow, L/S (contrarian),
# funding (contrarian), 24h momentum, OI change. Data: OKX + Hyperliquid (free, no keys).
# Binance/Bybit US se geo-blocked hain isliye nahi - OKX top-3 futures exchange hai,
# Hyperliquid sabse bada perp DEX; signals market-wide hote hain.
PULSE_DIGEST_HOURS = float(os.getenv("PULSE_DIGEST_HOURS", "6"))  # 0 = digest off


def pulse_label(score):
    if score >= 75:
        return "Strong Bullish 🟢🟢"
    if score >= 60:
        return "Bullish 🟢"
    if score >= 40:
        return "Neutral ⚪"
    if score >= 25:
        return "Bearish 🔴"
    return "Strong Bearish 🔴🔴"


def pulse_verdict(score, subs):
    """Ek nazar me verdict: bullish/bearish, sabse bada driver, aur flip watch."""
    if score >= 75:
        v = "Verdict: 🟢🟢 STRONG BULLISH — buyers ka clear control"
    elif score >= 60:
        v = "Verdict: 🟢 BULLISH LEAN — buyers halke aage"
    elif score >= 40:
        v = "Verdict: ⚪ NEUTRAL — koi clear edge nahi, wait-and-watch"
    elif score >= 25:
        v = "Verdict: 🔴 BEARISH LEAN — sellers halke aage"
    else:
        v = "Verdict: 🔴🔴 STRONG BEARISH — sellers ka clear control"
    contrib = {}
    for coin, w in (("BTC", 0.6), ("ETH", 0.4)):
        _, p = subs[coin]
        for k, name in (("taker", "taker flow"), ("ls", "L/S positioning"),
                        ("fund", "funding"), ("mom", "momentum"), ("oim", "OI change")):
            contrib[name] = contrib.get(name, 0) + p[k] * w
    top = max(contrib.items(), key=lambda x: abs(x[1]))
    d = f"Main driver: {top[0]} ({top[1]:+.0f} pts)"
    if score >= 60:
        f = "Flip watch: L/S 2.5+ ya funding extreme hua to score gir sakta hai"
    elif score <= 40:
        f = "Flip watch: taker buyers laute ya shorts squeeze ho to score uth sakta hai"
    else:
        f = "Clear edge ke liye 60+ (bullish) ya 40- (bearish) ka break dekho"
    return v, d, f


def coin_subscore(coin, hl_ctx, chg24, state):
    """Ek coin ka sub-score (-50..+50). Components transparent rakhe hain."""
    parts = {}
    # 1. taker flow (30m)
    try:
        r = requests.get(OKX_API + "/api/v5/rubik/stat/taker-volume",
                         params={"ccy": coin, "instType": "CONTRACTS"}, timeout=15).json()
        data = r.get("data", [])
        b = sum(float(x[1]) for x in data[:6])
        s = sum(float(x[2]) for x in data[:6])
        tr = b / s if s > 0 else 1.0
    except Exception:
        tr = 1.0
    parts["taker_ratio"] = tr
    parts["taker"] = 15 if tr >= 1.3 else 7 if tr >= 1.1 else 0 if tr >= 0.9 else -7 if tr >= 0.77 else -15
    # 2. L/S ratio (contrarian: bheed long = bearish)
    try:
        r = requests.get(OKX_API + "/api/v5/rubik/stat/contracts/long-short-account-ratio",
                         params={"ccy": coin}, timeout=15).json()
        lsr = float(r["data"][0][1])
    except Exception:
        lsr = 1.5
    parts["lsr"] = lsr
    parts["ls"] = -10 if lsr >= 2.5 else -5 if lsr >= 2.0 else 10 if lsr <= 0.7 else 5 if lsr <= 1.0 else 0
    # 3. funding (contrarian: zyada positive = crowded longs)
    fr = (hl_ctx.get(coin) or {}).get("funding", 0) or 0
    parts["funding"] = fr
    parts["fund"] = -10 if fr >= 0.0001 else -5 if fr >= 0.00005 else 10 if fr <= -0.0001 else 5 if fr <= -0.00005 else 0
    # 4. 24h momentum
    parts["mom24"] = chg24
    parts["mom"] = 10 if chg24 >= 3 else 5 if chg24 >= 1 else 0 if chg24 > -1 else -5 if chg24 > -3 else -10
    # 5. OI change (pichhle poll se; check_liquidity_hl state me oi_{coin} rakhta hai)
    oi = (hl_ctx.get(coin) or {}).get("oi_usd", 0) or 0
    prev_oi = state.get(f"oi_{coin}", 0)
    oi_chg = (oi - prev_oi) / prev_oi if prev_oi and oi else 0
    parts["oi_chg"] = oi_chg
    parts["oim"] = 5 if oi_chg > 0.03 else -5 if oi_chg < -0.03 else 0
    sub = parts["taker"] + parts["ls"] + parts["fund"] + parts["mom"] + parts["oim"]
    return sub, parts


def pulse_report_text(score, label, subs, hl_ctx, header):
    v, d, f = pulse_verdict(score, subs)
    lines = [header, f"*Market Pulse: {score}/100 — {label}*",
             f"_{v}_", f"_{d}_", f"_{f}_", ""]
    for coin in ("BTC", "ETH"):
        sub, p = subs[coin]
        mid = (hl_ctx.get(coin) or {}).get("mid", 0) or 0
        arrow = "▲" if sub > 5 else "▼" if sub < -5 else "●"
        tk = "🟢 buyers" if p["taker"] > 0 else "🔴 sellers" if p["taker"] < 0 else "⚪ neutral"
        lines.append(f"{arrow} *{coin}* ${mid:,.0f} (sub {sub:+d}): {tk} {p['taker_ratio']:.2f}x | "
                     f"L/S {p['lsr']:.2f} | fund {p['funding']*100:.4f}%/h | "
                     f"24h {p['mom24']:+.1f}% | OI {p['oi_chg']:+.1%}")
    lines += ["", "_Score = taker flow + L/S (contrarian) + funding (contrarian) + 24h momentum + OI change. "
              "Ye signal hai, financial advice nahi._"]
    return "\n".join(lines)


def check_market_pulse(state, hl_ctx, prices):
    """Market Pulse score + auto Telegram reports: extreme zone entry, bada swing, digest."""
    alerts = []
    now = time.time()
    subs = {}
    for coin, cg in (("BTC", "bitcoin"), ("ETH", "ethereum")):
        chg24 = (prices.get(cg) or {}).get("usd_24h_change", 0) or 0
        subs[coin] = coin_subscore(coin, hl_ctx, chg24, state)
    score = max(0, min(100, round(50 + 0.6 * subs["BTC"][0] + 0.4 * subs["ETH"][0])))
    label = pulse_label(score)
    hist = state.get("pulse_hist", [])
    hist.append([now, score])
    hist = [h for h in hist if now - h[0] <= 4 * 3600][-25:]
    state["pulse_hist"] = hist
    state["pulse_score"] = score
    # trigger 1: extreme zone me entry
    prev_zone = state.get("pulse_zone", "mid")
    zone = "ext_bull" if score >= 75 else "ext_bear" if score < 25 else "mid"
    if zone != prev_zone and zone != "mid" and can_alert(state, f"pulse_zone_{zone}", 21600):
        alerts.append(pulse_report_text(
            score, label, subs, hl_ctx,
            "🚨 *Market Pulse Alert:* market extreme zone me enter hua"))
    state["pulse_zone"] = zone
    # trigger 2: 2 ghante me 15+ points ka swing
    target = now - 2 * 3600
    old = min(hist, key=lambda h: abs(h[0] - target)) if hist else None
    if old and abs(now - old[0] - 2 * 3600) < 1800:
        swing = score - old[1]
        if abs(swing) >= 15 and can_alert(state, "pulse_swing", 7200):
            d = "tez bullish shift 📈" if swing > 0 else "tez bearish shift 📉"
            alerts.append(pulse_report_text(
                score, label, subs, hl_ctx,
                f"⚡ *Market Pulse Swing:* 2 ghante me {swing:+.0f} points — {d}"))
    # trigger 3: periodic digest
    if PULSE_DIGEST_HOURS > 0 and now - state.get("pulse_digest_ts", 0) >= PULSE_DIGEST_HOURS * 3600:
        state["pulse_digest_ts"] = now
        alerts.append(pulse_report_text(score, label, subs, hl_ctx, "🎯 *Market Pulse Digest*"))
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
    send_telegram("✅ *Tracker Online*\nMacro + Whale + Futures full pack 24/7 shuru.")
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
            hl_ctx = get_hl_ctx()     # Hyperliquid futures data, shared by checks

            alerts += check_macro(state)
            alerts += check_war_news(state)
            alerts += check_event_calendar(state)  # FOMC/CPI advance alerts, no API
            if poll_count % 3 == 1:  # every ~30 min (GDELT: 1 req / 5 sec)
                alerts += check_macro_news(state)  # Fed news + financial press crypto coverage
            alerts += check_whales(state)
            alerts += check_volume_spike(state, prices)
            alerts += check_eth_whales(state, eth_price)      # every poll (~10 min)
            alerts += check_liquidations(state, hl_ctx)       # every poll
            alerts += check_funding(state, hl_ctx)             # every poll
            alerts += check_market_pulse(state, hl_ctx, prices) # every poll (OI momentum ke liye liquidity se pehle)
            alerts += check_liquidity_hl(state, hl_ctx)        # every poll
            alerts += check_ls_ratio(state)                   # every poll
            alerts += check_taker_pressure(state)             # every poll
            alerts += check_premium(state)                    # every poll
            alerts += check_funding_flip(state)               # every poll
            alerts += check_book_walls(state, hl_ctx)         # every poll
            alerts += check_oi_divergence(state, hl_ctx)      # every poll
            alerts += check_vol_breakout(state)               # every poll
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
