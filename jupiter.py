"""
jupiter.py — Price feeds (CoinGecko) + Jupiter v1 swaps via api.jup.ag
"""

import os
import json
import base64
import time
import requests
import base58
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

# --- Constants ---
SOL_MINT = "So11111111111111111111111111111111111111112"
JUPITER_API = "https://api.jup.ag"
COINGECKO_API = "https://api.coingecko.com/api/v3"
LAMPORTS_PER_SOL = 1_000_000_000

# Cache for CoinGecko to respect rate limits
_CG_LAST_CALL = 0
_CG_CACHE_TTL = 6  # seconds between calls (free tier: ~10/min)

# Token -> CoinGecko ID mapping
TOKEN_CG_MAP = {
    "So11111111111111111111111111111111111111112": "solana",
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xgdBqaYneZkUzK4h": "bonk",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "usd-coin",
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": "jupiter",
    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm": "dogwifcoin",
    "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr": "popcat",
}

# HTTP Session
_session = requests.Session()
_session.headers.update({
    "User-Agent": "SolanaTradingBot/1.0",
    "Accept": "application/json",
})


# ──────────────────────────────────────────
#  PRICE FEED (CoinGecko)
# ──────────────────────────────────────────

def get_token_price_usd(token_mint: str) -> float | None:
    """Get token price in USD via CoinGecko with rate-limit awareness."""
    global _CG_LAST_CALL

    cg_id = TOKEN_CG_MAP.get(token_mint)
    if not cg_id:
        print(f"  ⚠️  No CoinGecko mapping for mint {token_mint[:12]}...")
        return None

    # Rate limit: wait if called too soon
    elapsed = time.time() - _CG_LAST_CALL
    if elapsed < _CG_CACHE_TTL:
        time.sleep(_CG_CACHE_TTL - elapsed)

    try:
        resp = _session.get(
            f"{COINGECKO_API}/simple/price",
            params={"ids": cg_id, "vs_currencies": "usd"},
            timeout=10,
        )
        _CG_LAST_CALL = time.time()
        resp.raise_for_status()
        data = resp.json()
        return data.get(cg_id, {}).get("usd")
    except requests.HTTPError as e:
        if resp.status_code == 429:
            print(f"  ⚠️  CoinGecko rate limited. Waiting 10s...")
            time.sleep(10)
            return get_token_price_usd(token_mint)  # retry
        print(f"  ⚠️  Price fetch error: {e}")
        return None
    except Exception as e:
        print(f"  ⚠️  Price fetch error: {e}")
        return None


def get_token_price_sol(token_mint: str) -> float | None:
    """Get token price in SOL via CoinGecko."""
    token_usd = get_token_price_usd(token_mint)
    sol_usd = get_sol_price_usd()
    if token_usd and sol_usd and sol_usd > 0:
        return token_usd / sol_usd
    return None


def get_sol_price_usd() -> float | None:
    """Get SOL price in USD."""
    return get_token_price_usd(SOL_MINT)


# ──────────────────────────────────────────
#  QUOTE & SWAP (Jupiter v1 via api.jup.ag)
# ──────────────────────────────────────────

def get_quote(
    input_mint: str,
    output_mint: str,
    amount: int,                 # in smallest unit of input token
    slippage_bps: int = 100,     # 1% default
    only_direct_routes: bool = False,
) -> dict | None:
    """Get a swap quote from Jupiter v1 API."""
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount),
        "slippageBps": slippage_bps,
        "onlyDirectRoutes": str(only_direct_routes).lower(),
    }
    try:
        resp = _session.get(
            f"{JUPITER_API}/swap/v1/quote",
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  ⚠️  Quote error: {e}")
        if resp and resp.text:
            print(f"  Response: {resp.text[:200]}")
        return None


def execute_swap(
    quote_response: dict,
    wallet_keypair: Keypair,
    dry_run: bool = False,
) -> dict:
    """Execute a swap via Jupiter v1 API."""
    if dry_run:
        in_amount = int(quote_response.get("inAmount", 0))
        out_amount = int(quote_response.get("outAmount", 0))
        route = quote_response.get("routePlan", [{}])
        return {
            "status": "dry_run",
            "message": (
                f"Would swap {in_amount} → {out_amount}\n"
                f"  Price impact: {quote_response.get('priceImpactPct', '?')}%\n"
                f"  Route: {len(route)} hop(s)\n"
                f"  (dry_run=True — no real transaction)"
            ),
        }

    try:
        # Step 1: Get swap instructions from Jupiter
        swap_payload = {
            "quoteResponse": quote_response,
            "userPublicKey": str(wallet_keypair.pubkey()),
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
        }

        resp = _session.post(
            f"{JUPITER_API}/swap/v1/swap",
            json=swap_payload,
            timeout=30,
        )
        resp.raise_for_status()
        swap_data = resp.json()

        # Step 2: Decode the base64 transaction
        swap_tx_b64 = swap_data["swapTransaction"]
        swap_tx_bytes = base64.b64decode(swap_tx_b64)
        tx = VersionedTransaction.from_bytes(swap_tx_bytes)

        # Step 3: Sign it
        signed_tx = VersionedTransaction(tx.message, [wallet_keypair])

        # Step 4: Serialize back to base64 for RPC
        tx_bytes = bytes(signed_tx)
        tx_b64_signed = base64.b64encode(tx_bytes).decode()

        # Step 5: Send via RPC
        rpc_url = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
        send_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [
                tx_b64_signed,
                {
                    "skipPreflight": False,
                    "preflightCommitment": "confirmed",
                    "encoding": "base64",
                },
            ],
        }
        send_resp = _session.post(rpc_url, json=send_payload, timeout=30)
        send_resp.raise_for_status()
        send_data = send_resp.json()

        if "error" in send_data:
            return {
                "status": "error",
                "message": f"Transaction failed: {send_data['error']}",
            }

        txid = send_data.get("result")
        return {
            "status": "success",
            "txid": txid,
            "message": f"Swap executed! TxID: {txid}",
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Swap execution error: {e}",
        }


def load_keypair_from_env() -> Keypair | None:
    """Load a solders Keypair from the private key in environment."""
    priv_b58 = os.getenv("SOLANA_PRIVATE_KEY")
    if not priv_b58:
        return None
    try:
        priv_bytes = base58.b58decode(priv_b58)
        return Keypair.from_bytes(priv_bytes[:64])
    except Exception as e:
        print(f"  ⚠️  Failed to load keypair from env: {e}")
        return None
