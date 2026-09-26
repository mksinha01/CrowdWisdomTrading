# S34 — Scripts & fixture recording

**Phase** 7 · **Depends on** S33 · **Blocks** S35
**Spec** `doc/video-ads-agent.md` lines **975–1036** (§5.2, §5.3), **4352–4395** (§11.2), **339** and **1801–1803** (`scrub_fixtures.py`), **338–341** (fixture gitignore policy)
**Context budget** ~14k (spec 3.5k + story 1.8k + output 8k)
**Produces** `scripts/bootstrap.ps1`, `scripts/bootstrap.sh`, `scripts/record_fixtures.py`, `scripts/scrub_fixtures.py`, `scripts/record_kanban_video.ps1`, `scripts/fetch_piper_voice.py`, `tests/test_scripts.py`

---

## Goal

The one-time and per-run shell entrypoints, plus the tooling that keeps the offline fixture set honest
and secret-free.

> **§5.2 line 982.** *"All real logic lives in Python. This script only creates a venv and calls the
> CLI, deliberately: every line of shell here is a line that behaves differently on Windows."* That
> principle governs every script in this story.

## Interface contract — FROZEN

| Script | Purpose | Network |
|---|---|---|
| `bootstrap.ps1` | **Windows primary.** Python gate → venv → install → copy `.env` → `cwt doctor` | yes |
| `bootstrap.sh` | Linux/macOS secondary. Same four steps | yes |
| `record_fixtures.py` | Deliberately re-record HTTP fixtures. **Makes REAL API calls and spends credits** | yes |
| `scrub_fixtures.py` | Strip tokens from recorded fixtures. Belt-and-braces for Rule R1 | no |
| `record_kanban_video.ps1` | Print the recording recipe; verify the dashboard is up | localhost only |
| `fetch_piper_voice.py` | Download the Piper voice model (too large to commit) | yes |

## Rules that bind this story

- **Rule W1** — no shell string interpolation of user data anywhere. `bootstrap.ps1` passes arguments
  as a list; Python scripts use `argparse` and `run_tool()`.
- **Rule W8 — `.sh` files must be LF.** `.gitattributes` pins `*.sh text eol=lf` (S01), and
  `scrub_fixtures.py` writes any generated file with `newline="\n"`. A CRLF shell script fails on
  Linux with the notoriously opaque `bash\r: No such file or directory`.
- **Rule R1 — fixtures may contain a token in a URL.** Apify passes the token as a query parameter, so
  a recorded response *will* contain it. `fixtures/http/**/*.raw.json` is gitignored until
  `scrub_fixtures.py` has run (spec line 339). The recorder scrubs **on write**; the scrubber is the
  second layer.
- **§11.2 line 4349 — security, non-negotiable.** The dashboard's plugin routes are unauthenticated by
  design. **Never `hermes dashboard --host 0.0.0.0`.** Bind to localhost. `record_kanban_video.ps1`
  probes `127.0.0.1:9119` — keep it that way.
- **`record_fixtures.py --force` spends real money.** It must print a warning and require `--force` or
  an interactive confirmation before making any call.
- **§5.4** — the bootstrap scripts end by running `cwt doctor`, never by running `cwt run`. Spending
  money is a separate, deliberate act.

## Build steps

1. `bootstrap.ps1` — copy spec lines 980–1010 verbatim:
   - `$ErrorActionPreference = "Stop"`
   - Python present? parse `sys.version_info` → require `>= 3.11`, else throw with the python.org URL
   - create `.venv` if absent, install `requirements.txt` + `requirements-dev.txt` + `-e .`
   - copy `.env.example` → `.env` if absent, printing **`FILL IN YOUR KEYS`** in yellow
   - `& .\.venv\Scripts\python.exe -m cwt doctor`
   No logic beyond that. Every added line is a line that behaves differently on Windows.
2. `bootstrap.sh` — copy spec lines 1014–1036 verbatim. `set -euo pipefail`. Same four steps.
3. `scrub_fixtures.py`:
   - walk `fixtures/http/**/*.json` (the committed, non-`.raw` set)
   - apply the **same** regex S10 uses:
     `([?&]token=)[^&\s"']+` → `\1<redacted>`, plus `apify_api_`, `sk-or-v1-`, `nvapi-`, `tvly-`
     prefixes → `<redacted>`
   - also redact `Authorization` / `x-api-key` header values inside any recorded `headers` block
   - **rewrite in place with `newline="\n"` and `ensure_ascii=False`**
   - print a per-file diff count; exit non-zero (1) if anything was found, so it can gate CI
   - `--check` mode: report only, change nothing, exit 1 if any secret remains
4. `record_fixtures.py`:
   - `--stage {ads,patterns,research,storyboard,claims,render,all}` — record only what that stage needs
   - `--force` required to overwrite an existing fixture; otherwise skip existing
   - **print, before any call**: the estimated Apify spend at `APIFY_MAX_CHARGE_USD` and the Tavily
     credit cost (`2 × queries` for advanced)
   - run the real client calls through an `HttpCache(root, offline=False)` so the write-through path
     is the same one `--offline` reads
   - scrub on write — reuse `scrub_fixtures.py`'s function, do not reimplement
5. `fetch_piper_voice.py`:
   - download `en_US-ryan-high.onnx` (≈60 MB) into `fixtures/assets/voices/`
   - verify a SHA-256 against a pinned constant; **refuse to install on a mismatch**
   - absent file ⇒ `piper.available()` is `False` and the TTS chain (S12) falls through to `silent`.
     That is a supported path, so this script is optional.
6. `record_kanban_video.ps1` — copy spec lines 4361–4394 verbatim:
   - probe `http://127.0.0.1:9119` with a 5s timeout; on failure print
     `dashboard NOT reachable. Run: hermes gateway start; hermes dashboard` and `exit 1`
   - print the `RECORDING CHECKLIST` here-string (8 steps) and the `MOMENTS WORTH CAPTURING` block
   - **the checklist must name `--offline --record-pacing`**, because the recording is done offline
   - include the `NOTE:` about `--record-pacing` inserting 3–6s pauses and being for recording only
7. Tests (`tests/test_scripts.py`):
   - `scrub_fixtures.py --check` returns 1 on a fixture containing `?token=apify_api_x`
   - `scrub_fixtures.py` rewrites that fixture and a second `--check` returns 0
   - the scrubber redacts `Authorization: Bearer sk-or-v1-...` inside a `headers` block
   - a rewritten fixture is **LF-only** (`b"\r\n" not in read_bytes()`)
   - `bootstrap.sh` parses under `bash -n`
   - `record_fixtures.py --help` lists every stage; without `--force` on an existing fixture it skips
   - **`record_kanban_video.ps1` contains no `0.0.0.0`** (Rule P1 / dashboard security)

## Decisions the spec leaves open

- **`scrub_fixtures.py` (G6).** Referenced at spec line 339 and never specified. Defined above.
- **`fetch_piper_voice.py`** is an addition — a 60 MB binary must not be committed, and `piper` is
  already an optional chain element with a working fallback.
- **`record_fixtures.py` scrub-on-write** makes `scrub_fixtures.py` redundant in the happy path, which
  is exactly what the spec says: *"The recorder scrubs on write; this is belt-and-braces."*
- **`.gitignore` policy.** `fixtures/http/**/*.raw.json` stays ignored. The scrubbed `<hash>.json`
  files **are** committed — they are the offline fixture set and the reviewer's ability to rerun.
  Verify against S01's `.gitignore`.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_scripts.py -q -v      # green

bash -n scripts/bootstrap.sh && echo "sh ok"
.venv/Scripts/python scripts/scrub_fixtures.py --check; echo "exit=$?"   # 0 when clean

pwsh -File scripts/record_kanban_video.ps1        # prints the recipe; exits 1 if the dashboard is down

grep -rn '0\.0\.0\.0' scripts/ || echo "no wildcard binds (dashboard security ok)"
.venv/Scripts/python scripts/record_fixtures.py --help
```

## Handoff

S35's clean-clone proof runs `./scripts/bootstrap.ps1` then `cwt run --engine local --offline`. S36's
README quotes the recording checklist from `record_kanban_video.ps1` — **keep one source of truth** by
having the README point at the script rather than duplicating the list.
