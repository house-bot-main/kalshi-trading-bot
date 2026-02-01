"""
Kalshi API Client with RSA signature authentication.

IMPORTANT: This client defaults to SANDBOX mode. Production trading
must be explicitly enabled and is strongly discouraged for this bot.
"""
import os
import time
import base64
import hashlib
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
import structlog
import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.backends import default_backend

log = structlog.get_logger()

# API URLs - SANDBOX IS DEFAULT
# Note: Kalshi migrated production API in late 2025
SANDBOX_BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"
PRODUCTION_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

# WebSocket URLs
SANDBOX_WS_URL = "wss://demo-api.kalshi.co/trade-api/ws/v2"
PRODUCTION_WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"


@dataclass
class RateLimiter:
    """Simple rate limiter for API calls."""
    calls_per_second: float = 10.0
    last_call: float = 0.0
    
    async def wait(self):
        """Wait if needed to respect rate limits."""
        now = time.time()
        elapsed = now - self.last_call
        min_interval = 1.0 / self.calls_per_second
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        self.last_call = time.time()


class KalshiClient:
    """
    Async Kalshi API client with RSA signature authentication.
    
    CRITICAL: Defaults to SANDBOX mode. This bot should NEVER use
    production API for automated trading without explicit review.
    """
    
    def __init__(
        self,
        api_key_id: str,
        private_key_path: str,
        sandbox: bool = True,  # DEFAULT TO SANDBOX
    ):
        """
        Initialize Kalshi client.
        
        Args:
            api_key_id: Kalshi API key ID
            private_key_path: Path to RSA private key PEM file
            sandbox: If True (default), use sandbox API. NEVER set False for bot.
        """
        self.api_key_id = api_key_id
        self.sandbox = sandbox
        
        # SAFETY: Warn loudly if production mode
        if not sandbox:
            log.warning("⚠️ PRODUCTION MODE ENABLED - Real money at risk!")
        else:
            log.info("🧪 Sandbox mode - no real money")
        
        # Set URLs based on mode
        self.base_url = SANDBOX_BASE_URL if sandbox else PRODUCTION_BASE_URL
        self.ws_url = SANDBOX_WS_URL if sandbox else PRODUCTION_WS_URL
        
        # Load private key
        self.private_key = self._load_private_key(private_key_path)
        
        # HTTP client
        self._client: Optional[httpx.AsyncClient] = None
        
        # Rate limiting
        self.rate_limiter = RateLimiter(calls_per_second=8.0)  # Conservative
        
        # Session/token (Kalshi uses tokens that expire)
        self._token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
        
    def _load_private_key(self, key_path: str) -> rsa.RSAPrivateKey:
        """Load RSA private key from PEM file."""
        path = Path(key_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Private key not found: {path}")
        
        with open(path, "rb") as f:
            private_key = serialization.load_pem_private_key(
                f.read(),
                password=None,
                backend=default_backend()
            )
        log.info("Loaded private key", path=str(path))
        return private_key
    
    def _sign_request(self, timestamp: str, method: str, path: str) -> str:
        """
        Sign a request using RSA-SHA256 with PSS padding.
        
        Kalshi signature format: timestamp + method + path (without query params)
        """
        # Strip query parameters from path before signing
        path_without_query = path.split('?')[0]
        message = f"{timestamp}{method}{path_without_query}"
        
        # Kalshi requires PSS padding, not PKCS1v15
        signature = self.private_key.sign(
            message.encode('utf-8'),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        return base64.b64encode(signature).decode('utf-8')
    
    def _get_auth_headers(self, method: str, path: str) -> Dict[str, str]:
        """Generate authentication headers for a request."""
        # Timestamp in milliseconds
        timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        
        # Sign the request
        signature = self._sign_request(timestamp, method.upper(), path)
        
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Make an authenticated request to Kalshi API."""
        await self.rate_limiter.wait()
        
        path = f"/trade-api/v2{endpoint}"
        url = f"{self.base_url}{endpoint}"
        headers = self._get_auth_headers(method.upper(), path)
        
        client = await self._get_client()
        
        try:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=json_data,
            )
            
            if response.status_code == 429:
                log.warning("Rate limited, waiting...")
                await asyncio.sleep(1.0)
                return await self._request(method, endpoint, params, json_data)
            
            response.raise_for_status()
            return response.json()
            
        except httpx.HTTPStatusError as e:
            log.error(
                "API error",
                status=e.response.status_code,
                body=e.response.text[:500],
                endpoint=endpoint,
            )
            raise
    
    # ==================== Market Data ====================
    
    async def get_markets(
        self,
        status: str = "open",
        limit: int = 100,
        cursor: Optional[str] = None,
        series_ticker: Optional[str] = None,
        event_ticker: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get list of markets.
        
        Args:
            status: Filter by status (open, closed, settled)
            limit: Max results per page (1-200)
            cursor: Pagination cursor
            series_ticker: Filter by series
            event_ticker: Filter by event
        """
        params = {"status": status, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
            
        return await self._request("GET", "/markets", params=params)
    
    async def get_market(self, ticker: str) -> Dict[str, Any]:
        """Get details for a specific market."""
        return await self._request("GET", f"/markets/{ticker}")
    
    async def get_orderbook(self, ticker: str, depth: int = 10) -> Dict[str, Any]:
        """
        Get order book for a market.
        
        Args:
            ticker: Market ticker
            depth: Number of price levels (default 10)
        """
        return await self._request(
            "GET", 
            f"/markets/{ticker}/orderbook",
            params={"depth": depth}
        )
    
    async def get_trades(
        self,
        ticker: str,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get recent trades for a market."""
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return await self._request("GET", f"/markets/{ticker}/trades", params=params)
    
    async def get_events(
        self,
        status: str = "open",
        limit: int = 100,
        with_nested_markets: bool = False,
    ) -> Dict[str, Any]:
        """Get list of events (groups of markets)."""
        params = {
            "status": status,
            "limit": limit,
            "with_nested_markets": with_nested_markets,
        }
        return await self._request("GET", "/events", params=params)
    
    async def get_series(self, limit: int = 100) -> Dict[str, Any]:
        """Get list of series (recurring event types)."""
        return await self._request("GET", "/series", params={"limit": limit})
    
    # ==================== Portfolio ====================
    
    async def get_balance(self) -> Dict[str, Any]:
        """Get account balance."""
        return await self._request("GET", "/portfolio/balance")
    
    async def get_positions(
        self,
        status: str = "open",
        limit: int = 100,
        cursor: Optional[str] = None,
        event_ticker: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get current positions.
        
        Args:
            status: Filter by position status
            limit: Max results
            cursor: Pagination cursor
            event_ticker: Filter by event
        """
        params = {"limit": limit}
        if status:
            params["settlement_status"] = status
        if cursor:
            params["cursor"] = cursor
        if event_ticker:
            params["event_ticker"] = event_ticker
        return await self._request("GET", "/portfolio/positions", params=params)
    
    async def get_fills(
        self,
        limit: int = 100,
        cursor: Optional[str] = None,
        ticker: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get order fills (executed trades)."""
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if ticker:
            params["ticker"] = ticker
        return await self._request("GET", "/portfolio/fills", params=params)
    
    # ==================== Orders ====================
    
    async def create_order(
        self,
        ticker: str,
        side: str,  # "yes" or "no"
        action: str,  # "buy" or "sell"
        count: int,  # Number of contracts
        type: str = "limit",  # "limit" or "market"
        yes_price: Optional[int] = None,  # Price in cents (1-99)
        no_price: Optional[int] = None,
        expiration_ts: Optional[int] = None,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Place an order.
        
        CRITICAL: This will place REAL orders in production mode!
        Always verify sandbox=True before calling.
        
        Args:
            ticker: Market ticker
            side: "yes" or "no"
            action: "buy" or "sell"
            count: Number of contracts
            type: Order type ("limit" or "market")
            yes_price: Limit price for YES contracts (in cents, 1-99)
            no_price: Limit price for NO contracts (in cents, 1-99)
            expiration_ts: Order expiration timestamp
            client_order_id: Your order ID for tracking
        """
        # SAFETY CHECK
        if not self.sandbox:
            log.warning("⚠️ PLACING REAL ORDER - Production mode!")
        
        order = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "type": type,
        }
        
        if yes_price is not None:
            order["yes_price"] = yes_price
        if no_price is not None:
            order["no_price"] = no_price
        if expiration_ts:
            order["expiration_ts"] = expiration_ts
        if client_order_id:
            order["client_order_id"] = client_order_id
        
        log.info("Creating order", order=order, sandbox=self.sandbox)
        return await self._request("POST", "/portfolio/orders", json_data=order)
    
    async def get_orders(
        self,
        ticker: Optional[str] = None,
        status: str = "resting",  # resting, pending, executed, canceled
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Get orders."""
        params = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if status:
            params["status"] = status
        return await self._request("GET", "/portfolio/orders", params=params)
    
    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an order."""
        log.info("Canceling order", order_id=order_id)
        return await self._request("DELETE", f"/portfolio/orders/{order_id}")
    
    async def batch_cancel_orders(
        self,
        ticker: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cancel multiple orders."""
        params = {}
        if ticker:
            params["ticker"] = ticker
        return await self._request("DELETE", "/portfolio/orders", params=params)
    
    # ==================== Utilities ====================
    
    async def get_exchange_status(self) -> Dict[str, Any]:
        """Get exchange status and schedule."""
        return await self._request("GET", "/exchange/status")
    
    async def close(self):
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


# ==================== Factory Function ====================

def create_client_from_env(sandbox: bool = True) -> KalshiClient:
    """
    Create KalshiClient using environment variables.
    
    Expects:
        KALSHI_API_KEY: API key ID
        Private key at: ~/.openclaw/.secrets/kalshi_private_key.pem
    
    Args:
        sandbox: Use sandbox API (default True, HIGHLY RECOMMENDED)
    """
    from dotenv import load_dotenv
    
    # Load from ~/.openclaw/.env
    env_path = Path.home() / ".openclaw" / ".env"
    load_dotenv(env_path)
    
    api_key = os.getenv("KALSHI_API_KEY")
    if not api_key:
        raise ValueError(f"KALSHI_API_KEY not found in {env_path}")
    
    private_key_path = Path.home() / ".openclaw" / ".secrets" / "kalshi_private_key.pem"
    
    return KalshiClient(
        api_key_id=api_key,
        private_key_path=str(private_key_path),
        sandbox=sandbox,
    )
