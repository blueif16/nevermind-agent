"""Gate evaluator - binary pass/fail based on error rate."""
from src.central_sheet import CentralSheet


async def gate_evaluator(agent_id: str, sheet: CentralSheet, **kwargs) -> None:
    """Binary pass/fail gate. Marks agents with >50% error rate as dead.

    No LLM needed — pure data check.

    Args:
        agent_id: Agent identifier
        sheet: CentralSheet instance
        **kwargs: Additional arguments (unused)
    """
    probes = sheet.read_probes(agent_id=agent_id)
    if not probes:
        return

    total = len(probes)
    errors = sum(1 for p in probes if p["error"] is not None)
    success_rate = (total - errors) / total

    sheet.write_evaluation(
        agent_id=agent_id,
        evaluator="gate",
        metrics={
            "total_probes": total,
            "errors": errors,
            "success_rate": round(success_rate, 3),
            "passed": success_rate > 0.5,
        },
        summary=f"{'PASS' if success_rate > 0.5 else 'FAIL'}: "
                f"{total - errors}/{total} probes succeeded",
    )

    if success_rate <= 0.5:
        sheet.update_agent_status(agent_id, "dead")
