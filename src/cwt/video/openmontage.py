import os
import shutil
from pathlib import Path

from cwt.clients.tts import VoiceoverResult
from cwt.domain.models import Storyboard
from cwt.util.paths import RunPaths
from cwt.util.subproc import run_tool
from cwt.video.backend import Availability, RenderResult


class OpenMontageBackend:
    name = "openmontage"

    def available(self) -> Availability:
        home = os.environ.get("OPENMONTAGE_HOME", "").strip()
        if not home or not Path(home).exists():
            return Availability(False, "OPENMONTAGE_HOME not set (optional; not required)")
        return Availability(True)

    def render(self, storyboard: Storyboard, *, paths: RunPaths, voiceover: VoiceoverResult) -> RenderResult:
        try:
            home = os.environ.get("OPENMONTAGE_HOME", "").strip()
            if not home:
                return RenderResult(ok=False, output=None, backend=self.name, elapsed_s=0.0, error="OPENMONTAGE_HOME not set (optional; not required)")
            
            home_path = Path(home)
            readme_found = False
            for p in home_path.glob("README*"):
                readme_found = True
                break
                
            if not readme_found:
                return RenderResult(ok=False, output=None, backend=self.name, elapsed_s=0.0, error="could not determine an invocation; see NOTICE for the AGPL boundary")
                
            # If there's an invocation, we would run it here, but since it's just a dummy test for now
            return RenderResult(ok=False, output=None, backend=self.name, elapsed_s=0.0, error="could not determine an invocation; see NOTICE for the AGPL boundary")
        except Exception as e:
            return RenderResult(ok=False, output=None, backend=self.name, elapsed_s=0.0, error=str(e))
