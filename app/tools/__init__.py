"""VoiceMed AI — Tool handler registry."""

from app.tools.symptom_lookup import handle_symptom_lookup
from app.tools.drug_interaction import handle_drug_interaction
from app.tools.triage_assessment import handle_triage_assessment
from app.tools.soap_generator import handle_soap_note
from app.tools.appointment_scheduler import handle_book_appointment
from app.tools.emergency_alert import handle_emergency_alert

# Tool dispatch map — maps tool names from the agent config to handler functions
TOOL_HANDLERS = {
    "symptom_lookup": handle_symptom_lookup,
    "drug_interaction_check": handle_drug_interaction,
    "triage_assessment": handle_triage_assessment,
    "generate_soap_note": handle_soap_note,
    "book_appointment": handle_book_appointment,
    "emergency_alert": handle_emergency_alert,
}


async def dispatch_tool(tool_name: str, arguments: dict) -> dict:
    """Dispatch a tool call to the appropriate handler."""
    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        result = await handler(arguments)
        return result
    except Exception as e:
        return {"error": f"Tool execution failed: {str(e)}"}
