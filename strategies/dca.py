"""
dca.py — Dollar Cost Averaging strategy module.

Buys a fixed amount of SOL worth of a token at regular intervals.
Tracks DCA history in a state file so it knows what it's already bought.
Integrates with watchdog for stop-loss protection.
"""

import os
import json
import time
from pathlib import Path

PROJECT = Path(__file__).parent.parent
STATE_FILE = PROJECT / ".dca_state.json"


def save_dca_state(state: dict):
    """Save DCA state to JSON file."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def load_dca_state() -> dict:
    """Load DCA state from JSON file."""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def get_dca_config() -> dict:
    """Get DCA config - defaults that can be overridden via Telegram."""
    state = load_dca_state()
    return state.get("config", {
        "token_mint": "HFq1GAEfsFamEQYVccRemH37qWR1VgSJZPxdVGYVpump",
        "token_symbol": "NOVA",
        "buy_amount_sol": 0.01,        # How much SOL to spend per buy
        "stop_loss_pct": 5,             # Tight 5% stop loss
        "take_profit_pct": 25,           # Standard TP
        "interval_hours": 6,             # Buy every 6 hours
        "max_total_sol": None,           # No max yet
    })


def buy_next_dca() -> str:
    """
    Execute one DCA buy. Called by cron job.
    Returns a status message for the user.
    """
    import sys
    sys.path.insert(0, str(PROJECT))
    
    from jupiter import get_quote, execute_swap, SOL_MINT, LAMPORTS_PER_SOL, get_any_token_price_sol
    from watchdog import _load_keypair
    from wallet import get_balance_sol

    config = get_dca_config()
    state = load_dca_state()

    mint = config["token_mint"]
    sym = config["token_symbol"]
    amount_sol = config["buy_amount_sol"]
    sl_pct = config["stop_loss_pct"]
    tp_pct = config["take_profit_pct"]

    # Track buys
    buys = state.get("buys", [])
    total_spent = sum(b.get("sol", 0) for b in buys)
    total_tokens = sum(b.get("tokens", 0) for b in buys)

    # Check wallet balance
    bal = get_balance_sol()
    if bal < amount_sol * 1.05:  # Need 5% more for fees/slippage
        return (
            f"❌ **DCA {sym} — Insufficient SOL**\n"
            f"Need: {amount_sol:.4f} SOL | Have: {bal:.4f} SOL"
        )

    # Execute swap: SOL → token
    print(f"  🔄 DCA buy #{len(buys) + 1}: {amount_sol} SOL → {sym}...")
    keypair = _load_keypair()
    lamports = int(amount_sol * LAMPORTS_PER_SOL)

    quote = get_quote(
        input_mint=SOL_MINT,
        output_mint=mint,
        amount=lamports,
        slippage_bps=5000,  # 50% for pump.fun
    )
    if not quote:
        return f"❌ **DCA {sym} — Quote failed**\nCould not get swap quote from Jupiter."

    out_amount = int(quote.get("outAmount", 0))
    result = execute_swap(quote, keypair, dry_run=False)

    if result["status"] != "success":
        return f"❌ **DCA {sym} — Swap failed**\n{result.get('message', 'Unknown error')}"

    txid = result["txid"]
    price_at_buy = amount_sol / out_amount if out_amount > 0 else 0

    # Record this buy
    buy_record = {
        "timestamp": time.time(),
        "sol_spent": amount_sol,
        "tokens_bought": out_amount,
        "price_sol": price_at_buy,
        "txid": txid,
    }
    buys.append(buy_record)
    total_spent += amount_sol
    total_tokens += out_amount

    # Get current price for P&L
    current_price = get_any_token_price_sol(mint)
    avg_price = total_spent / total_tokens if total_tokens > 0 else 0
    pnl_pct = ((current_price - avg_price) / avg_price) * 100 if current_price and avg_price else 0

    # Update state
    state["buys"] = buys
    state["total_spent_sol"] = total_spent
    state["total_tokens"] = total_tokens
    state["avg_price_sol"] = avg_price
    state["config"] = config
    save_dca_state(state)

    # Also update or add position to config.yaml for watchdog protection
    _sync_to_config(mint, sym, avg_price, total_tokens, sl_pct, tp_pct)

    return (
        f"✅ **DCA #{len(buys)} — {sym} Bought!**\n\n"
        f"🔄 `{amount_sol} SOL` → `{out_amount:,}` tokens\n"
        f"💰 Total: `{total_spent:.4f} SOL` → `{total_tokens:,}` tokens\n"
        f"📊 Avg price: `{avg_price:.15f} SOL`\n"
        f"📈 Current P&L: `{pnl_pct:+.2f}%`\n"
        f"🛑 SL: `-{sl_pct}%` | 🎯 TP: `+{tp_pct}%`\n"
        f"🔗 [Solscan](https://solscan.io/tx/{txid})"
    )


def _sync_to_config(mint: str, symbol: str, avg_price: float, balance: int, sl_pct: float, tp_pct: float):
    """Add/update position in config.yaml so watchdog protects it."""
    import yaml

    config_path = PROJECT / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    positions = config.get("positions", [])

    # Find or create position
    found = False
    for p in positions:
        if p.get("token_mint") == mint:
            p["entry_price"] = avg_price
            p["token_balance"] = balance
            p["stop_loss_pct"] = sl_pct
            p["take_profit_pct"] = tp_pct
            p["sold"] = False
            p["already_in_position"] = True
            found = True
            break

    if not found:
        positions.append({
            "token_mint": mint,
            "token_symbol": symbol,
            "entry_price": avg_price,
            "stop_loss_pct": sl_pct,
            "take_profit_pct": tp_pct,
            "amount_in_sol": 0.01,
            "already_in_position": True,
            "token_balance": balance,
            "sold": False,
        })

    config["positions"] = positions
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)


def get_dca_summary() -> str:
    """Get a summary of DCA position."""
    import sys
    sys.path.insert(0, str(PROJECT))
    from jupiter import get_any_token_price_sol, get_sol_price_usd

    state = load_dca_state()
    buys = state.get("buys", [])
    config = state.get("config", get_dca_config())

    if not buys:
        return "📭 No DCA buys yet."

    sym = config["token_symbol"]
    total_spent = state.get("total_spent_sol", 0)
    total_tokens = state.get("total_tokens", 0)
    avg_price = state.get("avg_price_sol", 0)

    current_price = get_any_token_price_sol(config["token_mint"])
    pnl_pct = ((current_price - avg_price) / avg_price) * 100 if current_price and avg_price else 0
    current_value_sol = total_tokens * current_price if current_price else 0
    sol_usd = get_sol_price_usd()

    msg = (
        f"**📊 DCA {sym}**\n\n"
        f"💰 Spent: `{total_spent:.4f} SOL`\n"
        f"🪙 Tokens: `{total_tokens:,}`\n"
        f"📉 Avg price: `{avg_price:.15f} SOL`\n"
        f"📈 Current: `{current_price:.15f} SOL`\n"
        f"📊 P&L: `{pnl_pct:+.2f}%`\n"
        f"💵 Value: `{current_value_sol:.4f} SOL`"
    )
    if sol_usd:
        msg += f" (`${current_value_sol * sol_usd:.2f}`)\n"
    else:
        msg += "\n"

    msg += (
        f"🛑 SL: `-{config.get('stop_loss_pct', 5)}%`\n"
        f"🎯 TP: `+{config.get('take_profit_pct', 25)}%`\n"
        f"⏱ Every `{config.get('interval_hours', 6)}h`\n"
        f"🛒 Per buy: `{config.get('buy_amount_sol', 0.01)} SOL`\n"
        f"📦 Buys so far: `{len(buys)}`"
    )
    return msg


def dca_status_str() -> str:
    """Quick one-liner for status display."""
    state = load_dca_state()
    buys = state.get("buys", [])
    if buys:
        return f"📊 DCA: {len(buys)} buys | {state.get('total_spent_sol', 0):.4f} SOL in"
    return "📭 No DCA buys yet"
