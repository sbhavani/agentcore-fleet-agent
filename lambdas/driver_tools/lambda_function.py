"""
Fleet driver compliance tools — Lambda target for Amazon Bedrock AgentCore Gateway.

One Lambda hosts three MCP tools:
  - get_driver_profile       raw record lookup   (in production: DynamoDB)
  - evaluate_driver_fitness  DETERMINISTIC rules engine — the agent must NOT
                             decide fitness itself
  - notify_fleet_owner       mock notification   (in production: SNS/SES)

The Gateway invokes this function once per tool call and names the requested
tool in context.client_context.custom["bedrockAgentCoreToolName"], prefixed
as "<target_name>___<tool_name>". Everything after the last "___" is our tool.

Response contract: return plain JSON that matches the tool's outputSchema.
"""

from datetime import date

# ---------------------------------------------------------------------------
# Mock "system of record". In production this is a DynamoDB/Aurora lookup.
# ---------------------------------------------------------------------------

DRIVERS = {
    "driver-47": {
        "driver_id": "driver-47",
        "name": "Maria Alvarez",
        "cdl_status": "valid",
        "cdl_expires": "2027-03-14",
        "medical_cert_expires": "2027-11-02",
        "drug_test_status": "negative",
        "hos_violation": False,
        "vehicle_inspection": "passed",
    },
    "driver-12": {
        "driver_id": "driver-12",
        "name": "James Okafor",
        "cdl_status": "valid",
        "cdl_expires": "2027-12-01",
        "medical_cert_expires": "2020-01-15",  # expired -> UNFIT
        "drug_test_status": "negative",
        "hos_violation": False,
        "vehicle_inspection": "passed",
    },
    "driver-88": {
        "driver_id": "driver-88",
        "name": "Chen Wei",
        "cdl_status": "valid",
        "cdl_expires": "2027-01-20",
        "medical_cert_expires": "2027-12-30",
        "drug_test_status": "negative",
        "hos_violation": True,                 # active HOS violation -> UNFIT
        "vehicle_inspection": "passed",
    },
    "driver-31": {
        "driver_id": "driver-31",
        "name": "Priya Nair",
        "cdl_status": "valid",
        "cdl_expires": "2027-05-09",
        "medical_cert_expires": None,          # missing -> NEEDS_REVIEW
        "drug_test_status": "unknown",
        "hos_violation": False,
        "vehicle_inspection": "unknown",
    },
}


def _today() -> date:
    return date.today()


def _expired(iso_date: str | None) -> bool:
    if not iso_date:
        return False
    return date.fromisoformat(iso_date) <= _today()


def get_driver_profile(driver_id: str) -> dict:
    record = DRIVERS.get(driver_id)
    if record is None:
        return {
            "found": False,
            "driver_id": driver_id,
            "known_driver_ids": sorted(DRIVERS.keys()),
        }
    return {"found": True, **record}


def evaluate_driver_fitness(driver_id: str) -> dict:
    """Deterministic rules engine. This — not the LLM — owns the decision."""
    record = DRIVERS.get(driver_id)
    if record is None:
        return {
            "driver_id": driver_id,
            "status": "UNKNOWN_DRIVER",
            "violations": [],
            "review_reasons": [],
            "known_driver_ids": sorted(DRIVERS.keys()),
        }

    violations: list[str] = []
    review_reasons: list[str] = []

    # CDL
    if record["cdl_status"] != "valid":
        violations.append(f"CDL status is '{record['cdl_status']}', not valid.")
    elif _expired(record["cdl_expires"]):
        violations.append(f"CDL expired on {record['cdl_expires']}.")

    # Medical certificate
    if record["medical_cert_expires"] is None:
        review_reasons.append("Medical certificate on file is missing.")
    elif _expired(record["medical_cert_expires"]):
        violations.append(
            f"Medical certificate expired on {record['medical_cert_expires']}."
        )

    # Drug & alcohol testing
    if record["drug_test_status"] == "positive":
        violations.append("Most recent drug/alcohol test was positive.")
    elif record["drug_test_status"] != "negative":
        review_reasons.append(
            f"Drug/alcohol test status is '{record['drug_test_status']}'."
        )

    # Hours of service
    if record["hos_violation"]:
        violations.append("Active hours-of-service (HOS) violation on record.")

    # Vehicle inspection
    if record["vehicle_inspection"] == "failed":
        violations.append("Most recent vehicle inspection failed.")
    elif record["vehicle_inspection"] != "passed":
        review_reasons.append(
            f"Vehicle inspection status is '{record['vehicle_inspection']}'."
        )

    if violations:
        status = "UNFIT_TO_DRIVE"
    elif review_reasons:
        status = "NEEDS_REVIEW"
    else:
        status = "FIT"

    return {
        "driver_id": driver_id,
        "name": record["name"],
        "status": status,
        "violations": violations,
        "review_reasons": review_reasons,
        "evaluated_on": _today().isoformat(),
        "note": "Deterministic evaluation by rules engine. Not an LLM judgment.",
    }


def notify_fleet_owner(driver_id: str, message: str) -> dict:
    """Mock notification. Production: publish to an SNS topic or send via SES."""
    return {
        "notification_id": f"notif-{driver_id}-{_today().isoformat()}",
        "driver_id": driver_id,
        "channel": "email",
        "delivered_to": "fleet-owner@example.com",
        "cc": "safety-manager@example.com",
        "message": message,
        "status": "sent",
    }


TOOLS = {
    "get_driver_profile": lambda event: get_driver_profile(event["driver_id"]),
    "evaluate_driver_fitness": lambda event: evaluate_driver_fitness(event["driver_id"]),
    "notify_fleet_owner": lambda event: notify_fleet_owner(
        event["driver_id"], event["message"]
    ),
}


def _requested_tool_name(context) -> str:
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    prefixed = custom.get("bedrockAgentCoreToolName", "")
    # Gateway prefixes the visible tool name with "<target_name>___"
    return prefixed.rsplit("___", 1)[-1]


def lambda_handler(event, context):
    tool_name = _requested_tool_name(context)
    print(f"tool={tool_name} event={event}")

    tool = TOOLS.get(tool_name)
    if tool is None:
        return {"error": f"Unknown tool '{tool_name}'", "event": event}

    try:
        return tool(event)
    except KeyError as exc:
        return {"error": f"Missing required input field: {exc}", "tool": tool_name}
