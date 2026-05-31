"""
watchdog.py — Bag watcher + auto-sell on dump.
Runs every 5 minutes. Checks prices. Sells if stop-loss hit.
"""

import sys, os, yaml, json, base64, requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from jupiter import get_sol_price_usd
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 1_000_000_000

# Load wallet keypair once
_KEYPAIR = None


def _load_keypair():
    global _KEYPAIR
    if _KEYPAIR:
        return _KEYPAIR
    kp_path = os.path.expanduser("~/.config/solana/id.json")
    with open(kp_path, "r") as f:
        secret_bytes = json.load(f)
    _KEYPAIR = Keypair.from_bytes(bytes(secret_bytes[:64]))
    return _KEYPAIR


def get_token_price_sol(token_mint: str) -> float | None:
    """Get token price in SOL via Jupiter quote API."""
    try:
        r = requests.get(
            "https://api.jup.ag/swap/v1/quote",
            params={
                "inputMint": SOL_MINT,
                "outputMint": token_mint,
                "amount": str(10_000_000),
                "slippageBps": "500",
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        out_amount = int(data.get("outAmount", 0))
        if out_amount > 0:
            return 0.01 / out_amount
        return None
    except Exception as e:
        print(f"  ⚠️  Price error for {token_mint[:12]}: {e}")
        return None


def sell_position(mint: str, symbol: str, balance: int, slippage_bps: int = 300) -> dict:
    """Sell entire token position back to SOL via Jupiter. Returns result dict."""
    keypair = _load_keypair()
    addr = str(keypair.pubkey())

    # 1. Get quote: token → SOL
    print(f"  💰 Getting sell quote for {balance} {symbol}...")
    q = requests.get(
        "https://api.jup.ag/swap/v1/quote",
        params={
            "inputMint": mint,
            "outputMint": SOL_MINT,
            "amount": str(balance),
            "slippageBps": str(slippage_bps),
        },
        timeout=15,
    )
    q.raise_for_status()
    quote = q.json()
    out_sol = int(quote.get("outAmount", 0)) / LAMPORTS_PER_SOL
    print(f"  📊 Sell quote: {balance} → {out_sol:.6f} SOL")

    # 2. Execute swap
    swap_payload = {
        "quoteResponse": quote,
        "userPublicKey": addr,
        "wrapAndUnwrapSol": True,
        "dynamicComputeUnitLimit": True,
    }
    r = requests.post(
        "https://api.jup.ag/swap/v1/swap",
        json=swap_payload,
        timeout=30,
    )
    r.raise_for_status()
    swap_data = r.json()

    # 3. Sign transaction
    tx_bytes = base64.b64decode(swap_data["swapTransaction"])
    tx = VersionedTransaction.from_bytes(tx_bytes)
    signed_tx = VersionedTransaction(tx.message, [keypair])
    tx_b64 = base64.b64encode(bytes(signed_tx)).decode()

    # 4. Send via RPC
    rpc_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendTransaction",
        "params": [
            tx_b64,
            {"skipPreflight": False, "preflightCommitment": "confirmed", "encoding": "base64"},
        ],
    }
    r = requests.post("https://api.mainnet-beta.solana.com", json=rpc_payload, timeout=30)
    r.raise_for_status()
    result = r.json()

    if "error" in result:
        return {"status": "error", "message": str(result["error"])}

    return {
        "status": "success",
        "txid": result["result"],
        "sol_received": out_sol,
    }


def check_and_act(config_path: str) -> str:
    """Check all positions. Sell if stop-loss hit. Return status message."""
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    positions = config.get("positions", [])
    if not positions:
        return "📭 No positions configured."

    lines = []
    actions = []

    for p in positions:
        mint = p["token_mint"]
        sym = p.get("token_symbol", mint[:8])
        entry = p["entry_price"]
        bal = p.get("token_balance", 0)
        sl_pct = p.get("stop_loss_pct", 15)
        tp_pct = p.get("take_profit_pct", 25)

        # Get price
        price = get_token_price_sol(mint)
        if price is None:
            lines.append(f"  ⚠️  {sym}: Price unavailable")
            continue

        # Calculate P&L
        pnl_pct = ((price - entry) / entry) * 100
        value_sol = bal * price
        sol_usd = get_sol_price_usd()
        value_usd = value_sol * sol_usd if sol_usd else None

        emoji = "📈" if pnl_pct >= 0 else "📉"
        val_str = f"${value_usd:.2f}" if value_usd else f"{value_sol:.6f} SOL"

        # Check for dump → SELL
        if pnl_pct < -sl_pct:
            print(f"\n  🚨 {sym} DUMPING! -{pnl_pct:.1f}% — SELLING {bal} tokens...")
            sell_result = sell_position(mint, sym, bal)
            if sell_result["status"] == "success":
                sol_recv = sell_result.get("sol_received", 0)
                txid = sell_result["txid"]
                actions.append(
                    f"🚨 **{sym} DUMPED -{pnl_pct:.1f}%**\n"
                    f"  💰 Sold {bal:,} tokens → {sol_recv:.6f} SOL\n"
                    f"  🔗 https://solscan.io/tx/{txid}"
                )
                lines.append(f"  💀 {sym} | SOLD @ {pnl_pct:.1f}% | Recovered {sol_recv:.6f} SOL ✅")
            else:
                actions.append(f"❌ **{sym} SELL FAILED**: {sell_result.get('message', 'Unknown error')}")
                lines.append(f"  ❌ {sym} | SELL FAILED")
            continue

        # Check for take-profit
        if pnl_pct > tp_pct:
            print(f"\n  🎯 {sym} TP HIT! +{pnl_pct:.1f}% — SELLING {bal} tokens...")
            sell_result = sell_position(mint, sym, bal)
            if sell_result["status"] == "success":
                sol_recv = sell_result.get("sol_received", 0)
                txid = sell_result["txid"]
                actions.append(
                    f"🎯 **{sym} TOOK PROFIT +{pnl_pct:.1f}%**\n"
                    f"  💰 Sold {bal:,} tokens → {sol_recv:.6f} SOL\n"
                    f"  🔗 https://solscan.io/tx/{txid}"
                )
                lines.append(f"  💰 {sym} | SOLD @ +{pnl_pct:.1f}% | Got {sol_recv:.6f} SOL ✅")
            else:
                actions.append(f"❌ **{sym} TP SELL FAILED**: {sell_result.get('message', 'Unknown error')}")
                lines.append(f"  ❌ {sym} | SELL FAILED")
            continue

        # Normal status
        lines.append(f"  {emoji} {sym:6s} | {pnl_pct:+.2f}% | Bag: {val_str}")

    # Build output
    output = ""
    if actions:
        output += "\n\n".join(actions) + "\n\n"
    output += "🤖 **Bag Watch**\n"
    output += "\n".join(lines)
    if not actions:
        output += "\n_Next check in 5 min_"

    return output


if __name__ == "__main__":
    config = Path(__file__).parent / "config.yaml"
    msg = check_and_act(str(config))
    print(msg)
