This directory holds Piper TTS voice model files (.onnx + .json config).

The primary voice used by the pipeline is:
  en_US-ryan-high.onnx  (~60 MB)
  en_US-ryan-high.onnx.json

These files are NOT committed to the repository because of their size.

HOW TO OBTAIN THEM
------------------
Run the fetch script:

    .venv/Scripts/python scripts/fetch_piper_voice.py

This downloads from the official Piper releases on GitHub:
  https://github.com/rhasspy/piper/releases

OFFLINE FALLBACK
----------------
If the .onnx file is absent, the TTS chain (src/cwt/clients/tts.py) reports
``available=False`` for the piper backend and falls through to the ``silent``
path, which burns-in captions. The resulting video is still watchable and
fully compliant — the voiceover is delivered as on-screen text.

This is a supported path, not an error. The ``cwt run --engine local --offline``
demo works without this file.
