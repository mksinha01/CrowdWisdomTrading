import json
from pathlib import Path
from cwt.domain.models import Storyboard
from cwt.clients.tts import VoiceoverResult
from cwt.video.hyperframes import HyperFramesBackend

storyboard_path = Path("runs/20260929-1331-8eff/artifacts/storyboard.json")
data = json.loads(storyboard_path.read_text(encoding="utf-8"))
storyboard = Storyboard.model_validate(data)

vo_path = Path("runs/20260929-1331-8eff/artifacts/voiceover.json")
vo_data = json.loads(vo_path.read_text(encoding="utf-8"))
vo_result = VoiceoverResult(
    backend_used=vo_data.get("backend_used", "edge_tts"),
    audio_path=Path("runs/20260929-1331-8eff/render/vo.mp3"),
    duration_s=vo_data.get("duration_s", 46.61),
    words=vo_data.get("words", []),
    transcript="",
    chain_tried=["edge_tts"],
)

backend = HyperFramesBackend()
html = backend.to_composition(storyboard, voiceover=vo_result)
output_dir = Path("scratch/test_full_motion")
output_dir.mkdir(parents=True, exist_ok=True)
(output_dir / "index.html").write_text(html, encoding="utf-8")
print(f"Generated synchronized index.html with {len(html)} bytes")
