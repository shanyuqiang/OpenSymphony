"""문자열 처리 유틸리티 테스트.

문자열 역순, 회문 판별, 단어 수 계산 함수가 올바르게 동작하는지 검증한다.
"""

from __future__ import annotations

import pytest

from lib.string_utils import is_palindrome, reverse, word_count


class TestReverse:
    """reverse 함수 테스트."""

    def test_일반_문자열_역순(self) -> None:
        assert reverse("hello") == "olleh"

    def test_빈_문자열(self) -> None:
        assert reverse("") == ""

    def test_단일_문자(self) -> None:
        assert reverse("a") == "a"

    def test_회문_문자열(self) -> None:
        assert reverse("radar") == "radar"

    def test_공백_포함_문자열(self) -> None:
        assert reverse("hello world") == "dlrow olleh"


class TestIsPalindrome:
    """is_palindrome 함수 테스트."""

    def test_일반_회문(self) -> None:
        assert is_palindrome("radar") is True

    def test_대소문자_混用_회문(self) -> None:
        assert is_palindrome("RaceCar") is True

    def test_숫자_회문(self) -> None:
        assert is_palindrome("12321") is True

    def test_회문_아님(self) -> None:
        assert is_palindrome("hello") is False

    def test_공백_포함_회문(self) -> None:
        assert is_palindrome("A man a plan a canal Panama") is True

    def test_특수문자_포함_회문(self) -> None:
        assert is_palindrome("Was it a car or a cat I saw") is True

    def test_빈_문자열(self) -> None:
        assert is_palindrome("") is True

    def test_단일_문자(self) -> None:
        assert is_palindrome("a") is True


class TestWordCount:
    """word_count 함수 테스트."""

    def test_일반_문장(self) -> None:
        assert word_count("hello world") == 2

    def test_여러_공백(self) -> None:
        assert word_count("hello   world") == 2

    def test_빈_문자열(self) -> None:
        assert word_count("") == 0

    def test_공백만_포함(self) -> None:
        assert word_count("   ") == 0

    def test_단어_하나(self) -> None:
        assert word_count("hello") == 1

    def test_여러_단어(self) -> None:
        assert word_count("the quick brown fox jumps over the lazy dog") == 9
