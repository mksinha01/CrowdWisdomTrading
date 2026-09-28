# scripts/record_kanban_video.ps1
Write-Host "=== CWT Kanban recording recipe ===" -ForegroundColor Cyan

# 1. Verify the dashboard is up before you start recording.
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:9119" -UseBasicParsing -TimeoutSec 5
    Write-Host "  dashboard OK ($($r.StatusCode))" -ForegroundColor Green
} catch {
    Write-Host "  dashboard NOT reachable. Run: hermes gateway start; hermes dashboard" -ForegroundColor Red
    exit 1
}

@"
RECORDING CHECKLIST
  1.  hermes kanban init            (one-time)
  2.  hermes gateway start
  3.  hermes dashboard              -> open http://127.0.0.1:9119 -> Kanban tab
  4.  Turn ON "Lanes by profile"    -> makes the parallelism legible
  5.  Start your screen recorder    (Screen Studio, OBS, or Win+Shift+S->video)
  6.  Layout: terminal left ~40%, dashboard right ~60%
  7.  Run:  cwt run --offline --record-pacing
  8.  Record the first 5-10 minutes, then speed up 4-8x in post

MOMENTS WORTH CAPTURING
  - The seed command, and 11 cards appearing at once
  - The three research cards running CONCURRENTLY (this is the money shot)
  - The storyboard card moving running -> review -> running -> done
  - The compliance card's comment quoting a blocked claim
  - The render card finishing, and final.mp4 appearing on disk

NOTE: --record-pacing inserts 3-6s pauses between stage completions so the
      recording is watchable rather than a blur. Use it ONLY when recording.
"@ | Write-Host
