"""Python 模块依赖图使用的结构化数据模型。

本模块只定义数据，不负责读取磁盘、解析源码或调用大模型。
确定的数据结构便于 LangGraph 节点、代码影响智能体和 API 复用。
"""

from typing import Literal

from pydantic import BaseModel, Field

DependencyKind = Literal[
    "internal",
    "external",
    "unresolved_internal",
]


class PythonModuleNode(BaseModel):
    """依赖图中的一个仓库内 Python 模块。"""

    # Python 模块名称，例如 order_app.service。
    module_name: str = Field(
        min_length=1,
        description="Python 模块名称",
    )

    # 模块对应的仓库相对文件路径。
    file_path: str = Field(
        min_length=1,
        description="模块对应的仓库相对文件路径",
    )

    # 文件是否存在语法解析错误。
    # 即使解析失败，模块节点仍然保留在图中。
    has_parse_error: bool = Field(
        default=False,
        description="模块是否存在 Python 语法解析错误",
    )


class ModuleDependency(BaseModel):
    """一条从导入方指向被导入模块的依赖边。"""

    # 发起 import 的仓库内模块。
    source_module: str = Field(
        min_length=1,
        description="导入方模块名称",
    )

    # 发起 import 的文件路径。
    source_file: str = Field(
        min_length=1,
        description="导入方文件路径",
    )

    # 解析后的目标模块名称。
    target_module: str = Field(
        min_length=1,
        description="被导入模块名称",
    )

    # 仓库内部依赖可以定位到具体文件。
    # 外部依赖或仓库内未解析依赖没有对应文件时为 None。
    target_file: str | None = Field(
        default=None,
        description="被导入模块对应的仓库相对文件路径",
    )

    # from module import name 中的 name。
    # 普通 import module 时为 None。
    imported_name: str | None = Field(
        default=None,
        description="从目标模块导入的具体名称",
    )

    # internal 表示目标模块存在于当前仓库。
    # external 表示第三方库或 Python 标准库。
    # unresolved_internal 表示导入看起来属于当前仓库，
    # 但扫描快照中没有找到对应模块。
    kind: DependencyKind = Field(
        description="依赖关系类型",
    )

    # 原始 Python 相对导入层级。
    relative_level: int = Field(
        default=0,
        ge=0,
        description="Python 相对导入层级",
    )

    # import 语句所在行号，便于展示证据。
    line_number: int = Field(
        ge=1,
        description="导入语句所在行号",
    )


class RepositoryDependencyGraph(BaseModel):
    """整个代码仓库的 Python 模块依赖图。"""

    repository_name: str = Field(
        min_length=1,
        description="代码仓库名称",
    )

    modules: list[PythonModuleNode] = Field(
        default_factory=list,
        description="仓库内 Python 模块节点",
    )

    dependencies: list[ModuleDependency] = Field(
        default_factory=list,
        description="模块导入依赖边",
    )

    internal_dependency_count: int = Field(
        ge=0,
        description="仓库内部依赖数量",
    )

    external_dependency_count: int = Field(
        ge=0,
        description="外部依赖数量",
    )

    unresolved_internal_dependency_count: int = Field(
        ge=0,
        description="仓库内未解析依赖数量",
    )


class DependencyImpact(BaseModel):
    """一个或多个模块发生变更后的反向影响查询结果。"""

    # 用户声明发生变更的仓库内模块。
    changed_modules: list[str] = Field(
        default_factory=list,
        description="发生变更的模块",
    )

    # 直接导入变更模块的第一层模块。
    direct_dependents: list[str] = Field(
        default_factory=list,
        description="直接依赖变更模块的模块",
    )

    # 通过两层或更多依赖链受到影响的模块。
    transitive_dependents: list[str] = Field(
        default_factory=list,
        description="间接依赖变更模块的模块",
    )

    # 变更模块与所有上游依赖方的合集。
    affected_modules: list[str] = Field(
        default_factory=list,
        description="全部可能受影响的模块",
    )

    # affected_modules 对应的仓库相对文件路径。
    affected_files: list[str] = Field(
        default_factory=list,
        description="全部可能受影响的文件",
    )
