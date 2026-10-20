# NLP-Driven Compiler Assistant with AI-Based Code Analysis

Compiles C, finds what is wrong with it, and explains every finding in plain
English instead of compiler jargon.

**Team:** Nehal Arun (24BCT0151), Ram prasath RP (24BCE0176)

## Status

All nine steps implemented. The pipeline compiles, detects, explains, scores,
advises and reports.

| Step | What | State |
|------|------|-------|
| 1 | Scope contract (`docs/scope.md`) | **done** — subset grammar settled |
| 2 | Repo, CLI, corpus | **done** — 21 cases |
| 3 | Pipeline + ground-truth compiler + runtime executor | **done** |
| 4 | Our own front end (lexer → parser → semantic) | **done** — owns 9 of the corpus cases |
| 5 | Explanation layer, rules first | **done** — 100% corpus coverage |
| 5b | Model explanations for unmatched findings | **done** — cached, optional |
| 6 | Quality scoring, five dimensions | **done** — 14 rules, traceable score |
| 7 | Optimization recommender | **done** — objective-filtered |
| 8 | Report generation | **done** — JSON source, Markdown render |
| 9 | Natural-language → C generation | **done** — validated by our own pipeline |

Not built, deliberately: React frontend, PostgreSQL, multi-language support.
The CLI is the deliverable. See `docs/scope.md`.

## Running it

```bash
./run.sh
```

Installs anything missing the first time, starts both servers, and opens
http://localhost:5173. Ctrl-C stops both.

```bash
./run.sh setup     # install dependencies only
./run.sh test      # run the test suite
./run.sh stop      # stop whatever is left on the ports
```

The terminal interface, if you prefer it:

```bash
./.venv/bin/python -m nlpcc analyze tests/corpus/syntax/missing_semicolon.c
./.venv/bin/python -m nlpcc analyze tests/corpus/runtime/divide_by_zero.c --run
./.venv/bin/python -m nlpcc analyze program.c --json
./.venv/bin/python -m nlpcc rules
```

Needs a C compiler on `PATH` (clang or gcc). Note that on macOS `gcc` is a
clang shim — harmless here, but it means `-fdiagnostics-format=json` is
unavailable, which is why diagnostics are scraped from text.

## Asking in plain English

```bash
./.venv/bin/python -m nlpcc ask "why does this not compile" -f program.c
./.venv/bin/python -m nlpcc ask "make it faster and use less memory" -f program.c
./.venv/bin/python -m nlpcc ask "write me a program that reverses a string"
```

The request is routed by keyword rules in `nlpcc/nlp/intent.py` — no model,
no API key, instant, and it reports why it decided what it decided. It picks
the stage of the pipeline to run and, for "make it faster", pulls the
objectives out of the sentence.

## Running without an API key

```bash
./.venv/bin/python -m nlpcc models        # says which model it found
```

The model-backed parts — explaining unmatched diagnostics, AI code analysis,
and code generation — use whatever is available:

1. **Gemini** — `export GEMINI_API_KEY=...` (free key from
   [AI Studio](https://aistudio.google.com/apikey)).
2. **Anthropic** — used if its credentials resolve.
3. **LM Studio** — load a model and run `lms server start`. No API key.
4. **Ollama** — just run it. No API key.

Pin one with `NLPCC_PROVIDER=gemini` (or `lmstudio`, `ollama`, `anthropic`).

The order is about quality, not availability. **Everything outside these three
features works with no model at all** — the compiler, the explanations, the
five dimensions, the score and the suggestions are all deterministic. The
local providers are the fallback that always works; a small local model is
rougher and slower, which is why the provider that produced a given answer is
reported alongside it.

Measured on `generate`, same prompt, five runs each: a local 0.8B model
produced three compiling programs out of five; Gemini produced five.

Gemini's free tier meters each model separately, so a quota refusal on one
falls back to the other rather than failing.

**Never commit the key.** Put it in `.env`, which is gitignored and read
automatically at startup:

```bash
echo 'GEMINI_API_KEY=your-key' >> .env
```

Anything already exported in the shell wins over `.env`.

## The web interface

`./run.sh` starts both halves: the Python API on :8000 and the React dev
server on :5173, which proxies `/api` to it. To run them separately:

```bash
./.venv/bin/python -m nlpcc serve            # terminal 1 -- API on :8000
cd frontend && npm run dev                   # terminal 2 -- UI on :5173
```

Paste C code, pick what to optimize for, and press Analyze — or type what you
want in plain English and press Ask. The options are fetched from `/api/options`, so the UI can
only ever offer objectives the analyzer actually supports.

The server binds to localhost and nothing else: "Run the program" compiles and
executes whatever is submitted, with a timeout and a scratch directory as its
only containment. That is fine on your own machine and nowhere else.

## How it fits together

A diagram of the whole flow is in [docs/workflow.md](docs/workflow.md).

```
source.c
   │
   ├─► nlpcc/frontend/      our lexer, parser, semantic analyzer   (step 4)
   ├─► nlpcc/backends/clang_runner.py   real compiler, ground truth
   └─► nlpcc/backends/executor.py       compile, run, catch crashes
                    │
                    ▼
          Diagnostic  (nlpcc/diagnostics.py)   ← the one shared shape
                    │
                    ▼
          nlpcc/explain/rules.py   plain-English explanation + fix
                    │
                    ▼
              CLI / JSON / report
```

Everything emits `Diagnostic` and nothing else. That is the whole reason a new
analysis source costs nothing downstream: the explanation layer, the scorer and
the report never learn where a finding came from.

Two deliberate decisions:

**Our own front end, with a real compiler beside it.** The front end under
`nlpcc/frontend/` handles the C subset in `docs/scope.md` and is where the
compiler-design content of the project lives — real tokens, a real parse tree,
a real symbol table. clang runs on every file too, as ground truth: anything
clang reports that we miss is a gap in our analyzer, and the corpus makes that
gap visible. Programs outside the subset still work, because clang covers them.

**Rules before the model.** `nlpcc/explain/rules.py` maps a diagnostic code to
an explanation deterministically, offline, with no model call. The LLM layer
will sit *behind* it, consulted only for findings no rule matched and labelled
as model-generated in the report. Built the other way round, the demo dies
with the network and no two runs produce the same report.

## The corpus is the spec

`tests/corpus/` holds small broken programs, each with an `.expected.json`
saying what the assistant must report. Two assertions per case: the finding is
reported at the right line and severity, and **every** finding has an
explanation. The second is the one that matters — an unexplained finding fails
the suite even when detection is correct, because raw compiler text reaching
the user is the exact problem this project exists to fix.

Three cases `xfail` on purpose (`unchecked_malloc`, `deep_nesting`,
`poor_naming`). They are legal C that no compiler warns about, waiting on the
step-6 rule engine. They will start passing when it lands.

### Adding a case

```bash
$EDITOR tests/corpus/semantic/my_case.c          # the broken program
./.venv/bin/python -m nlpcc analyze tests/corpus/semantic/my_case.c -v
```

Write the `.expected.json` by hand from what you *want* reported — not by
pasting current output, which only freezes today's behaviour:

```json
{ "title": "what is wrong with this program",
  "run": false,
  "expect": [{ "line": 5, "severity": "error", "phase": "semantic",
               "code": "clang:...", "explained": true }] }
```

`code` and `phase` are optional; `line: null` matches a finding with no line.
If the case needs a step that does not exist yet, add
`"pending": "step N: why"` and it xfails instead of lying.

### Adding an explanation rule

Append a `Rule` to `nlpcc/explain/rules.py`. The code to key on comes from
`analyze -v`. Write for someone in their first semester: say what the compiler
was trying to do, why it could not, and what to type instead. Keep the `fix`
to one actionable sentence.

## Runtime errors

Programs are compiled with `-fsanitize=undefined` before running, so a division
by zero or an out-of-bounds write is reported *with a line number* rather than
as a bare "segmentation fault". This matters more than it sounds: on Apple
Silicon, integer division by zero does not fault at all — the program keeps
running with a meaningless value, and without the sanitizer there is nothing to
report. See `tests/corpus/runtime/divide_by_zero.c`.

## Security note

`nlpcc/backends/executor.py` runs user-supplied code. Its only containment
today is a wall-clock timeout and a scratch working directory — fine for a
local CLI, not fine for anything networked. Before this is ever exposed beyond
the terminal it needs rlimits, no network, and an unprivileged user.
# Compiler_Project
