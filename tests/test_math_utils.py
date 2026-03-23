# tests/test_math_utils.py
"""Tests for math_utils module."""
import sys
from pathlib import Path

import pytest

# Add lib to path for imports
lib_path = Path(__file__).parent.parent / "lib"
sys.path.insert(0, str(lib_path))

from math_utils import add, subtract, multiply


def test_add_positive_numbers():
    assert add(2, 3) == 5


def test_add_negative_numbers():
    assert add(-1, -1) == -2


def test_add_mixed_numbers():
    assert add(-1, 1) == 0


def test_add_floats():
    assert add(1.5, 2.5) == 4.0


def test_subtract_positive_numbers():
    assert subtract(5, 3) == 2


def test_subtract_negative_result():
    assert subtract(3, 5) == -2


def test_subtract_negative_numbers():
    assert subtract(-5, -3) == -2


def test_subtract_floats():
    assert subtract(5.5, 2.5) == 3.0


def test_multiply_positive_numbers():
    assert multiply(3, 4) == 12


def test_multiply_negative_numbers():
    assert multiply(-2, -3) == 6


def test_multiply_mixed_signs():
    assert multiply(-2, 3) == -6


def test_multiply_floats():
    assert multiply(2.5, 4.0) == 10.0


def test_multiply_by_zero():
    assert multiply(5, 0) == 0
