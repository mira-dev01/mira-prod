"""The 6 LLM tool function definitions, verbatim from MIRA_Tech_Architecture_Spec.pdf.

Used both to build the `tools` array on the transient assistant config
returned from the assistant-request webhook, and by
scripts/setup_vapi_assistant.py when registering a persistent assistant.
"""

CHECK_CALENDAR_TOOL = {
    "type": "function",
    "function": {
        "name": "check_calendar",
        "description": "Check if property is available for given dates",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {"type": "string"},
                "check_in": {"type": "string", "format": "date"},
                "check_out": {"type": "string", "format": "date"},
                "num_guests": {"type": "integer"},
            },
            "required": ["property_id", "check_in", "check_out"],
        },
    },
}

GET_PRICING_TOOL = {
    "type": "function",
    "function": {
        "name": "get_pricing",
        "description": "Get total price including base rate, cleaning fee, taxes",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {"type": "string"},
                "check_in": {"type": "string", "format": "date"},
                "check_out": {"type": "string", "format": "date"},
                "num_guests": {"type": "integer"},
                "apply_discounts": {"type": "boolean", "default": True},
            },
            "required": ["property_id", "check_in", "check_out", "num_guests"],
        },
    },
}

DISPATCH_TECHNICIAN_TOOL = {
    "type": "function",
    "function": {
        "name": "dispatch_technician",
        "description": "Call a local technician for physical issues",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {"type": "string"},
                "issue_type": {
                    "type": "string",
                    "enum": ["plumbing", "electrical", "ac", "wifi", "lock", "general"],
                },
                "urgency": {"type": "string", "enum": ["low", "medium", "high", "emergency"]},
                "guest_phone": {"type": "string"},
            },
            "required": ["property_id", "issue_type", "urgency"],
        },
    },
}

SEND_WHATSAPP_TOOL = {
    "type": "function",
    "function": {
        "name": "send_whatsapp",
        "description": "Send WhatsApp message to guest or host",
        "parameters": {
            "type": "object",
            "properties": {
                "phone": {"type": "string"},
                "message": {"type": "string"},
                "template_name": {"type": "string"},
            },
            "required": ["phone", "message"],
        },
    },
}

ESCALATE_HOST_TOOL = {
    "type": "function",
    "function": {
        "name": "escalate_to_host",
        "description": "Escalate urgent issue to property host",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {"type": "string"},
                "reason": {"type": "string"},
                "urgency": {"type": "string", "enum": ["low", "medium", "high", "emergency"]},
                "guest_phone": {"type": "string"},
                "call_summary": {"type": "string"},
            },
            "required": ["property_id", "reason", "urgency"],
        },
    },
}

NEGOTIATE_RATE_TOOL = {
    "type": "function",
    "function": {
        "name": "negotiate_rate",
        "description": "Calculate negotiated rate based on rules and occupancy",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {"type": "string"},
                "check_in": {"type": "string", "format": "date"},
                "check_out": {"type": "string", "format": "date"},
                "num_guests": {"type": "integer"},
                "guest_offer": {"type": "number"},
                "guest_loyalty": {"type": "string", "enum": ["new", "returning", "frequent"]},
            },
            "required": ["property_id", "check_in", "check_out", "guest_offer"],
        },
    },
}

ALL_TOOLS = [
    CHECK_CALENDAR_TOOL,
    GET_PRICING_TOOL,
    DISPATCH_TECHNICIAN_TOOL,
    SEND_WHATSAPP_TOOL,
    ESCALATE_HOST_TOOL,
    NEGOTIATE_RATE_TOOL,
]
