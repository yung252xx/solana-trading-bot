#!/usr/bin/env python3
"""
Solana Trading Bot — CLI Entry Point

Usage:
    python bot.py start              Run the rug-or-run strategy
    python bot.py check              Quick wallet + balance check
    python bot.py price <TOKEN>      Look up a token price
    python bot.py status             Show current positions from config
"""

import sys
import os
import yaml
import argparse

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wallet import load_keypair, get_balance_sol, get_public_key
from jupiter import get_token_price_sol, get_token_price_usd, get_sol_price_usd, SOL_MINT
from strategies.rug_or_run import run, load_config, resolve_config_path, Position


# ──────────────────────────────────────────
#  ASCII BANNER
# ──────────────────────────────────────────

BANNER = r"""
   ███████╗ ██████╗ ██╗      █████╗ ███╗   ██╗ █████╗
   ██╔════╝██╔═══██╗██║     ██╔══██╗████╗  ██║██╔══██╗
   ███████╗██║   ██║██║     ███████║██╔██╗ ██║███████║
   ╚════██║██║   ██║██║     ██╔══██║██║╚██╗██║██╔══██║
   ███████║╚██████╔╝███████╗██║  ██║██║ ╚████║██║  ██║
   ╚══════╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝
   ════════════════════════════════════════════════════
   ██████╗ ██╗   ██╗███╗   ██╗
   ██╔══██╗██║   ██║████╗  ██║
   ██████╔╝██║   ██║██╔██╗ ██║
   ██╔══██╗██║   ██║██║╚██╗██║
   ██║  ██║╚██████╔╝██║ ╚████║
   ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝
   ════════════════════════════════════════════════════
"""


# ──────────────────────────────────────────
#  COMMANDS
# ──────────────────────────────────────────

def cmd_check():
    """Check wallet status and SOL balance."""
    print(BANNER)
    print("🔍 WALLET CHECK\n")

    try:
        keypair = load_keypair()
        pubkey = keypair["public_key"]
        print(f"  📍 Address: {pubkey}")
    except FileNotFoundError:
        print("  ⚠️  No keypair found. Run `solana config get` to check.")

    try:
        bal = get_balance_sol()
        print(f"  💰 Balance: {bal} SOL")
    except Exception as e:
        print(f"  ⚠️  Balance: {e}")

    print()


def cmd_price(token_mint: str):
    """Look up current price of a token."""
    from jupiter import get_sol_price_usd

    print(f"\n  🔎 Looking up price for token: {token_mint[:12]}...\n")

    price_sol = get_token_price_sol(token_mint)
    price_usd = get_token_price_usd(token_mint)

    if price_sol is None and price_usd is None:
        print("  ❌ Could not fetch price. Check the token mint address.")
        return

    print(f"  Price in SOL:  {price_sol:.12f} SOL" if price_sol else "  Price in SOL:  N/A")
    print(f"  Price in USD:  ${price_usd:.8f}" if price_usd else "  Price in USD:  N/A")

    # Also show SOL price if we got a USD price
    if price_usd:
        sol_usd = get_sol_price_usd()
        if sol_usd:
            print(f"  SOL/USD:       ${sol_usd:.2f}")

    print()


def cmd_status(config_path: str = "config.yaml"):
    """Show configured positions and their current status."""
    config_path = resolve_config_path(config_path)
    config = load_config(config_path)
    positions_cfg = config.get("positions", [])

    print(f"\n  📋 POSITIONS ({len(positions_cfg)} configured)\n")

    if not positions_cfg:
        print("  No positions configured. Edit config.yaml and add some!")
        return

    for p in positions_cfg:
        mint = p["token_mint"]
        sym = p.get("token_symbol", mint[:8])
        entry = p["entry_price"]
        sl_pct = p["stop_loss_pct"]
        tp_pct = p["take_profit_pct"]
        sl_price = entry * (1 - sl_pct / 100)
        tp_price = entry * (1 + tp_pct / 100)

        # Try to fetch live price
        price_sol = get_token_price_sol(mint)
        pnl = ((price_sol - entry) / entry) * 100 if price_sol else None

        print(f"  📍 {sym}")
        print(f"     Token:    {mint}")
        print(f"     Entry:    {entry:.10f} SOL")
        print(f"     Current:  {price_sol:.10f} SOL" if price_sol else "     Current:  N/A")
        if pnl is not None:
            emoji = "📈" if pnl >= 0 else "📉"
            print(f"     P&L:      {emoji} {pnl:+.2f}%")
        print(f"     Stop-Loss:  -{sl_pct}% @ {sl_price:.10f} SOL")
        print(f"     Take-Profit: +{tp_pct}% @ {tp_price:.10f} SOL")
        print()


def cmd_start(config_path: str = "config.yaml"):
    """Start the rug-or-run trading bot."""
    print(BANNER)
    run(config_path)


# ──────────────────────────────────────────
#  CLI DISPATCHER
# ──────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Solana Trading Bot — Rug or Run Strategy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python bot.py start          Start the trading bot
  python bot.py check          Check wallet status
  python bot.py price BONK     Look up BONK price by symbol
  python bot.py status         Show all positions
        """,
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="start",
        choices=["start", "check", "price", "status"],
        help="Command to run",
    )
    parser.add_argument(
        "arg",
        nargs="?",
        default=None,
        help="Optional argument (e.g. token mint or config path)",
    )

    args = parser.parse_args()

    if args.command == "check":
        cmd_check()
    elif args.command == "price":
        token_mint = args.arg
        if not token_mint:
            print("\n  ❌ Usage: python bot.py price <TOKEN_MINT>\n")
            print("  Examples:")
            print("    python bot.py price DezXAZ8z7PnrnRJjz3wXBoRgixCa6xgdBqaYneZkUzK4h")
            print("    python bot.py price JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN")
            return
        cmd_price(token_mint)
    elif args.command == "status":
        cmd_status(args.arg or "config.yaml")
    else:
        cmd_start(args.arg or "config.yaml")


if __name__ == "__main__":
    main()
