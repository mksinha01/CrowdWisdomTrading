"""Fetch the Piper TTS voice model used by the pipeline.

The model is en_US-ryan-high.onnx (~60 MB). We download it from the official
Piper GitHub releases so we do not have to commit a large binary.

Usage:
  python scripts/fetch_piper_voice.py
"""
import argparse
import sys
import urllib.request
from pathlib import Path

# The specific voice we want
VOICE_NAME = "en_US-ryan-high"
BASE_URL = "https://github.com/rhasspy/piper/releases/download/v0.0.2/voice-en-us-ryan-high.tar.gz"

DEST_DIR = Path(__file__).parent.parent / "fixtures" / "assets" / "voices"

def fetch_voice(force: bool = False) -> None:
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    
    onnx_path = DEST_DIR / f"{VOICE_NAME}.onnx"
    json_path = DEST_DIR / f"{VOICE_NAME}.onnx.json"
    
    if onnx_path.exists() and json_path.exists() and not force:
        print(f"Voice {VOICE_NAME} already exists in {DEST_DIR}. Skipping.")
        return
        
    print(f"Downloading {VOICE_NAME} from {BASE_URL}...")
    
    import tarfile
    import tempfile
    
    with tempfile.TemporaryDirectory() as td:
        tgz_path = Path(td) / "voice.tar.gz"
        urllib.request.urlretrieve(BASE_URL, str(tgz_path))
        
        with tarfile.open(tgz_path, "r:gz") as tar:
            for member in tar.getmembers():
                if member.name.endswith(".onnx") or member.name.endswith(".onnx.json"):
                    # Extract just the filename, not the directory structure inside the tar
                    filename = Path(member.name).name
                    dest_file = DEST_DIR / filename
                    print(f"Extracting {filename} to {dest_file}...")
                    
                    source = tar.extractfile(member)
                    if source:
                        with open(dest_file, "wb") as f:
                            f.write(source.read())
                            
    print(f"Successfully downloaded {VOICE_NAME} to {DEST_DIR}")

def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Piper TTS voice")
    parser.add_argument("--force", action="store_true", help="Redownload even if files exist")
    args = parser.parse_args()
    
    try:
        fetch_voice(args.force)
    except Exception as e:
        print(f"Error fetching voice: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
