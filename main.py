#!/usr/bin/env python3
"""
Kalshi Trading Bot - Main Entry Point

CRITICAL: This bot uses SANDBOX mode by default.
DO NOT disable sandbox mode for automated trading.

Usage:
    python main.py              # Run with defaults (sandbox)
    python main.py --once       # Run single scan cycle
    python main.py --status     # Check API connection
"""
import argparse
import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

import structlog

# Configure logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.dev.ConsoleRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

log = structlog.get_logger()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Kalshi Trading Bot - Paper trading with multiple strategies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python main.py                  # Run continuous trading loop
    python main.py --once           # Run single scan cycle and exit
    python main.py --status         # Check API connection and exit
    python main.py --config my.yaml # Use custom config file

IMPORTANT: This bot defaults to SANDBOX mode. Real trading must
be explicitly enabled and is NOT RECOMMENDED for automated use.
        """
    )
    
    parser.add_argument(
        "--config", "-c",
        default="config.yaml",
        help="Path to config file (default: config.yaml)"
    )
    
    parser.add_argument(
        "--sandbox",
        action="store_true",
        default=True,
        help="Use sandbox API (default: True)"
    )
    
    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="⚠️ USE PRODUCTION API - REAL MONEY (NOT RECOMMENDED)"
    )
    
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run single scan cycle and exit"
    )
    
    parser.add_argument(
        "--status",
        action="store_true",
        help="Check API connection and exit"
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompts (for automated runs)"
    )
    
    return parser.parse_args()


async def check_status(sandbox: bool = True):
    """Check API connection and display status."""
    from src.api.kalshi_client import create_client_from_env
    
    log.info("Checking API connection...", sandbox=sandbox)
    
    try:
        async with create_client_from_env(sandbox=sandbox) as client:
            # Check exchange status
            status = await client.get_exchange_status()
            log.info("Exchange status", **status)
            
            # Check balance
            balance = await client.get_balance()
            log.info("Account balance", **balance)
            
            # Count open markets
            markets = await client.get_markets(status="open", limit=10)
            market_count = len(markets.get("markets", []))
            log.info("Sample markets", count=market_count)
            
            if market_count > 0:
                sample = markets["markets"][0]
                log.info(
                    "Example market",
                    ticker=sample.get("ticker"),
                    title=sample.get("title", "")[:50],
                )
            
            print("\n✅ API connection successful!")
            return True
            
    except Exception as e:
        log.error("API connection failed", error=str(e))
        print(f"\n❌ API connection failed: {e}")
        return False


async def run_once(config_path: str, sandbox: bool = True):
    """Run a single scan cycle."""
    from src.config import Config
    from src.orchestrator import Orchestrator
    
    config = Config.load(config_path)
    config.sandbox = sandbox
    
    orchestrator = Orchestrator(config)
    
    if not await orchestrator.initialize():
        log.error("Failed to initialize")
        return False
    
    try:
        result = await orchestrator.run_once()
        
        print("\n📊 Single Cycle Results:")
        print(f"   Markets scanned: {result['markets_scanned']}")
        print(f"   Signals generated: {result['signals_generated']}")
        print(f"   Portfolio summary:")
        
        summary = result["summary"]
        print(f"     Total capital: ${summary['total_capital']:.2f}")
        print(f"     Total P&L: ${summary['total_pnl']:.2f}")
        
        for name, stats in summary["strategies"].items():
            print(f"\n   {name}:")
            print(f"     Capital: ${stats['capital']:.2f}")
            print(f"     Open positions: {stats['open_positions']}")
            print(f"     Total trades: {stats['total_trades']}")
            print(f"     Win rate: {stats['win_rate']:.1f}%")
        
        return True
        
    finally:
        await orchestrator.cleanup()


async def run_continuous(config_path: str, sandbox: bool = True, skip_confirm: bool = False):
    """Run continuous trading loop."""
    from src.orchestrator import run_bot
    
    if not sandbox and not skip_confirm:
        print("\n" + "="*60)
        print("⚠️  WARNING: LIVE TRADING MODE")
        print("="*60)
        print("You are about to trade with REAL MONEY.")
        print("This is NOT RECOMMENDED for automated trading.")
        print("="*60)
        
        confirm = input("Type 'I UNDERSTAND THE RISK' to continue: ")
        if confirm != "I UNDERSTAND THE RISK":
            print("Aborted.")
            return False
    elif not sandbox:
        log.warning("⚠️ Running in live mode with --yes flag (confirmation skipped)")
    
    await run_bot(config_path, sandbox=sandbox)
    return True


def main():
    args = parse_args()
    
    # Determine sandbox mode
    sandbox = not args.live
    
    if args.live:
        log.warning("⚠️ LIVE TRADING MODE ENABLED - REAL MONEY AT RISK")
    else:
        log.info("🧪 Sandbox mode - paper trading only")
    
    # Run appropriate mode
    if args.status:
        success = asyncio.run(check_status(sandbox=sandbox))
        sys.exit(0 if success else 1)
    
    elif args.once:
        success = asyncio.run(run_once(args.config, sandbox=sandbox))
        sys.exit(0 if success else 1)
    
    else:
        # Continuous mode
        try:
            asyncio.run(run_continuous(args.config, sandbox=sandbox, skip_confirm=args.yes))
        except KeyboardInterrupt:
            log.info("Interrupted by user")
            sys.exit(0)


if __name__ == "__main__":
    main()
