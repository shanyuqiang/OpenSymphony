# src/symphony/tracker/base.py
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any


class TrackerError(Exception):
    """Tracker API 错误"""
    pass


class Tracker(ABC):
    """Issue Tracker 抽象基类"""

    @abstractmethod
    async def fetch_candidate_issues(self) -> list[dict[str, Any]]:
        """获取候选 issues（active states）"""
        pass

    @abstractmethod
    async def fetch_issues_by_states(self, states: list[str]) -> list[dict[str, Any]]:
        """获取指定状态的 issues（用于启动时清理）"""
        pass

    @abstractmethod
    async def fetch_issue_states_by_ids(self, issue_ids: list[str]) -> list[dict[str, Any]]:
        """刷新指定 issues 的状态"""
        pass

    @abstractmethod
    async def add_label(self, issue_number: int, label: str) -> bool:
        """为 issue 添加标签"""
        pass

    @abstractmethod
    async def remove_label(self, issue_number: int, label: str) -> bool:
        """从 issue 移除标签"""
        pass
