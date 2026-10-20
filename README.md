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
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m nlpcc analyze tests/corpus/syntax/missing_semicolon.c
./.venv/bin/python -m nlpcc analyze tests/corpus/runtime/divide_by_zero.c --run
./.venv/bin/python -m nlpcc analyze program.c --json
./.venv/bin/python -m nlpcc rules
./.venv/bin/python -m pytest tests -q
```

Needs a C compiler on `PATH` (clang or gcc). Note that on macOS `gcc` is a
clang shim — harmless here, but it means `-fdiagnostics-format=json` is
unavailable, which is why diagnostics are scraped from text.

## How it fits together

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
