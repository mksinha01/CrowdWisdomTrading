import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from cwt.video.hyperframes import HyperFramesBackend
from cwt.video.openmontage import OpenMontageBackend
from cwt.domain.models import Storyboard
from cwt.util.paths import RunPaths
from cwt.clients.tts import VoiceoverResult

def test_hyperframes_port_guard():
    content = Path("src/cwt/video/hyperframes.py").read_text()
    assert "9119" not in content, "Rule P1 violation: port 9119 is reserved"
    assert "8000" not in content, "Rule P1 violation: port 8000 is reserved"

def test_openmontage_port_guard():
    content = Path("src/cwt/video/openmontage.py").read_text()
    assert "9119" not in content, "Rule P1 violation: port 9119 is reserved"
    assert "8000" not in content, "Rule P1 violation: port 8000 is reserved"

def test_hyperframes_available_missing_node(monkeypatch):
    monkeypatch.setenv("HYPERFRAMES_ENABLED", "auto")
    monkeypatch.setattr("shutil.which", lambda x: None)
    backend = HyperFramesBackend()
    avail = backend.available()
    assert not avail.available
    assert avail.reason == "node not found"

def test_openmontage_available_missing_home(monkeypatch):
    monkeypatch.delenv("OPENMONTAGE_HOME", raising=False)
    backend = OpenMontageBackend()
    avail = backend.available()
    assert not avail.available
    assert avail.reason == "OPENMONTAGE_HOME not set (optional; not required)"

def test_hyperframes_render_tool_failure(monkeypatch, tmp_path):
    backend = HyperFramesBackend()
    
    # mock run_tool to fail
    def mock_run_tool(cmd, **kwargs):
        class Result:
            returncode = 1
            stdout = "lint error"
            stderr = "clip s07 has no data-start"
            elapsed_s = 1.0
        return Result()
        
    monkeypatch.setattr("cwt.video.hyperframes.run_tool", mock_run_tool)
    monkeypatch.setattr("shutil.which", lambda x: "dummy")
    
    sb = Storyboard.model_construct(shots=[])
    paths = RunPaths(run_dir=tmp_path)
    paths.render.mkdir(parents=True, exist_ok=True)
    
    res = backend.render(sb, paths=paths, voiceover=MagicMock())
    assert not res.ok
    assert "clip s07 has no data-start" in res.error

def test_hyperframes_render_zero_byte_file(monkeypatch, tmp_path):
    backend = HyperFramesBackend()
    
    def mock_run_tool(cmd, **kwargs):
        class Result:
            returncode = 0
            stdout = ""
            stderr = ""
            elapsed_s = 1.0
        
        # produce 0 byte file
        Path(kwargs["cwd"] / "hf.mp4").touch()
        return Result()
        
    monkeypatch.setattr("cwt.video.hyperframes.run_tool", mock_run_tool)
    monkeypatch.setattr("shutil.which", lambda x: "dummy")
    
    sb = Storyboard.model_construct(shots=[])
    paths = RunPaths(run_dir=tmp_path)
    paths.render.mkdir(parents=True, exist_ok=True)
    
    res = backend.render(sb, paths=paths, voiceover=MagicMock())
    assert not res.ok
    assert "0-byte file produced" in res.error

def test_hyperframes_to_composition():
    backend = HyperFramesBackend()
    
    shots = []
    for i in range(1, 13):
        cam = MagicMock()
        cam.move = "push_in"
        cam.intensity = 0.5
        
        shot = MagicMock()
        shot.id = f"s{i:02d}"
        shot.start_s = 0.0
        shot.duration_s = 3.0
        shot.description = "test"
        shot.camera = cam
        shots.append(shot)
        
    sb = MagicMock()
    sb.shots = shots
    
    html = backend.to_composition(sb)
    
    for i in range(1, 13):
        assert f'id="s{i:02d}"' in html
        
    assert "C:" not in html
    assert "file://" not in html
