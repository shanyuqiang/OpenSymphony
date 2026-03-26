# src/symphony/workspace.py
import re
import shutil
from pathlib import Path
from symphony.config import WorkspaceConfig, HooksConfig
from symphony.models import Issue, Workspace


def sanitize_identifier(identifier: str) -> str:
    """将 identifier 净化为安全的目录名（仅保留 [A-Za-z0-9._-]）"""
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", identifier)
    return sanitized[:100]


def ensure_safe_path(workspace_root: Path, target_path: Path) -> bool:
    """确保 target_path 在 workspace_root 内"""
    try:
        root = workspace_root.resolve()
        target = target_path.resolve()
        return str(target).startswith(str(root))
    except (OSError, ValueError):
        return False


class WorkspaceManager:
    def __init__(self, config: WorkspaceConfig, hooks: HooksConfig | None = None):
        self.root = Path(config.root).expanduser().resolve()
        self.hooks = hooks or HooksConfig()

    async def create_for_issue(self, issue: Issue) -> Workspace:
        """为 issue 创建或复用 workspace"""
        key = sanitize_identifier(issue.identifier)
        path = self.root / key

        if not ensure_safe_path(self.root, path):
            raise WorkspaceError(f"不安全的路径: {path}")

        created_now = not path.exists()
        path.mkdir(parents=True, exist_ok=True)

        workspace = Workspace(path=path, workspace_key=key, created_now=created_now)

        if created_now and self.hooks.after_create:
            await self._run_hook("after_create", path, fatal=True)

        return workspace

    async def remove_for_issue(self, issue: Issue) -> None:
        """清理 workspace"""
        key = sanitize_identifier(issue.identifier)
        path = self.root / key

        if not ensure_safe_path(self.root, path):
            raise WorkspaceError(f"不安全的路径: {path}")

        if path.exists():
            if self.hooks.before_remove:
                await self._run_hook("before_remove", path, fatal=False)
            shutil.rmtree(path)

    async def _run_hook(self, hook_name: str, workspace_path: Path, fatal: bool) -> bool:
        """运行 hook 脚本"""
        import asyncio

        hook_script = getattr(self.hooks, hook_name)
        if not hook_script:
            return True

        try:
            proc = await asyncio.create_subprocess_shell(
                hook_script,
                cwd=workspace_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.hooks.timeout_ms / 1000
            )

            if proc.returncode != 0:
                if fatal:
                    raise WorkspaceError(
                        f"Hook {hook_name} failed with exit code {proc.returncode}: {stderr.decode()}"
                    )
                return False
            return True
        except asyncio.TimeoutError:
            if fatal:
                raise WorkspaceError(f"Hook {hook_name} timed out")
            return False


class WorkspaceError(Exception):
    """Workspace 操作错误"""
    pass
