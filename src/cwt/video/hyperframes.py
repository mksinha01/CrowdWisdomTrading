import os
import re
import shutil
from pathlib import Path

from cwt.clients.tts import VoiceoverResult
from cwt.domain.models import Storyboard
from cwt.util.paths import RunPaths
from cwt.util.subproc import run_tool
from cwt.video.backend import Availability, RenderResult


class HyperFramesBackend:
    name = "hyperframes"

    def available(self) -> Availability:
        enabled = os.environ.get("HYPERFRAMES_ENABLED", "auto").strip().lower()
        if enabled == "0" or enabled == "false":
            return Availability(False, "HYPERFRAMES_ENABLED=0")
        
        node = shutil.which("node")
        if not node:
            return Availability(False, "node not found")
        
        res_node = run_tool([node, "--version"], timeout_s=10)
        if res_node.returncode != 0:
            return Availability(False, "node not found")
        
        m = re.search(r"v(\d+)\.", res_node.stdout)
        if not m:
            return Availability(False, "node not found")
        major = int(m.group(1))
        
        version_str = res_node.stdout.strip()
        if major < 22:
            return Availability(False, f"node {version_str} < required v22")
            
        npx = shutil.which("npx")
        if not npx:
            return Availability(False, "npx not found")
            
        try:
            res_npx = run_tool([npx, "hyperframes", "--version"], timeout_s=5)
            if res_npx.returncode != 0:
                return Availability(False, "npx hyperframes not installed")
        except Exception as exc:
            return Availability(False, f"npx hyperframes unavailable: {exc}")
            
        return Availability(True)

    def render(self, storyboard: Storyboard, *, paths: RunPaths, voiceover: VoiceoverResult) -> RenderResult:
        try:
            html = self.to_composition(storyboard)
            comp_path = paths.render / "composition.html"
            comp_path.write_text(html, encoding="utf-8")
            
            out_file = "hf.mp4"
            npx = shutil.which("npx")
            if not npx:
                return RenderResult(ok=False, output=None, backend=self.name, elapsed_s=0.0, error="npx not found")
            
            res = run_tool([npx, "hyperframes", "render", "composition.html", "-o", out_file], cwd=paths.render, timeout_s=900)
            if res.returncode != 0:
                err = (res.stderr or res.stdout or "").strip()
                # "Every failure path returns ok=False with the stderr tail in error."
                tail = "\n".join(err.splitlines()[-5:])
                return RenderResult(ok=False, output=None, backend=self.name, elapsed_s=res.elapsed_s, error=tail)
            
            out_path = paths.render / out_file
            if not out_path.exists() or out_path.stat().st_size == 0:
                return RenderResult(ok=False, output=None, backend=self.name, elapsed_s=res.elapsed_s, error="0-byte file produced")
                
            return RenderResult(ok=True, output=out_path, backend=self.name, elapsed_s=res.elapsed_s)
        except Exception as e:
            return RenderResult(ok=False, output=None, backend=self.name, elapsed_s=0.0, error=str(e))

    def to_composition(self, storyboard: Storyboard) -> str:
        # Create HTML with brand palette, shots as timed sections, camera moves as CSS transforms, and VO as track
        html = ["<!DOCTYPE html>", "<html>", "<head>", "<style>"]
        
        # Add brand palette (if available on storyboard?)
        html.append("body { background: black; color: white; margin: 0; overflow: hidden; }")
        
        html.extend(["</style>", "</head>", "<body>"])
        
        for shot in storyboard.shots:
            cam = shot.camera
            transform = "scale(1)"
            if cam.move == "push_in":
                transform = f"scale({1.0 + cam.intensity * 0.2})"
            elif cam.move == "pull_out":
                transform = f"scale({1.0 - cam.intensity * 0.2})"
            html.append(f'<section id="{shot.id}" data-start="{shot.start_s}" data-duration="{shot.duration_s}" style="transform: {transform};">')
            html.append(f'<!-- {shot.description} -->')
            html.append('</section>')
            
        html.append("<track src='vo.vtt' kind='captions' default>")
        html.extend(["</body>", "</html>"])
        
        return "\n".join(html)
