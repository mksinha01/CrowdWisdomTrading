"""Tool surface — market research and brief assembly (Story S21).

Provides:
- research_angle: One angle against the last-month window using Tavily and Exa,
  extracting and sourcing claims, and writing artifacts/angles/<angle>.json.
- assemble_brief: Select (do not summarise) the 8-12 best claims across the three
  angles, pick the strongest angle, merge baseline prohibited facts, and write
  artifacts/research_brief.json.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from datetime import date, timedelta
import json
import logging
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import Field

from cwt.clients.exa import exa_search
from cwt.clients.http_cache import HttpCache, OfflineFixtureMissing
from cwt.clients.llm import (
    ArtifactValidationError,
    BudgetExceeded,
    LLMClient,
    Tier,
)
from cwt.clients.tavily import SearchHit, tavily_search
from cwt.config import Settings
from cwt.domain.artifacts import ArtifactError, ArtifactStore
from cwt.domain.models import (
    SCHEMA_VERSION,
    AngleName,
    BrandPalette,
    PricingTier,
    ProductFacts,
    ProhibitedFact,
    ResearchAngle,
    ResearchBrief,
    ResearchClaim,
    _CwtBaseModel,
)
from cwt.prompts.research import (
    build_brief_prompt,
    build_research_prompt,
)
from cwt.util.jsonio import read_json, write_json
from cwt.util.paths import RunPaths

logger = logging.getLogger("cwt.tools.research")

ANGLES: tuple[str, ...] = ("pain", "unique_data", "crowd_effect")

BASELINE_PROHIBITED_FACTS: list[dict[str, str]] = [
    {
        "fact": "74.1% of tracked directions hit",
        "reason": "Unverifiable. Appears as 73%, 73.8% and 74.1% on the product's own site. The track-record page and /api/predictions that would substantiate it both return HTTP 404 as of 2026-09-26.",
        "rule": "Must never appear in any generated script, in any form, including paraphrases and rounded variants.",
    },
    {
        "fact": "16,564 professional traders tracked",
        "reason": "Self-reported with no published counting or de-duplication methodology.",
        "rule": "May not be stated as a verified fact. If used, it must be attributed to the company ('the company says it tracks...') or avoided.",
    },
    {
        "fact": "Institutional sentiment feature",
        "reason": "No methodology or data source disclosed anywhere.",
        "rule": "May not be described or implied in creative.",
    },
]

DEFAULT_QUERIES: dict[str, list[str]] = {
    "pain": [
        "retail trader signal overload",
        "trading signal fatigue 2026",
        "following too many analysts",
        "trader decision paralysis",
    ],
    "unique_data": [
        "crowdwisdomtrading predictions track record",
        "crowdwisdomtrading transparency",
        "published trading calls with outcomes",
    ],
    "crowd_effect": [
        "wisdom of crowds forecast accuracy",
        "superforecaster aggregation",
        "Tetlock superforecasters",
        "information cascades herding traders",
    ],
}


class AngleClaimInput(_CwtBaseModel):
    """Raw claim model used for validating LLM angle outputs before dropping unsourced ones."""

    text: str
    source_url: str = ""
    source_title: str = ""
    published_date: str | date | None = None
    confidence: float = 0.5
    provider: str = ""


class PainAngleOutput(_CwtBaseModel):
    """Structured response from PAIN_RESEARCH_PROMPT."""

    angle: Literal["pain"] = "pain"
    claims: list[AngleClaimInput] = Field(default_factory=list)
    synthesis: str
    search_queries_used: list[str] = Field(default_factory=list)


class UniqueDataAngleOutput(_CwtBaseModel):
    """Structured response from UNIQUE_DATA_RESEARCH_PROMPT."""

    angle: Literal["unique_data"] = "unique_data"
    claims: list[AngleClaimInput] = Field(default_factory=list)
    synthesis: str
    search_queries_used: list[str] = Field(default_factory=list)
    prohibited_facts_found: list[dict[str, str] | ProhibitedFact] = Field(default_factory=list)


class CrowdEffectAngleOutput(_CwtBaseModel):
    """Structured response from CROWD_EFFECT_RESEARCH_PROMPT."""

    angle: Literal["crowd_effect"] = "crowd_effect"
    claims: list[AngleClaimInput] = Field(default_factory=list)
    synthesis: str
    search_queries_used: list[str] = Field(default_factory=list)
    counterargument: str = ""


class BriefClaimSelection(_CwtBaseModel):
    """Selected claim entry within BRIEF_ASSEMBLY_PROMPT response."""

    angle: AngleName
    text: str
    source_url: str
    confidence: float


class BriefAssemblyOutput(_CwtBaseModel):
    """Structured response from BRIEF_ASSEMBLY_PROMPT."""

    selected_claims: list[BriefClaimSelection] = Field(default_factory=list)
    strongest_angle: AngleName
    angle_rationale: str
    counterargument_to_address: str = ""


def _run_async(coro: Any) -> Any:
    """Run an async coroutine synchronously, even if an event loop is already active."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def _parse_date(date_val: str | date | None) -> date | None:
    """Parse date from date object or ISO string. Returns None for null / placeholder values."""
    if date_val is None:
        return None
    if isinstance(date_val, date):
        return date_val
    s = str(date_val).strip()
    if not s or s.lower() in ("null", "none", "yyyy-mm-dd or null"):
        return None
    if "T" in s:
        s = s.split("T")[0]
    elif " " in s:
        s = s.split(" ")[0]
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


def _to_claims(hits: list[SearchHit], *, window_start: date) -> list[ResearchClaim]:
    """Convert search hits to ResearchClaim instances.

    - Drop any hit with no url.
    - Drop any hit with published_date before window_start.
    - If undated, multiply confidence by 0.6 and annotate text with '(undated)'.
    """
    claims: list[ResearchClaim] = []
    for hit in hits:
        url = hit.url.strip() if hit.url else ""
        if not url:
            continue

        pub_date = _parse_date(hit.published_date)
        if pub_date is not None:
            if pub_date < window_start:
                continue
            confidence = max(0.0, min(1.0, float(hit.score)))
            text = hit.content.strip() or hit.title.strip()
        else:
            confidence = max(0.0, min(1.0, round(float(hit.score) * 0.6, 4)))
            text = hit.content.strip() or hit.title.strip()
            if "(undated)" not in text.lower() and "[undated]" not in text.lower():
                text = f"{text} (undated)"

        title = hit.title.strip() if hit.title else url
        provider = hit.provider or "search"
        claims.append(
            ResearchClaim(
                text=text,
                source_url=url,
                source_title=title,
                published_date=pub_date,
                provider=provider,
                confidence=confidence,
            )
        )
    return claims


async def _search_angle(
    client: Any = None,
    angle: str = "",
    queries: list[str] | None = None,
    *,
    cache: HttpCache | None = None,
    tavily_api_key: str = "",
    exa_api_key: str = "",
    http_client: httpx.AsyncClient | None = None,
    days: int = 30,
) -> list[SearchHit]:
    """Run both Tavily and Exa for each query, dedupe by URL, and keep the higher score."""
    actual_cache = cache
    if actual_cache is None and isinstance(client, HttpCache):
        actual_cache = client
    if actual_cache is None:
        cache_root = Path("fixtures/http") if Path("fixtures/http").exists() else Path("runs/cache")
        actual_cache = HttpCache(root=cache_root, offline=False)

    actual_http_client = http_client
    if actual_http_client is None and isinstance(client, httpx.AsyncClient):
        actual_http_client = client

    actual_queries = queries if (queries is not None and len(queries) > 0) else DEFAULT_QUERIES.get(angle, [])

    all_hits: list[SearchHit] = []

    async def _search_tavily(query: str) -> list[SearchHit]:
        if not tavily_api_key:
            return []
        try:
            return await tavily_search(
                api_key=tavily_api_key,
                query=query,
                days=days,
                cache=actual_cache,
                client=actual_http_client,
            )
        except OfflineFixtureMissing:
            raise
        except Exception as exc:
            logger.warning("Tavily search failed for query %r: %s", query, exc)
            return []

    async def _search_exa(query: str) -> list[SearchHit]:
        if not exa_api_key:
            return []
        try:
            return await exa_search(
                api_key=exa_api_key,
                query=query,
                days=days,
                cache=actual_cache,
                client=actual_http_client,
            )
        except OfflineFixtureMissing:
            raise
        except Exception as exc:
            logger.warning("Exa search failed for query %r: %s", query, exc)
            return []

    tasks = []
    for q in actual_queries:
        tasks.append(_search_tavily(q))
        tasks.append(_search_exa(q))

    results = await asyncio.gather(*tasks)
    for batch in results:
        all_hits.extend(batch)

    # Deduplicate by URL, keeping higher score
    deduped: dict[str, SearchHit] = {}
    for hit in all_hits:
        url = hit.url.strip() if hit.url else ""
        if not url:
            continue
        if url not in deduped:
            deduped[url] = hit
        else:
            if hit.score > deduped[url].score:
                deduped[url] = hit

    return list(deduped.values())


def research_angle(
    *,
    settings: Settings,
    paths: RunPaths,
    angle: str,
    queries: list[str] | None = None,
    client: LLMClient | None = None,
    cache: HttpCache | None = None,
    today: date | None = None,
) -> dict:
    """One angle: Tavily + Exa, last-month window, sourced claims only.

    CALL THIS: on one of the three research cards (`res_pain`, `res_unique`, `res_crowd`),
    once each, concurrently. Pass `angle` explicitly — the three cards differ only
    by that argument.

    WHEN NOT TO CALL: do not call it twice for the same angle in one run; the content
    cache makes the second call free but the board should have one card per angle.
    Do not call it with a `window_days` other than 30 — the brief specifies last month.

    WRITES artifacts/angles/<angle>.json   (a scratch artifact, not in ARTIFACT_NAMES)
    RETURNS {"angle","claims":int,"sourced":int,"queries_used":[...],"window":{...},
             "prohibited_facts_found":int,"warnings":[...]}
    """
    if angle not in ANGLES:
        raise ValueError(f"Invalid research angle: {angle!r}. Must be one of: {ANGLES}")

    paths.ensure()
    ref_date = today or date.today()
    window_days = 30
    window_end = ref_date
    window_start = ref_date - timedelta(days=window_days)

    actual_queries = queries if (queries is not None and len(queries) > 0) else DEFAULT_QUERIES[angle]

    if cache is None:
        cache_root = Path("fixtures/http") if Path("fixtures/http").exists() else paths.cache
        cache = HttpCache(root=cache_root, offline=False)

    # 1. Search Tavily + Exa
    hits: list[SearchHit] = _run_async(
        _search_angle(
            cache=cache,
            angle=angle,
            queries=actual_queries,
            tavily_api_key=settings.tavily_api_key,
            exa_api_key=settings.exa_api_key,
            days=window_days,
        )
    )

    url_to_hit = {h.url: h for h in hits if h.url}
    search_claims = _to_claims(hits, window_start=window_start)

    # 2. Setup LLM Client
    if client is None:
        api_key = (
            settings.openrouter_api_key
            if settings.llm_provider == "openrouter"
            else settings.nvidia_api_key
        )
        client = LLMClient(
            provider=settings.llm_provider,
            api_key=api_key,
            model_cheap=settings.model_cheap,
            model_strong=settings.model_strong,
            fallbacks=settings.model_fallbacks,
            max_concurrency=settings.llm_max_concurrency,
            max_usd=settings.max_usd,
            ledger_path=paths.ledger,
            timeout_s=settings.llm_timeout_seconds,
            repair_attempts=settings.llm_json_repair_attempts,
        )

    # 3. Call LLM for synthesis & claims
    base_prompt = build_research_prompt(angle, str(window_start), str(window_end))
    prompt = base_prompt
    if search_claims:
        context_lines = ["\n\n--- SEARCH RESULTS (LAST 30 DAYS) ---"]
        for i, sc in enumerate(search_claims[:12], 1):
            context_lines.append(f"Result {i}:")
            context_lines.append(f"  Title: {sc.source_title}")
            context_lines.append(f"  URL: {sc.source_url}")
            context_lines.append(f"  Published: {sc.published_date or 'undated'}")
            context_lines.append(f"  Snippet: {sc.text}")
        context_lines.append("--- END SEARCH RESULTS ---\n")
        prompt += "\n".join(context_lines)

    schema_map: dict[str, type[_CwtBaseModel]] = {
        "pain": PainAngleOutput,
        "unique_data": UniqueDataAngleOutput,
        "crowd_effect": CrowdEffectAngleOutput,
    }
    schema = schema_map[angle]

    angle_res = _run_async(
        client.complete_validated(
            tier=Tier.CHEAP,
            messages=[{"role": "user", "content": prompt}],
            schema=schema,
            stage=f"research_{angle}",
        )
    )

    # 4. Filter and process claims
    warnings: list[str] = []
    valid_claims: list[ResearchClaim] = []
    raw_claims = getattr(angle_res, "claims", [])

    for c in raw_claims:
        url = c.source_url.strip() if c.source_url else ""
        if not url:
            # Per §6.3 line 1181: A claim with empty source_url is dropped before it reaches artifact
            continue

        pub_date = _parse_date(c.published_date)
        if pub_date is not None and pub_date < window_start:
            # Dropped if dated before window_start
            continue

        confidence = float(c.confidence)
        text = c.text.strip()
        if pub_date is None:
            # Undated: scale confidence by 0.6 if not already low and annotate text
            confidence = max(0.0, min(1.0, round(confidence * 0.6, 4)))
            if "(undated)" not in text.lower() and "[undated]" not in text.lower():
                text = f"{text} (undated)"
        else:
            confidence = max(0.0, min(1.0, confidence))

        if c.provider:
            provider = c.provider
        elif url in url_to_hit:
            provider = url_to_hit[url].provider
        else:
            provider = "tavily"

        title = c.source_title.strip() if c.source_title else (url_to_hit[url].title if url in url_to_hit else url)

        valid_claims.append(
            ResearchClaim(
                text=text,
                source_url=url,
                source_title=title,
                published_date=pub_date,
                provider=provider,
                confidence=confidence,
            )
        )

    # If LLM returned no valid sourced claims, fallback to search_claims
    if not valid_claims and search_claims:
        valid_claims = search_claims

    # Extract extra fields
    prohibited_facts_found: list[ProhibitedFact] = []
    if angle == "unique_data":
        raw_pfs = getattr(angle_res, "prohibited_facts_found", [])
        for pf in raw_pfs:
            if isinstance(pf, ProhibitedFact):
                prohibited_facts_found.append(pf)
            elif isinstance(pf, dict):
                try:
                    prohibited_facts_found.append(ProhibitedFact.model_validate(pf))
                except Exception as exc:
                    warnings.append(f"invalid_prohibited_fact:{exc}")

    counterargument = ""
    if angle == "crowd_effect":
        counterargument = getattr(angle_res, "counterargument", "")

    # 5. Write artifacts/angles/<angle>.json (scratch artifact outside ArtifactStore registry)
    angles_dir = paths.artifacts / "angles"
    angles_dir.mkdir(parents=True, exist_ok=True)
    angle_file = angles_dir / f"{angle}.json"

    angle_dict: dict[str, Any] = {
        "angle": angle,
        "claims": [c.model_dump(mode="json") for c in valid_claims],
        "synthesis": angle_res.synthesis,
        "search_queries_used": actual_queries,
    }
    if angle == "unique_data":
        angle_dict["prohibited_facts_found"] = [
            f.model_dump(mode="json") for f in prohibited_facts_found
        ]
    elif angle == "crowd_effect":
        angle_dict["counterargument"] = counterargument

    write_json(angle_file, angle_dict)

    return {
        "angle": angle,
        "claims": len(valid_claims),
        "sourced": sum(1 for c in valid_claims if c.source_url.strip()),
        "queries_used": actual_queries,
        "window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
        },
        "prohibited_facts_found": len(prohibited_facts_found),
        "warnings": warnings,
    }


def assemble_brief(
    *,
    settings: Settings,
    paths: RunPaths,
    client: LLMClient | None = None,
    cache: HttpCache | None = None,
    today: date | None = None,
) -> dict:
    """SELECT the 8-12 best claims across the three angles; pick the strongest angle.

    CALL THIS: on the `brief` card once all three research angles (`res_pain`,
    `res_unique`, `res_crowd`) have completed. Merges discovered prohibited facts
    with the baseline facts.

    WHEN NOT TO CALL: do not call before all three angles have produced their
    scratch artifacts in artifacts/angles/.

    WRITES artifacts/research_brief.json
    RETURNS {"artifact_path","selected_claims","strongest_angle","angle_rationale",
             "prohibited_facts":int}
    """
    paths.ensure()
    angles_dir = paths.artifacts / "angles"
    missing = [a for a in ANGLES if not (angles_dir / f"{a}.json").exists()]
    if missing:
        raise ArtifactError(f"Missing angle scratch artifacts for: {missing}. Run research_angle first.")

    pain_data = read_json(angles_dir / "pain.json")
    unique_data_data = read_json(angles_dir / "unique_data.json")
    crowd_effect_data = read_json(angles_dir / "crowd_effect.json")

    # Merge prohibited facts: baseline is floor, union only, never remove or overwrite
    merged_prohibited: list[ProhibitedFact] = []
    seen_facts: set[str] = set()

    for item in BASELINE_PROHIBITED_FACTS:
        pf = ProhibitedFact.model_validate(item)
        norm = pf.fact.strip().lower()
        seen_facts.add(norm)
        merged_prohibited.append(pf)

    for item in unique_data_data.get("prohibited_facts_found", []):
        pf = ProhibitedFact.model_validate(item)
        norm = pf.fact.strip().lower()
        if norm not in seen_facts:
            seen_facts.add(norm)
            merged_prohibited.append(pf)

    # Setup LLM client
    if client is None:
        api_key = (
            settings.openrouter_api_key
            if settings.llm_provider == "openrouter"
            else settings.nvidia_api_key
        )
        client = LLMClient(
            provider=settings.llm_provider,
            api_key=api_key,
            model_cheap=settings.model_cheap,
            model_strong=settings.model_strong,
            fallbacks=settings.model_fallbacks,
            max_concurrency=settings.llm_max_concurrency,
            max_usd=settings.max_usd,
            ledger_path=paths.ledger,
            timeout_s=settings.llm_timeout_seconds,
            repair_attempts=settings.llm_json_repair_attempts,
        )

    # Format angle outputs for BRIEF_ASSEMBLY_PROMPT
    angle_outputs = json.dumps(
        {
            "pain": pain_data,
            "unique_data": unique_data_data,
            "crowd_effect": crowd_effect_data,
        },
        indent=2,
    )
    brief_prompt = build_brief_prompt(angle_outputs)

    # Rule A5: Tier.CHEAP for brief assembly
    brief_output = _run_async(
        client.complete_validated(
            tier=Tier.CHEAP,
            messages=[{"role": "user", "content": brief_prompt}],
            schema=BriefAssemblyOutput,
            stage="assemble_brief",
        )
    )

    # Verify landing_url returns 200 via HEAD request through cache
    landing_url = "https://crowdwisdomtrading.com/"
    landing_url_verified_200 = False
    if cache is None:
        cache_root = Path("fixtures/http") if Path("fixtures/http").exists() else paths.cache
        cache = HttpCache(root=cache_root, offline=False)

    try:
        resp = _run_async(cache.request("HEAD", landing_url, timeout=10.0))
        landing_url_verified_200 = (resp.status_code == 200)
    except Exception as exc:
        logger.warning("Failed to verify landing URL %s: %s", landing_url, exc)
        landing_url_verified_200 = False

    # Build ProductFacts (§3.3 lines 569-596)
    product = ProductFacts(
        name="CrowdWisdomTrading",
        tagline="Collective Intelligence for Traders",
        legal_entity="Tsuroni LTD",
        landing_url=landing_url,
        landing_url_verified_200=landing_url_verified_200,
        markets=["US stocks", "crypto", "forex", "commodities"],
        pricing=[
            PricingTier(tier="Weekly CrowdWisdom", price="$0", period="free"),
            PricingTier(tier="Pro Access", price="$29.99", period="month"),
            PricingTier(tier="Pay As You Go", price="$9.99", period="per 10-credit pack"),
        ],
        explicit_disclaimers={
            "not_copy_trading": True,
            "not_algo_trading": True,
            "not_personalised_advice": True,
            "no_position_access": True,
            "note": "The product's own FAQ states they do not have access to traders' positions.",
        },
        brand=BrandPalette(
            background="#050505",
            surface="#0a0a0a",
            primary_accent="#22d3ee",
            secondary_accents=["#fb923c", "#fbbf24", "#818cf8", "#fb7185"],
            success="#34d399",
            text="#cbd5e1",
            mood="dark terminal aesthetic, cyan-led, gradient-filled display numerals",
        ),
    )

    # Dates
    ref_date = today or date.today()
    window_start = ref_date - timedelta(days=30)
    window_end = ref_date

    # Build ResearchAngle per angle with selected claims
    raw_angles: dict[str, dict[str, Any]] = {
        "pain": pain_data,
        "unique_data": unique_data_data,
        "crowd_effect": crowd_effect_data,
    }
    angles_dict: dict[AngleName, ResearchAngle] = {}

    for angle_str, angle_raw in raw_angles.items():
        angle_enum = AngleName(angle_str)
        orig_claims = [ResearchClaim.model_validate(c) for c in angle_raw.get("claims", [])]

        selected_for_angle: list[ResearchClaim] = []
        for sel in brief_output.selected_claims:
            if sel.angle == angle_enum or str(sel.angle) == angle_str:
                match = next((c for c in orig_claims if c.source_url == sel.source_url or c.text == sel.text), None)
                if match:
                    selected_for_angle.append(match)
                else:
                    selected_for_angle.append(
                        ResearchClaim(
                            text=sel.text,
                            source_url=sel.source_url,
                            source_title=sel.text[:50],
                            published_date=None,
                            provider="tavily",
                            confidence=sel.confidence,
                        )
                    )

        final_claims = selected_for_angle if selected_for_angle else orig_claims

        angles_dict[angle_enum] = ResearchAngle(
            angle=angle_enum,
            claims=final_claims,
            synthesis=angle_raw.get("synthesis", ""),
            search_queries_used=angle_raw.get("search_queries_used", []),
        )

    brief = ResearchBrief(
        schema_version=SCHEMA_VERSION,
        window={"start": window_start, "end": window_end},
        angles=angles_dict,
        product=product,
        prohibited_facts=merged_prohibited,
    )

    # Write contracted artifact via ArtifactStore
    store = ArtifactStore(paths, run_id=paths.run_dir.name)
    artifact_path = store.write("research_brief", brief)

    return {
        "artifact_path": str(artifact_path),
        "selected_claims": len(brief_output.selected_claims),
        "strongest_angle": str(brief_output.strongest_angle),
        "angle_rationale": brief_output.angle_rationale,
        "prohibited_facts": len(brief.prohibited_facts),
    }
