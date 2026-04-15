"""Contract tests for FastMCP tool wrappers in apt_domain_mcp.server.

Purpose: guard against the class of bug where an internal handler in
`tools/handlers.py` declares a parameter as optional but the corresponding
FastMCP tool wrapper in `server.py` still declares it required (or vice versa),
causing the MCP client-facing schema to drift from the backend contract.

History:
- `5789c01` made `handlers.search_regulation.query` optional.
- `server.py` wrapper was NOT updated, so MCP input schema kept `query` in
  `required`. `search_regulation(category="반려동물")` kept erroring with
  "Field required" until `18cd5ea` fixed the wrapper.
- These tests freeze the wrapper signatures as the authoritative contract and
  will fail if a future handler change drifts from the wrapper (or vice versa).

The tests do NOT need a database — `build_mcp()` only constructs the FastMCP
instance and registers tool callables. No network, no env vars required.
"""
from __future__ import annotations

import pytest

from apt_domain_mcp.server import build_mcp


def _input_schema(tool):
    """Return the tool's JSON schema dict regardless of SDK attr naming."""
    for attr in ("inputSchema", "input_schema"):
        schema = getattr(tool, attr, None)
        if schema is not None:
            return schema
    # Fall back to model_dump with protocol alias
    return tool.model_dump(by_alias=True).get("inputSchema", {})


@pytest.fixture
async def tools_by_name() -> dict:
    mcp = build_mcp()
    result = mcp.list_tools()
    # FastMCP.list_tools() may be sync or async depending on SDK version.
    if hasattr(result, "__await__"):
        result = await result
    return {t.name: t for t in result}


EXPECTED_TOOLS = {
    "list_complexes",
    "get_complex_info",
    "search_regulation",
    "get_regulation_article",
    "list_regulation_revisions",
    "search_meeting_decisions",
    "get_meeting_detail",
    "get_wiki_page",
}

# Tools that are tenant-scoped must always require complex_id
TENANT_SCOPED_TOOLS = EXPECTED_TOOLS - {"list_complexes"}


async def test_all_expected_tools_registered(tools_by_name):
    got = set(tools_by_name.keys())
    missing = EXPECTED_TOOLS - got
    extra = got - EXPECTED_TOOLS
    assert not missing, f"missing tools: {missing}"
    assert not extra, f"unexpected tools: {extra}"


async def test_tenant_scoped_tools_require_complex_id(tools_by_name):
    for name in TENANT_SCOPED_TOOLS:
        schema = _input_schema(tools_by_name[name])
        required = set(schema.get("required", []))
        assert "complex_id" in required, (
            f"{name!r} must declare complex_id as required. "
            f"current required={sorted(required)}"
        )


async def test_search_regulation_query_and_category_optional(tools_by_name):
    """18cd5ea regression guard: query and category are both optional.
    The handler enforces 'at least one of them' at call time."""
    schema = _input_schema(tools_by_name["search_regulation"])
    required = set(schema.get("required", []))
    props = schema.get("properties", {}) or {}

    assert required == {"complex_id"}, (
        f"search_regulation required must be exactly {{complex_id}}, got {required}"
    )
    for field in ("query", "category", "version", "limit"):
        assert field in props, f"search_regulation missing property {field!r}"


async def test_get_regulation_article_requires_complex_id_and_article_number(tools_by_name):
    schema = _input_schema(tools_by_name["get_regulation_article"])
    required = set(schema.get("required", []))
    assert {"complex_id", "article_number"} <= required, (
        f"get_regulation_article required must include complex_id and article_number, "
        f"got {required}"
    )


async def test_get_wiki_page_requires_complex_id_and_topic(tools_by_name):
    schema = _input_schema(tools_by_name["get_wiki_page"])
    required = set(schema.get("required", []))
    assert {"complex_id", "topic"} <= required


async def test_search_meeting_decisions_all_filters_optional(tools_by_name):
    """Only complex_id should be required. Query, category, result, date_from,
    date_to, limit are all filters."""
    schema = _input_schema(tools_by_name["search_meeting_decisions"])
    required = set(schema.get("required", []))
    assert required == {"complex_id"}, (
        f"search_meeting_decisions required must be exactly {{complex_id}}, got {required}"
    )


async def test_get_meeting_detail_requires_complex_id_and_meeting_id(tools_by_name):
    schema = _input_schema(tools_by_name["get_meeting_detail"])
    required = set(schema.get("required", []))
    assert {"complex_id", "meeting_id"} <= required


async def test_list_complexes_has_no_required_args(tools_by_name):
    schema = _input_schema(tools_by_name["list_complexes"])
    required = set(schema.get("required", []))
    assert required == set(), f"list_complexes should have no required args, got {required}"
