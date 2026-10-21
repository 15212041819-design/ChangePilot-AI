"""代码仓库扫描结果使用的数据模型。"""

from pydantic import BaseModel, Field


class RepositoryFile(BaseModel):
    """代码仓库中的一个源代码文件。"""

    # 文件相对于代码仓库根目录的路径。
    # 使用相对路径可以避免把用户电脑上的绝对路径发送给模型。
    relative_path: str = Field(
        description="文件相对于代码仓库根目录的路径",
        examples=["src/services/order.py"],
    )

    # 根据文件扩展名判断出的编程语言。
    language: str = Field(
        description="文件使用的编程语言或配置格式",
        examples=["python"],
    )

    # 文件在磁盘上的字节数，可用于限制过大的文件。
    size_bytes: int = Field(
        ge=0,
        description="文件大小，单位为字节",
    )

    # 文件的文本内容，后续会提供给代码影响分析智能体。
    content: str = Field(
        description="源代码文件的文本内容",
    )


class RepositorySnapshot(BaseModel):
    """某一时刻的代码仓库扫描快照。"""

    # 代码仓库目录名称，不包含完整的本地绝对路径。
    repository_name: str = Field(
        description="代码仓库名称",
    )

    # 扫描得到的源代码文件集合。
    files: list[RepositoryFile] = Field(
        default_factory=list,
        description="代码仓库中被成功读取的文件",
    )

    # 实际读取的文件数量。
    scanned_file_count: int = Field(
        ge=0,
        description="成功扫描的文件数量",
    )

    # 如果文件数量超过限制，该字段为 True。
    truncated: bool = Field(
        default=False,
        description="扫描结果是否因为文件数量限制而被截断",
    )
