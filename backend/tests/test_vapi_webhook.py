from datetime import date, timedelta

HEADERS = {"x-vapi-secret": "test-secret"}


async def test_rejects_missing_secret(client):
    resp = await client.post("/api/v1/webhooks/vapi", json={"message": {"type": "status-update"}})
    assert resp.json() == {"error": "unauthorized"}


async def test_assistant_request_returns_dynamic_config(client, test_property):
    body = {
        "message": {
            "type": "assistant-request",
            "call": {
                "id": "call_abc",
                "phoneNumber": {"number": test_property.exophone},
                "customer": {"number": "+919876543210"},
            },
        }
    }
    resp = await client.post("/api/v1/webhooks/vapi", json=body, headers=HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assistant = data["assistant"]
    assert test_property.name in assistant["firstMessage"]
    assert str(test_property.id) in assistant["model"]["messages"][0]["content"]
    tool_names = {t["function"]["name"] for t in assistant["model"]["tools"]}
    assert tool_names == {
        "check_calendar",
        "get_pricing",
        "dispatch_technician",
        "send_whatsapp",
        "escalate_to_host",
        "negotiate_rate",
    }


async def test_assistant_request_unknown_number_returns_error(client):
    body = {"message": {"type": "assistant-request", "call": {"id": "call_xyz", "phoneNumber": {"number": "+910000000000"}}}}
    resp = await client.post("/api/v1/webhooks/vapi", json=body, headers=HEADERS)
    assert "error" in resp.json()


async def test_tool_calls_check_calendar_via_http(client, test_property):
    check_in = date.today() + timedelta(days=20)
    check_out = check_in + timedelta(days=2)
    body = {
        "message": {
            "type": "tool-calls",
            "call": {"id": "call_tools_1"},
            "toolCallList": [
                {
                    "id": "tc1",
                    "name": "check_calendar",
                    "arguments": {
                        "property_id": str(test_property.id),
                        "check_in": check_in.isoformat(),
                        "check_out": check_out.isoformat(),
                    },
                }
            ],
        }
    }
    resp = await client.post("/api/v1/webhooks/vapi", json=body, headers=HEADERS)
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results[0]["toolCallId"] == "tc1"
    assert "AVAILABLE" in results[0]["result"]


async def test_tool_calls_openai_style_shape_also_works(client, test_property):
    """Older/alternate Vapi payload shape: toolCalls[].function.{name,arguments}
    with arguments as a JSON string -- must keep working alongside toolCallList."""
    import json

    check_in = date.today() + timedelta(days=25)
    check_out = check_in + timedelta(days=1)
    body = {
        "message": {
            "type": "tool-calls",
            "call": {"id": "call_tools_2"},
            "toolCalls": [
                {
                    "id": "tc-openai-1",
                    "function": {
                        "name": "check_calendar",
                        "arguments": json.dumps(
                            {
                                "property_id": str(test_property.id),
                                "check_in": check_in.isoformat(),
                                "check_out": check_out.isoformat(),
                            }
                        ),
                    },
                }
            ],
        }
    }
    resp = await client.post("/api/v1/webhooks/vapi", json=body, headers=HEADERS)
    results = resp.json()["results"]
    assert results[0]["toolCallId"] == "tc-openai-1"
    assert "AVAILABLE" in results[0]["result"]


async def test_tool_calls_invalid_args_returns_graceful_message(client, test_property):
    body = {
        "message": {
            "type": "tool-calls",
            "call": {"id": "call_tools_3"},
            "toolCallList": [{"id": "tc-bad", "name": "get_pricing", "arguments": {"property_id": str(test_property.id)}}],
        }
    }
    resp = await client.post("/api/v1/webhooks/vapi", json=body, headers=HEADERS)
    assert resp.status_code == 200
    result = resp.json()["results"][0]["result"]
    assert "missing some details" in result.lower()


async def test_end_of_call_report_finalizes_session(client, test_property):
    assistant_request_body = {
        "message": {
            "type": "assistant-request",
            "call": {"id": "call_finalize", "phoneNumber": {"number": test_property.exophone}},
        }
    }
    await client.post("/api/v1/webhooks/vapi", json=assistant_request_body, headers=HEADERS)

    report_body = {
        "message": {
            "type": "end-of-call-report",
            "call": {"id": "call_finalize"},
            "transcript": "hello world",
            "summary": "guest asked about availability",
        }
    }
    resp = await client.post("/api/v1/webhooks/vapi", json=report_body, headers=HEADERS)
    assert resp.json() == {"result": "ok"}
