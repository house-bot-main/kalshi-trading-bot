# Kalshi Trading Bot

A multi-strategy paper trading bot for Kalshi prediction markets.

> **API Status:** ✅ Verified working (Production API at `api.elections.kalshi.com`)
> 
> **Note:** The production API migrated to a new URL in late 2025. Demo API requires separate credentials.

## Features

- **Multiple Strategies**: Mean Reversion, Momentum, Market Making
- **Paper Trading**: Simulates trades without real money
- **Performance Tracking**: Sharpe ratio, win rate, max drawdown, profit factor
- **Dynamic Capital Allocation**: Ranks strategies and allocates more to winners
- **SQLite Storage**: Persistent trade history and metrics

## ⚠️ Important: API Setup

**Kalshi has separate demo and production environments with different credentials.**

### For Paper Trading (Recommended)

1. Create a **demo account** at [demo.kalshi.com](https://demo.kalshi.com)
   - You can use fake information for demo
   - Use a real email you have access to

2. Generate API key in demo account settings

3. Save credentials:
   ```bash
   # Add to ~/.openclaw/.env
   KALSHI_API_KEY=your-demo-api-key-id
   
   # Save private key
   # ~/.openclaw/.secrets/kalshi_private_key.pem
   ```

### For Production (NOT RECOMMENDED for automated trading)

1. Create production account at [kalshi.com](https://kalshi.com)
2. Complete KYC verification
3. Generate API key in production account settings
4. Use `--live` flag (requires explicit confirmation)

## Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Test API connection
python main.py --status

# Run single scan cycle
python main.py --once

# Run continuous trading loop
python main.py
```

## Configuration

Edit `config.yaml` to customize:

- Risk limits (max position size, daily loss limit)
- Strategy parameters
- Scanner intervals
- Capital allocation weights

## Project Structure

```
kalshi-trading-bot/
├── main.py                 # CLI entry point
├── config.yaml             # Configuration
├── requirements.txt        # Dependencies
├── src/
│   ├── api/
│   │   └── kalshi_client.py    # Kalshi API client with RSA auth
│   ├── strategies/
│   │   ├── base.py             # Base strategy interface
│   │   ├── mean_reversion.py   # Fades extreme prices
│   │   ├── momentum.py         # Follows price trends
│   │   └── market_making.py    # Captures bid-ask spreads
│   ├── scanner.py              # Market data scanner
│   ├── paper_trader.py         # Paper trading engine
│   ├── performance.py          # Performance metrics & SQLite storage
│   ├── allocator.py            # Dynamic capital allocation
│   ├── orchestrator.py         # Main trading loop
│   └── config.py               # Configuration management
├── tests/
│   ├── test_api_minimal.py     # Minimal API test (no deps)
│   └── test_api_connection.py  # Full test suite
└── data/                       # SQLite database storage
```

## Risk Limits (Default)

- Total capital: $200
- Max per trade: $10
- Max daily loss: $20 (stops trading)
- Max concurrent positions: 10
- Max exposure: 20% of capital

## Strategies

### Mean Reversion
Bets against extreme prices, expecting reversion to the mean.
- Sell YES when price > 95¢
- Buy YES when price < 5¢

### Momentum
Follows price trends using moving average crossovers.
- Buys on upward momentum (short MA > long MA)
- Sells on downward momentum

### Market Making
Captures bid-ask spreads by providing liquidity.
- Places limit orders on both sides
- Profits from the spread

## Usage Examples

```bash
# Check connection and account status
python main.py --status

# Run single scan cycle (good for testing)
python main.py --once

# Run continuous paper trading
python main.py

# Use custom config
python main.py --config my_config.yaml

# Verbose logging
python main.py -v
```

## Development

```bash
# Run tests (requires pytest)
pytest tests/ -v

# Run minimal API test (no pytest needed)
python tests/test_api_minimal.py
```

## License

MIT
