"""Quality judge evaluator - LLM-based quality scoring with ROI calculation."""
import asyncio
import json
from strands import Agent, tool
from strands.models import BedrockModel

from src.central_sheet import CentralSheet


def create_quality_judge(model: BedrockModel) -> callable:
    """Factory: creates a quality_judge evaluator bound to a model.

    Returns an async callable matching the Evaluator protocol.

    Args:
        model: BedrockModel instance for quality assessment

    Returns:
        Async evaluator function that reads probes, scores quality, and writes evaluation
    """

    @tool
    def read_probes(agent_id: str) -> dict:
        """Read all successful probe results for an agent."""
        # Injected at call time via closure
        return {"probes": _sheet.read_probes(agent_id=agent_id)}

    @tool
    def write_evaluation(agent_id: str, metrics_json: str, summary: str) -> dict:
        """Write evaluation results for an agent."""
        metrics = json.loads(metrics_json)
        _sheet.write_evaluation(
            agent_id=agent_id,
            evaluator="quality_judge",
            metrics=metrics,
            summary=summary,
        )
        return {"status": "ok"}

    scorer = Agent(
        model=model,
        tools=[read_probes, write_evaluation],
        system_prompt="""You are a quality assessment agent.
You evaluate agents based on their actual responses to test queries.

When asked to evaluate an agent:
1. Call read_probes to get all probe results.
2. For each successful probe, assess the response quality on these dimensions:
   - Relevance: Does the response address the query?
   - Completeness: Is the answer thorough?
   - Accuracy: Is the information correct?
   - Clarity: Is it well-structured and understandable?
3. Calculate an overall quality_score (0-100 scale).
4. Calculate total credits_spent across all probes.
5. Calculate ROI: quality_score / credits_spent (higher is better).
6. Produce your metrics as a JSON object with these required fields:
   - quality_score: float (0-100)
   - credits_spent: int
   - roi: float (quality_score / credits_spent)
   - probe_count: int
   - avg_quality_per_probe: float
7. Call write_evaluation with your metrics JSON and a human-readable summary.

Your metrics MUST include a 'roi' field: quality_score / credits_spent.
Beyond that, you may add additional metrics as needed.""",
    )

    # Closure variable for tools to access
    _sheet = None

    async def quality_judge_evaluator(
        agent_id: str, sheet: CentralSheet, **kwargs
    ) -> None:
        """Evaluate agent quality using LLM-based assessment.

        Args:
            agent_id: Agent identifier
            sheet: CentralSheet instance
            **kwargs: Additional arguments (unused)
        """
        nonlocal _sheet
        _sheet = sheet
        await asyncio.to_thread(
            scorer, f"Evaluate agent {agent_id}. Read its probes and score them."
        )

    return quality_judge_evaluator
