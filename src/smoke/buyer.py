"""Scripted x402 buyer for smoke testing.

Performs full x402 purchase flow without LLM:
1. Discover pricing from seller
2. Check balance
3. Purchase data via x402 token
4. Print result
"""
import logging
from payments_py import Payments, PaymentOptions

from src.config import NVM_API_KEY, NVM_ENVIRONMENT, SELLER_URL
from src.buy_impl import (
    discover_pricing_impl,
    check_balance_impl,
    purchase_data_impl,
    build_token_options,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    """Run smoke test buyer flow."""
    if not SELLER_URL:
        logger.error("❌ SELLER_URL not set in .env")
        logger.info("💡 Set SELLER_URL=http://localhost:3000 in .env")
        return

    logger.info("🚀 Starting smoke test buyer")
    logger.info(f"📍 Target seller: {SELLER_URL}")

    # Initialize Payments client
    payments = Payments(
        PaymentOptions(
            nvm_api_key=NVM_API_KEY,
            environment=NVM_ENVIRONMENT,
        )
    )

    # Step 1: Discover pricing
    logger.info("\n📋 Step 1: Discovering pricing...")
    pricing_data = discover_pricing_impl(SELLER_URL)

    if not pricing_data:
        logger.error("❌ Failed to discover pricing")
        return

    plan_id = pricing_data.get("planId", "")
    agent_id = pricing_data.get("agentId", "")
    tiers = pricing_data.get("tiers", {})

    logger.info(f"✅ Discovered plan: {plan_id}")
    logger.info(f"✅ Agent ID: {agent_id}")
    logger.info(f"✅ Tiers: {tiers}")

    if not plan_id:
        logger.error("❌ No plan ID in pricing response")
        return

    # Step 2: Check balance
    logger.info("\n💰 Step 2: Checking balance...")
    balance_data = check_balance_impl(payments, plan_id)

    if not balance_data:
        logger.error("❌ Failed to check balance")
        logger.info("💡 You may need to order the plan first:")
        logger.info(f"   payments.plans.order_plan('{plan_id}')")
        return

    balance = balance_data.get("balance", 0)
    is_subscriber = balance_data.get("isSubscriber", False)

    logger.info(f"✅ Balance: {balance} credits")
    logger.info(f"✅ Subscriber: {is_subscriber}")

    if balance <= 0:
        logger.warning("⚠️  Zero balance - attempting purchase anyway (may fail)")

    # Step 3: Purchase data
    logger.info("\n🛒 Step 3: Purchasing data...")
    test_query = "What is the best investment strategy for 2026?"

    result = purchase_data_impl(
        payments=payments,
        plan_id=plan_id,
        seller_url=SELLER_URL,
        query=test_query,
        agent_id=agent_id,
    )

    if not result:
        logger.error("❌ Purchase failed")
        return

    # Step 4: Print result
    logger.info("\n✅ Purchase successful!")
    logger.info(f"📊 Result: {result.get('result', {})}")
    logger.info(f"💳 Credits used: {result.get('credits_used', 0)}")

    logger.info("\n🎉 Smoke test completed successfully!")


if __name__ == "__main__":
    main()
