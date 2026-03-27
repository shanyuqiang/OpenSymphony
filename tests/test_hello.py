"""Tests for hello module."""

from lib.hello import greet


def test_greet_returns_correct_greeting() -> None:
    """Test that greet returns the correct greeting format."""
    result = greet("World")
    assert result == "Hello, World!"

    result = greet("Alice")
    assert result == "Hello, Alice!"


def test_greet_with_empty_string() -> None:
    """Test greet with an empty string."""
    result = greet("")
    assert result == "Hello, !"
