"""
watchdog.py — Bag watcher cron job.
Runs every 5 minutes, checks prices, alerts on dumps.
Delivers to Telegram automatically via cron.
"""
import sys, os, yaml, json, time, requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from jupiter import get_sol_price_usd

SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 1_000_000_000


def get_token_price_via_jupiter(token_mint: str) -> float | None:
    """
    Get token price in SOL using Jupiter quote API.
    Asks: what does 0.01 SOL buy of this token?
    Price = amount_in_sol / tokens_received
    """
    try:
        r = requests.get(
            "https://api.jup.ag/swap/v1/quote",
            params={
                "inputMint": SOL_MINT,
                "outputMint": token_mint,
                "amount": str(10_000_000),  # 0.01 SOL
                "slippageBps": "500",
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        out_amount = int(data.get("outAmount", 0))
        if out_amount > 0:
            return 0.01 / (out_amount / 1)  # SOL per token
        return None
    except Exception as e:
        print(f"  ⚠️  Jupiter price error for {token_mint[:12]}: {e}")
        return None


def check_positions(config_path: str) -> str:
    """Check all positions and return a status message."""
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    positions = config.get("positions", [])
    if not positions:
        return "📭 No positions configured."

    lines = []
    dump_alerts = []
    total_pnl_pct = 0

    for p in positions:
        mint = p["token_mint"]
        sym = p.get("token_symbol", mint[:8])
        entry = p["entry_price"]
        bal = p.get("token_balance", 0)

        # Get price
        price = get_token_price_via_jupiter(mint)
        if price is None:
            lines.append(f"  ⚠️  {sym}: Price unavailable")
            continue

        # Calculate P&L
        pnl_pct = ((price - entry) / entry) * 100
        value_sol = bal * price
        sol_usd = get_sol_price_usd()
        value_usd = value_sol * sol_usd if sol_usd else None

        emoji = "📈" if pnl_pct >= 0 else "📉"
        dump_emoji = ""

        # Check for dump
        if pnl_pct < -10:
            dump_alerts.append(f"🚨 **{sym} DUMPING!** -{pnl_pct:.1f}% from entry!")
            dump_emoji = " 🚨"

        val_str = f"${value_usd:.2f}" if value_usd else f"{value_sol:.6f} SOL"
        lines.append(
            f"  {emoji} {sym:6s} | "
            f"{pnl_pct:+.2f}% | "
            f"Bag: {val_str}"
            f"{dump_emoji}"
        )

    # Build message
    header = "🤖 **Bag Watch**"
    body = "\n".join(lines)
    alerts = "\n".join(dump_alerts) if dump_alerts else ""
    footer = "\n_Next check in 5 min_"

    msg = header + "\n" + body
    if alerts:
        msg = alerts + "\n\n" + msg
    msg += footer

    return msg


if __name__ == "__main__":
    config = Path(__file__).parent / "config.yaml"
    msg = check_positions(str(config))
    print(msg)
