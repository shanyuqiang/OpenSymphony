"""문자열 처리 유틸리티 모듈.

문자열 역순, 회문 판별, 단어 수 계산 등 일반적인 텍스트 처리 함수를 제공한다.
"""

from __future__ import annotations


def reverse(s: str) -> str:
    """문자열을 역순으로 뒤집는다.

    Args:
        s: 입력 문자열

    Returns:
        역순으로 뒤집은 문자열
    """
    return s[::-1]


def is_palindrome(s: str) -> bool:
    """문자열이 회문(palindrome)인지 판별한다.

    영문자/숫자만 대상으로 하며, 대소문자는 구분하지 않는다.

    Args:
        s: 입력 문자열

    Returns:
        회문이면 True, 아니면 False
    """
    cleaned = "".join(c.lower() for c in s if c.isalnum())
    return cleaned == cleaned[::-1]


def word_count(s: str) -> int:
    """문자열에 포함된 단어 수를 계산한다.

    공백을 기준으로 단어를 분리한다.

    Args:
        s: 입력 문자열

    Returns:
        단어 수
    """
    if not s:
        return 0
    return len(s.split())
