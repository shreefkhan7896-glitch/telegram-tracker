# 24/7 Macro + Whale Telegram Tracker (100% FREE)

No fixed price targets. Sirf smart alerts. Bina credit card, bina ek rupya.

## Kya track karta hai
- War / geopolitical news (GDELT free API)
- Macro bearish: DXY up, US10Y yields up, Crude spike (Yahoo Finance free)
- Fed/FOMC/CPI breaking news + bade financial press ki crypto coverage (GDELT free API);
  foreign-language titles auto-translate hokar English me aate hain (🌐 tag ke saath)
- Event calendar: FOMC + CPI dates (Fed/BLS verified) — 1 din pehle aur us din advance alert
- Whales $1M+: ETH (Etherscan free API, har 10 min latest block), BTC
  (blockchain.info free API, har ~3 ghante latest block — BTC blocks ~8MB
  hote hain, isliye free bandwidth limit ke hisaab se kam frequent)
- Whales $10M+: Whale Alert API key optional (agar ho to)
- Volume momentum: BTC/ETH/SOL me 8%+ 24h move
- **Futures (100% free, koi key nahi chahiye):**
  - Liquidation cascade: $2M+ liquidations (10 min me), long/short split ke saath —
    **Binance se pehle try** (sabse zyada volume), geo-block hua to OKX fallback
  - Mega liquidation: koi single $1M+ liquidation turant alert
  - Funding extreme: ±0.01%/h se zyada ho to alert — Binance first, Hyperliquid fallback
    (crowded longs = contrarian bearish, crowded shorts = contrarian bullish)
  - Book imbalance: bid/ask liquidity 65/35 se zyada tilted ho to alert (Hyperliquid)
  - Open Interest: 8%+ change par alert (leverage build-up ya deleverage)
- **Futures advanced pack (100% free, koi key nahi):**
  - L/S Ratio extremes: OKX par bheed ek taraf (2.5+ long ya 0.7- short) = contrarian signal
  - Taker pressure: aggressive buyers vs sellers ka volume (30 min window)
  - OI + Price divergence: rally me dum hai ya nahi, ye batata hai
  - Perp premium extremes: futures spot se ±0.1% zyada upar/neeche = overheated/panic
  - Funding flips: funding ka sign flip = sentiment shift ka pehla ishara
  - Book walls radar: $5M+ ki diwarein (2% ke andar) = scalping ke live S/R
  - Volatility breakout: 15m candle 2x ATR se bada = expansion shuru
- **Market Pulse (0-100 bullish/bearish gauge):** har poll me BTC (50%) + ETH (30%) + SOL (20%)
  se score — taker flow, L/S ratio (contrarian), funding (contrarian), 24h momentum,
  OI change. Report me **verdict + main driver + flip watch** + Fear&Greed/BTC.D context.
  Auto Telegram reports: extreme zone entry (75+ / 25-),
  2 ghante me 15+ points ka swing, aur har 6 ghante me digest. Data: OKX + Hyperliquid
  (Binance/Bybit US se geo-blocked, isliye nahi — dono top-tier volume wale hain)
- **Sentiment & positioning:** Fear & Greed extremes (contrarian alerts), BTC dominance
  trend (alt pressure vs alt-friendly), Deribit BTC/ETH options put/call OI ratio + max pain
- **Macro intelligence v2 (v8.2):** FRED official data — CPI, Core CPI, PPI, unemployment,
  payrolls (free key, no card — `FRED_API_KEY` env var). Naya print release hote hi bot
  khud detect karke ek line me crypto verdict bhejta hai: **Bullish 🟢 / Bearish 🔴 /
  Neutral ⚪**. DeFiLlama stablecoin supply flows (7d me ±$3B = liquidity in/out signal).
  Polymarket ke agle-FOMC rate-cut odds + 10pp+ shifts par alert
- **L1/L2 Pump Radar:** SOL/AVAX/NEAR/SUI/APT/SEI/INJ/ATOM/ARB/OP/DOT/POL ka OKX 1H
  volume anomaly scan (har ~30 min) — 3x+ volume + shant price = "Volume Building"
  early warning; 3x+ volume + price up = "Pump Alert". HL perps par deeply negative
  funding + solid OI wale alts = short-squeeze setups. Note: koi indicator pump ki
  100% guarantee nahi deta — volume-leads-price hi asal edge hai
- **Whale radar detail:** har $1M+ transfer me from→to + explorer link (tap karke
  full details); $5M+ ETH flow jo verified Binance hot wallet se jude = **inflow**
  (possible sell pressure) / **withdrawal** (holding signal) tag ke saath
- **L1/L2 Pump Radar:** SOL/AVAX/NEAR/SUI/APT/SEI/INJ/ATOM/ARB/OP/DOT/POL ka OKX 1H
  volume anomaly scan (har ~30 min) — 3x+ volume + shant price = "Volume Building"
  early warning; 3x+ volume + price up = "Pump Alert". Har alert me **RSI (15m/1H)**
  + **spot-vs-perp fakeout check** (futures OI 24h me +15% se zyada = high-leverage
  trap, OI flat/girta = real spot buying). **Relative strength:** BTC 1h me 2.5%+ gire
  lekin koi watchlist coin hold kare (top 3) = alag alert. HL perps par deeply negative
  funding + solid OI wale alts = short-squeeze setups. Note: koi indicator pump ki
  100% guarantee nahi deta — volume-leads-price hi asal edge hai

Note: Binance/Bybit US IPs ko regulatory wajah se geo-block karte hain.
Proxy/VPN se ye block todna unke ToS ke khilaaf hai (ban ka risk), isliye
code Binance ko seedha try karta hai — jahan chalega wahan Binance ka data,
block hua to khud OKX/Hyperliquid par switch. Koi jugaad nahi, sab legit.

Honest note: ye sampled coverage hai (free tier ki majboori). Har ek
transfer pakadne ke liye paid API chahiye hoti hai — lekin bade moves
aksar pakde jayenge.

## Tarika: Render Free Web Service + UptimeRobot

Render ka free tier bina credit card ke hai (750 free hours/month = 24/7 ke liye kaafi).
Free service 15 min idle ke baad so jati hai, isliye UptimeRobot (free, 50 monitors,
5-min interval, no card) har 5 min par /health ko ping karke jagaye rakhta hai.

### 1. Telegram Bot banao
1. Telegram me @BotFather ko open karo
2. `/newbot` bhejo, naam do
3. BotFather se `BOT_TOKEN` lo (kisi ko mat bhejo, seedha Render me dalna)
4. Apne bot ko open karke `/start` dabao
5. @userinfobot se apna `CHAT_ID` lo

### 2. GitHub repo banao
1. GitHub.com → New repository → naam `telegram-tracker`, Public
2. Is folder ki 5 files upload karo: tracker.py, requirements.txt,
   render.yaml, README.md (Procfile ki zaroorat nahi)

### 3. Render par deploy karo (FREE plan dhyaan se)
1. Render.com → Sign up (GitHub se, card nahi mangta)
2. New → Web Service → apna `telegram-tracker` repo select karo
3. **Instance Type: Free chuno (Starter $7 nahi!)**
4. Build Command: `pip install -r requirements.txt`
5. Start Command: `python tracker.py`
6. Environment Variables me dalo:
   - `TELEGRAM_BOT_TOKEN` = BotFather wala token
   - `TELEGRAM_CHAT_ID` = tumhara chat ID
   - `ETHERSCAN_API_KEY` = etherscan.io se free key (card nahi mangta) —
     isse ETH whale scan chalta hai
   - `WHALE_ALERT_API_KEY` = optional
   - `POLL_INTERVAL_SEC` = 600
   - `PULSE_DIGEST_HOURS` = 6 (Market Pulse digest har 6 ghante; 0 karne se digest band, event alerts chalte rahenge)
7. Deploy dabao. URL milega jaise:
   `https://crypto-macro-whale-tracker.onrender.com`
8. Browser me `https://tumhara-url.onrender.com/health` kholo,
   "OK - tracker alive" dikhna chahiye. Telegram par "Tracker Online" ayega.

### 4. UptimeRobot lagao (taaki 24/7 na soye)
1. UptimeRobot.com → free account banao (card nahi mangta)
2. Add New Monitor → Monitor Type: HTTP(s)
3. URL: `https://tumhara-url.onrender.com/health`
4. Interval: 5 minutes → Create
5. Bas! Ab Render ki service kabhi nahi soyegi.

## Test
Deploy hote hi Telegram par "✅ Tracker Online" message ayega.
