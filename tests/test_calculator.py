"""Tests for the calculator module."""

from decimal import Decimal

import pytest

from symphony.utils.calculator import (
    Calculator,
    CalculatorError,
    CalculatorResult,
    DivisionByZeroError,
    InvalidInputError,
    OperationType,
)


class TestCalculator:
    """Test suite for Calculator class."""

    @pytest.fixture
    def calculator(self) -> Calculator:
        """Create a calculator instance for testing."""
        return Calculator()

    # Addition tests
    def test_add_integers(self, calculator: Calculator) -> None:
        result = calculator.add(10, 5)
        assert result.value == Decimal("15")
        assert result.operation == OperationType.ADD
        assert result.operands == (Decimal("10"), Decimal("5"))

    def test_add_floats(self, calculator: Calculator) -> None:
        result = calculator.add(3.5, 2.5)
        assert result.value == Decimal("6.0")

    def test_add_decimals(self, calculator: Calculator) -> None:
        result = calculator.add(Decimal("10.5"), Decimal("5.5"))
        assert result.value == Decimal("16.0")

    def test_add_strings(self, calculator: Calculator) -> None:
        result = calculator.add("10.5", "5.5")
        assert result.value == Decimal("16.0")

    def test_add_negative_numbers(self, calculator: Calculator) -> None:
        result = calculator.add(-5, 3)
        assert result.value == Decimal("-2")

    def test_add_zero(self, calculator: Calculator) -> None:
        result = calculator.add(10, 0)
        assert result.value == Decimal("10")

    # Subtraction tests
    def test_subtract_integers(self, calculator: Calculator) -> None:
        result = calculator.subtract(10, 5)
        assert result.value == Decimal("5")
        assert result.operation == OperationType.SUBTRACT

    def test_subtract_floats(self, calculator: Calculator) -> None:
        result = calculator.subtract(5.5, 2.5)
        assert result.value == Decimal("3.0")

    def test_subtract_negative_result(self, calculator: Calculator) -> None:
        result = calculator.subtract(5, 10)
        assert result.value == Decimal("-5")

    def test_subtract_negative_from_positive(self, calculator: Calculator) -> None:
        result = calculator.subtract(10, -5)
        assert result.value == Decimal("15")

    # Multiplication tests
    def test_multiply_integers(self, calculator: Calculator) -> None:
        result = calculator.multiply(10, 5)
        assert result.value == Decimal("50")
        assert result.operation == OperationType.MULTIPLY

    def test_multiply_floats(self, calculator: Calculator) -> None:
        result = calculator.multiply(2.5, 4)
        assert result.value == Decimal("10.0")

    def test_multiply_by_zero(self, calculator: Calculator) -> None:
        result = calculator.multiply(10, 0)
        assert result.value == Decimal("0")

    def test_multiply_negative_numbers(self, calculator: Calculator) -> None:
        result = calculator.multiply(-3, 4)
        assert result.value == Decimal("-12")

    def test_multiply_two_negatives(self, calculator: Calculator) -> None:
        result = calculator.multiply(-3, -4)
        assert result.value == Decimal("12")

    # Division tests
    def test_divide_integers(self, calculator: Calculator) -> None:
        result = calculator.divide(10, 5)
        assert result.value == Decimal("2")
        assert result.operation == OperationType.DIVIDE

    def test_divide_floats(self, calculator: Calculator) -> None:
        result = calculator.divide(10.0, 4)
        assert result.value == Decimal("2.5")

    def test_divide_by_larger(self, calculator: Calculator) -> None:
        result = calculator.divide(5, 10)
        assert result.value == Decimal("0.5")

    def test_divide_negative(self, calculator: Calculator) -> None:
        result = calculator.divide(-10, 2)
        assert result.value == Decimal("-5")

    def test_divide_by_zero_raises_error(self, calculator: Calculator) -> None:
        with pytest.raises(DivisionByZeroError) as exc_info:
            calculator.divide(10, 0)
        assert "Division by zero" in str(exc_info.value)

    def test_divide_zero_by_number(self, calculator: Calculator) -> None:
        result = calculator.divide(0, 10)
        assert result.value == Decimal("0")

    # Invalid input tests
    def test_invalid_string_input(self, calculator: Calculator) -> None:
        with pytest.raises(InvalidInputError):
            calculator.add("not a number", 5)

    def test_invalid_none_input(self, calculator: Calculator) -> None:
        with pytest.raises(InvalidInputError):
            calculator.add(None, 5)  # type: ignore

    # Calculate method tests
    def test_calculate_add(self, calculator: Calculator) -> None:
        result = calculator.calculate(OperationType.ADD, 10, 5)
        assert result.value == Decimal("15")

    def test_calculate_subtract(self, calculator: Calculator) -> None:
        result = calculator.calculate(OperationType.SUBTRACT, 10, 5)
        assert result.value == Decimal("5")

    def test_calculate_multiply(self, calculator: Calculator) -> None:
        result = calculator.calculate(OperationType.MULTIPLY, 10, 5)
        assert result.value == Decimal("50")

    def test_calculate_divide(self, calculator: Calculator) -> None:
        result = calculator.calculate(OperationType.DIVIDE, 10, 5)
        assert result.value == Decimal("2")

    # Precision tests
    def test_precision(self) -> None:
        calculator = Calculator(precision=2)
        result = calculator.divide(10, 3)
        assert result.value == Decimal("3.33")

    def test_default_precision(self, calculator: Calculator) -> None:
        result = calculator.divide(10, 3)
        # Default precision is 10 decimal places
        assert result.value == Decimal("3.3333333333")


class TestCalculatorResult:
    """Test suite for CalculatorResult class."""

    def test_str_representation(self) -> None:
        result = CalculatorResult(
            value=Decimal("15.5"),
            operation=OperationType.ADD,
            operands=(Decimal("10"), Decimal("5.5")),
        )
        assert str(result) == "15.5"

    def test_float_conversion(self) -> None:
        result = CalculatorResult(
            value=Decimal("15.5"),
            operation=OperationType.ADD,
            operands=(Decimal("10"), Decimal("5.5")),
        )
        assert float(result) == 15.5

    def test_int_conversion(self) -> None:
        result = CalculatorResult(
            value=Decimal("15"),
            operation=OperationType.ADD,
            operands=(Decimal("10"), Decimal("5")),
        )
        assert int(result) == 15

    def test_immutability(self) -> None:
        result = CalculatorResult(
            value=Decimal("15"),
            operation=OperationType.ADD,
            operands=(Decimal("10"), Decimal("5")),
        )
        with pytest.raises(AttributeError):
            result.value = Decimal("20")  # type: ignore


class TestCalculatorChain:
    """Test suite for CalculatorChain class."""

    @pytest.fixture
    def calculator(self) -> Calculator:
        return Calculator()

    def test_chain_single_add(self, calculator: Calculator) -> None:
        result = calculator.chain(10).add(5).value()
        assert result == Decimal("15")

    def test_chain_multiple_operations(self, calculator: Calculator) -> None:
        result = calculator.chain(10).add(5).multiply(2).value()
        assert result == Decimal("30")

    def test_chain_all_operations(self, calculator: Calculator) -> None:
        # ((10 + 5) * 2 - 5) / 5 = 5
        result = (
            calculator.chain(10)
            .add(5)
            .multiply(2)
            .subtract(5)
            .divide(5)
            .value()
        )
        assert result == Decimal("5")

    def test_chain_with_floats(self, calculator: Calculator) -> None:
        result = calculator.chain(10.5).add(2.5).multiply(2).value()
        assert result == Decimal("26.0")

    def test_chain_str_conversion(self, calculator: Calculator) -> None:
        chain = calculator.chain(10).add(5)
        # Decimal preserves trailing zeros based on precision
        assert str(chain) == "15.0000000000"

    def test_chain_float_conversion(self, calculator: Calculator) -> None:
        chain = calculator.chain(10).add(5.5)
        assert float(chain) == 15.5

    def test_chain_int_conversion(self, calculator: Calculator) -> None:
        chain = calculator.chain(10).add(5)
        assert int(chain) == 15

    def test_chain_divide_by_zero(self, calculator: Calculator) -> None:
        with pytest.raises(DivisionByZeroError):
            calculator.chain(10).divide(0).value()


class TestExceptions:
    """Test suite for custom exceptions."""

    def test_division_by_zero_error_message(self) -> None:
        error = DivisionByZeroError()
        assert str(error) == "Division by zero is not allowed"

    def test_invalid_input_error_message(self) -> None:
        error = InvalidInputError("abc")
        assert "Invalid number" in str(error)
        assert "abc" in str(error)

    def test_exception_inheritance(self) -> None:
        assert issubclass(DivisionByZeroError, CalculatorError)
        assert issubclass(InvalidInputError, CalculatorError)


class TestOperationType:
    """Test suite for OperationType enum."""

    def test_operation_values(self) -> None:
        assert OperationType.ADD.value == "add"
        assert OperationType.SUBTRACT.value == "subtract"
        assert OperationType.MULTIPLY.value == "multiply"
        assert OperationType.DIVIDE.value == "divide"

    def test_all_operations_exist(self) -> None:
        operations = list(OperationType)
        assert len(operations) == 4
