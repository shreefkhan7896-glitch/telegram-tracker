# 24/7 Macro + Whale Telegram Tracker

No fixed price targets. Sirf smart alerts.

## Kya track karta hai
- War / geopolitical news (GDELT free API)
- Macro bearish: DXY up, US10Y yields up, Crude spike (Yahoo Finance free)
- Whales: $10M+ moves (Whale Alert API key optional)
- Volume momentum: BTC/ETH/SOL me 8%+ 24h move

## 1. Telegram Bot banao
1. Telegram me @BotFather ko open karo
2. `/newbot` bhejo, naam do (jaise `MacroWhaleTrackerBot`)
3. BotFather tumhe `BOT_TOKEN` dega, usko safe rakho
4. Apne bot ko Telegram me open karke `/start` dabao
5. @userinfobot ko open karke apna `CHAT_ID` le lo

## 2. Free cloud par host karo (Render)
1. Ye folder ko GitHub par push karo
2. render.com par jao → New → Background Worker
3. GitHub repo connect karo
4. Build: `pip install -r requirements.txt`
5. Start: `python tracker.py`
6. Env vars me dalo:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `WHALE_ALERT_API_KEY` (optional, whale-alert.io se free)
   - `POLL_INTERVAL_SEC` = 600

## 3. Railway (alternative)
1. railway.app → New Project → Deploy from GitHub
2. Same env vars dalo
3. Start command: `python tracker.py`

## Test
Bot online hote hi tumhe Telegram par "Tracker Online" message ayega.
