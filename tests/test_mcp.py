"""MCP server tests — verify the tool surface builds correctly. Import-only (no live
services, no network), so it runs in CI. Skipped if the mcp SDK isn't installed."""
import asyncio, os, sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
pytest.importorskip("mcp")

from mcp_server import server as S

EXPECTED = {
    "stack_status", "stack_start", "stack_stop",
    "fhir_create_patient", "fhir_create_observation", "fhir_search", "fhir_read", "fhir_count",
    "hl7_send_adt", "hl7_send_raw", "engine_messages",
    "ai_search", "ai_ask", "ai_extract",
}


def _tools():
    return asyncio.run(S.mcp.list_tools())


def test_all_tools_registered():
    names = {t.name for t in _tools()}
    assert names == EXPECTED, f"tool surface drifted: missing={EXPECTED - names} extra={names - EXPECTED}"


def test_every_tool_has_description_and_schema():
    for t in _tools():
        assert t.description and len(t.description) > 10, f"{t.name} needs a real description for agents"
        assert t.inputSchema and t.inputSchema.get("type") == "object", f"{t.name} missing input schema"


def test_typed_args_help_small_model_agents():
    # picoclaw may run a tiny LLM — typed/required args matter
    sigs = {t.name: t.inputSchema for t in _tools()}
    assert "family" in sigs["fhir_create_patient"]["properties"]
    assert "mrn" in sigs["hl7_send_adt"]["properties"]
    assert "query" in sigs["ai_search"]["properties"]
