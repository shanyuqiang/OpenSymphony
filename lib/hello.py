"""Simple hello module."""


def greet(name: str) -> str:
    """Return a greeting for the given name.

    Args:
        name: The name to greet.

    Returns:
        A greeting string in the format "Hello, {name}!"
    """
    return f"Hello, {name}!"
