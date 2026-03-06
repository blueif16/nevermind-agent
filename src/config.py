"""Configuration module with worktree-aware environment loading."""
import os
from pathlib import Path
from dotenv import load_dotenv
import subprocess


def _find_main_worktree() -> Path:
    """Find the main worktree directory."""
    try:
        result = subprocess.run(
            ["git", "worktree", "list"],
            capture_output=True,
            text=True,
            check=True
        )
        # First line is the main worktree
        main_line = result.stdout.strip().split("\n")[0]
        main_path = main_line.split()[0]
        return Path(main_path)
    except (subprocess.CalledProcessError, IndexError):
        # Fallback to current directory if not in a git worktree
        return Path.cwd()


# Load .env from main worktree
_main_worktree = _find_main_worktree()
_env_path = _main_worktree / ".env"
load_dotenv(_env_path)

# Export all environment variables as typed constants
NVM_API_KEY: str = os.getenv("NVM_API_KEY", "")
NVM_ENVIRONMENT: str = os.getenv("NVM_ENVIRONMENT", "sandbox")
NVM_PLAN_ID: str = os.getenv("NVM_PLAN_ID", "")
NVM_AGENT_ID: str = os.getenv("NVM_AGENT_ID", "")
AWS_REGION: str = os.getenv("AWS_REGION", "us-east-1")
NVM_PLAN_ID_USDC: str = os.getenv("NVM_PLAN_ID_USDC", "")
NVM_PLAN_ID_FIAT: str = os.getenv("NVM_PLAN_ID_FIAT", "")
OUR_HOST: str = os.getenv("OUR_HOST", "http://localhost:3000")
PORT: int = int(os.getenv("PORT", "3000"))
SCAN_INTERVAL: int = int(os.getenv("SCAN_INTERVAL", "300"))
SELLER_URL: str = os.getenv("SELLER_URL", "")
