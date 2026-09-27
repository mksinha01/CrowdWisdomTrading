"""Tests for AssetSourcer (S17)."""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch

from cwt.config import Settings
from cwt.domain.models import AssetRef, Shot, BeatName, SubjectName, Camera, Lighting, Composition, Transition, CameraMove, DepthOfField, Stabilisation, Contrast
from cwt.util.paths import RunPaths
from cwt.video.assets import AssetSourcer, _CC0_ALLOWLIST, AssetRecord
from cwt.clients.http_cache import HttpCache

@pytest.fixture
def run_paths(tmp_path):
    rp = RunPaths(tmp_path)
    rp.ensure()
    return rp

@pytest.fixture
def mock_cache():
    cache = Mock(spec=HttpCache)
    # mock request to return a basic response
    # It would be Async, but AssetSourcer handles async loop internally
    return cache

@pytest.fixture
def sourcer(run_paths, mock_cache):
    return AssetSourcer(
        paths=run_paths,
        settings=Settings.from_env(),
        cache=mock_cache,
    )

def _make_dummy_shot(shot_id: str, ref: str, subject: SubjectName = SubjectName.ABSTRACT_MARKET_DATA) -> Shot:
    return Shot(
        id=shot_id,
        beat=BeatName.HOOK,
        start_s=0.0,
        duration_s=3.0,
        description="test",
        subject=subject,
        asset=AssetRef(kind="generated_chart", ref=ref, source="internal"),
        camera=Camera(move=CameraMove.STATIC, intensity=0.0, lens_mm=50, depth_of_field="deep", stabilisation="locked"),
        lighting=Lighting(key="none", contrast="extreme", colour_temp_k=6500),
        palette=["#050505", "#1a1a1a", "#ef4444", "#22d3ee"],
        composition=Composition(framing="center", text_safe_area={"top": 0.15, "bottom": 0.25}),
        transition_in=Transition(type="cut", duration_s=0.0),
        transition_out=Transition(type="cut", duration_s=0.0),
    )

def test_resolve_is_idempotent(sourcer: AssetSourcer):
    shot = _make_dummy_shot("s1", "test_ref")
    
    with patch.object(sourcer, "generate") as mock_gen:
        # Mock generate so it doesn't actually do Pillow drawing here to isolate resolve
        mock_gen.return_value = AssetRecord(
            ref="test_ref", path=Path("foo.png"), license="internal", 
            source_url="generated://pillow", attribution_required=False, 
            kind="generated_chart", sha256="123"
        )
        
        # Call once
        rec1 = sourcer.resolve(shot.asset, shot)
        # Call twice
        rec2 = sourcer.resolve(shot.asset, shot)
        
        assert rec1 is rec2
        # Generate should only be called once because of cache dict
        mock_gen.assert_called_once()

def test_generation_is_deterministic(sourcer: AssetSourcer):
    # Two calls with the same shot.id should produce identical SHA-256
    shot1 = _make_dummy_shot("s_deterministic", "candle_proliferation")
    shot2 = _make_dummy_shot("s_deterministic", "candle_proliferation") # same id
    
    # We clear the in-memory cache to force it to re-generate, but write to different paths 
    # to compare content. Actually generate writes to the same path, so we can just compare
    # the returned records if we force regeneration.
    
    rec1 = sourcer.generate(shot1, sourcer._gen_out_path("candle_proliferation_1"))
    rec2 = sourcer.generate(shot2, sourcer._gen_out_path("candle_proliferation_2"))
    
    assert rec1.sha256 == rec2.sha256
    
    # Different shot.id -> different seed -> different sha256
    shot3 = _make_dummy_shot("s_different", "candle_proliferation")
    rec3 = sourcer.generate(shot3, sourcer._gen_out_path("candle_proliferation_3"))
    
    assert rec1.sha256 != rec3.sha256

def test_non_allowlisted_host_raises(sourcer: AssetSourcer):
    # If a manifest entry points to a non-allowlisted host, resolve_cc0 raises
    asset = AssetRef(kind="cc0", ref="bad_host_asset", source="cc0")
    bad_manifest_entry = {
        "ref": "bad_host_asset",
        "license": "CC0-1.0",
        "source_url": "https://evil.com/asset.png",
        "attribution_required": False
    }
    
    with pytest.raises(PermissionError, match="not in the CC0 allowlist"):
        sourcer._fetch_cc0(asset, bad_manifest_entry)
        
def test_manifest_entries_have_required_fields(sourcer: AssetSourcer):
    # Verify the generated manifest has all required fields
    
    # Populate a few assets
    shot = _make_dummy_shot("s1", "candle_proliferation")
    sourcer.resolve(shot.asset, shot)
    
    manifest = sourcer.manifest()
    assert len(manifest) > 0
    
    for entry in manifest:
        assert "ref" in entry
        assert "path" in entry
        assert "license" in entry
        assert "source_url" in entry
        assert "attribution_required" in entry
        
        # Values shouldn't be empty for license and source_url
        assert entry["license"]
        assert entry["source_url"]

def test_generate_output_size(sourcer: AssetSourcer):
    # generate() output is exactly 1080x1920
    from PIL import Image
    
    shot = _make_dummy_shot("s1", "candle_proliferation")
    rec = sourcer.generate(shot, sourcer._gen_out_path("test_size"))
    
    assert rec.path.exists()
    
    with Image.open(rec.path) as img:
        assert img.size == (1080, 1920)

def test_cc0_fetch_allowlist_check(sourcer: AssetSourcer):
    # Check that a valid host doesn't raise a PermissionError
    # We will mock the cache request to prevent actual network I/O
    asset = AssetRef(kind="cc0", ref="good_host_asset", source="cc0")
    good_manifest_entry = {
        "ref": "good_host_asset",
        "license": "CC0-1.0",
        "source_url": f"https://{list(_CC0_ALLOWLIST)[0]}/asset.png",
        "attribution_required": False
    }
    
    # Setup mock to return a dummy response
    class MockResponse:
        status_code = 200
        body = b"dummy content"
    
    async def mock_req(*args, **kwargs):
        return MockResponse()
        
    sourcer._cache.request = Mock(side_effect=mock_req)
    
    try:
        rec = sourcer._fetch_cc0(asset, good_manifest_entry)
        assert rec.sha256
        assert rec.path.exists()
    except Exception as e:
        pytest.fail(f"Should not have raised exception: {e}")
