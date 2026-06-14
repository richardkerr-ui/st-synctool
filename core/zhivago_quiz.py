"""The Dr. Zhivago exam — the gate for disabling bootup music.

"Sim Nights" by Kirk Casey plays on every app launch. A user may only turn it
off if they prove themselves by passing a five-question exam on David Lean's
1965 film *Doctor Zhivago*: four of five correct.

Pure logic, no PyQt6. The GUI layer (gui.zhivago_quiz) renders these questions
and calls ``grade``.
"""

from __future__ import annotations

from typing import NamedTuple

PASS_THRESHOLD = 4  # correct answers (out of 5) needed to earn the right to disable


class Question(NamedTuple):
    prompt: str
    options: list          # display order shown to the user
    answer_index: int      # index into options of the correct choice


QUESTIONS: list = [
    Question(
        "Who directed the 1965 film Doctor Zhivago?",
        ["David Lean", "Stanley Kubrick", "Sergei Bondarchuk", "Fred Zinnemann"],
        0,
    ),
    Question(
        "Who plays the title role of Yuri Zhivago?",
        ["Peter O'Toole", "Omar Sharif", "Alec Guinness", "Rod Steiger"],
        1,
    ),
    Question(
        "What is the title of the film's famous recurring melody?",
        ["Lara's Theme", "Tonya's Waltz", "The Steppe Suite", "Moscow Nights"],
        0,
    ),
    Question(
        "Who composed the score?",
        ["Ennio Morricone", "Nino Rota", "Maurice Jarre", "John Barry"],
        2,
    ),
    Question(
        "Whose novel is the film based on?",
        ["Leo Tolstoy", "Boris Pasternak", "Mikhail Sholokhov", "Aleksandr Solzhenitsyn"],
        1,
    ),
]


def grade(selected: list) -> int:
    """Return the number correct given a list of chosen option-indices.

    ``selected[i]`` is the user's chosen option index for QUESTIONS[i]; a value
    of None (unanswered) counts as wrong.
    """
    correct = 0
    for q, choice in zip(QUESTIONS, selected):
        if choice is not None and choice == q.answer_index:
            correct += 1
    return correct


def passed(selected: list) -> bool:
    """True when the user got at least PASS_THRESHOLD answers right."""
    return grade(selected) >= PASS_THRESHOLD
