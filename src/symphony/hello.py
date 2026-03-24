"""Simple hello module."""


def hello() -> str:
    """Return a greeting message.

    Returns:
        str: The greeting message "hello world".
    """
    return "hello world"


if __name__ == "__main__":
    print(hello())
