"""
Telegram notifications for the trading bot.

Uses OpenClaw's message tool via subprocess to send alerts.
"""
import json
import subprocess
from datetime import datetime
from typing import Optional, Dict, List
import structlog

log = structlog.get_logger()


class TelegramNotifier:
    """Send notifications via OpenClaw's Telegram integration."""
    
    def __init__(self, enabled: bool = False):  # Disabled by default for now
        self.enabled = enabled
        self._last_daily_summary = None
    
    def _send(self, message: str) -> bool:
        """Send a message via OpenClaw CLI."""
        if not self.enabled:
            return False
        
        try:
            # Use openclaw CLI to send message
            # Target is Spencer's Telegram ID
            result = subprocess.run(
                ["openclaw", "message", "send", "-t", "6640078900", "-m", message],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                log.info("Notification sent", message=message[:50])
                return True
            else:
                log.warning("Notification failed", error=result.stderr)
                return False
        except FileNotFoundError:
            log.warning("OpenClaw CLI not found, skipping notification")
            return False
        except Exception as e:
            log.error("Notification error", error=str(e))
            return False
    
    def trade_executed(
        self,
        strategy: str,
        ticker: str,
        side: str,
        price: float,
        size: float,
        paper: bool = True
    ):
        """Notify on trade execution."""
        mode = "📝 PAPER" if paper else "💰 LIVE"
        emoji = "🟢" if side == "yes" else "🔴"
        
        msg = (
            f"{mode} Trade Executed\n"
            f"{emoji} {strategy}: {side.upper()} {ticker}\n"
            f"Price: {price:.0%} | Size: ${size:.2f}"
        )
        self._send(msg)
    
    def position_closed(
        self,
        strategy: str,
        ticker: str,
        pnl: float,
        pnl_pct: float,
        paper: bool = True
    ):
        """Notify on position close."""
        mode = "📝 PAPER" if paper else "💰 LIVE"
        emoji = "✅" if pnl >= 0 else "❌"
        
        msg = (
            f"{mode} Position Closed\n"
            f"{emoji} {strategy}: {ticker}\n"
            f"P&L: ${pnl:+.2f} ({pnl_pct:+.1%})"
        )
        self._send(msg)
    
    def daily_summary(
        self,
        total_pnl: float,
        total_trades: int,
        win_rate: float,
        strategy_stats: Dict[str, Dict]
    ):
        """Send daily performance summary."""
        emoji = "📈" if total_pnl >= 0 else "📉"
        
        lines = [
            f"{emoji} Daily Summary",
            f"Total P&L: ${total_pnl:+.2f}",
            f"Trades: {total_trades} | Win Rate: {win_rate:.0%}",
            ""
        ]
        
        for name, stats in strategy_stats.items():
            pnl = stats.get("pnl", 0)
            trades = stats.get("trades", 0)
            emoji_s = "✅" if pnl >= 0 else "❌"
            lines.append(f"{emoji_s} {name}: ${pnl:+.2f} ({trades} trades)")
        
        self._send("\n".join(lines))
    
    def error(self, error_type: str, details: str):
        """Notify on error."""
        msg = f"⚠️ Bot Error: {error_type}\n{details[:200]}"
        self._send(msg)
    
    def startup(self, mode: str, strategies: List[str], capital: float):
        """Notify on bot startup."""
        msg = (
            f"🤖 Kalshi Bot Started\n"
            f"Mode: {mode}\n"
            f"Strategies: {', '.join(strategies)}\n"
            f"Capital: ${capital:.2f}"
        )
        self._send(msg)
    
    def shutdown(self, reason: str = "normal"):
        """Notify on bot shutdown."""
        msg = f"🛑 Kalshi Bot Stopped\nReason: {reason}"
        self._send(msg)
