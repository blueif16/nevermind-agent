"""FastAPI server for portfolio manager agent (stub)."""
from fastapi import FastAPI
import uvicorn

app = FastAPI()


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


def main():
    """Run the server."""
    uvicorn.run(app, host="0.0.0.0", port=3000)


if __name__ == "__main__":
    main()
