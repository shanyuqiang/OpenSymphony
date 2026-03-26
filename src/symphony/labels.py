# src/symphony/labels.py
from symphony.models import Issue
from symphony.tracker.gitea import GiteaTracker


SYMPHONY_DOING_LABEL = "symphony-doing"
SYMPHONY_DONE_LABEL = "symphony-done"


class LabelLifecycleManager:
    """管理 issue 的 label 生命周期"""

    def __init__(self, tracker: GiteaTracker):
        self.tracker = tracker

    async def on_dispatch(self, issue: Issue) -> bool:
        """调度时调用：添加 symphony-doing"""
        return await self.tracker.add_label(issue.number, SYMPHONY_DOING_LABEL)

    async def on_completion_detected(self, issue: Issue) -> bool:
        """检测到完成时调用：移除 symphony-doing"""
        return await self.tracker.remove_label(issue.number, SYMPHONY_DOING_LABEL)

    def should_dispatch(self, issue: Issue) -> bool:
        """检查是否应该调度此 issue"""
        labels = [label.lower() for label in issue.labels]

        # 如果有 symphony-doing，说明正在被其他实例处理
        if SYMPHONY_DOING_LABEL in labels:
            return False

        # 如果有 symphony-done，说明已完成，不需要再调度
        if SYMPHONY_DONE_LABEL in labels:
            return False

        return True

    def is_completed(self, issue: Issue) -> bool:
        """检查 issue 是否已完成"""
        labels = [label.lower() for label in issue.labels]
        return SYMPHONY_DONE_LABEL in labels
