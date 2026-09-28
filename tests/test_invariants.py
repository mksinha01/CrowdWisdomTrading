"""Cross-story invariants — the properties no single story owns (Story S35).

Each test here guards a place where the architecture rots *silently*:

- a card whose assignee has no profile never starts a worker (Rule K2)
- a tool advertised in plugin.yaml with no schema or no handler fails at call
  time, inside an agent turn, where the traceback is least useful
- a backend chain that does not terminate in ``local_ffmpeg`` renders nothing
  (Rule V4 — the whole offline contract rests on this)
- a stray ``subprocess.run`` loses the Windows fixes in ``util/subproc.py``
  (§8.1: that module is the single chokepoint, Rules W1/W4/W7/W9)
- a raw Apify field read outside ``clients/apify.py`` couples the whole codebase
  to an actor's private shape (§3.1 lines 466-468, §8.3 mapping table)
- a committed credential leaks the reviewer's tokens with the public repo

Spec: doc/video-ads-agent.md — Rule K2 (line 4927), Rule V4 (line 4789),
§8.1 (line 2029), §8.3 (lines 2378-2399), §3.1 (lines 466-468).

None of these need the network, ffmpeg, or Hermes. They are pure source and
data assertions, so they run in the default suite on a bare machine.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from cwt.bootstrap import PROFILE_NAMES, verify_dag_assignees
from cwt.config import ConfigError, Settings
from cwt.hermes.dag import DAG_SPEC

REPO_ROOT = Path(__file__).resolve().parents[1]

# Source trees that ship in the package or are executed as part of it.
# ``tests/`` is deliberately excluded: a test may legitimately shell out to
# exercise a script, and test fixtures carry fake secrets on purpose.
_SOURCE_ROOTS = ("src", "hermes", "scripts")


def _source_py_files() -> list[Path]:
    files: list[Path] = []
    for root in _SOURCE_ROOTS:
        files.extend(sorted((REPO_ROOT / root).rglob("*.py")))
    return files


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


# ===========================================================================
# 1. Rule K2 — every DAG card assignee resolves to a Hermes profile
# ===========================================================================


def test_rule_k2_every_dag_assignee_has_a_profile() -> None:
    """A card whose assignee is not an exact profile name sits on `ready` forever."""
    assignees = {card.assignee for card in DAG_SPEC}
    missing = sorted(assignees - set(PROFILE_NAMES))
    assert not missing, (
        f"Rule K2 violation: DAG assignees with no Hermes profile: {missing}. "
        "Add the profile to bootstrap.PROFILE_NAMES."
    )
    # The shipped checker must agree with the assertion above.
    assert verify_dag_assignees(PROFILE_NAMES) == []


def test_rule_k2_profiles_are_declared_without_duplicates() -> None:
    assert len(PROFILE_NAMES) == len(set(PROFILE_NAMES)), "duplicate profile name"
    assert all(p.startswith("cwt-") for p in PROFILE_NAMES)


def test_every_dag_card_has_a_title_and_known_parents() -> None:
    keys = {c.key for c in DAG_SPEC}
    assert DAG_SPEC, "DAG_SPEC is empty"
    for card in DAG_SPEC:
        assert card.title.strip(), f"card {card.key!r} has no title"
        assert card.assignee in set(PROFILE_NAMES)
        assert set(card.parents) <= keys, (
            f"card {card.key!r} names unknown parent(s) "
            f"{sorted(set(card.parents) - keys)}"
        )


# ===========================================================================
# 2. Every plugin.yaml tool has a schema AND a handler (S29 / S30)
# ===========================================================================


@pytest.fixture(scope="module")
def plugin_yaml() -> dict:
    path = REPO_ROOT / "hermes" / "plugins" / "cwt" / "plugin.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_every_plugin_tool_has_a_schema_and_a_handler(plugin_yaml: dict) -> None:
    """plugin.yaml is the advertised surface. Drift here is a runtime failure."""
    from cwt.hermes.plugins.cwt import schemas
    from hermes.plugins.cwt import handlers

    advertised = list(plugin_yaml["provides_tools"])
    assert advertised, "plugin.yaml advertises no tools"

    schema_names = {s["function"]["name"] for s in schemas.ALL_SCHEMAS}

    without_schema = sorted(set(advertised) - schema_names)
    assert not without_schema, (
        f"plugin.yaml advertises tools with no schema entry: {without_schema}"
    )

    without_handler = [
        name
        for name in advertised
        if not callable(getattr(handlers, name.removeprefix("cwt_"), None))
    ]
    assert not without_handler, (
        f"plugin.yaml advertises tools with no handler in handlers.py: {without_handler}"
    )

    not_advertised = sorted(schema_names - set(advertised))
    assert not not_advertised, (
        f"schemas defined but not advertised in plugin.yaml: {not_advertised}"
    )


def test_handlers_dunder_all_matches_plugin_yaml(plugin_yaml: dict) -> None:
    from hermes.plugins.cwt import handlers

    expected = {name.removeprefix("cwt_") for name in plugin_yaml["provides_tools"]}
    assert set(handlers.__all__) == expected


def test_plugin_yaml_declares_its_env_requirements(plugin_yaml: dict) -> None:
    """A missing var must disable the plugin with a message, not fail at call time."""
    names = {entry["name"] for entry in plugin_yaml["requires_env"]}
    assert {"APIFY_TOKEN", "TAVILY_API_KEY", "EXA_API_KEY", "OPENROUTER_API_KEY"} <= names


# ===========================================================================
# 3. Rule V4 — the backend chain always terminates in local_ffmpeg
# ===========================================================================

# The only variable Rule V4 cares about. APIFY_MAX_CHARGE_USD is the one var
# Settings.from_env() hard-requires (Rule A2), so every env supplies it.
_BASE_ENV = {"APIFY_MAX_CHARGE_USD": "1.00"}


def _settings(**overrides: str) -> Settings:
    """Build Settings from an explicit mapping — never touches .env or os.environ."""
    return Settings.from_env({**_BASE_ENV, **overrides})


@pytest.mark.parametrize(
    "chain_value",
    [
        None,                                          # absent -> default chain
        "local_ffmpeg",
        "hyperframes,local_ffmpeg",
        "hyperframes,openmontage,local_ffmpeg",
    ],
)
def test_rule_v4_chain_always_ends_in_local_ffmpeg(chain_value: str | None) -> None:
    env = {} if chain_value is None else {"VIDEO_BACKEND_CHAIN": chain_value}
    settings = _settings(**env)
    assert settings.video_backend_chain, "backend chain must not be empty"
    assert settings.video_backend_chain[-1] == "local_ffmpeg", (
        f"Rule V4 violation: chain {settings.video_backend_chain} does not "
        "terminate in local_ffmpeg"
    )


@pytest.mark.parametrize(
    "chain_value",
    ["", "hyperframes", "hyperframes,openmontage", "local_ffmpeg,hyperframes"],
)
def test_rule_v4_rejects_a_chain_not_ending_in_local_ffmpeg(chain_value: str) -> None:
    with pytest.raises(ConfigError, match="Rule V4"):
        _settings(VIDEO_BACKEND_CHAIN=chain_value)


def test_rule_v4_enforced_by_with_backend_chain() -> None:
    settings = _settings()
    with pytest.raises(ConfigError, match="Rule V4"):
        settings.with_backend_chain(["hyperframes", "openmontage"])
    ok = settings.with_backend_chain(["hyperframes", "local_ffmpeg"])
    assert ok.video_backend_chain == ["hyperframes", "local_ffmpeg"]


def test_rule_v4_enforced_at_chain_construction() -> None:
    """build_chain() is the last line of defence — it must reject a bad tail."""
    from dataclasses import replace

    from cwt.video.backend import build_chain

    settings = _settings()
    with pytest.raises(ValueError, match="Rule V4"):
        build_chain(replace(settings, video_backend_chain=["hyperframes", "openmontage"]))
    with pytest.raises(ValueError, match="Rule V4"):
        build_chain(replace(settings, video_backend_chain=[]))
    assert build_chain(settings)[-1].name == "local_ffmpeg"


# ===========================================================================
# 4. util/subproc.py is the only place a subprocess is spawned (§8.1)
# ===========================================================================

_SUBPROCESS_CALL = re.compile(
    r"\b(import\s+subprocess|from\s+subprocess\s+import|subprocess\.|"
    r"os\.system\s*\(|os\.popen\s*\()"
)
_SUBPROC_CHOKEPOINT = "src/cwt/util/subproc.py"


def test_only_subproc_module_spawns_subprocesses() -> None:
    offenders: list[str] = []
    for path in _source_py_files():
        rel = _rel(path)
        if rel == _SUBPROC_CHOKEPOINT:
            continue
        for lineno, line in enumerate(_read(path).splitlines(), start=1):
            if _SUBPROCESS_CALL.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "subprocess spawned outside the chokepoint "
        f"{_SUBPROC_CHOKEPOINT} (spec §8.1):\n  " + "\n  ".join(offenders)
    )


def test_subproc_chokepoint_still_exists_and_is_used() -> None:
    """The invariant above is vacuous if the chokepoint itself disappeared."""
    chokepoint = REPO_ROOT / _SUBPROC_CHOKEPOINT
    assert chokepoint.is_file()
    assert "def run_tool(" in _read(chokepoint)


# ===========================================================================
# 5. Raw Apify actor fields stay inside clients/apify.py (§8.3)
# ===========================================================================

# The raw actor OUTPUT fields from §8.3's mapping table (lines 2383-2399).
# Request parameters (`startUrls`, `resultsLimit`, `onlyAdsNewerThan`) are
# excluded: those are Apify's documented request vocabulary and may be named in
# prose, whereas reading an actor *output* field is what couples us to the
# actor's private shape. `maxTotalChargeUsd` is likewise a documented request
# parameter (Rule A2) named in tools/ads.py's docstring.
_RAW_APIFY_FIELDS = (
    "adArchiveID",
    "adArchiveId",
    "collationId",
    "collationCount",
    "pageName",
    "pageID",
    "pageId",
    "isActive",
    "startDateFormatted",
    "endDateFormatted",
    "publisherPlatform",
    "linkDescription",
    "ctaType",
    "ctaText",
    "displayFormat",
    "linkUrl",
    "originalImageUrl",
    "videoHdUrl",
    "videoSdUrl",
    "usageTotalUsd",
)
_APIFY_NORMALISER = "src/cwt/clients/apify.py"


def test_raw_apify_fields_only_referenced_in_the_apify_client() -> None:
    offenders: list[str] = []
    for path in _source_py_files():
        rel = _rel(path)
        if rel == _APIFY_NORMALISER:
            continue
        for lineno, line in enumerate(_read(path).splitlines(), start=1):
            for field in _RAW_APIFY_FIELDS:
                if field in line:
                    offenders.append(f"{rel}:{lineno}: {field} -> {line.strip()}")
    assert not offenders, (
        "raw Apify actor field read outside "
        f"{_APIFY_NORMALISER} (spec §3.1 lines 466-468):\n  " + "\n  ".join(offenders)
    )


def test_apify_normaliser_is_where_the_mapping_lives() -> None:
    """Guard against the exclusion above silently becoming meaningless."""
    text = _read(REPO_ROOT / _APIFY_NORMALISER)
    assert "adArchiveID" in text, "the ad_id mapping moved — update this invariant"


# ===========================================================================
# 6. No source file contains a hardcoded credential
# ===========================================================================

# The five credential prefixes this project's own secret-scan uses
# (S07/S08/S34, and S36's public-docs grep).
_SECRET_PREFIXES = ("apify_api_", "sk-or-v1-", "nvapi-", "tvly-", "exa_api")

# A prefix followed by >=16 token characters. This deliberately does NOT match
# the scrubbers' own regex literals: in `apify_api_[A-Za-z0-9_-]+` the body
# begins with `[`, which is not a token character, so no match is produced.
_SECRET_RE = re.compile(
    r"(?:" + "|".join(re.escape(p) for p in _SECRET_PREFIXES) + r")([A-Za-z0-9_\-]{16,})"
)


def test_no_source_file_contains_a_hardcoded_credential() -> None:
    offenders: list[str] = []
    for path in _source_py_files():
        for lineno, line in enumerate(_read(path).splitlines(), start=1):
            if _SECRET_RE.search(line):
                offenders.append(f"{_rel(path)}:{lineno}")
    assert not offenders, (
        "hardcoded credential found in source — this repository is public:\n  "
        + "\n  ".join(offenders)
    )


def test_the_credential_scan_actually_fires() -> None:
    """A scanner that cannot fail proves nothing. Feed it a known leak."""
    leak = 'token = "apify_api_abc123def456ghi789jkl012"'
    assert _SECRET_RE.search(leak) is not None
    assert _SECRET_RE.search('r"(apify_api_|sk-or-v1-)"') is None


@pytest.mark.parametrize("prefix", _SECRET_PREFIXES)
def test_every_prefix_is_detectable(prefix: str) -> None:
    assert _SECRET_RE.search(f'{prefix}{"a1b2c3d4e5f6g7h8"}') is not None
