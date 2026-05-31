"""
One-shot swap test — LIVE on mainnet
Swaps 0.001 SOL → custom token via Jupiter
"""
import sys, os, json, base64, requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wallet import load_keypair
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

TOKEN_MINT = "CmGsBfi3Zbsygpt1FcvFcQa3mD2drYGX6w5WdsWZpump"
AMOUNT_LAMPORTS = 5_000_000  # 0.005 SOL

print("=" * 50)
print("🧪 LIVE SWAP TEST")
print("=" * 50)

# 1. Load wallet from Solana CLI keypair
print("\n🔑 Loading wallet...")
kp_info = load_keypair()
print(f"   Address: {kp_info['public_key']}")
print(f"   From: {kp_info['path']}")

# Convert CLI keypair to solders Keypair
# CLI keypair JSON is a list of 64 bytes (first 32 = private key)
with open(kp_info['path'], 'r') as f:
    secret_bytes = json.load(f)
keypair = Keypair.from_bytes(bytes(secret_bytes[:64]))
print(f"   Keypair loaded ✅")

# 2. Get Jupiter quote
print(f"\n📊 Getting quote: 0.001 SOL → {TOKEN_MINT[:16]}...")
resp = requests.get(
    "https://api.jup.ag/swap/v1/quote",
    params={
        "inputMint": "So11111111111111111111111111111111111111112",
        "outputMint": TOKEN_MINT,
        "amount": str(AMOUNT_LAMPORTS),
        "slippageBps": "500",
    },
    timeout=15,
)
resp.raise_for_status()
quote = resp.json()
out_amount = int(quote.get("outAmount", 0))
print(f"   Quote: 0.001 SOL → {out_amount:,} tokens ✅")
print(f"   Price impact: {quote.get('priceImpactPct', '?')}%")

# 3. Execute swap
print(f"\n⚡ Executing swap...")
swap_payload = {
    "quoteResponse": quote,
    "userPublicKey": str(keypair.pubkey()),
    "wrapAndUnwrapSol": True,
    "dynamicComputeUnitLimit": True,
}

resp = requests.post(
    "https://api.jup.ag/swap/v1/swap",
    json=swap_payload,
    timeout=30,
)
resp.raise_for_status()
swap_data = resp.json()

# 4. Sign transaction
tx_bytes = base64.b64decode(swap_data["swapTransaction"])
tx = VersionedTransaction.from_bytes(tx_bytes)  # deserialize to get message
signed_tx = VersionedTransaction(tx.message, [keypair])  # re-sign with keypair
tx_b64 = base64.b64encode(bytes(signed_tx)).decode()

# 5. Send via RPC
print(f"\n📤 Sending transaction...")
rpc_payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "sendTransaction",
    "params": [
        tx_b64,
        {"skipPreflight": False, "preflightCommitment": "confirmed", "encoding": "base64"},
    ],
}
resp = requests.post(
    "https://api.mainnet-beta.solana.com",
    json=rpc_payload,
    timeout=30,
)
resp.raise_for_status()
result = resp.json()

if "error" in result:
    print(f"\n❌ Error: {result['error']}")
else:
    txid = result["result"]
    print(f"\n✅ SWAP EXECUTED!")
    print(f"   TxID: {txid}")
    print(f"   Solscan: https://solscan.io/tx/{txid}")
    print(f"\n   0.001 SOL → {out_amount:,} tokens 💰")
