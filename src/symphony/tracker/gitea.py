# src/symphony/tracker/gitea.py
from datetime import datetime
from typing import Any
import httpx
from symphony.config import TrackerConfig
from symphony.models import Issue, Blocker
from symphony.tracker.base import Tracker, TrackerError


class GiteaTracker(Tracker):
    """Gitea REST API 实现"""

    def __init__(self, config: TrackerConfig):
        self.config = config
        self.endpoint = config.endpoint.rstrip("/")
        self.client = httpx.AsyncClient(
            headers={"Authorization": f"token {config.api_key}"},
            timeout=30.0,
            trust_env=False,  # Disable proxy from environment
        )

    async def fetch_candidate_issues(self) -> list[Issue]:
        """获取候选 issues（active states）"""
        issues = []
        page = 1

        while True:
            resp = await self.client.get(
                f"{self.endpoint}/repos/{self.config.owner}/{self.config.repo}/issues",
                params={"state": "open", "page": page, "limit": 50},
            )
            resp.raise_for_status()

            data = resp.json()
            if not data:
                break

            for item in data:
                if item.get("pull_request"):
                    continue  # 跳过 PR
                issues.append(self._normalize_issue(item))

            if len(data) < 50:
                break
            page += 1

        return issues

    async def fetch_issues_by_states(self, states: list[str]) -> list[Issue]:
        """获取指定状态的 issues（用于启动时清理）"""
        if not states:
            return []

        issues = []
        for state in states:
            page = 1
            while True:
                resp = await self.client.get(
                    f"{self.endpoint}/repos/{self.config.owner}/{self.config.repo}/issues",
                    params={"state": state, "page": page, "limit": 50},
                )
                resp.raise_for_status()

                data = resp.json()
                if not data:
                    break

                for item in data:
                    if item.get("pull_request"):
                        continue
                    issues.append(self._normalize_issue(item))

                if len(data) < 50:
                    break
                page += 1

        return issues

    async def fetch_issue_states_by_ids(self, issue_ids: list[str]) -> list[Issue]:
        """刷新指定 issues 的状态"""
        results = []
        for issue_id in issue_ids:
            # issue_id 是 Gitea 内部 ID，使用 number 查询
            resp = await self.client.get(
                f"{self.endpoint}/repos/{self.config.owner}/{self.config.repo}/issues/{issue_id}"
            )
            if resp.status_code == 200:
                results.append(self._normalize_issue(resp.json()))
        return results

    async def add_label(self, issue_number: int, label: str) -> bool:
        """为 issue 添加标签"""
        resp = await self.client.post(
            f"{self.endpoint}/repos/{self.config.owner}/{self.config.repo}/issues/{issue_number}/labels",
            json={"labels": [label]},
        )
        return resp.status_code in (200, 201)

    async def remove_label(self, issue_number: int, label: str) -> bool:
        """从 issue 移除标签"""
        resp = await self.client.delete(
            f"{self.endpoint}/repos/{self.config.owner}/{self.config.repo}/issues/{issue_number}/labels/{label}",
        )
        return resp.status_code in (200, 204)

    def _normalize_issue(self, data: dict[str, Any]) -> Issue:
        """将 Gitea API 响应标准化为 Issue 模型"""
        return Issue(
            id=str(data["id"]),  # Gitea 内部 ID
            identifier=f"{self.config.owner}/{self.config.repo}#{data['number']}",
            number=data["number"],
            title=data["title"],
            description=data.get("body"),
            state=data["state"],  # "open" or "closed"
            labels=[label["name"].lower() for label in data.get("labels", [])],
            priority=self._extract_priority(data.get("labels", [])),
            url=data["html_url"],
            blocked_by=[],  # Gitea 需要额外 API 获取依赖关系
            created_at=datetime.fromisoformat(data["created_at"].replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00")),
            owner=self.config.owner,
            repo=self.config.repo,
        )

    def _extract_priority(self, labels: list[dict]) -> int | None:
        """从 label 中提取优先级"""
        priority_map = {
            "priority/urgent": 1,
            "priority/high": 2,
            "priority/medium": 3,
            "priority/low": 4,
            "urgent": 1,
            "high": 2,
            "medium": 3,
            "low": 4,
        }

        for label in labels:
            name = label["name"].lower()
            if name in priority_map:
                return priority_map[name]

        return None

    async def close(self):
        """关闭 HTTP 客户端"""
        await self.client.aclose()
