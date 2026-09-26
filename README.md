# CWT Video Ads Agent

Autonomous video advertising pipeline powered by Hermes Agent and local/cloud models.

## Quickstart

```powershell
.\scripts\bootstrap.ps1
```

Or manually:
```bash
python -m venv .venv
.venv/Scripts/pip install -e . -r requirements.txt -r requirements-dev.txt
cp .env.example .env
.venv/Scripts/python -m cwt doctor
```
