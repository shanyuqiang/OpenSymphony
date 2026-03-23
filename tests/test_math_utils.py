"""Tests for math_utils module."""
import pytest
from lib.math_utils import add, subtract, multiply


def test_add_positive_numbers():
    assert add(2, 3) == 5


def test_add_negative_numbers():
    assert add(-1, -1) == -2


def test_add_zero():
    assert add(5, 0) == 5


def test_subtract_positive_numbers():
    assert subtract(10, 3) == 7


def test_subtract_negative_result():
    assert subtract(3, 10) == -7


def test_subtract_zero():
    assert subtract(5, 0) == 5


def test_multiply_positive_numbers():
    assert multiply(3, 4) == 12


def test_multiply_negative_numbers():
    assert multiply(-2, 3) == -6


def test_multiply_by_zero():
    assert multiply(5, 0) == 0


def test_multiply_floats():
    assert multiply(2.5, 4) == 10.0
