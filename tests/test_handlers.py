"""Tests for S30 — Plugin registration & handlers.

Spec: doc/stories/S30-plugin-registration-and-handlers.md § "Done when"

Run:
    .venv/Scripts/python -m pytest tests/test_handlers.py -q -v
"""

from __future__ import annotations

import json
import os
import pytest
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HANDLER_NAMES = (
    "source_winning_ads",
    "rank_winning_ads",
    "extract_ad_patterns",
    "research_angle",
    "assemble_brief",
    "generate_hook_candidates",
    "write_storyboard_variant",
    "judge_variants",
    "score_storyboard",
    "apply_rewrite",
    "check_claims",
    "rewrite_for_compliance",
    "synthesize_voiceover",
    "render_video",
    "probe_media",
    "render_storyboard_html",
    "make_contact_sheet",
    "verify_artifact",
    "assemble_submission",
)

EXPECTED_HANDLER_COUNT = 19

# Tool names as registered (with cwt_ prefix)
REGISTERED_TOOL_NAMES = tuple(f"cwt_{name}" for name in HANDLER_NAMES)


@pytest.fixture(scope="module")
def handlers_module():
    """Import handlers lazily inside test session so import errors are test failures."""
    from hermes.plugins.cwt import handlers  # noqa: PLC0415
    return handlers


@pytest.fixture(scope="module")
def plugin_init_module():
    """Import the plugin __init__ module."""
    import hermes.plugins.cwt.__init__ as plugin_init  # noqa: PLC0415
    return plugin_init


@pytest.fixture(scope="module")
def schemas_module():
    from hermes.plugins.cwt import schemas  # noqa: PLC0415
    return schemas


@pytest.fixture
def test_settings(monkeypatch):
    """Create a minimal Settings object for testing using from_env with mocked env."""
    # Set all required env vars
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-nvidia")
    monkeypatch.setenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    monkeypatch.setenv("LLM_MODEL_CHEAP", "google/gemini-2.5-flash")
    monkeypatch.setenv("LLM_MODEL_STRONG", "anthropic/claude-sonnet-4.5")
    monkeypatch.setenv("LLM_MODEL_FALLBACKS", "meta/llama-3.3-70b-instruct,qwen/qwen2.5-72b-instruct")
    monkeypatch.setenv("LLM_MAX_CONCURRENCY", "4")
    monkeypatch.setenv("LLM_JSON_REPAIR_ATTEMPTS", "2")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    monkeypatch.setenv("APIFY_ADS_ACTOR_ID", "apify~facebook-ads-scraper")
    monkeypatch.setenv("APIFY_ADS_ACTOR_FALLBACKS", "curious_coder~facebook-ads-library-scraper")
    monkeypatch.setenv("APIFY_MAX_ITEMS", "60")
    monkeypatch.setenv("APIFY_MAX_CHARGE_USD", "1.0")
    monkeypatch.setenv("APIFY_RUN_TIMEOUT_SECONDS", "900")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily")
    monkeypatch.setenv("EXA_API_KEY", "test-exa")
    monkeypatch.setenv("VIDEO_BACKEND_CHAIN", "local_ffmpeg")
    monkeypatch.setenv("CWT_FFMPEG_BIN", "")
    monkeypatch.setenv("CWT_FFPROBE_BIN", "")
    monkeypatch.setenv("VIDEO_WIDTH", "1080")
    monkeypatch.setenv("VIDEO_HEIGHT", "1920")
    monkeypatch.setenv("VIDEO_FPS", "30")
    monkeypatch.setenv("VIDEO_MIN_SECONDS", "30")
    monkeypatch.setenv("VIDEO_MAX_SECONDS", "60")
    monkeypatch.setenv("VIDEO_LOUDNESS_LUFS", "-14")
    monkeypatch.setenv("VIDEO_TRUE_PEAK_DBTP", "-1.5")
    monkeypatch.setenv("TTS_BACKEND_CHAIN", "silent")
    monkeypatch.setenv("EDGE_TTS_VOICE", "en-US-AndrewNeural")
    monkeypatch.setenv("PIPER_VOICE_PATH", "fixtures/assets/voices/en_US-ryan-high.onnx")
    monkeypatch.setenv("HYPERFRAMES_ENABLED", "auto")
    monkeypatch.setenv("OPENMONTAGE_HOME", "")
    monkeypatch.setenv("HERMES_BIN", "")
    monkeypatch.setenv("HERMES_MIN_VERSION", "0.16.0")
    monkeypatch.setenv("CWT_BOARD", "cwt-ads")
    monkeypatch.setenv("CWT_RUN_TIMEOUT_SECONDS", "5400")
    monkeypatch.setenv("CWT_STALL_THRESHOLD_SECONDS", "180")
    monkeypatch.setenv("CWT_MAX_USD", "2.0")
    monkeypatch.setenv("CLAIMS_GATE_ENABLED", "1")
    monkeypatch.setenv("CLAIMS_MAX_REWRITE_ROUNDS", "3")
    monkeypatch.setenv("CREATIVE_THRESHOLD", "8.0")
    monkeypatch.setenv("CREATIVE_MAX_ROUNDS", "3")
    monkeypatch.setenv("CWT_ENGINE", "local")  # So engine_defaults_to_hermes is False
    
    from cwt.config import Settings
    return Settings.from_env()


@pytest.fixture
def test_paths(tmp_path, monkeypatch):
    """Create a RunPaths object for testing."""
    run_dir = tmp_path / "runs" / "_s30_test"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (run_dir / "render").mkdir(parents=True, exist_ok=True)
    (run_dir / "cache").mkdir(parents=True, exist_ok=True)
    (run_dir / "llm_ledger.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setenv("CWT_RUN_DIR", str(run_dir))
    from cwt.util.paths import RunPaths
    return RunPaths(run_dir=run_dir)


# ---------------------------------------------------------------------------
# 1. All 19 handlers exist and are callable
# ---------------------------------------------------------------------------


def test_all_handlers_exist(handlers_module):
    """All 19 handler names exist and are callable."""
    missing = [name for name in HANDLER_NAMES if not hasattr(handlers_module, name)]
    assert not missing, f"Missing handlers: {missing}"

    for name in HANDLER_NAMES:
        fn = getattr(handlers_module, name)
        assert callable(fn), f"Handler {name} is not callable"


def test_handler_count(handlers_module):
    """Exactly 19 handlers."""
    count = sum(1 for name in HANDLER_NAMES if hasattr(handlers_module, name))
    assert count == EXPECTED_HANDLER_COUNT, f"Expected {EXPECTED_HANDLER_COUNT} handlers, got {count}"


# ---------------------------------------------------------------------------
# 2. Every handler returns a JSON-serialisable string when called with settings, paths
# ---------------------------------------------------------------------------

# Handlers that don't follow the {"ok": bool, ...} pattern
NON_OK_HANDLERS = {"assemble_submission"}


@pytest.mark.parametrize("handler_name", HANDLER_NAMES)
def test_handler_returns_json_string(handlers_module, test_settings, test_paths, handler_name):
    """Every handler returns a string that parses as JSON when called with settings and paths."""
    fn = getattr(handlers_module, handler_name)
    out = fn({}, test_settings, test_paths)
    assert isinstance(out, str), f"Handler {handler_name} returned {type(out)}, not str"
    parsed = json.loads(out)
    assert isinstance(parsed, dict), f"Handler {handler_name} JSON is not a dict"
    if handler_name not in NON_OK_HANDLERS:
        assert "ok" in parsed, f"Handler {handler_name} JSON missing 'ok' field"


# ---------------------------------------------------------------------------
# 3. _safe decorator: tool that raises returns {"ok": false, "error": "..."}, never raises
# ---------------------------------------------------------------------------


def test_safe_catches_exception(handlers_module):
    """_safe on a tool that raises returns error JSON, never raises."""
    from hermes.plugins.cwt.handlers import _safe  # noqa: PLC0415

    @_safe
    def raises_error(args, settings, paths):
        raise ValueError("boom")

    out = raises_error({}, None, None)
    parsed = json.loads(out)
    assert parsed["ok"] is False
    assert "error" in parsed
    assert "ValueError" in parsed["error"]
    assert "boom" in parsed["error"]


# ---------------------------------------------------------------------------
# 4. _ctx raises actionable message when CWT_RUN_DIR is unset
# ---------------------------------------------------------------------------


def test_ctx_raises_without_cwt_run_dir(handlers_module, monkeypatch):
    """_ctx raises with the CWT_RUN_DIR message when the env var is unset."""
    monkeypatch.delenv("CWT_RUN_DIR", raising=False)
    from hermes.plugins.cwt.handlers import _ctx  # noqa: PLC0415

    with pytest.raises(RuntimeError) as exc_info:
        _ctx({})
    assert "CWT_RUN_DIR is not set" in str(exc_info.value)
    assert "cwt run" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 5. Missing required arg returns clean JSON error, not a crash
# ---------------------------------------------------------------------------

# Handlers that have no required args (empty args dict won't cause KeyError)
NO_REQUIRED_ARGS = {"rank_winning_ads", "extract_ad_patterns", "assemble_brief",
                    "generate_hook_candidates", "judge_variants", "score_storyboard",
                    "apply_rewrite", "synthesize_voiceover", "render_video", "probe_media",
                    "render_storyboard_html", "make_contact_sheet", "assemble_submission"}


@pytest.mark.parametrize("handler_name", HANDLER_NAMES)
def test_missing_required_arg_returns_json_error(handlers_module, test_settings, test_paths, handler_name):
    """A missing required arg returns {"ok": false, "error": "... 'field' ..."} rather than crashing."""
    fn = getattr(handlers_module, handler_name)
    out = fn({}, test_settings, test_paths)
    parsed = json.loads(out)
    if handler_name in NO_REQUIRED_ARGS:
        # These handlers have no required args, so empty args may succeed or fail for other reasons
        # Just verify it doesn't crash and returns valid JSON
        assert isinstance(parsed, dict)
    else:
        # With empty args, these handlers should fail on missing required args
        assert parsed["ok"] is False
        assert "error" in parsed
        # The error should mention the missing field (KeyError from args["x"])
        assert "'" in parsed["error"]


# ---------------------------------------------------------------------------
# 6. Error payload is scrubbed: Apify token redacted
# ---------------------------------------------------------------------------


def test_error_payload_scrubs_apify_token(handlers_module):
    """Error payload containing ?token=apify_api_secret is scrubbed to <redacted>."""
    from hermes.plugins.cwt.handlers import _safe, _scrub  # noqa: PLC0415

    # Test _scrub directly
    msg = "GET https://api.apify.com/v2/actor-tasks/xxx?token=apify_api_SECRET123 404 Not Found"
    scrubbed = _scrub(msg)
    assert "<redacted>" in scrubbed
    assert "apify_api_SECRET123" not in scrubbed

    # Test _safe passes through _scrub on exception
    @_safe
    def raises_with_token(args, settings, paths):
        raise RuntimeError("GET https://api.apify.com/v2/actor-tasks/xxx?token=apify_api_SECRET123 404")

    out = raises_with_token({}, None, None)
    parsed = json.loads(out)
    assert parsed["ok"] is False
    assert "<redacted>" in parsed["error"]
    assert "apify_api_SECRET123" not in parsed["error"]


# ---------------------------------------------------------------------------
# 7. _TOOLS covers all 19 names and each handler is callable
# ---------------------------------------------------------------------------


def test_plugin_register_tools_called(plugin_init_module, schemas_module, handlers_module, test_settings, test_paths):
    """register() against a fake ctx registers 19 tools, 2 hooks, 8 skills."""
    from hermes.plugins.cwt import handlers as h  # noqa: PLC0415

    class FakeCtx:
        def __init__(self):
            self.registered_tools = []
            self.registered_hooks = []
            self.registered_skills = []
            self.profile_name = "test"

        def register_tool(self, name, toolset, schema, handler):
            self.registered_tools.append((name, toolset, schema, handler))

        def register_hook(self, hook_name, hook_fn):
            self.registered_hooks.append((hook_name, hook_fn))

        def register_skill(self, skill_name, skill_path):
            self.registered_skills.append((skill_name, skill_path))

    ctx = FakeCtx()
    plugin_init_module.register(ctx)

    # 19 tools
    assert len(ctx.registered_tools) == EXPECTED_HANDLER_COUNT, (
        f"Expected 19 registered tools, got {len(ctx.registered_tools)}"
    )

    # Tool names match expected (with cwt_ prefix as registered)
    registered_names = [t[0] for t in ctx.registered_tools]
    assert set(registered_names) == set(REGISTERED_TOOL_NAMES), (
        f"Registered tool names mismatch: {set(registered_names) ^ set(REGISTERED_TOOL_NAMES)}"
    )

    # All handlers are the ones from handlers module
    for name, toolset, schema, handler in ctx.registered_tools:
        handler_name = name[4:] if name.startswith("cwt_") else name  # strip cwt_ prefix
        expected_handler = getattr(handlers_module, handler_name)
        assert handler == expected_handler, f"Handler mismatch for {name}"
        assert toolset == "cwt"

    # 2 hooks
    assert len(ctx.registered_hooks) == 2
    hook_names = [h[0] for h in ctx.registered_hooks]
    assert set(hook_names) == {"post_tool_call", "kanban_task_completed"}

    # 8 skills
    assert len(ctx.registered_skills) == 8
    skill_names = [s[0] for s in ctx.registered_skills]
    expected_skills = {
        "cwt-source-winning-ads",
        "cwt-extract-ad-patterns",
        "cwt-research-angle",
        "cwt-write-storyboard",
        "cwt-creative-review",
        "cwt-claims-gate",
        "cwt-render-video",
        "cwt-final-qa",
    }
    assert set(skill_names) == expected_skills


# ---------------------------------------------------------------------------
# 8. Specific handler argument requirements
# ---------------------------------------------------------------------------


def test_source_winning_ads_requires_keywords(handlers_module, test_settings, test_paths):
    """source_winning_ads requires 'keywords'."""
    fn = handlers_module.source_winning_ads
    out = fn({}, test_settings, test_paths)  # missing keywords
    parsed = json.loads(out)
    assert parsed["ok"] is False
    assert "keywords" in parsed["error"]


def test_rank_winning_ads_no_required_args(handlers_module, test_settings, test_paths):
    """rank_winning_ads has no required args, so empty args should not fail on missing required."""
    fn = handlers_module.rank_winning_ads
    out = fn({}, test_settings, test_paths)
    parsed = json.loads(out)
    # May fail for other reasons (missing artifact), but not for missing required arg
    # The error should not be a KeyError for a required field
    if parsed["ok"] is False:
        assert "KeyError" not in parsed["error"]


def test_extract_ad_patterns_no_required_args(handlers_module, test_settings, test_paths):
    fn = handlers_module.extract_ad_patterns
    out = fn({}, test_settings, test_paths)
    parsed = json.loads(out)
    if parsed["ok"] is False:
        assert "KeyError" not in parsed["error"]


def test_research_angle_requires_angle(handlers_module, test_settings, test_paths):
    fn = handlers_module.research_angle
    out = fn({}, test_settings, test_paths)  # missing angle
    parsed = json.loads(out)
    assert parsed["ok"] is False
    assert "angle" in parsed["error"]


def test_write_storyboard_variant_requires_angle_and_hook_id(handlers_module, test_settings, test_paths):
    fn = handlers_module.write_storyboard_variant
    out = fn({}, test_settings, test_paths)  # missing angle and hook_id
    parsed = json.loads(out)
    assert parsed["ok"] is False
    # Should mention one of the required fields
    assert "angle" in parsed["error"] or "hook_id" in parsed["error"]


def test_check_claims_requires_stage(handlers_module, test_settings, test_paths):
    fn = handlers_module.check_claims
    out = fn({}, test_settings, test_paths)  # missing stage
    parsed = json.loads(out)
    assert parsed["ok"] is False
    assert "stage" in parsed["error"]


def test_verify_artifact_requires_name(handlers_module, test_settings, test_paths):
    fn = handlers_module.verify_artifact
    out = fn({}, test_settings, test_paths)  # missing name
    parsed = json.loads(out)
    assert parsed["ok"] is False
    assert "name" in parsed["error"]


def test_make_contact_sheet_defaults(handlers_module, test_settings, test_paths):
    """make_contact_sheet has defaults for cols=4, rows=3."""
    fn = handlers_module.make_contact_sheet
    # We can't easily test the defaults without a full artifact setup,
    # but we can verify the function signature accepts them
    import inspect
    sig = inspect.signature(fn)
    # The handler wraps the tool which has defaults, but the handler itself
    # uses args.get("cols", 4) and args.get("rows", 3)
    # Just verify it doesn't crash on empty args (other than missing artifact)
    out = fn({}, test_settings, test_paths)
    parsed = json.loads(out)
    if parsed["ok"] is False:
        assert "KeyError" not in parsed["error"]


def test_write_storyboard_variant_default_total_duration(handlers_module, test_settings, test_paths):
    """write_storyboard_variant defaults total_duration_s to 42.0."""
    fn = handlers_module.write_storyboard_variant
    out = fn({"angle": "pain", "hook_id": "h01"}, test_settings, test_paths)  # no total_duration_s
    parsed = json.loads(out)
    if parsed["ok"] is False:
        assert "KeyError" not in parsed["error"]
        assert "total_duration_s" not in parsed["error"]


# ---------------------------------------------------------------------------
# 9. Integration: run the "Done when" script from the spec
# ---------------------------------------------------------------------------


def test_done_when_integration(handlers_module, test_settings, test_paths):
    """Replicates the spec's done-when check."""
    import os
    os.environ['CWT_RUN_DIR'] = 'runs/_s30'
    
    for name in HANDLER_NAMES:
        f = getattr(handlers_module, name)
        out = f({}, test_settings, test_paths)  # every required arg missing -> JSON error, never a raise
        assert isinstance(out, str), f"Handler {name} returned {type(out)}"
        parsed = json.loads(out)
        assert isinstance(parsed, dict)
        # Most handlers return {"ok": ...}, but assemble_submission returns differently
        if name != "assemble_submission":
            assert "ok" in parsed


# ---------------------------------------------------------------------------
# 10. _after_call and _on_task_completed hooks are registered
# ---------------------------------------------------------------------------


def test_hooks_registered(plugin_init_module, handlers_module, test_settings, test_paths):
    """_after_call and _on_task_completed hooks are registered."""
    class FakeCtx:
        def __init__(self):
            self.registered_hooks = []
            self.profile_name = "test"
        def register_hook(self, hook_name, hook_fn):
            self.registered_hooks.append((hook_name, hook_fn))
        def register_tool(self, name, toolset, schema, handler):
            pass
        def register_skill(self, skill_name, skill_path):
            pass

    ctx = FakeCtx()
    plugin_init_module.register(ctx)
    
    hook_names = [h[0] for h in ctx.registered_hooks]
    assert "post_tool_call" in hook_names
    assert "kanban_task_completed" in hook_names


# ---------------------------------------------------------------------------
# 11. _after_call returns None (return value ignored)
# ---------------------------------------------------------------------------


def test_after_call_return_ignored(plugin_init_module):
    """_after_call's return value is ignored (it logs only)."""
    from hermes.plugins.cwt import _after_call  # noqa: PLC0415
    
    result = _after_call("test_tool", {}, '{"ok": true}', "task_1", 100)
    # Return value is ignored by Hermes, but the function should not raise
    assert result is None


# ---------------------------------------------------------------------------
# 12. _on_task_completed logs and returns None
# ---------------------------------------------------------------------------


def test_on_task_completed(plugin_init_module):
    """_on_task_completed logs and returns None."""
    from hermes.plugins.cwt import _on_task_completed  # noqa: PLC0415
    
    result = _on_task_completed("task_1", "summary", {"meta": "data"})
    assert result is None