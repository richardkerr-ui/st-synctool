"""Tests for core/zhivago_quiz.py — the multiple-choice exam that gates
disabling bootup music ("Sim Nights" by Kirk Casey).

Pure scoring logic, no PyQt6, so these run headless anywhere.
"""

import pytest
from core import zhivago_quiz as quiz


# The answer key: the correct option-index for each question, in order.
KEY = [q.answer_index for q in quiz.QUESTIONS]


class TestQuestionBank:
    def test_has_five_questions(self):
        assert len(quiz.QUESTIONS) == 5

    def test_pass_threshold_is_four(self):
        assert quiz.PASS_THRESHOLD == 4

    def test_every_question_is_multiple_choice(self):
        # At least two options to choose between, so it's genuinely a choice.
        for q in quiz.QUESTIONS:
            assert len(q.options) >= 2

    def test_answer_index_is_in_range_for_every_question(self):
        for q in quiz.QUESTIONS:
            assert 0 <= q.answer_index < len(q.options)


class TestGrade:
    def test_all_correct_scores_five(self):
        assert quiz.grade(KEY) == 5

    def test_all_wrong_scores_zero(self):
        wrong = [(k + 1) % len(q.options) for k, q in zip(KEY, quiz.QUESTIONS)]
        assert quiz.grade(wrong) == 0

    def test_unanswered_counts_as_wrong(self):
        assert quiz.grade([None] * 5) == 0

    def test_partial_score_counts_only_correct(self):
        selected = KEY[:]
        selected[0] = (selected[0] + 1) % len(quiz.QUESTIONS[0].options)  # spoil one
        assert quiz.grade(selected) == 4

    def test_mix_of_none_and_correct(self):
        selected = KEY[:]
        selected[0] = None
        selected[1] = None
        assert quiz.grade(selected) == 3


class TestPassed:
    def test_five_correct_passes(self):
        assert quiz.passed(KEY) is True

    def test_four_correct_passes(self):
        selected = KEY[:]
        selected[0] = (selected[0] + 1) % len(quiz.QUESTIONS[0].options)
        assert quiz.passed(selected) is True

    def test_three_correct_fails(self):
        selected = KEY[:]
        selected[0] = (selected[0] + 1) % len(quiz.QUESTIONS[0].options)
        selected[1] = (selected[1] + 1) % len(quiz.QUESTIONS[1].options)
        assert quiz.passed(selected) is False

    def test_all_wrong_fails(self):
        assert quiz.passed([None] * 5) is False
