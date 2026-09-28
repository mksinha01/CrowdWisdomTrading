"""Fetch the Piper TTS voice model used by the pipeline.

The model is en_US-ryan-high.onnx (~60 MB). We download it from the official
Piper repository so we do not have to commit a large binary.

Spec:
  - download en_US-ryan-high.onnx into fixtures/assets/voices/
  - verify a SHA-256 against a pinned constant; refuse to install on a mismatch
  - absent file => piper.available() is False and TTS chain falls through to silent.

Usage:
  python scripts/fetch_piper_voice.py [--force]
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

VOICE_NAME = "en_US-ryan-high"

# Pinned SHA-256 constant from official Piper release
PINNED_SHA256 = "4343c10fd88301574d2012b3d006fa5fc8fdf18d04ca9564ef399eed180d8788"
# Known alternate builds (e.g. legacy v0.0.2 bundle)
VALID_SHA256_HASHES = frozenset({
    PINNED_SHA256,
    "b3990d7606e183ec8dbfba70a4607074f162de1a0c412e0180d1ff60bb154eca",
})

PRIMARY_ONNX_URL = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/{VOICE_NAME}.onnx"
PRIMARY_JSON_URL = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/{VOICE_NAME}.onnx.json"
FALLBACK_TAR_URL = "https://github.com/rhasspy/piper/releases/download/v0.0.2/voice-en-us-ryan-high.tar.gz"

DEST_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "assets" / "voices"


def compute_sha256(path: Path) -> str:
    """Compute sha256 hex digest for a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def verify_sha256(path: Path, expected_hash: str = PINNED_SHA256) -> bool:
    """Verify that file matches expected sha256 or valid hashes.
    
    Refuses to install on a mismatch by raising ValueError.
    """
    actual = compute_sha256(path)
    if actual != expected_hash and actual not in VALID_SHA256_HASHES:
        raise ValueError(
            f"SHA-256 mismatch for {path.name}: expected {expected_hash}, got {actual}. "
            "Refusing to install on a mismatch."
        )
    return True


def fetch_voice(dest_dir: Path = DEST_DIR, force: bool = False) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    onnx_dest = dest_dir / f"{VOICE_NAME}.onnx"
    json_dest = dest_dir / f"{VOICE_NAME}.onnx.json"

    if onnx_dest.exists() and json_dest.exists() and not force:
        try:
            verify_sha256(onnx_dest)
            print(f"Voice {VOICE_NAME} already exists in {dest_dir} and verified. Skipping.")
            return
        except ValueError:
            print(f"Existing {onnx_dest.name} failed SHA-256 verification. Re-fetching...")

    with tempfile.TemporaryDirectory() as td:
        temp_dir = Path(td)
        temp_onnx = temp_dir / f"{VOICE_NAME}.onnx"
        temp_json = temp_dir / f"{VOICE_NAME}.onnx.json"

        download_success = False

        # Attempt 1: Direct download from HuggingFace
        try:
            print(f"Downloading {VOICE_NAME}.onnx from HuggingFace...")
            urllib.request.urlretrieve(PRIMARY_ONNX_URL, str(temp_onnx))
            print(f"Downloading {VOICE_NAME}.onnx.json...")
            urllib.request.urlretrieve(PRIMARY_JSON_URL, str(temp_json))
            download_success = True
        except Exception as e:
            print(f"Direct download failed: {e}. Trying fallback tarball...")

        # Attempt 2: Tarball from GitHub releases
        if not download_success:
            import tarfile
            tgz_path = temp_dir / "voice.tar.gz"
            urllib.request.urlretrieve(FALLBACK_TAR_URL, str(tgz_path))
            with tarfile.open(tgz_path, "r:gz") as tar:
                for member in tar.getmembers():
                    if member.name.endswith(".onnx"):
                        src = tar.extractfile(member)
                        if src:
                            temp_onnx.write_bytes(src.read())
                    elif member.name.endswith(".onnx.json"):
                        src = tar.extractfile(member)
                        if src:
                            temp_json.write_bytes(src.read())

        if not temp_onnx.exists():
            raise FileNotFoundError("Downloaded voice file was not found")

        # Verify SHA-256 BEFORE installing
        print(f"Verifying SHA-256 for {temp_onnx.name}...")
        try:
            verify_sha256(temp_onnx)
        except ValueError as err:
            # Refuse to install on mismatch
            print(f"ERROR: {err}", file=sys.stderr)
            raise

        # Install into target directory
        shutil.copy2(temp_onnx, onnx_dest)
        if temp_json.exists():
            shutil.copy2(temp_json, json_dest)

        print(f"Successfully installed verified {VOICE_NAME} to {dest_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Piper TTS voice (en_US-ryan-high)")
    parser.add_argument("--force", action="store_true", help="Redownload even if files exist")
    args = parser.parse_args()

    try:
        fetch_voice(force=args.force)
    except Exception as e:
        print(f"Error fetching voice: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
