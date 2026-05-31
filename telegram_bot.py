"""
telegram_bot.py — Telegram interface for the Solana Trading Bot.
Run: python telegram_bot.py
Requires TELEGRAM_BOT_TOKEN and SOLANA_PRIVATE_KEY in .env

Commands:
  /start        — Welcome + menu
  /balance      — Check SOL balance
  /buy <mint> <sol> — Buy tokens with SOL
  /sell <sym>   — Sell a position back to SOL
  /positions    — List all positions with P&L
  /price <mint> — Check token price
  /watch <mint> <sym> <entry> <sl%> <tp%> — Add to watchdog
  /stopwatch    — Stop the bag watchdog
  /funds        — Full wallet overview
"""

import os
import sys
import yaml
import asyncio
import logging
import json
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Load .env from project root
PROJECT = Path(__file__).parent
load_dotenv(PROJECT / ".env")

# Ensure project root is on path
sys.path.insert(0, str(PROJECT))

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler,
    MessageHandler, filters, ConversationHandler,
)

from watchdog import check_and_act, _load_keypair, sell_position as watchdog_sell
from jupiter import (
    get_token_price_sol, get_sol_price_usd, get_quote,
    execute_swap, SOL_MINT, LAMPORTS_PER_SOL, get_any_token_price_sol,
)
from wallet import load_keypair, get_balance_sol
from strategies.dca import buy_next_dca, dca_status_str, get_dca_summary

# ──────────────────────────────────────────
#  CONFIG
# ──────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CONFIG_PATH = str(PROJECT / "config.yaml")
USER_CHAT_IDS_FILE = str(PROJECT / ".bot_users.json")

# Conversation states
WAITING_MINT, WAITING_SOL_AMOUNT = range(2)

# ──────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────

def _load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

def _save_config(config):
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False)

def _load_users():
    if os.path.exists(USER_CHAT_IDS_FILE):
        with open(USER_CHAT_IDS_FILE) as f:
            return json.load(f)
    return []

def _save_users(users):
    with open(USER_CHAT_IDS_FILE, "w") as f:
        json.dump(users, f)

def _register_user(chat_id):
    users = _load_users()
    if chat_id not in users:
        users.append(chat_id)
        _save_users(users)

def _wallet_keypair():
    """Load solders Keypair from CLI keypair file."""
    import json as _json
    from solders.keypair import Keypair
    kp_path = os.path.expanduser("~/.config/solana/id.json")
    with open(kp_path) as f:
        secret = _json.load(f)
    return Keypair.from_bytes(bytes(secret[:64]))

# ──────────────────────────────────────────
#  COMMAND HANDLERS
# ──────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message with inline keyboard."""
    _register_user(update.effective_chat.id)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Balance", callback_data="balance"),
         InlineKeyboardButton("📋 Positions", callback_data="positions")],
        [InlineKeyboardButton("🛒 Buy Token", callback_data="buy"),
         InlineKeyboardButton("💵 Sell Position", callback_data="sell")],
        [InlineKeyboardButton("🔍 Price Check", callback_data="price"),
         InlineKeyboardButton("🎯 Watchdog", callback_data="watchdog")],
    ])

    await update.message.reply_text(
        "🚀 **Solana Trading Bot**\n\n"
        "I monitor your bags and execute trades on Solana via Jupiter.\n\n"
        "**Commands:**\n"
        "`/balance` — Check SOL balance\n"
        "`/buy <mint> <sol>` — Buy tokens\n"
        "`/sell <symbol>` — Sell position\n"
        "`/positions` — View all positions\n"
        "`/price <mint>` — Token price\n"
        "`/watch <mint> <sym> <entry> <sl%> <tp%>` — Add to watch\n"
        "`/stopwatch` — Stop watchdog\n"
        "`/funds` — Full overview\n\n"
        "Or tap a button below 👇",
        reply_markup=kb,
        parse_mode="Markdown",
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "balance":
        await cmd_balance(query)
    elif data == "positions":
        await cmd_positions(query)
    elif data == "buy":
        await query.edit_message_text(
            "🛒 To buy tokens, use:\n`/buy <token_mint> <sol_amount>`\n\n"
            "Example:\n`/buy DezXAZ8z7PnrnRJjz3wXBoRgixCa6xgdBqaYneZkUzK4h 0.01`\n\n"
            "This swaps 0.01 SOL for the token.",
            parse_mode="Markdown",
        )
    elif data == "sell":
        await query.edit_message_text(
            "💵 To sell a position, use:\n`/sell <symbol>`\n\n"
            "Your positions are listed in `config.yaml`.\nCheck `/positions` first.",
            parse_mode="Markdown",
        )
    elif data == "price":
        await query.edit_message_text(
            "🔍 To check a token price:\n`/price <token_mint>`\n\n"
            "Example:\n`/price DezXAZ8z7PnrnRJjz3wXBoRgixCa6xgdBqaYneZkUzK4h`",
            parse_mode="Markdown",
        )
    elif data == "watchdog":
        await query.edit_message_text(
            "🎯 **Watchdog System**\n\n"
            "The watchdog checks positions every 5 min and auto-sells on SL/TP.\n\n"
            "To add a position:\n"
            "`/watch <mint> <symbol> <entry_price> <sl%> <tp%>`\n\n"
            "Example:\n"
            "`/watch DezXAZ8z7PnrnRJjz3wXBoRgixCa6xgdBqaYneZkUzK4h BONK 0.00000001 15 25`\n\n"
            "Use `/stopwatch` to stop it.",
            parse_mode="Markdown",
        )

async def cmd_balance(update_or_query):
    """Show SOL balance."""
    try:
        bal = get_balance_sol()
        sol_usd = get_sol_price_usd()
        usd_str = f" (${bal * sol_usd:.2f})" if sol_usd else ""
        msg = (
            f"📍 `6D6oVbiApw...`\n"
            f"💰 **{bal:.6f} SOL**{usd_str}"
        )
    except Exception as e:
        msg = f"❌ Balance error: {e}"

    if hasattr(update_or_query, 'edit_message_text'):
        await update_or_query.edit_message_text(msg, parse_mode="Markdown")
    else:
        await update_or_query.message.reply_text(msg, parse_mode="Markdown")


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_balance(update)


async def cmd_positions(update_or_query):
    """Show current positions from config + live prices."""
    config = _load_config()
    positions = config.get("positions", [])
    if not positions:
        msg = "📭 No positions configured."
        if hasattr(update_or_query, 'edit_message_text'):
            await update_or_query.edit_message_text(msg)
        else:
            await update_or_query.message.reply_text(msg)
        return

    lines = ["📋 **Positions**\n"]
    for p in positions:
        sym = p.get("token_symbol", p["token_mint"][:8])
        entry = p["entry_price"]
        bal = p.get("token_balance", 0)
        sold = p.get("sold", False)

        if sold or bal <= 0:
            # Show sold status
            pnl = p.get("sold_pnl", 0)
            lines.append(f"✅ {sym} | SOLD {'+' if pnl >= 0 else ''}{pnl:.1f}%")
            continue

        # Live price
        price = get_token_price_sol(p["token_mint"])
        if price:
            pnl = ((price - entry) / entry) * 100
            val = bal * price
            sol_usd = get_sol_price_usd()
            usd_val = val * sol_usd if sol_usd else None
            emoji = "📈" if pnl >= 0 else "📉"
            usd_str = f" (${usd_val:.2f})" if usd_val else ""
            sl_pct = p.get("stop_loss_pct", 15)
            tp_pct = p.get("take_profit_pct", 25)
            lines.append(
                f"{emoji} **{sym}**\n"
                f"   P&L: {pnl:+.2f}%\n"
                f"   Bag: {val:.6f} SOL{usd_str}\n"
                f"   SL: -{sl_pct}% | TP: +{tp_pct}%"
            )
        else:
            lines.append(f"⚠️ {sym} | Price unavailable")

    msg = "\n\n".join(lines)

    if hasattr(update_or_query, 'edit_message_text'):
        await update_or_query.edit_message_text(msg, parse_mode="Markdown")
    else:
        await update_or_query.message.reply_text(msg, parse_mode="Markdown")


async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_positions(update)


async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Buy tokens: /buy <mint> <sol_amount>"""
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: `/buy <token_mint> <sol_amount>`\n\n"
            "Example: `/buy DezXAZ8z7PnrnRJjz3wXBoRgixCa6xgdBqaYneZkUzK4h 0.01`",
            parse_mode="Markdown",
        )
        return

    mint = context.args[0]
    try:
        sol_amount = float(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid SOL amount. Use a number like `0.01`.", parse_mode="Markdown")
        return

    if sol_amount <= 0:
        await update.message.reply_text("❌ Amount must be > 0.")
        return

    await update.message.reply_text(f"🔄 Buying tokens... swapping **{sol_amount} SOL**...\n_This may take 15-30s_", parse_mode="Markdown")

    try:
        lamports = int(sol_amount * LAMPORTS_PER_SOL)
        keypair = _wallet_keypair()

        # Get quote: SOL → token
        quote = get_quote(
            input_mint=SOL_MINT,
            output_mint=mint,
            amount=lamports,
            slippage_bps=5000,  # 50% for pump.fun
        )
        if not quote:
            await update.message.reply_text("❌ Failed to get swap quote from Jupiter.")
            return

        out_amount = int(quote.get("outAmount", 0))
        out_decimals = 9  # assume 9 decimals for pump.fun tokens
        logger.info(f"Buy quote: {sol_amount} SOL → {out_amount} tokens")

        # Execute swap
        result = execute_swap(quote, keypair, dry_run=False)

        if result["status"] == "success":
            txid = result["txid"]
            msg = (
                f"✅ **Buy executed!**\n\n"
                f"🔄 `{sol_amount} SOL` → `{out_amount:,}` tokens\n"
                f"🔗 [Solscan](https://solscan.io/tx/{txid})"
            )
        else:
            msg = f"❌ **Buy failed:** {result.get('message', 'Unknown error')}"

    except Exception as e:
        msg = f"❌ **Error:** {e}"

    await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)


async def sell_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sell a position: /sell <symbol>"""
    if not context.args:
        await update.message.reply_text(
            "Usage: `/sell <symbol>`\n\n"
            "Example: `/sell CMGS`\n\n"
            "Check `/positions` to see your active bags.",
            parse_mode="Markdown",
        )
        return

    sym = context.args[0].upper()
    config = _load_config()
    pos = None
    for p in config.get("positions", []):
        if p.get("token_symbol", "").upper() == sym and not p.get("sold", False) and p.get("token_balance", 0) > 0:
            pos = p
            break

    if not pos:
        await update.message.reply_text(f"❌ No active position found for `{sym}`.\nCheck `/positions`.", parse_mode="Markdown")
        return

    await update.message.reply_text(
        f"🔄 Selling **{pos['token_balance']:,} {sym}**...\n_This may take 15-30s_",
        parse_mode="Markdown",
    )

    try:
        from watchdog import sell_position as watchdog_sell
        result = watchdog_sell(pos["token_mint"], sym, pos["token_balance"])

        if result["status"] == "success":
            sol_recv = result.get("sol_received", 0)
            txid = result["txid"]

            # Mark as sold in config
            pos["sold"] = True
            pos["sold_pnl"] = 0  # Will calculate
            pos["token_balance"] = 0
            _save_config(config)

            msg = (
                f"✅ **{sym} SOLD!**\n\n"
                f"💰 Recovered: `{sol_recv:.6f} SOL`\n"
                f"🔗 [Solscan](https://solscan.io/tx/{txid})"
            )
        else:
            msg = f"❌ **Sell failed:** {result.get('message', 'Unknown error')}"

    except Exception as e:
        msg = f"❌ **Error:** {e}"

    await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)


async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check token price: /price <mint>"""
    if not context.args:
        await update.message.reply_text(
            "Usage: `/price <token_mint>`\n\n"
            "Example: `/price DezXAZ8z7PnrnRJjz3wXBoRgixCa6xgdBqaYneZkUzK4h`",
            parse_mode="Markdown",
        )
        return

    mint = context.args[0]
    await update.message.reply_text(f"🔍 Checking price for `{mint[:12]}...`", parse_mode="Markdown")

    try:
        price_sol = get_token_price_sol(mint)
        sol_usd = get_sol_price_usd()

        if price_sol is None:
            await update.message.reply_text("❌ Could not fetch price. Check the mint address.")
            return

        price_usd = price_sol * sol_usd if sol_usd else None
        msg = (
            f"**Token Price**\n"
            f"🔹 `{mint}`\n\n"
            f"Price: `{price_sol:.12f} SOL`\n"
        )
        if price_usd:
            msg += f"Price: `${price_usd:.10f}`\n"
        if sol_usd:
            msg += f"SOL/USD: `${sol_usd:.2f}`"

    except Exception as e:
        msg = f"❌ Error: {e}"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def watch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a position to the watchdog: /watch <mint> <symbol> <entry> <sl%> <tp%>"""
    if len(context.args) < 5:
        await update.message.reply_text(
            "Usage: `/watch <mint> <symbol> <entry_price> <sl%> <tp%>`\n\n"
            "Example:\n"
            "`/watch DezXAZ8z7PnrnRJjz3wXBoRgixCa6xgdBqaYneZkUzK4h BONK 0.00000001 15 25`\n\n"
            "Sets SL at -15%, TP at +25%.",
            parse_mode="Markdown",
        )
        return

    mint = context.args[0]
    symbol = context.args[1].upper()
    try:
        entry = float(context.args[2])
        sl = float(context.args[3])
        tp = float(context.args[4])
    except ValueError:
        await update.message.reply_text("❌ entry_price, sl%, and tp% must be numbers.", parse_mode="Markdown")
        return

    config = _load_config()
    positions = config.get("positions", [])

    # Check if already exists
    for p in positions:
        if p.get("token_symbol", "").upper() == symbol:
            await update.message.reply_text(f"⚠️ `{symbol}` already in config. Remove it manually or edit `config.yaml`.", parse_mode="Markdown")
            return

    new_pos = {
        "token_mint": mint,
        "token_symbol": symbol,
        "entry_price": entry,
        "stop_loss_pct": sl,
        "take_profit_pct": tp,
        "amount_in_sol": 0,
        "already_in_position": True,
        "token_balance": 0,
    }
    positions.append(new_pos)
    config["positions"] = positions
    _save_config(config)

    msg = (
        f"✅ **{symbol} added to watch!**\n\n"
        f"🪙 Entry: `{entry:.10f}`\n"
        f"🛑 SL: `-{sl}%`\n"
        f"🎯 TP: `+{tp}%`\n\n"
        "⚠️ Set `token_balance` in `config.yaml` if you already hold this token."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def stopwatch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop the watchdog cron job."""
    await update.message.reply_text(
        "🛑 **Stopping Watchdog**\n\n"
        "I can remove the watchdog cron job. Use this command on Hermes:\n"
        "`cronjob action=remove job_id=603445c70311`\n\n"
        "Or just tell me to stop it here and I'll handle it.",
        parse_mode="Markdown",
    )


async def dca_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trigger a manual DCA buy: /dca"""
    await update.message.reply_text("🔄 Executing DCA buy...\n_This may take 30-60s_", parse_mode="Markdown")
    try:
        msg = buy_next_dca()
        await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)
    except Exception as e:
        await update.message.reply_text(f"❌ **DCA Error:** {e}", parse_mode="Markdown")


async def dcastatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show DCA status: /dcastatus"""
    try:
        msg = get_dca_summary()
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ **Error:** {e}", parse_mode="Markdown")


async def funds_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Full wallet overview."""
    try:
        bal = get_balance_sol()
        sol_usd = get_sol_price_usd()
        usd_str = f" (${bal * sol_usd:.2f})" if sol_usd else ""

        config = _load_config()
        config = _load_config()
        positions = config.get("positions", [])
        active = [p for p in positions if not p.get("sold", False) and p.get("token_balance", 0) > 0]
        sold = [p for p in positions if p.get("sold", False)]

        msg = (
            f"**🏦 Portfolio Overview**\n\n"
            f"📍 `6D6oVbiApw...`\n"
            f"💰 **{bal:.6f} SOL**{usd_str}\n"
        )

        if active:
            msg += f"\n**📈 Active ({len(active)}):**\n"
            for p in active:
                sym = p.get("token_symbol", p["token_mint"][:8])
                price = get_token_price_sol(p["token_mint"])
                if price:
                    pnl = ((price - p["entry_price"]) / p["entry_price"]) * 100
                    emoji = "📈" if pnl >= 0 else "📉"
                    msg += f"  {emoji} {sym}: {pnl:+.2f}%\n"

        if sold:
            msg += f"\n**✅ Sold: {len(sold)}**\n"
            for p in sold:
                sym = p.get("token_symbol", "?")
                pnl = p.get("sold_pnl", 0)
                msg += f"  ✅ {sym}: {'+' if pnl >= 0 else ''}{pnl:.1f}%\n"

        msg += "\n⚡ Watchdog: Active (5 min checks)"

    except Exception as e:
        msg = f"❌ Error: {e}"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ Unknown command. Try `/start` to see available commands.",
        parse_mode="Markdown",
    )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors."""
    logger.error(f"Update {update} caused error {context.error}")


# ──────────────────────────────────────────
#  MAIN
# ──────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN not set in .env")
        print("   Create a .env file with: TELEGRAM_BOT_TOKEN=your_bot_token")
        sys.exit(1)

    app = Application.builder().token(BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("buy", buy_command))
    app.add_handler(CommandHandler("sell", sell_command))
    app.add_handler(CommandHandler("positions", positions_command))
    app.add_handler(CommandHandler("price", price_command))
    app.add_handler(CommandHandler("watch", watch_command))
    app.add_handler(CommandHandler("stopwatch", stopwatch_command))
    app.add_handler(CommandHandler("funds", funds_command))
    app.add_handler(CommandHandler("dca", dca_command))
    app.add_handler(CommandHandler("dcastatus", dcastatus_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    app.add_error_handler(error_handler)

    print("🤖 Solana Trading Bot — Telegram interface running...")
    print("   Bot: @Nxx666_bot (Dog Meat)")
    print("   Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
