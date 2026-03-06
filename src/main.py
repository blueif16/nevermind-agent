"""FastAPI server for portfolio manager agent."""
import asyncio

from fastapi import FastAPI
from payments_py import Payments
from payments_py.common.types import PaymentOptions
import uvicorn

from src import config
from src.central_sheet import CentralSheet
from src.probe_runner import run_probe
from src.scanner import scan_loop

app = FastAPI()

# Global instances
sheet = CentralSheet("portfolio.db")
payments = Payments(
    PaymentOptions(
        nvm_api_key=config.NVM_API_KEY or "nvm:eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0OmFjY291bnQiLCJpc3MiOiJuZXZlcm1pbmVkIiwibzExeSI6InRlc3QtaGVsaWNvbmUta2V5In0.7r7Ca7BhamuEvqZfpIGuc5sRMSdCAMehoJ1TEQnqpw4",
        environment=config.NVM_ENVIRONMENT,
        headers={"ai-protocol": "strands"},
    )
)


@app.on_event("startup")
async def startup_event():
    """Start background scanner task."""
    asyncio.create_task(
        scan_loop(
            sheet,
            payments,
            run_probe,
            config.SCAN_INTERVAL,
            config.NVM_API_KEY,
            "agents_config.json",
        )
    )


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/portfolio")
async def portfolio():
    """Return portfolio data from CentralSheet."""
    return sheet.read_portfolio()


def main():
    """Run the server."""
    uvicorn.run(app, host="0.0.0.0", port=3000)


if __name__ == "__main__":
    main()
