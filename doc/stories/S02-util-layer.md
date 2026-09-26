# S02 — Util layer

**Phase** 0 · **Depends on** S01 · **Blocks** S03, S08, S09, S14
**Spec** `doc/video-ads-agent.md` lines **2029–2148** (`util/subproc.py`), **3236–3334** (`util/jsonio.py`, `util/paths.py`)
**Context budget** ~10k (spec 3k + story 1.4k + output 5.5k)
**Produces** `src/cwt/util/__init__.py`, `util/jsonio.py`, `util/paths.py`, `util/retry.py`, `util/subproc.py`, `tests/test_subproc.py`, `tests/test_jsonio.py`

---

## Goal

Four small modules that every other module depends on. Three of them are the enforcement points for
five of the Windows rules — which is exactly why they are built first and tested first. If these are
wrong, every later story inherits the bug in a place that is expensive to find.

## Interface contract — FROZEN

```python
# util/jsonio.py
def read_json(path: Path) -> Any: ...
def write_json(path: Path, data: Any) -> None: ...       # utf-8 + newline="\n" + ensure_ascii=False
def append_jsonl(path: Path, record: dict) -> None: ...
def now_iso() -> str: ...                                 # "2026-09-26T14:02:11Z", UTC

# util/paths.py
def mint_run_id() -> str: ...                             # "20260926-1402-a7f3"
def long_path(p: Path) -> str: ...

@dataclass(frozen=True)
class RunPaths:
    run_dir: Path
    artifacts: Path      # run_dir/artifacts
    assets: Path         # run_dir/assets
    cache: Path          # run_dir/assets/cache
    render: Path         # run_dir/render
    failures: Path       # run_dir/failures
    ledger: Path         # run_dir/llm_ledger.jsonl
    provenance: Path     # run_dir/artifacts/provenance.json
    def ensure(self) -> "RunPaths": ...

# util/retry.py
async def retry_async(fn: Callable[[], Awaitable[T]], *, max_attempts: int = 5,
                      base: float = 1.0, cap: float = 60.0) -> T: ...

# util/subproc.py
class ToolNotFound(RuntimeError): ...
class ToolTimeout(RuntimeError):  # .stdout, .stderr
class ToolFailed(RuntimeError):   # .result

@dataclass(frozen=True)
class ToolResult:
    args: list[str]; cwd: str | None; returncode: int; stdout: str; stderr: str
    @property
    def ok(self) -> bool: ...

def resolve_exe(exe: str) -> str: ...
def run_tool(args: Sequence[str], *, cwd: Path | None = None, timeout_s: float = 300,
             env_extra: Mapping[str, str] | None = None, check: bool = False) -> ToolResult: ...
```

## Rules that bind this story

- **W1** — `run_tool()` must **raise `TypeError`** if `args` is a `str`. Shell strings are banned at
  the chokepoint, not by convention.
- **W4** — `resolve_exe()` uses `shutil.which()` on Windows so `npx`/`npm`/`hermes` resolve to the
  `.cmd` shim. A bare name raises `[WinError 193]`.
- **W7** — every spawned child gets `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8`, and the call uses
  `encoding="utf-8", errors="replace"`.
- **W8** — `write_json` uses `newline="\n"`. `Path.write_text` does **not** normalise newlines on
  Windows and writes CRLF, which breaks `.sh` files and ffmpeg filelists on Linux.
- **W9** — `RunPaths` stays shallow (`runs/<short-id>/`) to clear the 260-char MAX_PATH limit.

## Build steps

1. `jsonio.py` — copy spec lines 3239–3272. `write_json` must append a trailing newline if absent.
   Enforce `newline="\n"` via an explicit `open(...)`, **not** `Path.write_text`.
2. `paths.py` — copy spec lines 3275–3333. `mint_run_id` is `YYYYMMDD-HHMM-%4hex`; short by design.
3. `retry.py` — hand-rolled, exponential backoff with **full jitter**: `sleep = random.uniform(0, min(cap, base * 2**attempt))`.
   Honour a `Retry-After` header when the raised exception carries a response with one. Do not add
   `tenacity` — `requirements.txt` declares it "unused by design".
4. `subproc.py` — copy spec lines 2034–2147 verbatim.
5. Tests: `test_jsonio.py` asserts LF-only (`read_bytes()` contains no `b"\r\n"`) and utf-8 round-trip
   of an em dash and a smart quote. `test_subproc.py` asserts the `str`-args `TypeError`, `resolve_exe`
   on `sys.executable`, and a `ToolTimeout` from a `sys.executable -c "import time;time.sleep(5)"`
   call with `timeout_s=0.5`.

## Decisions the spec leaves open

- **No test for `retry_async` jitter** — it is non-deterministic. Test only that a function raising
  twice then succeeding returns on attempt 3, with `base=0.01` to keep the suite fast.
- **`now_iso()` has no spec.** `llm.py` (line 3102) calls it. Define as UTC, seconds precision,
  `Z` suffix — matching every timestamp in §3's artifact examples.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_jsonio.py tests/test_subproc.py -q     # green
.venv/Scripts/python -c "from cwt.util.jsonio import write_json; write_json(__import__('pathlib').Path('_t.json'), {'a':'—'}); print(open('_t.json','rb').read())"
# -> b'{\n  "a": "\xe2\x80\x94"\n}\n'   (no \r, non-ascii NOT escaped)
```

## Handoff

Later stories may assume: `run_tool` is the **only** place a subprocess is spawned; `write_json` is the
**only** way JSON hits disk; `RunPaths.ensure()` creates all five directories; `retry_async` takes a
zero-arg coroutine factory.
