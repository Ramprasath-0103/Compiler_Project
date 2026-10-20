# Scope contract

Settles what this project builds and, more usefully, what it does not. Every
other document defers to this one. **Draft — the grammar section is the gap.**

## Decided

**Our own front end, with clang as ground truth.** We implement lexical,
syntax and semantic analysis for a C subset. clang runs on every file as well:
where it reports something we miss, that is a tracked gap in our analyzer, not
a feature we outsource. Programs outside the subset still get full analysis
from clang, so the tool is useful on day one and the compiler-design work is
genuinely ours.

**Rules before the model.** Explanations come from a reviewable rule table.
The LLM is an enhancement layer for unmatched findings only, and its output is
labelled as model-generated. Non-negotiable: the demo must work offline and
two runs of the same input must produce the same report.

**Quality scores must be explainable.** Every point of the score traces to a
named rule with a published weight. No dimension is scored by asking a model
for a number — "readability: 7/10" with no derivation cannot be defended.

**C only.** One language, done properly.

## To decide

### The subset grammar — write this before step 4

List the exact accepted constructs, then write them as a grammar the parser's
method names mirror one-for-one. Starting proposal:

- types: `int`, `float`, `char`, `void`; one-dimensional arrays of those
- declarations: globals, locals with optional initializer, functions with
  parameters, `typedef` excluded
- statements: expression, compound block, `if`/`else`, `while`, `for`,
  `return`, `break`, `continue`
- expressions: assignment, the binary and unary operators in
  `frontend/tokens.py`, function calls, array indexing, literals
- preprocessor: `#include` recorded but not expanded; `#define` out of scope
- out of scope: pointers beyond declaration and dereference, `struct`,
  `switch`, `do/while`, varargs, multi-dimensional arrays

Anything outside this gets one clear "outside the analyzed subset" diagnostic
and falls through to clang. It never crashes the front end.

### The five quality dimensions — needed for step 6

Each needs measurable metrics, thresholds and a weight. Starting proposal:

| Dimension | Candidate metrics |
|---|---|
| Performance | loop nesting depth, allocation inside loops, repeated recomputation, `strlen` in a loop condition |
| Readability | function length, nesting depth, identifier length and naming convention, comment density, magic numbers |
| Maintainability | cyclomatic complexity, parameter count, duplicated blocks, global mutable state |
| Security | `gets`/`strcpy`/`sprintf`/`atoi` use, unchecked `malloc`, `malloc`/`free` pairing, unchecked array indices |
| Practices | missing `return` in non-void, unused variables, shadowed names, assignment inside a condition |

Open question: how the five combine into one score. Weighted mean is the
obvious choice, but a program with a buffer overflow should not score 80
because its naming is tidy. Consider a cap: any critical security finding
limits the overall score regardless of the other dimensions.

### Smaller open questions

- Persistence: SQLite. What is stored — every analysis, or only reports?
- Report format: JSON as the single source, rendered to Markdown. PDF needed?
- LLM provider and the offline fallback path.
- Step 9 generation: how is generated code validated before it is shown? It
  should at minimum have to pass through this pipeline cleanly first.

## Explicitly not building

React frontend, PostgreSQL, multi-language support, IDE plugin, anything
networked. The CLI is the deliverable.

## Missing input

The proposed-system workflow diagram linked on slide 7 lives in Excalidraw and
is not readable from the link alone. Export it and commit it as
`docs/workflow.png` so the architecture above can be checked against it.
