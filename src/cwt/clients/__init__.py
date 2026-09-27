"""External service clients."""
from cwt.clients.apify import (
    BASE as APIFY_BASE,
)
from cwt.clients.apify import (
    ApifyError,
    _scrub,
    normalise_actor_id,
    normalise_ad,
    run_actor,
)
from cwt.clients.apify import (
    build_input as build_apify_input,
)
from cwt.clients.exa import (
    exa_search,
)
from cwt.clients.http_cache import (
    CachedResponse,
    HttpCache,
    OfflineFixtureMissing,
    request_cache_key,
)
from cwt.clients.llm import (
    PROFILES,
    RETRYABLE,
    ArtifactValidationError,
    BudgetExceeded,
    LLMClient,
    LLMResult,
    ProviderProfile,
    Tier,
    UnparseableJson,
    estimate_cost,
    extract_json,
    schema_instruction,
)
from cwt.clients.tavily import (
    SearchHit,
    tavily_search,
)
from cwt.clients.tts import (
    KNOWN_EDGE_TTS_VOICES,
    KNOWN_PIPER_VOICES,
    TTSUnavailable,
    VoiceoverResult,
    WordTiming,
    estimate_word_timings,
    synthesize_voiceover,
)

__all__ = [
    "APIFY_BASE",
    "ApifyError",
    "ArtifactValidationError",
    "BudgetExceeded",
    "CachedResponse",
    "HttpCache",
    "KNOWN_EDGE_TTS_VOICES",
    "KNOWN_PIPER_VOICES",
    "LLMClient",
    "LLMResult",
    "OfflineFixtureMissing",
    "PROFILES",
    "ProviderProfile",
    "RETRYABLE",
    "SearchHit",
    "TTSUnavailable",
    "Tier",
    "UnparseableJson",
    "VoiceoverResult",
    "WordTiming",
    "_scrub",
    "build_apify_input",
    "estimate_cost",
    "estimate_word_timings",
    "exa_search",
    "extract_json",
    "normalise_actor_id",
    "normalise_ad",
    "request_cache_key",
    "run_actor",
    "schema_instruction",
    "synthesize_voiceover",
    "tavily_search",
]



