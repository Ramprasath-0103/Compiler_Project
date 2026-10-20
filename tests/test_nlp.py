"""Intent detection: plain English in, the right pipeline stage out.

No model is involved, which is the point -- this has to work on a machine
with no API key and no local server, instantly, and be able to say why it
decided what it decided.
"""

from __future__ import annotations

import pytest

from nlpcc.nlp import detect
from nlpcc.nlp.intent import (ANALYZE, EXPLAIN, GENERATE, OPTIMIZE, RUN, SCORE,
                              tokenize, _stem)


@pytest.mark.parametrize("text,expected", [
    ("why does this not compile?", EXPLAIN),
    ("what does this error mean", EXPLAIN),
    ("help me understand what is wrong", EXPLAIN),
    ("explain the error in plain english", EXPLAIN),
    ("write me a program that sorts an array", GENERATE),
    ("let ai build my code for a calculator", GENERATE),
    ("generate a program that reads a file", GENERATE),
    # "give me code" is as plain a request as "write a program", and used to
    # route correctly at 17% confidence -- only the one-point word "code"
    # matched, so the page told the user it was guessing.
    ("give me correct code", GENERATE),
    ("give code to sum two numbers", GENERATE),
    ("i need code that sorts an array", GENERATE),
    ("show me code for bubble sort", GENERATE),
    ("how good is my code", SCORE),
    ("rate my code", SCORE),
    ("make it faster", OPTIMIZE),
    ("how can i improve this", OPTIMIZE),
    ("make this easier to read", OPTIMIZE),
    ("run it and show the output", RUN),
    ("what does it print", RUN),
])
def test_requests_route_to_the_right_stage(text: str, expected: str) -> None:
    assert detect(text).action == expected


def test_objectives_are_pulled_out_of_the_sentence() -> None:
    intent = detect("make it faster and use less memory")
    assert intent.action == OPTIMIZE
    assert set(intent.objectives) == {"speed", "memory"}


def test_readability_and_maintainability_are_recognised() -> None:
    assert "readability" in detect("make this cleaner").objectives
    assert "maintainability" in detect("make it more maintainable").objectives


def test_description_drops_the_request_wrapper() -> None:
    """What gets built is the task, not the sentence asking for it."""
    intent = detect("please write me a program that reverses a string")
    assert intent.action == GENERATE
    assert "write" not in intent.description
    assert "reverses a string" in intent.description


def test_an_empty_editor_means_generate() -> None:
    """With nothing to analyze, a bare description can only be a request to build."""
    intent = detect("a program that adds two numbers", has_code=False)
    assert intent.action == GENERATE


def test_nonsense_is_admitted_rather_than_guessed() -> None:
    intent = detect("banana banana banana")
    assert intent.confidence == 0.0
    assert not intent.certain
    assert intent.action == ANALYZE          # a safe default, not a claim
    assert intent.because


def test_plain_requests_for_code_are_confident() -> None:
    """A confident route must not be presented to the user as a guess."""
    for text in ("give me correct code",
                 "give code to sum two numbers",
                 "i need code that sorts an array"):
        intent = detect(text)
        assert intent.action == GENERATE, text
        assert intent.certain, f"{text} -> only {intent.confidence:.0%}"


def test_the_task_survives_a_give_me_wrapper() -> None:
    assert "sum two numbers" in detect("give code to sum two numbers").description
    assert "bubble sort" in detect("show me code for bubble sort").description


def test_it_can_say_why() -> None:
    intent = detect("why does this not compile")
    assert intent.because
    assert intent.confidence > 0.5
    assert intent.summary


def test_stemming_groups_word_forms() -> None:
    assert _stem("writing") == "writ"
    assert _stem("generated") == "generat"
    assert _stem("is") == "is"               # too short to strip


def test_tokenizer_drops_punctuation() -> None:
    assert tokenize("Why does this, not compile?!") == \
        ["why", "does", "this", "not", "compile"]


def test_detection_is_instant_and_offline() -> None:
    """No network, no model: a thousand detections should be unnoticeable."""
    import time
    start = time.time()
    for _ in range(1000):
        detect("make it faster")
    assert time.time() - start < 1.0
