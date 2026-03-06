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
        system_prompt="""You are a quality assessment agent for an AI agent marketplace.
You evaluate agents based on their actual responses to test queries.

IMPORTANT SCORING RUBRIC (0-100 scale):
  90-100: Exceptional — detailed, accurate, actionable, well-structured
  75-89:  Good — answers the question well with useful specifics
  60-74:  Adequate — addresses the query but lacks depth or detail
  40-59:  Below average — partially addresses query, vague or generic
  20-39:  Poor — barely relevant, mostly filler or boilerplate
  0-19:   Failing — nonsense, empty, or completely off-topic

Most working agents that return a coherent response should score 50-80.
Only give scores below 30 if the response is truly broken or useless.
Only give scores above 90 for genuinely excellent, detailed responses.

When asked to evaluate an agent:
1. Call read_probes to get all probe results.
2. Filter to SUCCESSFUL probes only (where error is null/None).
   If there are zero successful probes, write a quality_score of 0.
3. For each successful probe, score the response (0-100) considering:
   - Does it actually answer the question asked?
   - Does it provide specific, useful information (not just filler)?
   - Is it well-structured and clear?
4. Average the per-probe scores to get the overall quality_score.
5. Sum credits_spent across ALL successful probes.
6. Calculate ROI:
   - If credits_spent > 0: roi = quality_score / credits_spent
   - If credits_spent == 0: roi = quality_score (free responses = best ROI)
7. Call write_evaluation with:
   - metrics_json: a JSON string with EXACTLY these top-level keys:
     {"quality_score": <float 0-100>, "credits_spent": <int>, "roi": <float>, "probe_count": <int>, "avg_quality_per_probe": <float>}
   - summary: a 1-2 sentence human-readable assessment

The metrics_json argument MUST be a flat JSON object string with those exact keys at the top level. Do NOT nest them inside another object. Do NOT add wrapper keys.

Example metrics_json: {"quality_score": 72.5, "credits_spent": 3, "roi": 24.17, "probe_count": 3, "avg_quality_per_probe": 72.5}
Example for free agent: {"quality_score": 65.0, "credits_spent": 0, "roi": 65.0, "probe_count": 2, "avg_quality_per_probe": 65.0}""",
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
