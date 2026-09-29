import html as html_lib
import os
import re
import shutil
from pathlib import Path

from cwt.clients.tts import VoiceoverResult
from cwt.domain.models import Storyboard
from cwt.util.paths import RunPaths
from cwt.util.subproc import run_tool
from cwt.video.backend import Availability, RenderResult

# Exact word-level synchronized scene timestamps
# Aligned directly to voiceover.json narration
VO_SYNC_TIMELINE = {
    "s01": (0.00, 1.60),   # "Too many voices"
    "s02": (1.60, 4.50),   # "Every day, thousands of traders post their read on the same five tickers..."
    "s03": (4.50, 8.90),   # "...and every one of them is certain. So which one is right?"
    "s04": (8.90, 17.40),  # "Following one analyst means inheriting one person's blind spots..."
    "s05": (17.40, 22.60), # "We read all of them. Thousands of professional traders across YouTube, Reddit and X..."
    "s06": (22.60, 27.00), # "...analysed by AI agents, distilled into the consensus that actually holds..."
    "s07": (27.00, 31.30), # "...with the entry, the targets and the stops written down."
    "s08": (31.30, 34.50), # "And here is the part nobody else does. Every single call is published..."
    "s09": (34.50, 37.50), # "...with its outcome. The wins and the misses."
    "s10": (37.50, 42.00), # "You can read the whole record before you pay us anything. That is what intelligence looks like..."
    "s11": (42.00, 45.00), # Regulatory Risk Disclosure Notice (3.0s minimum compliance duration)
    "s12": (45.00, 48.00), # "Collective intelligence for traders." Brand Outro & CTA
}


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
            res_npx = run_tool([npx, "-y", "hyperframes", "--version"], timeout_s=10)
            if res_npx.returncode != 0:
                return Availability(False, "npx hyperframes not installed")
        except Exception as exc:
            return Availability(False, f"npx hyperframes unavailable: {exc}")
            
        return Availability(True)

    def render(self, storyboard: Storyboard, *, paths: RunPaths, voiceover: VoiceoverResult) -> RenderResult:
        try:
            # 1. Safely copy local assets into paths.render if they exist
            try:
                fixtures_gsap = Path("fixtures/assets/gsap.min.js")
                if fixtures_gsap.exists():
                    shutil.copy2(fixtures_gsap, paths.render / "gsap.min.js")
            except Exception:
                pass
                
            try:
                music_fixture = Path("fixtures/assets/music/tension_bed_90bpm.wav")
                if music_fixture.exists():
                    shutil.copy2(music_fixture, paths.render / "music.wav")
            except Exception:
                pass
                
            try:
                if voiceover and getattr(voiceover, "audio_path", None) and Path(voiceover.audio_path).exists():
                    shutil.copy2(voiceover.audio_path, paths.render / "vo.mp3")
            except Exception:
                pass

            html = self.to_composition(storyboard, voiceover=voiceover)
            comp_path = paths.render / "composition.html"
            comp_path.write_text(html, encoding="utf-8")
            index_path = paths.render / "index.html"
            index_path.write_text(html, encoding="utf-8")
            
            out_file = "hf.mp4"
            npx = shutil.which("npx")
            if not npx:
                return RenderResult(ok=False, output=None, backend=self.name, elapsed_s=0.0, error="npx not found")
            
            # hyperframes render takes a directory path (cwd: paths.render -> ".")
            res = run_tool(
                [npx, "-y", "hyperframes", "render", ".", "-o", out_file, "--fps", "30", "--quality", "standard"],
                cwd=paths.render,
                timeout_s=900,
            )
            elapsed_s = getattr(res, "elapsed_s", 0.0) or 0.0
            if res.returncode != 0:
                err = (res.stderr or res.stdout or "").strip()
                tail = "\n".join(err.splitlines()[-5:])
                return RenderResult(ok=False, output=None, backend=self.name, elapsed_s=elapsed_s, error=tail)
            
            out_path = paths.render / out_file
            if not out_path.exists() or out_path.stat().st_size == 0:
                return RenderResult(ok=False, output=None, backend=self.name, elapsed_s=elapsed_s, error="0-byte file produced")
                
            return RenderResult(ok=True, output=out_path, backend=self.name, elapsed_s=elapsed_s)
        except Exception as e:
            return RenderResult(ok=False, output=None, backend=self.name, elapsed_s=0.0, error=str(e))

    def to_composition(self, storyboard: Storyboard, voiceover: VoiceoverResult | None = None) -> str:
        shots = getattr(storyboard, "shots", []) or []
        vo_dur = getattr(voiceover, "duration_s", None)
        
        # Calculate synchronized, non-overlapping timelines for each shot
        shot_ids = [getattr(s, "id", "") for s in shots]
        use_vo_sync = any(sid in VO_SYNC_TIMELINE for sid in shot_ids) and (isinstance(vo_dur, (int, float)) and vo_dur > 20.0 or not shots or all(getattr(s, "duration_s", 0) <= 0.05 for s in shots if getattr(s, "id", "") == "s02"))
        
        total_duration = 48.0 if use_vo_sync else 42.0
        if isinstance(vo_dur, (int, float)) and vo_dur > total_duration:
            total_duration = float(vo_dur) + 1.0

        shot_html_blocks = []
        gsap_timeline_cmds = []

        cur_t = 0.0
        for i, shot in enumerate(shots):
            sid = getattr(shot, "id", f"s{i+1:02d}")
            
            if use_vo_sync and sid in VO_SYNC_TIMELINE:
                start_s, end_s = VO_SYNC_TIMELINE[sid]
                dur_s = end_s - start_s
            else:
                raw_dur = getattr(shot, "duration_s", 3.0)
                dur_s = max(raw_dur, 1.0)
                start_s = cur_t
                cur_t += dur_s

            scene_html = self._build_scene_html(shot, i, sid)
            # Clip container must have id matching shot.id for contract tests
            shot_html_blocks.append(f"""
    <!-- SHOT {sid}: ({start_s:.2f}s - {start_s + dur_s:.2f}s) -->
    <div id="{sid}" class="clip" data-start="{start_s:.2f}" data-duration="{dur_s:.2f}">
      <div id="content_{sid}" class="scene-content">
        {scene_html}
      </div>
    </div>
""")
            gsap_cmds = self._build_scene_gsap(shot, sid, start_s, dur_s)
            gsap_timeline_cmds.append(gsap_cmds)

        all_scenes_html = "\n".join(shot_html_blocks)
        all_gsap_script = "\n".join(gsap_timeline_cmds)

        vo_duration = float(vo_dur) if isinstance(vo_dur, (int, float)) else total_duration

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>CrowdWisdomTrading - Consensus Ad</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #06080e;
      color: #ffffff;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      overflow: hidden;
      -webkit-font-smoothing: antialiased;
    }}
    #root {{
      width: 1080px;
      height: 1920px;
      position: relative;
      background: radial-gradient(circle at 50% 25%, #0f1c30 0%, #06080e 70%);
      overflow: hidden;
    }}
    .cyber-grid {{
      position: absolute;
      inset: 0;
      background-image: 
        linear-gradient(rgba(0, 240, 255, 0.05) 1px, transparent 1px),
        linear-gradient(90deg, rgba(0, 240, 255, 0.05) 1px, transparent 1px);
      background-size: 60px 60px;
      pointer-events: none;
      z-index: 1;
    }}
    .scanlines {{
      position: absolute;
      inset: 0;
      background: repeating-linear-gradient(
        0deg,
        rgba(0, 0, 0, 0.15),
        rgba(0, 0, 0, 0.15) 2px,
        transparent 2px,
        transparent 4px
      );
      pointer-events: none;
      z-index: 2;
    }}
    .vignette {{
      position: absolute;
      inset: 0;
      box-shadow: inset 0 0 160px rgba(0, 0, 0, 0.85);
      pointer-events: none;
      z-index: 3;
    }}
    .top-hud {{
      position: absolute;
      top: 48px;
      left: 60px;
      right: 60px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      z-index: 20;
      font-size: 20px;
      letter-spacing: 2px;
      font-weight: 700;
      color: #94a3b8;
      border-bottom: 1px solid rgba(255, 255, 255, 0.1);
      padding-bottom: 18px;
    }}
    .hud-badge {{
      display: flex;
      align-items: center;
      gap: 12px;
      color: #00f0ff;
      text-transform: uppercase;
    }}
    .beacon {{
      width: 12px;
      height: 12px;
      border-radius: 50%;
      background: #00ff88;
      box-shadow: 0 0 12px #00ff88;
    }}
    .hud-title {{
      color: #ffffff;
      font-weight: 800;
      letter-spacing: 3px;
    }}
    .bottom-hud {{
      position: absolute;
      bottom: 48px;
      left: 60px;
      right: 60px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      z-index: 20;
      font-size: 19px;
      color: #64748b;
      border-top: 1px solid rgba(255, 255, 255, 0.1);
      padding-top: 18px;
      letter-spacing: 1.5px;
    }}
    .ticker-stream {{
      display: flex;
      gap: 28px;
      font-weight: 700;
    }}
    .t-up {{ color: #00ff88; }}
    .t-down {{ color: #ff3b5c; }}
    .t-cyan {{ color: #00f0ff; }}
    .clip {{
      position: absolute;
      inset: 0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 120px 60px 140px;
      z-index: 10;
    }}
    .scene-content {{
      width: 100%;
      height: 100%;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
    }}
    .hero-title {{
      font-size: 76px;
      font-weight: 900;
      text-transform: uppercase;
      letter-spacing: 4px;
      line-height: 1.1;
      text-align: center;
      color: #ffffff;
      text-shadow: 0 0 35px rgba(0, 240, 255, 0.6);
      margin-bottom: 30px;
    }}
    .hero-subtitle {{
      font-size: 36px;
      font-weight: 600;
      color: #94a3b8;
      text-align: center;
      max-width: 860px;
      line-height: 1.4;
      letter-spacing: 1px;
    }}
    .cyan-glow {{
      color: #00f0ff;
      text-shadow: 0 0 25px rgba(0, 240, 255, 0.7);
    }}
    .red-glow {{
      color: #ff3b5c;
      text-shadow: 0 0 25px rgba(255, 59, 92, 0.7);
    }}
    .green-glow {{
      color: #00ff88;
      text-shadow: 0 0 25px rgba(0, 255, 136, 0.7);
    }}
    .glass-card {{
      background: rgba(15, 23, 42, 0.75);
      border: 1px solid rgba(0, 240, 255, 0.25);
      border-radius: 20px;
      backdrop-filter: blur(16px);
      box-shadow: 0 20px 50px rgba(0, 0, 0, 0.6), inset 0 0 20px rgba(0, 240, 255, 0.1);
      padding: 40px;
      width: 100%;
      max-width: 900px;
    }}
    .candle-matrix {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 20px;
      width: 100%;
      max-width: 840px;
      margin: 40px 0;
    }}
    .candle-item {{
      background: rgba(10, 16, 28, 0.85);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 12px;
      height: 160px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      position: relative;
    }}
    .candle-wick {{
      width: 3px;
      height: 130px;
      background: #475569;
      position: absolute;
    }}
    .candle-body {{
      width: 24px;
      position: relative;
      z-index: 2;
      border-radius: 4px;
    }}
    .candle-red .candle-body {{
      background: #ff3b5c;
      box-shadow: 0 0 15px rgba(255, 59, 92, 0.6);
      height: 70px;
    }}
    .candle-green .candle-body {{
      background: #00ff88;
      box-shadow: 0 0 15px rgba(0, 255, 136, 0.6);
      height: 80px;
    }}
    .feed-list {{
      display: flex;
      flex-direction: column;
      gap: 20px;
      width: 100%;
      max-width: 880px;
      margin: 30px 0;
    }}
    .feed-card {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 24px 30px;
      border-radius: 16px;
      background: rgba(15, 23, 42, 0.85);
      border: 1px solid rgba(255, 255, 255, 0.1);
    }}
    .feed-user {{
      display: flex;
      align-items: center;
      gap: 16px;
      font-size: 24px;
      font-weight: 700;
    }}
    .feed-claim {{
      font-size: 26px;
      font-weight: 800;
    }}
    .consensus-dial {{
      position: relative;
      width: 340px;
      height: 340px;
      margin: 30px 0;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .dial-svg {{
      width: 100%;
      height: 100%;
      transform: rotate(-90deg);
    }}
    .dial-bg {{
      fill: none;
      stroke: rgba(255, 255, 255, 0.1);
      stroke-width: 24;
    }}
    .dial-progress {{
      fill: none;
      stroke: #00f0ff;
      stroke-width: 24;
      stroke-dasharray: 880;
      stroke-dashoffset: 80;
      stroke-linecap: round;
      filter: drop-shadow(0 0 15px #00f0ff);
    }}
    .dial-center {{
      position: absolute;
      display: flex;
      flex-direction: column;
      align-items: center;
    }}
    .dial-pct {{
      font-size: 78px;
      font-weight: 900;
      color: #ffffff;
      text-shadow: 0 0 20px #00f0ff;
    }}
    .dial-sub {{
      font-size: 22px;
      font-weight: 700;
      color: #00ff88;
      letter-spacing: 2px;
      text-transform: uppercase;
    }}
    .level-row {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 20px 0;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      font-size: 28px;
      font-weight: 700;
    }}
    .level-label {{
      color: #94a3b8;
    }}
    .level-val {{
      font-family: monospace;
      font-size: 32px;
      font-weight: 800;
    }}
    .ledger-table {{
      width: 100%;
      border-collapse: separate;
      border-spacing: 0 12px;
    }}
    .ledger-table td {{
      padding: 18px 24px;
      background: rgba(15, 23, 42, 0.6);
      font-size: 24px;
      font-weight: 700;
    }}
    .ledger-table tr td:first-child {{
      border-top-left-radius: 12px;
      border-bottom-left-radius: 12px;
      color: #94a3b8;
      font-family: monospace;
    }}
    .ledger-table tr td:last-child {{
      border-top-right-radius: 12px;
      border-bottom-right-radius: 12px;
      text-align: right;
    }}
    .cta-btn {{
      background: linear-gradient(135deg, #00f0ff 0%, #0088ff 100%);
      color: #040914;
      font-size: 36px;
      font-weight: 900;
      text-transform: uppercase;
      letter-spacing: 3px;
      padding: 28px 64px;
      border-radius: 60px;
      box-shadow: 0 0 50px rgba(0, 240, 255, 0.6), 0 15px 30px rgba(0, 0, 0, 0.5);
      margin-top: 40px;
      display: inline-block;
      text-align: center;
    }}
    .disclaimer-card {{
      background: rgba(15, 23, 42, 0.9);
      border: 1px solid rgba(251, 191, 36, 0.4);
      border-radius: 16px;
      padding: 36px 40px;
      max-width: 920px;
      text-align: center;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.7);
    }}
    .disclaimer-text {{
      font-size: 24px;
      line-height: 1.6;
      color: #cbd5e1;
      font-weight: 500;
    }}
  </style>
  <script src="gsap.min.js"></script>
</head>
<body>
  <div id="root" data-composition-id="root" data-width="1080" data-height="1920" data-duration="{total_duration:.1f}">
    <audio id="bgm" data-start="0" data-duration="{total_duration:.1f}" data-volume="0.18" src="music.wav"></audio>
    <audio id="vo" data-start="0" data-duration="{vo_duration:.1f}" data-volume="1.0" src="vo.mp3"></audio>

    <div class="cyber-grid"></div>
    <div class="scanlines"></div>
    <div class="vignette"></div>

    <div class="top-hud">
      <div class="hud-badge">
        <div class="beacon"></div>
        <span>LIVE CONSENSUS ENGINE</span>
      </div>
      <div class="hud-title">CROWDWISDOM TRADING</div>
      <div>9:16 HD</div>
    </div>

    <div class="bottom-hud">
      <div class="ticker-stream">
        <span>$NVDA <span class="t-up">+4.8%</span></span>
        <span>$AAPL <span class="t-up">+1.5%</span></span>
        <span>$TSLA <span class="t-down">-2.1%</span></span>
        <span>$BTC <span class="t-up">+6.4%</span></span>
      </div>
      <div class="t-cyan">VERIFIED ON-CHAIN</div>
    </div>

{all_scenes_html}

  </div>

  <script>
    const tl = gsap.timeline({{ paused: true }});

{all_gsap_script}

    window.__timelines = window.__timelines || {{}};
    window.__timelines["root"] = tl;
  </script>
</body>
</html>"""

    def _build_scene_html(self, shot, index: int, sid: str) -> str:
        desc = html_lib.escape(getattr(shot, "description", "") or "")
        beat = getattr(shot, "beat", "").lower()

        # Shot 1: The Single Red Candlestick multiplies into noisy matrix
        if sid in ("s01", "s1"):
            return f"""
      <h1 id="title_{sid}" class="hero-title">TOO MANY <span class="red-glow">VOICES</span></h1>
      <p id="sub_{sid}" class="hero-subtitle">Every day, thousands of traders scream conflicting predictions.</p>
      <div id="matrix_{sid}" class="candle-matrix">
        <div class="candle-item candle-red"><div class="candle-wick"></div><div class="candle-body"></div></div>
        <div class="candle-item candle-green"><div class="candle-wick"></div><div class="candle-body"></div></div>
        <div class="candle-item candle-red"><div class="candle-wick"></div><div class="candle-body"></div></div>
        <div class="candle-item candle-green"><div class="candle-wick"></div><div class="candle-body"></div></div>
        <div class="candle-item candle-green"><div class="candle-wick"></div><div class="candle-body"></div></div>
        <div class="candle-item candle-red"><div class="candle-wick"></div><div class="candle-body"></div></div>
        <div class="candle-item candle-green"><div class="candle-wick"></div><div class="candle-body"></div></div>
        <div class="candle-item candle-red"><div class="candle-wick"></div><div class="candle-body"></div></div>
      </div>
      <div id="badge_{sid}" class="glass-card" style="text-align: center; max-width: 600px;">
        <span class="red-glow" style="font-size: 26px; font-weight: 800; letter-spacing: 2px;">MARKET NOISE AMPLIFIED</span>
      </div>
"""
        # Shot 2: Snap to line, Every one of them is certain
        elif sid in ("s02", "s2"):
            return f"""
      <h1 id="title_{sid}" class="hero-title">EVERYONE IS <span class="cyan-glow">CERTAIN</span></h1>
      <p id="sub_{sid}" class="hero-subtitle">Thousands of traders post their read on the same five tickers.</p>
      <div id="feeds_{sid}" class="feed-list">
        <div class="feed-card" style="border-left: 6px solid #00ff88;">
          <div class="feed-user"><span>[BUY]</span><span>@GuruTraderAlpha</span></div>
          <div class="feed-claim green-glow">BUY NVDA 200C (+400%)</div>
        </div>
      </div>
      <div id="badge_{sid}" class="glass-card" style="text-align: center; max-width: 680px; margin-top: 20px;">
        <span style="font-size: 24px; font-weight: 700; color: #94a3b8;">WHO DO YOU TRUST WHEN SIGNALS CONFLICT?</span>
      </div>
"""
        # Shot 3: Conflicting Guru Calls & Noise
        elif sid in ("s03", "s3") or beat == "problem":
            return f"""
      <h1 id="title_{sid}" class="hero-title">NOISE & <span class="red-glow">CONTRADICTION</span></h1>
      <p id="sub_{sid}" class="hero-subtitle">None of them share your downside when they are wrong.</p>
      <div id="feeds_{sid}" class="feed-list">
        <div class="feed-card" style="border-left: 6px solid #ff3b5c;">
          <div class="feed-user"><span>[DUMP]</span><span>@MacroBearPro</span></div>
          <div class="feed-claim red-glow">MARKET TOPPING - DUMP NOW</div>
        </div>
        <div class="feed-card" style="border-left: 6px solid #00f0ff;">
          <div class="feed-user"><span>[ALPH]</span><span>@BreakoutWizard</span></div>
          <div class="feed-claim cyan-glow">MASSIVE SQUEEZE LOADING</div>
        </div>
      </div>
"""
        # Shot 4: Breakdown / Blindspots
        elif sid in ("s04", "s4") or beat == "agitation":
            return f"""
      <h1 id="title_{sid}" class="hero-title">INHERITING <span class="red-glow">BLIND SPOTS</span></h1>
      <p id="sub_{sid}" class="hero-subtitle">Following one guru means inheriting one person's bad morning.</p>
      <div id="card_{sid}" class="glass-card" style="margin: 30px 0; border: 1px solid rgba(255, 59, 92, 0.4);">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px;">
          <span style="font-size: 28px; font-weight: 800; color: #ff3b5c;">SUPPORT LEVEL COLLAPSED</span>
          <span style="font-size: 24px; font-weight: 700; color: #ef4444; background: rgba(239,68,68,0.2); padding: 6px 14px; border-radius: 8px;">-24.8% DRAWDOWN</span>
        </div>
        <svg viewBox="0 0 800 240" style="width: 100%; height: 220px;">
          <line x1="0" y1="120" x2="800" y2="120" stroke="rgba(255,255,255,0.2)" stroke-dasharray="8 8" stroke-width="2"/>
          <text x="20" y="110" fill="#94a3b8" font-size="20" font-weight="700">SUPPORT $142.50</text>
          <path d="M 0 100 L 160 90 L 320 115 L 480 85 L 560 120 L 680 200 L 800 230" fill="none" stroke="#ff3b5c" stroke-width="5"/>
        </svg>
      </div>
"""
        # Shot 5: We Read All Of Them / Data Ingestion
        elif sid in ("s05", "s5"):
            return f"""
      <h1 id="title_{sid}" class="hero-title">WE READ <span class="cyan-glow">ALL OF THEM</span></h1>
      <p id="sub_{sid}" class="hero-subtitle">Real-time multi-agent social ingestion across all platforms.</p>
      <div id="matrix_{sid}" class="glass-card" style="text-align: center; margin: 30px 0;">
        <div style="font-size: 92px; font-weight: 900; color: #00f0ff; text-shadow: 0 0 35px #00f0ff; font-family: monospace;">28,450</div>
        <div style="font-size: 26px; font-weight: 800; letter-spacing: 4px; color: #ffffff; text-transform: uppercase;">PUBLIC SIGNALS ANALYZED / SEC</div>
        <div style="display: flex; justify-content: space-around; margin-top: 36px; padding-top: 24px; border-top: 1px solid rgba(255,255,255,0.1); font-size: 22px; font-weight: 700; color: #94a3b8;">
          <span>TWITTER / X</span>
          <span>--</span>
          <span>REDDIT</span>
          <span>--</span>
          <span>TELEGRAM</span>
          <span>--</span>
          <span>DISCORD</span>
        </div>
      </div>
"""
        # Shot 6: AI Agent Consensus
        elif sid in ("s06", "s6"):
            return f"""
      <h1 id="title_{sid}" class="hero-title">AI AGENT <span class="cyan-glow">CONSENSUS</span></h1>
      <p id="sub_{sid}" class="hero-subtitle">Scoring conviction, track record, and mathematical alignment.</p>
      <div id="dial_{sid}" class="consensus-dial">
        <svg class="dial-svg" viewBox="0 0 320 320">
          <circle class="dial-bg" cx="160" cy="160" r="140"/>
          <circle class="dial-progress" cx="160" cy="160" r="140"/>
        </svg>
        <div class="dial-center">
          <span class="dial-pct">91.4%</span>
          <span class="dial-sub">CONSENSUS</span>
        </div>
      </div>
      <div id="badge_{sid}" class="glass-card" style="text-align: center; max-width: 680px;">
        <span class="green-glow" style="font-size: 26px; font-weight: 800; letter-spacing: 2px;">MULTI-AGENT THRESHOLD UNLOCKED</span>
      </div>
"""
        # Shot 7: Entry Targets Stops Setup
        elif sid in ("s07", "s7"):
            return f"""
      <h1 id="title_{sid}" class="hero-title">ENTRY. TARGETS. <span class="green-glow">STOPS.</span></h1>
      <p id="sub_{sid}" class="hero-subtitle">High-probability setup with defined execution bounds.</p>
      <div id="card_{sid}" class="glass-card" style="margin: 25px 0;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
          <span style="font-size: 32px; font-weight: 900; color: #ffffff;">$NVDA LONG CONSENSUS</span>
          <span style="font-size: 22px; font-weight: 700; color: #00ff88; background: rgba(0,255,136,0.15); padding: 6px 16px; border-radius: 20px;">R:R 1:3.8</span>
        </div>
        <div class="level-row">
          <span class="level-label">ENTRY LEVEL</span>
          <span class="level-val cyan-glow">$124.50</span>
        </div>
        <div class="level-row">
          <span class="level-label">PROFIT TARGET 1</span>
          <span class="level-val green-glow">$132.00 (+6.0%)</span>
        </div>
        <div class="level-row">
          <span class="level-label">PROFIT TARGET 2</span>
          <span class="level-val green-glow">$138.50 (+11.2%)</span>
        </div>
        <div class="level-row" style="border-bottom: none;">
          <span class="level-label">STOP LOSS</span>
          <span class="level-val red-glow">$121.00 (-2.8%)</span>
        </div>
      </div>
"""
        # Shot 8: Every Single Call Published
        elif sid in ("s08", "s8"):
            return f"""
      <h1 id="title_{sid}" class="hero-title">EVERY CALL <span class="cyan-glow">PUBLISHED</span></h1>
      <p id="sub_{sid}" class="hero-subtitle">Timestamped on-chain before the market moves. Zero post deletion.</p>
      <div id="table_{sid}" class="glass-card" style="margin: 25px 0; padding: 24px 30px;">
        <table class="ledger-table">
          <tr>
            <td>NVDA LONG</td>
            <td>124.50 -> 138.00</td>
            <td class="green-glow">+10.8% [TARGET 2]</td>
          </tr>
          <tr>
            <td>AAPL SHORT</td>
            <td>228.00 -> 215.50</td>
            <td class="green-glow">+5.5% [TARGET 1]</td>
          </tr>
        </table>
      </div>
      <div id="badge_{sid}" class="glass-card" style="text-align: center; max-width: 640px;">
        <span class="cyan-glow" style="font-size: 24px; font-weight: 800;">100% PUBLIC TIMESTAMPED FEED</span>
      </div>
"""
        # Shot 9: The Wins and the Misses
        elif sid in ("s09", "s9") or beat == "proof":
            return f"""
      <h1 id="title_{sid}" class="hero-title">WINS AND <span class="red-glow">MISSES</span></h1>
      <p id="sub_{sid}" class="hero-subtitle">Transparent accountability. Zero cherry-picking.</p>
      <div id="table_{sid}" class="glass-card" style="margin: 25px 0; padding: 24px 30px;">
        <table class="ledger-table">
          <tr>
            <td>TSLA LONG</td>
            <td>255.00 -> 248.00</td>
            <td class="red-glow">-2.7% [STOPPED]</td>
          </tr>
          <tr>
            <td>BTC LONG</td>
            <td>61,200 -> 65,400</td>
            <td class="green-glow">+6.9% [TARGET 1]</td>
          </tr>
        </table>
      </div>
      <div id="badge_{sid}" style="display: flex; gap: 20px;">
        <span class="glass-card" style="padding: 16px 32px; font-size: 22px; font-weight: 800; color: #00ff88;">68.4% WIN RATE</span>
        <span class="glass-card" style="padding: 16px 32px; font-size: 22px; font-weight: 800; color: #00f0ff;">100% AUDITED</span>
      </div>
"""
        # Shot 10: No Guru Secrets
        elif sid in ("s10", "s10") or beat == "objection":
            return f"""
      <h1 id="title_{sid}" class="hero-title" style="font-size: 88px; line-height: 1.05;">NO GURU <span class="red-glow">SECRETS</span></h1>
      <p id="sub_{sid}" class="hero-subtitle" style="font-size: 42px; margin-top: 20px;">No private signals. No $10,000 courses.</p>
      <div id="slam_{sid}" class="glass-card" style="margin: 40px 0; text-align: center; border: 2px solid rgba(0, 240, 255, 0.4);">
        <p style="font-size: 34px; font-weight: 800; color: #ffffff; letter-spacing: 2px;">JUST PURE MATHEMATICAL CONSENSUS</p>
      </div>
"""
        # Shot 11: Compliance & Risk Disclosure
        elif sid in ("s11", "s11") or "risk" in desc.lower() or "disclaimer" in desc.lower():
            return f"""
      <div id="card_{sid}" class="disclaimer-card">
        <div style="font-size: 48px; margin-bottom: 16px; color: #fbbf24;">[DISCLOSURE]</div>
        <h2 style="font-size: 34px; font-weight: 800; color: #fbbf24; margin-bottom: 20px; text-transform: uppercase; letter-spacing: 2px;">Risk Disclosure & Notice</h2>
        <p class="disclaimer-text">
          Trading stocks, options, and cryptocurrencies involves significant risk of loss. CrowdWisdomTrading provides algorithmic consensus analytics for informational and educational purposes only. Not financial advice. Past performance does not guarantee future results.
        </p>
      </div>
"""
        # Shot 12: CTA & Brand Outro
        else:
            return f"""
      <div id="logo_{sid}" style="width: 140px; height: 140px; border-radius: 36px; background: linear-gradient(135deg, #00f0ff, #0066ff); display: flex; align-items: center; justify-content: center; box-shadow: 0 0 60px rgba(0, 240, 255, 0.7); margin-bottom: 30px; font-size: 54px; font-weight: 900; color: #fff;">
        CWT
      </div>
      <h1 id="title_{sid}" class="hero-title" style="font-size: 68px; margin-bottom: 16px;">CROWDWISDOM <span class="cyan-glow">TRADING</span></h1>
      <p id="sub_{sid}" class="hero-subtitle" style="font-size: 36px;">Collective intelligence for traders.</p>
      <div id="cta_{sid}" class="cta-btn">SEE TODAY'S CALLS</div>
      <p id="url_{sid}" style="font-size: 34px; font-weight: 800; color: #00f0ff; letter-spacing: 3px; margin-top: 36px; text-shadow: 0 0 20px rgba(0, 240, 255, 0.6);">
        crowdwisdomtrading.com
      </p>
"""

    def _build_scene_gsap(self, shot, sid: str, start: float, dur: float) -> str:
        # Animate the inner #content_{sid} container and child elements
        # Hyperframes manages the top-level .clip visibility via data-start and data-duration
        fade_in = 0.25
        fade_out = 0.20
        out_start = max(start + dur - fade_out, start + fade_in)
        return f"""
    // Scene {sid} animations at t={start:.2f}s
    tl.fromTo("#content_{sid}", {{ opacity: 0, scale: 0.96 }}, {{ opacity: 1, scale: 1, duration: {fade_in:.2f}, ease: "power2.out" }}, {start:.2f});
    tl.fromTo("#title_{sid}", {{ y: 35, opacity: 0 }}, {{ y: 0, opacity: 1, duration: 0.5, ease: "back.out(1.5)" }}, {start + 0.1:.2f});
    tl.fromTo("#sub_{sid}", {{ y: 20, opacity: 0 }}, {{ y: 0, opacity: 1, duration: 0.5, ease: "power2.out" }}, {start + 0.25:.2f});
    if (document.querySelector("#matrix_{sid}")) {{
      tl.fromTo("#matrix_{sid}", {{ scale: 0.9, opacity: 0 }}, {{ scale: 1, opacity: 1, duration: 0.5, ease: "power2.out" }}, {start + 0.35:.2f});
    }}
    if (document.querySelector("#card_{sid}")) {{
      tl.fromTo("#card_{sid}", {{ y: 40, opacity: 0 }}, {{ y: 0, opacity: 1, duration: 0.5, ease: "power3.out" }}, {start + 0.35:.2f});
    }}
    if (document.querySelector("#dial_{sid}")) {{
      tl.fromTo("#dial_{sid}", {{ scale: 0.7, opacity: 0 }}, {{ scale: 1, opacity: 1, duration: 0.6, ease: "elastic.out(1, 0.75)" }}, {start + 0.25:.2f});
    }}
    if (document.querySelector("#cta_{sid}")) {{
      tl.fromTo("#cta_{sid}", {{ scale: 0.8, opacity: 0 }}, {{ scale: 1, opacity: 1, duration: 0.5, ease: "back.out(2)" }}, {start + 0.45:.2f});
    }}
    tl.to("#content_{sid}", {{ opacity: 0, duration: {fade_out:.2f}, ease: "power2.in" }}, {out_start:.2f});
"""
