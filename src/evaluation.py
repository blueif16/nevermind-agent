"""Evaluation Pipeline - orchestrates evaluators."""
import asyncio
import logging
from typing import Protocol, Any

from src.central_sheet import CentralSheet

logger = logging.getLogger(__name__)


class Evaluator(Protocol):
    """Protocol for scorer sub-agents.

    Each evaluator:
    1. Reads probes (and optionally other evaluations) from the sheet
    2. Produces metrics (any JSON-serializable dict)
    3. Writes results to the evaluations table via sheet.write_evaluation()
    """

    async def __call__(
        self,
        agent_id: str,
        sheet: CentralSheet,
        **kwargs: Any,
    ) -> None:
        ...


class EvaluationPipeline:
    """Registry of scorer sub-agents. Dispatches evaluation for an agent."""

    def __init__(self):
        """Initialize the evaluation pipeline."""
        self._evaluators: dict[str, Evaluator] = {}

    def register(self, name: str, evaluator: Evaluator) -> None:
        """Register a scorer sub-agent.

        Args:
            name: Unique evaluator name (written to evaluations.evaluator column)
            evaluator: Async callable matching the Evaluator protocol
        """
        self._evaluators[name] = evaluator
        logger.info(f"Registered evaluator: {name}")

    def unregister(self, name: str) -> None:
        """Unregister an evaluator."""
        self._evaluators.pop(name, None)

    @property
    def evaluator_names(self) -> list[str]:
        """Get list of registered evaluator names."""
        return list(self._evaluators.keys())

    async def run(self, agent_id: str, sheet: CentralSheet, **kwargs) -> None:
        """Run all registered evaluators for an agent.

        Evaluators run concurrently. Each writes its own rows to the
        evaluations table. Failures in one evaluator don't block others.
        """
        # Pre-check: only evaluate if there are successful probes
        probes = sheet.read_probes(agent_id=agent_id)
        successful = [p for p in probes if p["error"] is None]
        if not successful:
            logger.info(f"No successful probes for {agent_id}, skipping eval")
            sheet.update_agent_status(agent_id, "dead")
            return

        tasks = []
        for name, evaluator in self._evaluators.items():
            tasks.append(
                self._run_one(name, evaluator, agent_id, sheet, **kwargs)
            )

        await asyncio.gather(*tasks)
        sheet.update_agent_status(agent_id, "evaluated")
        logger.info(
            f"Evaluation complete for {agent_id}: "
            f"{len(tasks)} evaluators ran"
        )

    async def _run_one(
        self,
        name: str,
        evaluator: Evaluator,
        agent_id: str,
        sheet: CentralSheet,
        **kwargs,
    ) -> None:
        """Run a single evaluator with error handling."""
        try:
            await evaluator(agent_id, sheet, **kwargs)
        except Exception as e:
            logger.error(
                f"Evaluator '{name}' failed for {agent_id}: {e}",
                exc_info=True,
            )


# Global pipeline instance
pipeline = EvaluationPipeline()
