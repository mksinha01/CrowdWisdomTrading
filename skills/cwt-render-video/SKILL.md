---
name: cwt-render-video
description: Render the 30-60s video from storyboard.json through the backend chain.
version: 1.0.0
metadata:
  hermes:
    tags: [video, render, ffmpeg]
    category: marketing
    requires_toolsets: [cwt]
---

# Render Video

## When to Use
You are the `cwt-video-editor` and a card asks you to render the video from the approved storyboard.

## Procedure
1. `kanban_show()` — read the `compliance` card's `metadata.artifact_path` to get `storyboard.json`
   (the compliance card overwrites it in place with any fixes).
2. Call `cwt_synthesize_voiceover` with the storyboard's voiceover text.
   The TTS chain is: edge-tts → piper → silent+captions.
3. Call `cwt_render_video` with the storyboard and voiceover audio path.
   It resolves assets (CC0 allowlist, content-hash cache) and renders through the backend chain:
   - hyperframes (optional, Node 22+)
   - openmontage (optional, best-effort, AGPL-3.0)
   - local_ffmpeg (guaranteed floor — ALWAYS tried last)
4. The tool returns a `render_manifest.json` with the exact ffmpeg argv, backend chain tried,
   asset licences, and output file path.
5. Call `cwt_probe_media` on the output file to verify duration, aspect, codecs, loudness.
6. Call `kanban_complete` with `metadata.artifact_path = render_manifest.json`.

## Pitfalls
- Never declare success without probing the output. ffmpeg exits 0 having written a
  0-byte file when the last frame is dropped (Rule V3).
- Never skip the backend chain. Even if hyperframes fails, you must try local_ffmpeg.
  Earlier backends failing is expected and is recorded, not reported as an error.
- Never produce an artifact without a `render_manifest.json` recording the exact argv.
- The chain ALWAYS terminates in local_ffmpeg — a failure there is a real failure.

## Verification
`cwt_verify_artifact --name render_manifest` returns `{"ok": true}` and
ffprobe confirms: duration 30-60s, 1080x1920, 30fps, h264/aac, loudness -14 LUFS ±1.