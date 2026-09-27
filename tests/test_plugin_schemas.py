"""Tests for S29 — Plugin manifest & tool schemas.

Spec: doc/stories/S29-plugin-manifest-and-schemas.md § "Done when"

Run:
    .venv/Scripts/python -m pytest tests/test_plugin_schemas.py -q -v
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PLUGIN_YAML = pathlib.Path("hermes/plugins/cwt/plugin.yaml")
EXPECTED_SCHEMA_COUNT = 19

# Spec-mandated constant names (§ "Interface contract — FROZEN")
EXPECTED_CONSTANTS = [
    "SOURCE_WINNING_ADS",
    "RANK_WINNING_ADS",
    "EXTRACT_AD_PATTERNS",
    "RESEARCH_ANGLE",
    "ASSEMBLE_BRIEF",
    "GENERATE_HOOKS",
    "WRITE_STORYBOARD",
    "JUDGE_VARIANTS",
    "SCORE_STORYBOARD",
    "APPLY_REWRITE",
    "CHECK_CLAIMS",
    "REWRITE_COMPLIANCE",
    "SYNTHESIZE_VO",
    "RENDER_VIDEO",
    "PROBE_MEDIA",
    "RENDER_SB_HTML",
    "MAKE_CONTACT_SHEET",
    "VERIFY_ARTIFACT",
    "ASSEMBLE_SUBMISSION",
]


@pytest.fixture(scope="module")
def schemas_module():
    """Import schemas lazily inside test session so import errors are test failures."""
    from cwt.hermes.plugins.cwt import schemas  # noqa: PLC0415
    return schemas


@pytest.fixture(scope="module")
def all_schemas(schemas_module):
    return schemas_module.ALL_SCHEMAS


@pytest.fixture(scope="module")
def plugin_yaml():
    raw = PLUGIN_YAML.read_text(encoding="utf-8")
    return yaml.safe_load(raw)


# ---------------------------------------------------------------------------
# 1. Constant-name contract
# ---------------------------------------------------------------------------


def test_all_constants_importable(schemas_module):
    """All 19 constants exist and are importable by the exact names."""
    missing = [name for name in EXPECTED_CONSTANTS if not hasattr(schemas_module, name)]
    assert not missing, f"Missing constants: {missing}"


def test_all_schemas_tuple_length(all_schemas):
    """ALL_SCHEMAS has exactly 19 entries."""
    assert len(all_schemas) == EXPECTED_SCHEMA_COUNT, (
        f"Expected {EXPECTED_SCHEMA_COUNT} schemas, got {len(all_schemas)}"
    )


# ---------------------------------------------------------------------------
# 2. Schema structural rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("schema", [
    pytest.param(None, id="placeholder")  # filled below
])
def test_schema_structure_placeholder():
    pass  # real tests follow


def test_every_schema_has_cwt_prefix(all_schemas):
    """Every schema's function.name starts with 'cwt_'."""
    bad = [
        s["function"]["name"]
        for s in all_schemas
        if not s["function"]["name"].startswith("cwt_")
    ]
    assert not bad, f"Tool names without cwt_ prefix: {bad}"


def test_every_schema_is_function_type(all_schemas):
    """Every schema has type='function'."""
    bad = [s for s in all_schemas if s.get("type") != "function"]
    assert not bad, f"Schemas missing type='function': {[s['function']['name'] for s in bad]}"


def test_every_description_non_empty(all_schemas):
    """Every schema has a non-empty description."""
    bad = [
        s["function"]["name"]
        for s in all_schemas
        if not s["function"].get("description", "").strip()
    ]
    assert not bad, f"Schemas with empty description: {bad}"


def test_every_description_contains_when_not_to_call(all_schemas):
    """Every description must contain 'WHEN NOT TO CALL' (spec §10.2 line 4089)."""
    bad = [
        s["function"]["name"]
        for s in all_schemas
        if "WHEN NOT TO CALL" not in s["function"].get("description", "")
    ]
    assert not bad, (
        f"Schemas missing 'WHEN NOT TO CALL' section: {bad}\n"
        "A tool schema without a 'when not to call' section invites the model to "
        "call it at the wrong time — the most expensive failure mode in an agent pipeline."
    )


def test_every_parameters_blocks_additional_properties(all_schemas):
    """Every parameters block sets additionalProperties: False."""
    bad = [
        s["function"]["name"]
        for s in all_schemas
        if s["function"]["parameters"].get("additionalProperties") is not False
    ]
    assert not bad, (
        f"Schemas without additionalProperties: False: {bad}\n"
        "A model that invents an argument should get a clear rejection, "
        "not a silent **kwargs swallow."
    )


def test_every_parameters_is_object_type(all_schemas):
    """Every parameters block has type='object'."""
    bad = [
        s["function"]["name"]
        for s in all_schemas
        if s["function"]["parameters"].get("type") != "object"
    ]
    assert not bad, f"Schemas with non-object parameters type: {bad}"


# ---------------------------------------------------------------------------
# 3. Enum integrity
# ---------------------------------------------------------------------------


def test_research_angle_enum():
    """angle enum has exactly the 3 values from the spec."""
    from cwt.hermes.plugins.cwt import schemas  # noqa: PLC0415
    props = schemas.RESEARCH_ANGLE["function"]["parameters"]["properties"]
    assert props["angle"]["enum"] == ["pain", "unique_data", "crowd_effect"]


def test_write_storyboard_angle_enum():
    """write_storyboard angle enum has exactly the 3 values."""
    from cwt.hermes.plugins.cwt import schemas  # noqa: PLC0415
    props = schemas.WRITE_STORYBOARD["function"]["parameters"]["properties"]
    assert props["angle"]["enum"] == ["pain", "unique_data", "crowd_effect"]


def test_check_claims_stage_enum():
    """stage enum has exactly ['pre_render', 'post_render']."""
    from cwt.hermes.plugins.cwt import schemas  # noqa: PLC0415
    props = schemas.CHECK_CLAIMS["function"]["parameters"]["properties"]
    assert props["stage"]["enum"] == ["pre_render", "post_render"]


def test_verify_artifact_name_enum_matches_artifact_names():
    """verify_artifact.name enum == ARTIFACT_NAMES (generated, not hand-listed)."""
    from cwt.domain.artifacts import ARTIFACT_NAMES  # noqa: PLC0415
    from cwt.hermes.plugins.cwt import schemas  # noqa: PLC0415
    props = schemas.VERIFY_ARTIFACT["function"]["parameters"]["properties"]
    assert set(props["name"]["enum"]) == set(ARTIFACT_NAMES), (
        f"enum={set(props['name']['enum'])} != ARTIFACT_NAMES={set(ARTIFACT_NAMES)}"
    )
    assert len(props["name"]["enum"]) == len(ARTIFACT_NAMES)


# ---------------------------------------------------------------------------
# 4. plugin.yaml ↔ schemas.py set equality
# ---------------------------------------------------------------------------


def test_plugin_yaml_provides_tools_count(plugin_yaml):
    """plugin.yaml declares exactly 19 tool names."""
    declared = plugin_yaml.get("provides_tools", [])
    assert len(declared) == EXPECTED_SCHEMA_COUNT, (
        f"plugin.yaml declares {len(declared)} tools, expected {EXPECTED_SCHEMA_COUNT}"
    )


def test_plugin_yaml_tool_names_match_all_schemas(plugin_yaml, all_schemas):
    """Set of names in plugin.yaml equals set of function names in ALL_SCHEMAS."""
    declared = set(plugin_yaml.get("provides_tools", []))
    built = {s["function"]["name"] for s in all_schemas}
    assert declared == built, (
        f"plugin.yaml / schemas.ALL_SCHEMAS mismatch:\n"
        f"  In yaml but not schemas: {declared - built}\n"
        f"  In schemas but not yaml: {built - declared}"
    )


def test_plugin_yaml_requires_env(plugin_yaml):
    """plugin.yaml has the 4 required_env entries (spec lines 1768–1783)."""
    required = {e["name"] for e in plugin_yaml.get("requires_env", [])}
    expected = {"APIFY_TOKEN", "TAVILY_API_KEY", "EXA_API_KEY", "OPENROUTER_API_KEY"}
    assert required == expected, f"requires_env mismatch: {required} != {expected}"


def test_plugin_yaml_secret_flag(plugin_yaml):
    """All requires_env entries have secret: true."""
    for entry in plugin_yaml.get("requires_env", []):
        assert entry.get("secret") is True, (
            f"requires_env entry '{entry['name']}' is missing secret: true"
        )


def test_plugin_yaml_provides_hooks(plugin_yaml):
    """plugin.yaml declares both hooks."""
    hooks = set(plugin_yaml.get("provides_hooks", []))
    assert hooks == {"post_tool_call", "kanban_task_completed"}


# ---------------------------------------------------------------------------
# 5. Snapshot / smoke — run the "Done when" script from the spec
# ---------------------------------------------------------------------------


def test_done_when_assertion():
    """Replicates the spec's done-when one-liner check."""
    from cwt.hermes.plugins.cwt import schemas  # noqa: PLC0415

    manifest = yaml.safe_load(PLUGIN_YAML.read_text(encoding="utf-8"))
    declared = set(manifest["provides_tools"])
    built = {s["function"]["name"] for s in schemas.ALL_SCHEMAS}

    assert len(declared) == EXPECTED_SCHEMA_COUNT
    assert len(built) == EXPECTED_SCHEMA_COUNT
    assert declared == built, declared ^ built
    assert all(
        "WHEN NOT TO CALL" in s["function"]["description"]
        for s in schemas.ALL_SCHEMAS
    ), "Not all schemas contain 'WHEN NOT TO CALL'"
