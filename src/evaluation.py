"""Evaluation Pipeline - orchestrates evaluators (stub)."""
from typing import Protocol, Any


class Evaluator(Protocol):
    """Protocol for evaluators."""

    async def evaluate(self, agent_id: str, probes: list[dict]) -> dict[str, Any]:
        """Evaluate an agent based on probe results."""
        ...


class EvaluationPipeline:
    """Orchestrates multiple evaluators."""

    def __init__(self):
        """Initialize the evaluation pipeline."""
        raise NotImplementedError("EvaluationPipeline.__init__ not yet implemented")

    def register(self, name: str, evaluator: Evaluator) -> None:
        """Register an evaluator."""
        raise NotImplementedError("EvaluationPipeline.register not yet implemented")

    async def run(self, agent_id: str, probes: list[dict]) -> dict[str, Any]:
        """Run all registered evaluators."""
        raise NotImplementedError("EvaluationPipeline.run not yet implemented")
