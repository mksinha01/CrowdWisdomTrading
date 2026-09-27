---
name: cwt-final-qa
description: Final QA — duration, aspect, loudness, post-render claims re-check.
version: 1.0.0
metadata:
  hermes:
    tags: [qa, verification, compliance]
    category: marketing
    requires_toolsets: [cwt]
---

# Final QA

## When to Use
You are the `cwt-qa` and a card asks you to verify the rendered output.

## Procedure
1. `kanban_show()` — read the `render` card's `metadata.artifact_path` to get `render_manifest.json`
   and the output video path.
2. Call `cwt_probe_media` on the output video to verify:
   - Duration within 30-60 seconds
   - Resolution 1080x1920 (9:16)
   - 30 fps
   - Video codec h264, audio codec aac
   - Integrated loudness -14 LUFS ±1, true peak -1.5 dBTP
3. Re-run the claims engine over the RENDERED voiceover transcript:
   - Extract the transcript from the rendered audio (or use the storyboard's voiceover segments
     with actual TTS timings).
   - Call `cwt_check_claims` on the rendered transcript.
   - TTS normalisation changes what is actually said — a line added during render would
     otherwise bypass the gate entirely.
4. Recompute the risk disclosure's on-screen duration from the rendered timeline,
   not the declared value.
5. Call `kanban_complete` with `metadata.artifact_path` (QA report).

## Pitfalls
- Never skip the post-render claims check. TTS normalisation changes what is actually said,
  and a line added during render would otherwise bypass the gate entirely.
- Never accept a file that fails ffprobe validation (duration, aspect, codecs).
- Never accept loudness outside the target LUFS range.
- The risk disclosure must be on-screen for at least 3 seconds in the rendered output.

## Verification
`cwt_verify_artifact --name render_manifest` returns `{"ok": true}` (re-verified) and
all QA checks pass. The final `claims_report.json` (post-render) verdict is `pass`.