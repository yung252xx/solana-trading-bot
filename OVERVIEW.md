# Solana Trading Bot — Rug or Run 🏃

## What it is
A CLI-based Solana trading bot that monitors your positions and **auto-sells** when your stop-loss or take-profit is hit. Built for Jupiter perps/swaps traders.

## Tech Stack
- **Backend:** Python 3.11 (solders, requests, PyYAML)
- **APIs:** Jupiter v6 (Price API, Quote API, Swap API)
- **Blockchain:** Solana (mainnet / devnet)
- **CLI:** argparse with start/check/price/status commands

## File Map
```
bot.py                          # CLI entry point
config.yaml                     # Your positions & strategy config
wallet.py                       # Load keypair, check SOL balance
jupiter.py                      # Jupiter API: prices, quotes, swaps
strategies/
  └── rug_or_run.py             # Stop-loss / take-profit engine
.env.example                    # Environment template
.gitignore
OVERVIEW.md
```

## Commands
```
python bot.py start             # Run the bot (monitors positions)
python bot.py check             # Check wallet + balance
python bot.py price <MINT>      # Look up a token price
python bot.py status            # Show positions with live prices
```

## How It Works
1. You configure positions in `config.yaml` (token mint, entry price, SL%, TP%)
2. Bot polls Jupiter price API every N seconds
3. If price hits stop-loss → auto-swap token back to SOL via Jupiter
4. If price hits take-profit → auto-swap token back to SOL via Jupiter
5. Dry-run mode logs everything without real swaps (default)

## Monetization Hook
- **Freemium model:** Basic rug-or-run is free. Premium features (trailing stops, multi-token grids, Telegram alerts) behind a license key / Stripe subscription
- **Patreon/Ko-fi:** Link baked into the app for supporters

## Current Status
- ✅ Core bot built and working
- ✅ Jupiter price feed integrated
- ✅ Jupiter quote + swap ready (dry-run tested)
- ✅ CLI with 4 commands
- ⏳ Needs mainnet test with small SOL amount
- 📦 Git repo initialized

## To Run Live
1. Set `dry_run: false` in config.yaml
2. Set `SOLANA_PRIVATE_KEY` in .env (OR just use `solana` CLI keypair)
3. Run: `python bot.py start`
4. Watch it protect your bags 🛡️
