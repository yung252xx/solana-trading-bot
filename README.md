<div align="center">
  <img src="https://img.shields.io/badge/Solana-9945FF?style=for-the-badge&logo=solana&logoColor=white" />
  <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Jupiter-FF6B35?style=for-the-badge&logo=jupyter&logoColor=white" />
  <br/>
  <h1>🏃 Rug or Run — Solana Trading Bot</h1>
  <p><i>Auto sell your bags before they dump. Or take profit when they pump.</i></p>
</div>

---

## What It Does

A CLI-based Solana trading bot that **watches your positions and auto-sells** when your **stop-loss** or **take-profit** targets are hit. Built for Jupiter DEX traders.

```
BONK @ $0.0000000660 → up +0.45% 📈 → watching...
BONK @ $0.0000000561 → STOP LOSS TRIGGERED ⚡ → swapping to SOL
```

## Commands

```bash
python bot.py start          # Start monitoring positions
python bot.py check          # Check wallet + SOL balance
python bot.py price <MINT>   # Look up a token price
python bot.py status         # Show all positions with live P&L
```

## Quick Start

```bash
# 1. Install deps
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 2. Configure your position
# Edit config.yaml — set your token mint, entry price, SL%, TP%

# 3. Start in dry-run mode (no real swaps)
python bot.py start

# 4. Go live when ready
# Set dry_run: false in config.yaml
# Add your private key to .env
python bot.py start
```

## Strategy: Rug or Run 🏃

Configure any number of positions in `config.yaml`:

```yaml
positions:
  - token_mint: "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xgdBqaYneZkUzK4h"  # BONK
    token_symbol: "BONK"
    entry_price: 0.000000066
    stop_loss_pct: 15        # -15% → auto-sell
    take_profit_pct: 25      # +25% → auto-sell
    amount_in_sol: 0.1
    already_in_position: true
    token_balance: 10000000
```

## Architecture

```
bot.py              # CLI entry point
config.yaml         # Position config
wallet.py           # Solana keypair loader
jupiter.py          # Jupiter v1 swap API + CoinGecko prices
strategies/
  └── rug_or_run.py # Stop-loss / take-profit engine
.env.example        # Environment template
```

## API Integrations

| Service | Purpose | Endpoint |
|---------|---------|----------|
| **Jupiter v1** | Token swaps (quote + execute) | `api.jup.ag/swap/v1` |
| **CoinGecko** | Price feeds (USD + SOL pairs) | `api.coingecko.com/api/v3` |

## Safety

- **Dry-run mode is ON by default** — no real transactions until you flip the switch
- **Use a trading wallet** — never run this with your main bag
- **`.env` is gitignored** — your private key never gets committed
- Stop-loss and take-profit are checked every 15 seconds

## Built With

- [Python](https://python.org) 3.11+
- [solders](https://pypi.org/project/solders/) — Solana transaction signing
- [Jupiter API](https://station.jup.ag/docs) — DEX aggregation & swaps
- [CoinGecko API](https://www.coingecko.com/en/api) — Price data

## Coming Soon

- [ ] YouTube auto-upload trade recaps
- [ ] X/Twitter signal bot
- [ ] Telegram alerts
- [ ] Trailing stop-loss
- [ ] Multi-token grid trading
- [ ] Web dashboard

---

<div align="center">
  <p><i>Built by <a href="https://github.com/yung252xx">@yung252xx</a></i></p>
</div>
