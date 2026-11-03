"""向本地前端提供允许分析的仓库名称。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.tools.workspace import DEFAULT_WORKSPACE_ROOT, list_repository_names

router = APIRouter(tags=["Repositories"])


class RepositoryListResponse(BaseModel):
    """仅返回目录名；前端不需要接触服务器绝对路径。"""

    repositories: tuple[str, ...]


def get_workspace_root() -> str:
    """以依赖形式暴露工作区位置，方便测试替换。"""

    return str(DEFAULT_WORKSPACE_ROOT)


@router.get("/repositories", response_model=RepositoryListResponse)
def list_repositories(
    workspace_root: Annotated[str, Depends(get_workspace_root)],
) -> RepositoryListResponse:
    """列出用户可以在分析表单中选择的仓库。"""

    try:
        names = list_repository_names(workspace_root=workspace_root)
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="代码工作区不可用，请检查 sample_repos 目录。",
        ) from exc
    return RepositoryListResponse(repositories=names)
