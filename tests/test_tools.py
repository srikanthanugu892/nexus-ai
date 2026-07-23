"""Tests for tool registry — ensures all tools are properly registered."""

from nexus_ai.tools.registry import TOOL_DEFINITIONS, TOOL_IMPLEMENTATIONS


def test_all_tools_have_definitions():
    """Every implementation should have a matching schema definition."""
    defined_names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
    for impl_name in TOOL_IMPLEMENTATIONS:
        assert impl_name in defined_names, f"Tool '{impl_name}' has implementation but no schema definition"


def test_all_definitions_have_implementations():
    """Every schema definition should have a matching implementation."""
    for tool_def in TOOL_DEFINITIONS:
        name = tool_def["function"]["name"]
        assert name in TOOL_IMPLEMENTATIONS, f"Tool '{name}' has schema definition but no implementation"


def test_tool_definitions_are_valid():
    """All tool definitions should have required fields."""
    for tool_def in TOOL_DEFINITIONS:
        assert tool_def["type"] == "function"
        func = tool_def["function"]
        assert "name" in func
        assert "description" in func
        assert "parameters" in func
        assert func["parameters"]["type"] == "object"


def test_model_routing():
    """Simple questions should route to the fast model."""
    from nexus_ai.agent.orchestrator import _select_model, settings

    assert _select_model("who owns the payment gateway?") == settings.llm_model_fast
    assert _select_model("list services for platform team") == settings.llm_model_fast
    # Complex questions should use the powerful model
    assert _select_model("what's the impact of removing /refunds?") == settings.llm_model
    assert _select_model("show me the latest orders from the database") == settings.llm_model
