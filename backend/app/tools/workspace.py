"""安全解析允许被 ChangePilot AI 分析的代码仓库目录。"""

from pathlib import Path

# 当前文件位于 backend/app/tools 目录。
# parents[3] 对应整个 ChangePilot-AI 项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# 当前开发阶段只允许分析 sample_repos 中的仓库。
# 后续接入数据库后，可以把它改为用户独立工作区。
DEFAULT_WORKSPACE_ROOT = PROJECT_ROOT / "sample_repos"


class WorkspacePathError(ValueError):
    """仓库路径超出允许的工作区范围。"""


def resolve_repository_directory(
    repository_name: str,
    *,
    workspace_root: str | Path = DEFAULT_WORKSPACE_ROOT,
) -> Path:
    """安全地解析工作区中的代码仓库目录。

    repository_name 是工作区内部的仓库名称，例如 order_service。
    workspace_root 是后端允许访问的工作区根目录。
    返回值是经过规范化和安全检查的绝对路径。
    """

    # 去除用户输入两侧的多余空格。
    normalized_name = repository_name.strip()

    if not normalized_name:
        raise WorkspacePathError("代码仓库名称不能为空。")

    # 将工作区根目录转换为规范化绝对路径。
    resolved_workspace_root = Path(workspace_root).expanduser().resolve()

    if not resolved_workspace_root.exists():
        raise FileNotFoundError(f"工作区根目录不存在：{resolved_workspace_root}")

    if not resolved_workspace_root.is_dir():
        raise NotADirectoryError(f"工作区根路径不是目录：{resolved_workspace_root}")

    # 将用户提交的仓库名称拼接到工作区根目录。
    # resolve 会处理路径中的 .. 和符号链接。
    resolved_repository = (resolved_workspace_root / normalized_name).resolve()

    # 仓库目录不能直接等于整个工作区根目录。
    if resolved_repository == resolved_workspace_root:
        raise WorkspacePathError("必须指定工作区中的具体代码仓库。")

    # 最终路径必须仍然位于允许的工作区内部。
    # 该检查可以拦截 ../、绝对路径和指向外部的符号链接。
    if not resolved_repository.is_relative_to(resolved_workspace_root):
        raise WorkspacePathError("代码仓库路径超出允许的工作区范围。")

    if not resolved_repository.exists():
        raise FileNotFoundError(f"代码仓库不存在：{normalized_name}")

    if not resolved_repository.is_dir():
        raise NotADirectoryError(f"代码仓库路径不是目录：{normalized_name}")

    return resolved_repository


def list_repository_names(
    *,
    workspace_root: str | Path = DEFAULT_WORKSPACE_ROOT,
) -> tuple[str, ...]:
    """列出工作区直属仓库，不把绝对路径或外部符号链接暴露给前端。"""

    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"工作区根目录不存在：{root}")

    names: list[str] = []
    for child in root.iterdir():
        if child.name.startswith(".") or not child.is_dir():
            continue
        if child.is_symlink() or not child.resolve().is_relative_to(root):
            continue
        names.append(child.name)

    return tuple(sorted(names, key=str.casefold))
