"""Recording pacing helpers for the Kanban board demo video.

These utilities support ``cwt run --record-pacing``. The pacing pause is a
deliberate sleep so the recording is watchable rather than a blur.

Security note, non-negotiable:
    The dashboard's plugin routes are unauthenticated by design.
    NEVER run ``hermes dashboard --host 0.0.0.0``.
    Always bind to localhost (the default). See §11.1 and the README.
"""
from __future__ import annotations

import os
import time


def record_pacing_pause(stage: str, *, seconds: float = 4.5) -> None:
    """Sleep between stage completions so the recording is watchable.

    Only called when ``--record-pacing`` is active. Use 4.5 seconds per stage
    (spec line 4392 specifies 3–6s; 4.5s is the midpoint).
    """
    if not os.getenv("CWT_RECORD_PACING"):
        return
    print(f"[record-pacing] Pausing {seconds}s after '{stage}' so the recording can breathe…")
    time.sleep(seconds)


def recording_recipe() -> str:
    """Return the canonical recording checklist string.

    S34's PowerShell script (``scripts/record_kanban_video.ps1``) prints this.
    Returned verbatim from spec lines 4374–4394.
    """
    return """\
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
"""
