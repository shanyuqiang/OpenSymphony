"""
Calculator module with basic arithmetic operations.

This module provides a functional-style calculator with immutable operations
and comprehensive error handling.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum


class CalculatorError(Exception):
    """Base exception for calculator operations."""

    pass


class DivisionByZeroError(CalculatorError):
    """Raised when attempting to divide by zero."""

    def __init__(self) -> None:
        super().__init__("Division by zero is not allowed")


class InvalidInputError(CalculatorError):
    """Raised when input is not a valid number."""

    def __init__(self, value: object) -> None:
        super().__init__(f"Invalid number: {value!r}")


class OperationType(Enum):
    """Supported calculator operations."""

    ADD = "add"
    SUBTRACT = "subtract"
    MULTIPLY = "multiply"
    DIVIDE = "divide"


@dataclass(frozen=True)
class CalculatorResult:
    """Immutable result of a calculator operation."""

    value: Decimal
    operation: OperationType
    operands: tuple[Decimal, Decimal]

    def __str__(self) -> str:
        return str(self.value)

    def __float__(self) -> float:
        return float(self.value)

    def __int__(self) -> int:
        return int(self.value)


class Calculator:
    """
    A functional calculator supporting basic arithmetic operations.

    All operations return new CalculatorResult instances, maintaining immutability.
    Uses Decimal for precise decimal arithmetic.

    Example:
        >>> calc = Calculator()
        >>> result = calc.add(10, 5)
        >>> print(result)
        15
        >>> calc.divide(10, 0)
        CalculatorError: Division by zero is not allowed
    """

    def __init__(self, precision: int = 10) -> None:
        """
        Initialize the calculator.

        Args:
            precision: Number of decimal places for results.
        """
        self._precision = precision

    def _to_decimal(self, value: int | float | str | Decimal) -> Decimal:
        """Convert input value to Decimal with validation."""
        if isinstance(value, Decimal):
            return value

        try:
            if isinstance(value, float):
                return Decimal(str(value))
            return Decimal(value)
        except (InvalidOperation, ValueError, TypeError) as e:
            raise InvalidInputError(value) from e

    def _round(self, value: Decimal) -> Decimal:
        """Round value to configured precision."""
        return round(value, self._precision)

    def add(
        self, a: int | float | str | Decimal, b: int | float | str | Decimal
    ) -> CalculatorResult:
        """
        Add two numbers.

        Args:
            a: First operand.
            b: Second operand.

        Returns:
            CalculatorResult with the sum.

        Raises:
            InvalidInputError: If inputs are not valid numbers.
        """
        dec_a = self._to_decimal(a)
        dec_b = self._to_decimal(b)
        result = self._round(dec_a + dec_b)
        return CalculatorResult(
            value=result,
            operation=OperationType.ADD,
            operands=(dec_a, dec_b),
        )

    def subtract(
        self, a: int | float | str | Decimal, b: int | float | str | Decimal
    ) -> CalculatorResult:
        """
        Subtract second number from first.

        Args:
            a: First operand.
            b: Second operand.

        Returns:
            CalculatorResult with the difference.

        Raises:
            InvalidInputError: If inputs are not valid numbers.
        """
        dec_a = self._to_decimal(a)
        dec_b = self._to_decimal(b)
        result = self._round(dec_a - dec_b)
        return CalculatorResult(
            value=result,
            operation=OperationType.SUBTRACT,
            operands=(dec_a, dec_b),
        )

    def multiply(
        self, a: int | float | str | Decimal, b: int | float | str | Decimal
    ) -> CalculatorResult:
        """
        Multiply two numbers.

        Args:
            a: First operand.
            b: Second operand.

        Returns:
            CalculatorResult with the product.

        Raises:
            InvalidInputError: If inputs are not valid numbers.
        """
        dec_a = self._to_decimal(a)
        dec_b = self._to_decimal(b)
        result = self._round(dec_a * dec_b)
        return CalculatorResult(
            value=result,
            operation=OperationType.MULTIPLY,
            operands=(dec_a, dec_b),
        )

    def divide(
        self, a: int | float | str | Decimal, b: int | float | str | Decimal
    ) -> CalculatorResult:
        """
        Divide first number by second.

        Args:
            a: Dividend.
            b: Divisor.

        Returns:
            CalculatorResult with the quotient.

        Raises:
            InvalidInputError: If inputs are not valid numbers.
            DivisionByZeroError: If divisor is zero.
        """
        dec_a = self._to_decimal(a)
        dec_b = self._to_decimal(b)

        if dec_b == 0:
            raise DivisionByZeroError()

        result = self._round(dec_a / dec_b)
        return CalculatorResult(
            value=result,
            operation=OperationType.DIVIDE,
            operands=(dec_a, dec_b),
        )

    def calculate(
        self, operation: OperationType, a: int | float | str | Decimal, b: int | float | str | Decimal
    ) -> CalculatorResult:
        """
        Perform a calculation based on operation type.

        Args:
            operation: The operation to perform.
            a: First operand.
            b: Second operand.

        Returns:
            CalculatorResult with the result.

        Raises:
            InvalidInputError: If inputs are not valid numbers.
            DivisionByZeroError: If dividing by zero.
        """
        operations = {
            OperationType.ADD: self.add,
            OperationType.SUBTRACT: self.subtract,
            OperationType.MULTIPLY: self.multiply,
            OperationType.DIVIDE: self.divide,
        }

        handler = operations.get(operation)
        if handler is None:
            raise CalculatorError(f"Unknown operation: {operation}")

        return handler(a, b)

    def chain(self, initial_value: int | float | str | Decimal) -> CalculatorChain:
        """
        Start a calculation chain.

        Args:
            initial_value: Starting value for the chain.

        Returns:
            CalculatorChain instance for fluent operations.
        """
        return CalculatorChain(self, self._to_decimal(initial_value))


class CalculatorChain:
    """
    Fluent interface for chained calculator operations.

    Example:
        >>> calc = Calculator()
        >>> result = calc.chain(10).add(5).multiply(2).value()
        >>> print(result)
        30
    """

    def __init__(self, calculator: Calculator, value: Decimal) -> None:
        self._calculator = calculator
        self._value = value

    def add(self, operand: int | float | str | Decimal) -> CalculatorChain:
        """Add operand to current value."""
        result = self._calculator.add(self._value, operand)
        return CalculatorChain(self._calculator, result.value)

    def subtract(self, operand: int | float | str | Decimal) -> CalculatorChain:
        """Subtract operand from current value."""
        result = self._calculator.subtract(self._value, operand)
        return CalculatorChain(self._calculator, result.value)

    def multiply(self, operand: int | float | str | Decimal) -> CalculatorChain:
        """Multiply current value by operand."""
        result = self._calculator.multiply(self._value, operand)
        return CalculatorChain(self._calculator, result.value)

    def divide(self, operand: int | float | str | Decimal) -> CalculatorChain:
        """Divide current value by operand."""
        result = self._calculator.divide(self._value, operand)
        return CalculatorChain(self._calculator, result.value)

    def value(self) -> Decimal:
        """Get the current value."""
        return self._value

    def __str__(self) -> str:
        return str(self._value)

    def __float__(self) -> float:
        return float(self._value)

    def __int__(self) -> int:
        return int(self._value)
