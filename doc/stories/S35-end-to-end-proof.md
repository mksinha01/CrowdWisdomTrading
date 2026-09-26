# S35 — End-to-end proof & test suite completion

**Phase** 8 · **Depends on** S34 · **Blocks** S36
**Spec** `doc/video-ads-agent.md` lines **5384–5097** (§13.3 what success looks like), **5099–5100** (the offline-first contract), **5456–5458** (the clean-clone test), **180–186** (the test file list), **4020–4029** (§9.6)
**Context budget** ~16k (spec 3k + story 1.9k + output 9k)
**Produces** full `tests/**`, `tests/fixtures/**`, `pytest.ini`/`pyproject` markers, `doc/stories/PROGRESS.md` completion

---

## Goal

Prove the whole thing works. This is not a formality — §13 line 5456 makes it the submission's
acceptance test:

> *"Before you send it, run this: `cwt run --engine local --offline` on a clean clone, in a fresh
> shell, with no keys in the environment. If it does not render a video, the reviewer cannot rerun your
> code — and that is an explicit submission requirement, not a nicety."*

## The six test files from §1

| File | Covers | State after S35 |
|---|---|---|
| `tests/test_claims.py` | the deterministic engine, exhaustively — "the guardrail is a control, so it is proven" | S06 wrote it; **complete it** to 1 positive + 1 negative per rule + the clean-script case |
| `tests/test_filtergraph.py` | pure `Shot → filtergraph`, no ffmpeg needed | S14 wrote it; add the golden strings for all four camera moves |
| `tests/test_models.py` | schema validation incl. the eleven storyboard validators | S04 wrote it; **complete** to 11 negative cases |
| `tests/test_llm_repair.py` | `extract_json` + the repair loop, against recorded failures | S09 wrote it; add the recorded-failure corpus |
| `tests/test_artifacts.py` | `ArtifactStore` round-trip + `schema_version` handling | S07 wrote it |
| `tests/test_dag.py` | topological sort, idempotency keys, parent wiring | S27 wrote it |

Plus the per-story tests written along the way. **§1's six are the required minimum; the others are
free coverage.**

## The three acceptance criteria to verify

From `DAG_SPEC`'s root card (spec lines 3394–3401). All must hold:

1. `render/final.mp4` exists, is **30–60s**, **1080×1920**, and ffprobe-valid
2. `artifacts/storyboard.json` validates and its `creative_scores.verdict == "pass"`
3. `artifacts/claims_report.json` verdict is `pass` on **both** the pre-render and post-render passes
4. `submission/` contains the bundle described in the README

## Build steps

1. **The offline proof — run it first, before writing any more tests.**
   ```bash
   cwt run --engine local --offline
   ```
   If this does not produce a playable 30–60s video, **stop**. Every other test in this story is
   secondary to this one command. The most likely causes, in order:
   - a stage artifact missing from `fixtures/artifacts/` (S32's replay approach)
   - `fixtures/assets/` incomplete — a shot referencing an unresolvable asset (S17)
   - the median timeline (S20) disagreeing with validator 6 (S04) — a storyboard written to one grammar
     and validated against another
2. **Verify the acceptance criteria** with a script `tests/test_end_to_end.py`:
   - probe `final.mp4` → duration, dimensions, codecs
   - `Storyboard.model_validate` + `creative_scores.verdict == "pass"`
   - both claims reports parse and both verdicts are `pass`
   - `submission/` file set matches S26's table
3. **The clean-clone test.** This is the one that catches missing files:
   ```bash
   git clone . /tmp/cwt-clean && cd /tmp/cwt-clean
   ./scripts/bootstrap.ps1                      # or bootstrap.sh
   env -i .venv/Scripts/python -m cwt run --engine local --offline
   ```
   `env -i` is the point — **no keys in the environment**. If the run touches the network or needs a
   credential, it fails here and the submission requirement is not met.
4. **The `--resume` cost test** (§9.6's promise): after a successful run, run
   `cwt run --resume` and assert `cost_usd == 0.0` and every stage reports `skipped`. *"After fixing a
   filtergraph bug, re-running costs zero API calls and about 90 seconds."* Prove the zero.
5. **Complete the coverage gaps:**
   - `test_claims.py` — assert `len(CLAIM_RULES) == 15` and the 12/3 HARD/SOFT split; the **clean-script
     case** (the §3.4 voiceover produces zero HARD findings) is the single most valuable test in the repo
   - `test_models.py` — 11 validator negative cases; assert a `schema_version: 99` artifact raises
     `ArtifactVersionError`
   - `test_filtergraph.py` — golden strings for `static` (no zoompan), `push_in`, `pull_out`, `whip_pan`
6. **Marker hygiene.** Mark anything needing network, ffmpeg, or a live Hermes with
   `@pytest.mark.network` / `@pytest.mark.ffmpeg` / `@pytest.mark.hermes` and register them in
   `pyproject.toml`. **The default `pytest` run must pass on a bare machine with no network** — that is
   the same property the offline proof asserts.
7. **Assert the invariants that cut across stories** — a `tests/test_invariants.py`:
   - every `DAG_SPEC.assignee` has a profile name (Rule K2)
   - every `plugin.yaml` tool has a schema and a handler (S29/S30)
   - `VIDEO_BACKEND_CHAIN` ends in `local_ffmpeg` for any `Settings` (Rule V4)
   - no module outside `util/subproc.py` calls `subprocess` — grep the source
   - no module outside `clients/apify.py` references a raw Apify field name
   - **no source file contains a hardcoded credential** — grep for the five prefixes

## Decisions the spec leaves open

- **`fixtures/artifacts/` is the mechanism that makes offline replay work** (S32's decision). This
  story is where it is exercised. If a stage cannot be replayed, the honest fix is to add the fixture —
  not to make the stage tolerate a missing input.
- **The clean-clone test is manual.** It cannot be a pytest case (it clones and installs). Record the
  command and its output in `doc/stories/PROGRESS.md` and in the submission README.
- **`env -i` on Windows.** Git Bash provides `env`; in PowerShell use
  `Get-ChildItem env: | Remove-Item` in a subshell, or run from a fresh terminal. Document both.
- **A green `pytest` is necessary but not sufficient.** The offline render is the gate. Say so in the
  README so a reader does not mistake test coverage for a working product.

## Done when

```bash
# 1. the gate
cwt run --engine local --offline
# -> Done. 11 stages, $0.0000, output: runs/<id>/render/final.mp4

# 2. the acceptance criteria
.venv/Scripts/python -m pytest tests/ -q                    # all green, no network
.venv/Scripts/python -m pytest tests/test_end_to_end.py -q -v

# 3. the clean clone, no keys
git clone . /tmp/cwt-clean && cd /tmp/cwt-clean && ./scripts/bootstrap.sh
env -i .venv/bin/python -m cwt run --engine local --offline

# 4. the resume promise
cwt run --resume
# -> Done. 11 stages, $0.0000   (every stage skipped)

# 5. invariants
.venv/Scripts/python -m pytest tests/test_invariants.py -q -v
```

## Handoff

S36 records this proof in the README as the "does it work" signal, alongside §13.3's expected `doctor`
and `run` output. **The output of step 1 goes into `submission/README-SUBMISSION.md` verbatim** — a
reviewer should see exactly what success looks like before they try it.
