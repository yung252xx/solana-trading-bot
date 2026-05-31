"""
strategy_runner.py — Rug or Run: Stop-loss & Take-profit engine.

Watches token prices and auto-triggers swaps when targets are hit.
"""

import time
import yaml
import json
import os
from datetime import datetime
from pathlib import Path

from jupiter import (
    get_token_price_sol,
    get_quote,
    execute_swap,
    load_keypair_from_env,
    SOL_MINT,
)

# ──────────────────────────────────────────
#  CONFIG LOADER
# ──────────────────────────────────────────

def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def resolve_config_path(path: str) -> str:
    """Resolve config path relative to script dir or cwd."""
    if os.path.isabs(path):
        return path
    # Try relative to script dir
    script_dir = Path(__file__).parent
    candidate = str(script_dir / path)
    if os.path.exists(candidate):
        return candidate
    return path  # fallback to cwd


# ──────────────────────────────────────────
#  POSITION STATE
# ──────────────────────────────────────────

class Position:
    """Tracks a single position's state."""

    def __init__(self, config: dict):
        self.mint = config["token_mint"]
        self.symbol = config.get("token_symbol", self.mint[:8])
        self.entry_price = config["entry_price"]
        self.stop_loss_pct = config["stop_loss_pct"]
        self.take_profit_pct = config["take_profit_pct"]
        self.amount_in_sol = config.get("amount_in_sol", 0.1)
        self.already_in = config.get("already_in_position", False)
        self.token_balance = config.get("token_balance", 0)

        # Derived
        self.stop_price = self.entry_price * (1 - self.stop_loss_pct / 100)
        self.take_price = self.entry_price * (1 + self.take_profit_pct / 100)
        self.triggered = False  # True once position is closed
        self.highest_price = self.entry_price
        self.lowest_price = self.entry_price

        # Trailing stop tracking (optional enhancement)
        self.trailing_stop_active = False
        self.trailing_stop_price = None

    def check(self, current_price: float) -> dict | None:
        """
        Check if current price triggers stop-loss or take-profit.
        Returns action dict if triggered, None otherwise.
        """
        if self.triggered:
            return None

        # Track extremes
        if current_price > self.highest_price:
            self.highest_price = current_price
        if current_price < self.lowest_price:
            self.lowest_price = current_price

        actions = []

        # --- TAKE PROFIT ---
        if current_price >= self.take_price:
            actions.append({
                "type": "TAKE_PROFIT",
                "reason": f"Price {current_price:.8f} ≥ take-profit target {self.take_price:.8f} (+{self.take_profit_pct}%)",
            })

        # --- STOP LOSS ---
        if current_price <= self.stop_price:
            actions.append({
                "type": "STOP_LOSS",
                "reason": f"Price {current_price:.8f} ≤ stop-loss target {self.stop_price:.8f} (-{self.stop_loss_pct}%)",
            })

        # --- TRAILING STOP (if activated by hitting take-profit zone) ---
        if self.trailing_stop_active and self.trailing_stop_price:
            if current_price <= self.trailing_stop_price:
                actions.append({
                    "type": "TRAILING_STOP",
                    "reason": f"Price dropped to trailing stop: {current_price:.8f} ≤ {self.trailing_stop_price:.8f}",
                })

        if actions:
            self.triggered = True
            return actions[0]  # Return first trigger

        return None

    def summary(self, current_price: float) -> str:
        """One-line summary of position status."""
        pnl_pct = ((current_price - self.entry_price) / self.entry_price) * 100
        symbol = "📈" if pnl_pct >= 0 else "📉"
        triggered_tag = " [✅ TRIGGERED]" if self.triggered else ""
        return (
            f"  {symbol} {self.symbol:8s} | "
            f"Entry: ${self.entry_price:.10f} | "
            f"Now: ${current_price:.10f} | "
            f"P&L: {pnl_pct:+.2f}% | "
            f"SL: {self.stop_price:.10f}  TP: {self.take_price:.10f}"
            f"{triggered_tag}"
        )


# ──────────────────────────────────────────
#  TRADE EXECUTOR
# ──────────────────────────────────────────

def execute_rug_or_run(position: Position, current_price: float, action: dict, config: dict):
    """
    Execute the trade: swap token back to SOL.
    """
    print(f"\n  ⚡ {action['type']}! {action['reason']}")

    dry_run = config.get("dry_run", True)
    slippage = config.get("max_slippage_bps", 100)

    if dry_run:
        print(f"  🟡 DRY RUN — Would sell {position.token_balance} of {position.symbol}")
        print(f"     Price: ${current_price:.10f}")
        print(f"     Entry: ${position.entry_price:.10f}")
        print(f"     P&L: {((current_price - position.entry_price) / position.entry_price) * 100:+.2f}%")
        return {
            "status": "dry_run",
            "action": action["type"],
            "position": position.symbol,
        }

    # Live mode — get quote and swap
    keypair = load_keypair_from_env()
    if not keypair:
        print("  ❌ Cannot trade live: no wallet keypair loaded (check .env)")
        return {"status": "error", "message": "No keypair"}

    # Convert token balance to smallest units
    quote = get_quote(
        input_mint=position.mint,
        output_mint=SOL_MINT,
        amount=position.token_balance,
        slippage_bps=slippage,
    )

    if not quote:
        print("  ❌ Failed to get swap quote")
        return {"status": "error", "message": "No quote"}

    result = execute_swap(quote, keypair, dry_run=False)
    print(f"  {'✅' if result['status'] == 'success' else '❌'} {result['message']}")
    return result


# ──────────────────────────────────────────
#  MAIN LOOP
# ──────────────────────────────────────────

def run(config_path: str = "config.yaml"):
    """Main trading loop."""
    config_path = resolve_config_path(config_path)
    config = load_config(config_path)

    dry_run = config.get("dry_run", True)
    poll_interval = config.get("poll_interval_seconds", 15)

    print("=" * 60)
    print("  🏃 RUG OR RUN — Solana Trading Bot")
    print("=" * 60)
    print(f"  Mode: {'🟡 DRY RUN' if dry_run else '🔴 LIVE TRADING'}")
    print(f"  Poll: every {poll_interval}s")
    print(f"  Positions: {len(config.get('positions', []))}")
    print()

    positions = [Position(p) for p in config.get("positions", [])]

    if not positions:
        print("  ❌ No positions configured in config.yaml")
        return

    # Show position summary
    for p in positions:
        print(f"  📍 {p.symbol:8s} | Entry: ${p.entry_price:.10f} | "
              f"SL: -{p.stop_loss_pct}% @ ${p.stop_price:.10f} | "
              f"TP: +{p.take_profit_pct}% @ ${p.take_price:.10f}")
    print()

    # Live price tracker for display
    last_prices = {}

    try:
        while True:
            now = datetime.now().strftime("%H:%M:%S")
            print(f"\n[{now}] Checking positions...")

            all_done = True

            for i, pos in enumerate(positions):
                if pos.triggered:
                    print(pos.summary(last_prices.get(i, 0)))
                    continue

                all_done = False

                # Fetch current price
                current_price = get_token_price_sol(pos.mint)

                if current_price is None:
                    print(f"  ⚠️  Could not fetch price for {pos.symbol}")
                    continue

                last_prices[i] = current_price

                # Check triggers
                action = pos.check(current_price)

                # Print status
                print(pos.summary(current_price))

                if action:
                    result = execute_rug_or_run(pos, current_price, action, config)
                    print(f"  └─ Result: {result.get('message', result.get('status', '?'))}")

            if all_done:
                print("\n  🎯 All positions triggered! Nothing left to watch.")
                print("  Add new positions to config.yaml and restart.\n")
                break

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print("\n\n  👋 Bot stopped by user. Good trades out there!")
