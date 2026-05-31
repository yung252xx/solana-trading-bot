"""
wallet.py — Load and manage Solana wallet from keypair file or private key.
"""

import os
import json
import base58
from pathlib import Path

# Default Solana CLI keypair path (Windows)
DEFAULT_KEYPAIR_PATH = os.path.expanduser("~/.config/solana/id.json")


def load_keypair(keypair_path: str = None) -> dict:
    """
    Load keypair from Solana CLI's id.json file.
    Returns dict with 'private_key' (base58) and 'public_key' (base58).
    """
    path = keypair_path or os.getenv("SOLANA_KEYPAIR_PATH") or DEFAULT_KEYPAIR_PATH
    path = os.path.expanduser(path)

    if not os.path.exists(path):
        raise FileNotFoundError(f"Keypair not found at: {path}")

    with open(path, "r") as f:
        secret_bytes = json.load(f)

    # First 32 bytes = private key, last 32 = public key
    private_key_bytes = bytes(secret_bytes[:64])
    public_key_bytes = bytes(secret_bytes[32:64])

    return {
        "private_key": base58.b58encode(private_key_bytes).decode(),
        "public_key": base58.b58encode(public_key_bytes).decode(),
        "path": path,
    }


def get_public_key() -> str:
    """Get wallet public key from Solana CLI config."""
    import subprocess
    result = subprocess.run(
        ["solana", "address"],
        capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to get address: {result.stderr}")
    return result.stdout.strip()


def get_balance_sol(address: str = None, rpc_url: str = None) -> float:
    """Get SOL balance for an address."""
    import subprocess
    cmd = ["solana", "balance"]
    if address:
        cmd.append(address)
    if rpc_url:
        cmd.extend(["--url", rpc_url])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to get balance: {result.stderr}")

    raw = result.stdout.strip()
    # Output like "5.123456789 SOL"
    try:
        return float(raw.split()[0])
    except (ValueError, IndexError):
        raise RuntimeError(f"Unexpected balance output: {raw}")
