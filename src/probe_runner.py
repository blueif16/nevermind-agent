"""Probe runner - executes test queries against discovered agents."""

import asyncio
import time
from typing import Any, Callable

from payments_py import Payments

from src.buy_impl import purchase_data_impl
from src.central_sheet import CentralSheet


DEFAULT_QUERIES = [
    "What services do you provide and at what price?",
    "Give me a sample analysis of current AI market trends",
    "Provide concrete data points on agent marketplace adoption",
]


async def run_probe(
    agent_info: dict,
    sheet: CentralSheet,
    payments: Payments,
    queries: list[str] | None = None,
    eval_callback: Callable[[str, CentralSheet], Any] | None = None,
) -> None:
    """Run probe queries against an agent and record results.

    Args:
        agent_info: Dict with agent_id, plan_id, url, name, etc.
        sheet: CentralSheet instance for recording results
        payments: Payments SDK instance
        queries: List of query strings (defaults to DEFAULT_QUERIES)
        eval_callback: Optional callback(agent_id, sheet) to trigger after probes
    """
    # Extract required fields
    agent_id = agent_info.get("agent_id", "")
    plan_id = agent_info.get("plan_id", "")
    url = agent_info.get("url", "")

    # Skip if missing required fields
    if not url or not plan_id:
        return

    # Use default queries if none provided
    if queries is None:
        queries = DEFAULT_QUERIES

    success_count = 0

    # Execute each query
    for query in queries:
        start_time = time.perf_counter()

        try:
            # Call sync purchase_data_impl via asyncio.to_thread
            result = await asyncio.to_thread(
                purchase_data_impl,
                payments,
                plan_id,
                url,
                query,
                agent_id,
            )

            end_time = time.perf_counter()
            latency_ms = (end_time - start_time) * 1000

            # Extract result fields
            status = result.get("status", "error")
            response_text = result.get("response", "")
            credits_used = result.get("credits_used", 0)

            # Calculate response bytes
            response_bytes = len(response_text.encode("utf-8")) if response_text else 0

            if status == "success":
                # Success case
                sheet.write_probe(
                    agent_id=agent_id,
                    query=query,
                    response=response_text,
                    credits_spent=credits_used,
                    latency_ms=latency_ms,
                    response_bytes=response_bytes,
                    http_status=200,
                    error=None,
                )

                # Write ledger entry for successful probe
                sheet.write_ledger(
                    direction="out",
                    credits=credits_used,
                    purpose="probe",
                    agent_id=agent_id,
                    detail=query,
                )

                success_count += 1

            elif status == "payment_required":
                # Payment required (HTTP 402)
                error_msg = result.get("content", [{}])[0].get("text", "Payment required")
                sheet.write_probe(
                    agent_id=agent_id,
                    query=query,
                    response="",
                    credits_spent=0,
                    latency_ms=latency_ms,
                    response_bytes=0,
                    http_status=402,
                    error=error_msg,
                )

            else:
                # Other error
                error_msg = result.get("content", [{}])[0].get("text", "Unknown error")
                sheet.write_probe(
                    agent_id=agent_id,
                    query=query,
                    response="",
                    credits_spent=0,
                    latency_ms=latency_ms,
                    response_bytes=0,
                    http_status=0,
                    error=error_msg,
                )

        except Exception as e:
            # Exception during probe execution
            end_time = time.perf_counter()
            latency_ms = (end_time - start_time) * 1000

            sheet.write_probe(
                agent_id=agent_id,
                query=query,
                response="",
                credits_spent=0,
                latency_ms=latency_ms,
                response_bytes=0,
                http_status=0,
                error=f"Exception: {str(e)}",
            )

    # Update agent status based on results
    if success_count > 0:
        sheet.update_agent_status(agent_id, "probed")
    else:
        sheet.update_agent_status(agent_id, "dead")

    # Trigger evaluation callback if provided
    if eval_callback is not None:
        eval_callback(agent_id, sheet)
