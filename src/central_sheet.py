"""Central Sheet - SQLite-backed portfolio database (stub)."""


class CentralSheet:
    """Central database for agent portfolio management."""

    def __init__(self, db_path: str = "portfolio.db"):
        """Initialize the central sheet with SQLite database."""
        raise NotImplementedError("CentralSheet.__init__ not yet implemented")

    def write_agent(self, agent_id: str, name: str, url: str, plan_id: str) -> None:
        """Write or update an agent record."""
        raise NotImplementedError("CentralSheet.write_agent not yet implemented")

    def read_agents(self) -> list[dict]:
        """Read all agents."""
        raise NotImplementedError("CentralSheet.read_agents not yet implemented")

    def write_probe(self, agent_id: str, query: str, response: str, credits_used: int) -> None:
        """Write a probe result."""
        raise NotImplementedError("CentralSheet.write_probe not yet implemented")

    def read_probes(self, agent_id: str | None = None) -> list[dict]:
        """Read probe results, optionally filtered by agent_id."""
        raise NotImplementedError("CentralSheet.read_probes not yet implemented")

    def write_evaluation(self, agent_id: str, evaluator: str, result: dict) -> None:
        """Write an evaluation result."""
        raise NotImplementedError("CentralSheet.write_evaluation not yet implemented")

    def read_evaluations(self, agent_id: str | None = None) -> list[dict]:
        """Read evaluations, optionally filtered by agent_id."""
        raise NotImplementedError("CentralSheet.read_evaluations not yet implemented")

    def write_ledger(self, agent_id: str, credits_spent: int, credits_earned: int) -> None:
        """Write a ledger entry."""
        raise NotImplementedError("CentralSheet.write_ledger not yet implemented")

    def get_pnl(self) -> dict:
        """Get profit and loss summary."""
        raise NotImplementedError("CentralSheet.get_pnl not yet implemented")

    def read_portfolio(self) -> list[dict]:
        """Read the portfolio view."""
        raise NotImplementedError("CentralSheet.read_portfolio not yet implemented")

    def get_top_agents(self, limit: int = 10) -> list[dict]:
        """Get top performing agents."""
        raise NotImplementedError("CentralSheet.get_top_agents not yet implemented")
