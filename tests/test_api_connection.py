"""
Test API Connection

Verifies that the Kalshi API client can connect to the sandbox
and fetch market data.

Run with: pytest tests/test_api_connection.py -v
"""
import pytest
import asyncio
import os
from pathlib import Path

# Ensure we can import from src
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def api_credentials_available():
    """Check if API credentials are available."""
    from dotenv import load_dotenv
    
    env_path = Path.home() / ".openclaw" / ".env"
    load_dotenv(env_path)
    
    api_key = os.getenv("KALSHI_API_KEY")
    key_path = Path.home() / ".openclaw" / ".secrets" / "kalshi_private_key.pem"
    
    if not api_key:
        pytest.skip("KALSHI_API_KEY not set")
    if not key_path.exists():
        pytest.skip(f"Private key not found: {key_path}")
    
    return True


@pytest.mark.asyncio
async def test_client_creation(api_credentials_available):
    """Test that we can create a client."""
    from src.api.kalshi_client import create_client_from_env
    
    client = create_client_from_env(sandbox=True)
    assert client is not None
    assert client.sandbox is True
    await client.close()


@pytest.mark.asyncio
async def test_api_connection(api_credentials_available):
    """Test that we can connect to the sandbox API."""
    from src.api.kalshi_client import create_client_from_env
    
    async with create_client_from_env(sandbox=True) as client:
        # Fetch exchange status
        status = await client.get_exchange_status()
        assert status is not None
        print(f"Exchange status: {status}")


@pytest.mark.asyncio
async def test_get_balance(api_credentials_available):
    """Test fetching account balance."""
    from src.api.kalshi_client import create_client_from_env
    
    async with create_client_from_env(sandbox=True) as client:
        balance = await client.get_balance()
        assert balance is not None
        print(f"Balance: {balance}")


@pytest.mark.asyncio
async def test_get_markets(api_credentials_available):
    """Test fetching markets."""
    from src.api.kalshi_client import create_client_from_env
    
    async with create_client_from_env(sandbox=True) as client:
        response = await client.get_markets(status="open", limit=5)
        
        assert "markets" in response
        markets = response["markets"]
        
        print(f"Found {len(markets)} markets")
        
        if markets:
            market = markets[0]
            print(f"Sample market: {market.get('ticker')} - {market.get('title', '')[:50]}")
            
            # Test fetching orderbook
            ticker = market.get("ticker")
            if ticker:
                orderbook = await client.get_orderbook(ticker)
                assert orderbook is not None
                print(f"Orderbook: {orderbook}")


@pytest.mark.asyncio
async def test_config_loading():
    """Test configuration loading."""
    from src.config import Config
    
    config = Config.load("config.yaml")
    
    assert config.sandbox is True
    assert config.risk.max_total_capital == 200.0
    assert config.risk.max_position_size == 10.0
    assert len(config.strategies) >= 3


@pytest.mark.asyncio
async def test_paper_trader_init():
    """Test paper trader initialization."""
    from src.config import Config
    from src.paper_trader import PaperTrader
    
    config = Config.load("config.yaml")
    trader = PaperTrader(config)
    
    # Initialize portfolios
    trader.initialize_portfolio("TestStrategy", 100.0)
    
    portfolio = trader.get_portfolio("TestStrategy")
    assert portfolio is not None
    assert portfolio.current_capital == 100.0
    assert portfolio.available_capital == 100.0


@pytest.mark.asyncio
async def test_strategy_signal():
    """Test strategy signal generation."""
    from src.strategies.mean_reversion import MeanReversionStrategy
    
    config = {
        "extreme_threshold": 0.95,
        "min_threshold": 0.05,
        "exit_target": 0.50,
        "base_position_size": 5.0,
        "max_position_size": 10.0,
    }
    
    strategy = MeanReversionStrategy(config)
    
    # Test extreme high price (should generate NO signal)
    market_data = {
        "market_id": "TEST-MARKET",
        "yes_price": 0.98,
        "no_price": 0.02,
    }
    
    signal = strategy.analyze(market_data)
    assert signal is not None
    assert signal.side == "no"  # Betting against extreme YES
    
    # Test normal price (no signal)
    market_data["yes_price"] = 0.50
    signal = strategy.analyze(market_data)
    assert signal is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
