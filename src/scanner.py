"""Scanner module - Discovery API integration and scan loop."""

import asyncio
import json
from pathlib import Path
from typing import Any, Callable

import httpx
from payments_py import Payments


async def discover_from_hackathon_api(nvm_api_key: str) -> list[dict]:
    """Discover agents from Nevermined Hackathon Discovery API.

    Returns list of agent dicts with fields:
    - agent_id (or endpointUrl as fallback)
    - name
    - url (from endpointUrl)
    - plan_id (first from planIds array)
    - pricing (dict)
    - tags (list)
    - category
    - team_name
    """
    url = "https://nevermined.ai/hackathon/register/api/discover?side=sell"
    headers = {"x-nvm-api-key": nvm_api_key}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        print(f"Discovery API error: {e}")
        return []

    # Handle error responses from API
    if isinstance(data, dict) and "error" in data:
        print(f"Discovery API returned error: {data.get('error')}")
        return []

    # Handle new API format with meta and sellers
    if isinstance(data, dict) and "sellers" in data:
        sellers = data["sellers"]
    elif isinstance(data, list):
        sellers = data
    else:
        print(f"Discovery API returned unexpected format: {type(data)}")
        return []

    agents = []
    for seller in sellers:
        # Agent ID fallback: use nvmAgentId or endpointUrl
        agent_id = seller.get("nvmAgentId") or seller.get("endpointUrl", "")
        if not agent_id:
            continue

        # Extract plan_id from planIds array (take first)
        plan_ids = seller.get("planIds", [])
        plan_id = plan_ids[0] if plan_ids else ""

        agents.append({
            "agent_id": agent_id,
            "name": seller.get("name", ""),
            "url": seller.get("endpointUrl", ""),
            "plan_id": plan_id,
            "pricing": seller.get("pricing", {}),
            "tags": seller.get("tags", []),
            "category": seller.get("category", ""),
            "team_name": seller.get("teamName", ""),
            "description": seller.get("description", ""),
        })

    return agents


async def discover_from_sdk(payments: Payments, agent_ids: list[str]) -> list[dict]:
    """Enrich agent metadata using Nevermined SDK.

    Extracts URL from endpoints array (POST method).
    Returns list of agent dicts with available fields.
    """
    agents = []

    for agent_id in agent_ids:
        try:
            # Run sync SDK call in thread pool
            agent_data = await asyncio.to_thread(
                payments.agents.get_agent, agent_id
            )

            # Extract URL from endpoints array
            url = ""
            endpoints = agent_data.get("endpoints", [])
            for endpoint in endpoints:
                if endpoint.get("method") == "POST":
                    url = endpoint.get("url", "")
                    break

            agents.append({
                "agent_id": agent_id,
                "name": agent_data.get("name", ""),
                "url": url,
                "plan_id": agent_data.get("plan_id", ""),
                "description": agent_data.get("description", ""),
            })
        except Exception as e:
            print(f"SDK enrichment failed for {agent_id}: {e}")
            continue

    return agents


def load_known_agents(config_path: str) -> list[dict]:
    """Load agents from agents_config.json.

    Expected format: array of objects with agent_id, name, url, plan_id.
    Returns empty list if file doesn't exist or is invalid.
    """
    path = Path(config_path)
    if not path.exists():
        return []

    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"Failed to load {config_path}: {e}")
        return []


async def probe_pricing(url: str) -> dict:
    """Probe agent's /pricing endpoint.

    Returns dict with:
    - status: 'success' or 'error'
    - plan_id: extracted plan ID (if available)
    - tiers: pricing tiers dict
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{url}/pricing")
            if response.status_code != 200:
                return {"status": "error", "tiers": {}}

            data = response.json()
            return {
                "status": "success",
                "plan_id": data.get("planId", ""),
                "tiers": data.get("tiers", {}),
            }
    except Exception as e:
        print(f"Pricing probe failed for {url}: {e}")
        return {"status": "error", "tiers": {}}


def _merge_agents(config: list[dict], sdk: list[dict], api: list[dict]) -> list[dict]:
    """Merge agent data with priority: config (lowest) → SDK → API (highest)."""
    merged = {}

    # Start with config (lowest priority)
    for agent in config:
        agent_id = agent.get("agent_id")
        if agent_id:
            merged[agent_id] = agent.copy()

    # Enrich with SDK data
    for agent in sdk:
        agent_id = agent.get("agent_id")
        if agent_id:
            if agent_id in merged:
                # Update existing with non-empty SDK values
                for key, value in agent.items():
                    if value:
                        merged[agent_id][key] = value
            else:
                merged[agent_id] = agent.copy()

    # Overwrite with API data (highest priority)
    for agent in api:
        agent_id = agent.get("agent_id")
        if agent_id:
            if agent_id in merged:
                # API overwrites all fields
                for key, value in agent.items():
                    if value:
                        merged[agent_id][key] = value
            else:
                merged[agent_id] = agent.copy()

    return list(merged.values())


async def scan_loop(
    sheet,
    payments: Payments,
    probe_callback: Callable,
    interval: int,
    nvm_api_key: str,
    config_path: str = "agents_config.json",
    eval_callback: Callable | None = None,
) -> None:
    """Main scanner loop.

    Discovers agents from API/SDK/config, diffs against known agents,
    probes pricing for new agents, spawns probe tasks, and re-probes
    agents with status='reeval'.

    Args:
        sheet: CentralSheet instance
        payments: Payments SDK instance
        probe_callback: async function(agent_info, sheet, payments, queries, eval_callback)
        interval: scan interval in seconds
        nvm_api_key: Nevermined API key for Discovery API
        config_path: path to agents_config.json
        eval_callback: Optional callback to trigger evaluation after probes
    """
    while True:
        try:
            print(f"[Scanner] Starting scan cycle...")

            # Discover from all sources
            api_agents = await discover_from_hackathon_api(nvm_api_key)
            config_agents = load_known_agents(config_path)

            # Extract agent IDs for SDK enrichment
            agent_ids = list({a.get("agent_id") for a in api_agents if a.get("agent_id")})
            sdk_agents = await discover_from_sdk(payments, agent_ids) if agent_ids else []

            # Merge with priority: config → SDK → API
            discovered = _merge_agents(config_agents, sdk_agents, api_agents)

            # Get known agents from sheet
            known = sheet.read_agents()
            known_ids = {a["agent_id"] for a in known}

            # Find new agents
            new_agents = [a for a in discovered if a.get("agent_id") not in known_ids]

            # Probe pricing for new agents and write to sheet
            for agent in new_agents:
                url = agent.get("url")
                if not url:
                    continue

                # Probe pricing
                pricing_result = await probe_pricing(url)
                if pricing_result["status"] == "success":
                    agent["pricing"] = pricing_result["tiers"]
                    # Update plan_id if available from pricing
                    if pricing_result.get("plan_id"):
                        agent["plan_id"] = pricing_result["plan_id"]

                # Write to sheet
                sheet.write_agent(
                    agent_id=agent["agent_id"],
                    name=agent.get("name", ""),
                    url=agent.get("url", ""),
                    plan_id=agent.get("plan_id", ""),
                    pricing=agent.get("pricing"),
                    tags=agent.get("tags", []),
                    description=agent.get("description", ""),
                    category=agent.get("category", ""),
                    team_name=agent.get("team_name", ""),
                )

                print(f"[Scanner] New agent discovered: {agent['agent_id']}")

                # Spawn probe task with eval callback
                asyncio.create_task(
                    probe_callback(agent, sheet, payments, None, eval_callback)
                )

            # Re-probe agents with status='reeval'
            reeval_agents = sheet.read_agents(status="reeval")
            for agent in reeval_agents:
                print(f"[Scanner] Re-probing agent: {agent['agent_id']}")
                asyncio.create_task(
                    probe_callback(agent, sheet, payments, None, eval_callback)
                )

            print(f"[Scanner] Scan complete. Found {len(new_agents)} new agents, {len(reeval_agents)} for re-eval.")

        except Exception as e:
            print(f"[Scanner] Error in scan cycle: {e}")

        # Wait for next cycle
        await asyncio.sleep(interval)
