"""Work out what the user wants from a plain-English request.

Deliberately not a model. This is classical NLP -- normalise, tokenize, stem,
score weighted keyword and phrase evidence, pick the best-supported reading --
and that choice buys three things a model cannot:

  * it needs no API key and no download, so it always works;
  * it answers in microseconds, which matters for something typed into a box;
  * it can say *why* it decided, which is what ``Intent.because`` carries.

"Make this faster" and "why does this not compile" are not hard sentences.
The hard part is routing them to the right stage of the pipeline, and a
keyword model does that honestly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

EXPLAIN = "explain"
GENERATE = "generate"
SCORE = "score"
OPTIMIZE = "optimize"
RUN = "run"
ANALYZE = "analyze"

ACTIONS = (EXPLAIN, GENERATE, SCORE, OPTIMIZE, RUN, ANALYZE)

# What each action does, for the UI to show next to its guess.
DESCRIPTIONS = {
    EXPLAIN: "explain the errors in plain English",
    GENERATE: "write the program from your description",
    SCORE: "rate the code across the five dimensions",
    OPTIMIZE: "suggest improvements",
    RUN: "compile and run it",
    ANALYZE: "compile it and report everything",
}

# Phrases are worth more than single words because they are less ambiguous:
# "write" alone is weak, "write a program" is not.
PHRASES: dict[str, tuple[tuple[str, int], ...]] = {
    GENERATE: (
        ("write a program", 6), ("write me a program", 6), ("write code", 5),
        ("build my code", 6), ("build me", 5), ("build a program", 6),
        ("generate code", 6), ("generate a program", 6), ("create a program", 6),
        ("make a program", 5), ("write a function", 5), ("implement a", 4),
        ("let ai build", 6), ("for me", 1),
    ),
    EXPLAIN: (
        ("what does this mean", 6), ("what is wrong", 6), ("what's wrong", 6),
        ("why does this", 5), ("why is this", 5), ("help me understand", 6),
        ("in plain english", 5), ("explain the error", 6), ("i do not understand", 5),
        ("i dont understand", 5), ("make sense of", 4),
    ),
    SCORE: (
        ("how good is", 6), ("how bad is", 6), ("rate my", 6), ("rate this", 6),
        ("quality score", 6), ("score my", 6), ("is my code good", 6),
    ),
    OPTIMIZE: (
        ("make it faster", 6), ("make this faster", 6), ("speed it up", 6),
        ("use less memory", 6), ("easier to read", 6), ("how can i improve", 6),
        ("what should i change", 5), ("clean it up", 5), ("make it better", 5),
    ),
    RUN: (
        ("what does it print", 6), ("run it", 5), ("run this", 5),
        ("execute it", 5), ("what is the output", 6), ("does it crash", 5),
    ),
}

# Single words, after stemming. Lower weight, more of them.
KEYWORDS: dict[str, tuple[tuple[str, int], ...]] = {
    GENERATE: (("write", 2), ("creat", 2), ("gener", 3), ("build", 2), ("mak", 1),
               ("implement", 3), ("program", 1), ("code", 1), ("function", 1)),
    EXPLAIN: (("explain", 4), ("why", 3), ("mean", 3), ("understand", 3),
              ("error", 2), ("wrong", 2), ("fail", 2), ("confus", 3),
              ("broken", 2), ("english", 2), ("simpl", 1)),
    SCORE: (("score", 4), ("rat", 3), ("qualiti", 3), ("grad", 3), ("good", 2),
            ("bad", 1), ("assess", 3), ("evalu", 3)),
    OPTIMIZE: (("optim", 4), ("improv", 3), ("faster", 3), ("fast", 2),
               ("speed", 3), ("memori", 3), ("readabl", 3), ("maintain", 3),
               ("refactor", 3), ("better", 2), ("suggest", 2), ("effici", 3)),
    RUN: (("run", 3), ("execut", 3), ("output", 2), ("print", 2), ("crash", 2)),
    ANALYZE: (("analyz", 3), ("analys", 3), ("check", 2), ("review", 2),
              ("compil", 2), ("everyth", 1), ("report", 2)),
}

# Words that name an optimization objective.
OBJECTIVE_WORDS: dict[str, tuple[str, ...]] = {
    "speed": ("fast", "faster", "speed", "quick", "quicker", "performance",
              "performant", "slow", "efficient", "efficiency"),
    "memory": ("memory", "ram", "allocation", "allocations", "leak", "leaks",
               "footprint", "space"),
    "readability": ("readable", "readability", "read", "clear", "clearer",
                    "clean", "cleaner", "understandable", "tidy"),
    "maintainability": ("maintainable", "maintainability", "maintain",
                        "modular", "structure", "organised", "organized"),
}

# Stripped in this order; crude but enough to group write/writes/writing.
SUFFIXES = ("ing", "edly", "ies", "es", "ed", "ly", "s", "y", "e")

_WORD = re.compile(r"[a-z0-9']+")


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def _stem(word: str) -> str:
    """Chop a common suffix off anything long enough to survive it."""
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def tokenize(text: str) -> list[str]:
    return _WORD.findall(_normalise(text))


@dataclass
class Intent:
    action: str
    confidence: float                       # 0.0 - 1.0
    objectives: tuple[str, ...] = ()
    description: str = ""                   # what to build, for GENERATE
    because: list[str] = field(default_factory=list)
    scores: dict[str, int] = field(default_factory=dict)

    @property
    def certain(self) -> bool:
        return self.confidence >= 0.5

    @property
    def summary(self) -> str:
        return DESCRIPTIONS.get(self.action, self.action)

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "summary": self.summary,
            "confidence": round(self.confidence, 2),
            "objectives": list(self.objectives),
            "description": self.description,
            "because": self.because,
            "scores": self.scores,
        }


# Lead-ins to strip when recovering what the user wants built.
_LEAD_IN = re.compile(
    r"^(please\s+)?(can you\s+|could you\s+|i want you to\s+|i want\s+|"
    r"let ai\s+|let the ai\s+|help me\s+)?"
    r"(write|build|create|generate|make|implement|code)\s+"
    r"(me\s+)?(a|an|the|my)?\s*(program|function|code|script)?\s*"
    r"(that|which|to|for|doing)?\s*",
    re.I)


def _extract_description(text: str) -> str:
    """What the program should do, with the request wrapper removed."""
    stripped = _LEAD_IN.sub("", _normalise(text)).strip(" .?!,")
    return stripped or _normalise(text).strip(" .?!,")


def _objectives(tokens: list[str]) -> tuple[str, ...]:
    found: list[str] = []
    present = set(tokens)
    for objective, words in OBJECTIVE_WORDS.items():
        if present & set(words) and objective not in found:
            found.append(objective)
    return tuple(found)


def detect(text: str, has_code: bool = True) -> Intent:
    """Read a plain-English request and decide which stage it asks for.

    ``has_code`` says whether the user has any code in front of them. With an
    empty editor, a bare "a program that sorts an array" can only mean
    generate, and that tips an otherwise ambiguous request.
    """
    normalised = _normalise(text)
    tokens = tokenize(text)
    stems = [_stem(t) for t in tokens]

    scores = {action: 0 for action in ACTIONS}
    because: list[str] = []

    for action, phrases in PHRASES.items():
        for phrase, weight in phrases:
            if phrase in normalised:
                scores[action] += weight
                because.append(f'"{phrase}"')

    for action, keywords in KEYWORDS.items():
        for keyword, weight in keywords:
            if keyword in stems:
                scores[action] += weight
                because.append(keyword)

    objectives = _objectives(tokens)
    if objectives:
        scores[OPTIMIZE] += 2 * len(objectives)
        because.extend(objectives)

    if not has_code:
        # Nothing to explain, score or run when the editor is empty.
        scores[GENERATE] += 4
        for action in (EXPLAIN, SCORE, OPTIMIZE, RUN, ANALYZE):
            scores[action] = 0
        because.append("no code in the editor")

    best = max(scores, key=lambda a: scores[a])
    top = scores[best]
    if top == 0:
        # Nothing matched: analyze is the useful default, and the caller can
        # see the confidence is zero and ask.
        return Intent(action=ANALYZE, confidence=0.0, scores=scores,
                      because=["nothing in the request matched a known action"],
                      description=_extract_description(text))

    total = sum(scores.values()) or 1
    confidence = min(1.0, (top / total) * min(1.0, top / 6))

    return Intent(
        action=best,
        confidence=round(confidence, 2),
        objectives=objectives if best == OPTIMIZE else (),
        description=_extract_description(text) if best == GENERATE else "",
        because=because[:6],
        scores=scores,
    )
