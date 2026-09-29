import json
from pathlib import Path
from cwt.domain.models import Storyboard
from cwt.video.hyperframes import HyperFramesBackend

storyboard_path = Path("runs/20260929-1331-8eff/artifacts/storyboard.json")
data = json.loads(storyboard_path.read_text(encoding="utf-8"))
storyboard = Storyboard.model_validate(data)

backend = HyperFramesBackend()
html = backend.to_composition(storyboard)
output_dir = Path("scratch/test_full_motion")
output_dir.mkdir(parents=True, exist_ok=True)
(output_dir / "index.html").write_text(html, encoding="utf-8")
print(f"Generated index.html with {len(html)} bytes")
