"""代码符号索引使用的结构化数据模型。"""

from typing import Literal

from pydantic import BaseModel, Field

SymbolKind = Literal[
    "class",
    "function",
    "async_function",
    "method",
    "async_method",
]


class CodeSymbol(BaseModel):
    """代码文件中定义的一个类、函数或方法。"""

    # 符号自身名称，例如 OrderService 或 cancel_order。
    name: str = Field(
        min_length=1,
        description="代码符号名称",
    )

    # 包含模块和父级作用域的完整名称。
    qualified_name: str = Field(
        min_length=1,
        description="代码符号完整限定名称",
        examples=["order_app.service.OrderService.cancel_order"],
    )

    # 区分类、普通函数、异步函数、方法和异步方法。
    kind: SymbolKind = Field(
        description="代码符号类型",
    )

    # 符号所在文件相对于仓库根目录的路径。
    file_path: str = Field(
        min_length=1,
        description="符号所在文件的仓库相对路径",
    )

    # 符号定义开始行。
    line_start: int = Field(
        ge=1,
        description="符号定义开始行号",
    )

    # 符号定义结束行。
    line_end: int = Field(
        ge=1,
        description="符号定义结束行号",
    )

    # 类或函数的声明签名。
    signature: str = Field(
        min_length=1,
        description="代码符号声明签名",
    )

    # 符号的文档字符串。
    # 没有文档字符串时为 None。
    docstring: str | None = Field(
        default=None,
        description="代码符号文档字符串",
    )


class ImportReference(BaseModel):
    """Python 文件中的一条导入关系。"""

    # 被导入的模块，例如 pathlib 或 order_app.models。
    # 对于 from . import models，module 可以为空字符串。
    module: str = Field(
        description="被导入模块名称",
    )

    # from module import name 中被导入的名称。
    # 普通 import module 时为 None。
    imported_name: str | None = Field(
        default=None,
        description="从模块中导入的具体名称",
    )

    # 使用 as 指定的别名。
    alias: str | None = Field(
        default=None,
        description="导入别名",
    )

    # 相对导入层级。
    # 绝对导入为 0，from . 为 1，from .. 为 2。
    relative_level: int = Field(
        default=0,
        ge=0,
        description="Python 相对导入层级",
    )

    # 导入语句所在行。
    line_number: int = Field(
        ge=1,
        description="导入语句所在行号",
    )


class PythonFileIndex(BaseModel):
    """一个 Python 文件的符号和导入索引。"""

    # 文件相对于仓库根目录的路径。
    file_path: str = Field(
        min_length=1,
        description="Python 文件相对路径",
    )

    # 根据文件路径推导出的 Python 模块名称。
    module_name: str = Field(
        min_length=1,
        description="Python 模块名称",
    )

    # 文件中定义的类、函数和方法。
    symbols: list[CodeSymbol] = Field(
        default_factory=list,
        description="文件中的代码符号",
    )

    # 文件中出现的导入关系。
    imports: list[ImportReference] = Field(
        default_factory=list,
        description="文件中的导入关系",
    )

    # 文件无法被 AST 解析时记录错误。
    # 其他文件仍然可以继续建立索引。
    parse_error: str | None = Field(
        default=None,
        description="Python 语法解析错误",
    )


class RepositorySymbolIndex(BaseModel):
    """整个代码仓库的 Python 符号索引。"""

    repository_name: str = Field(
        min_length=1,
        description="代码仓库名称",
    )

    files: list[PythonFileIndex] = Field(
        default_factory=list,
        description="已经建立索引的 Python 文件",
    )

    symbol_count: int = Field(
        ge=0,
        description="成功提取的代码符号总数",
    )

    import_count: int = Field(
        ge=0,
        description="成功提取的导入关系总数",
    )

    parse_error_count: int = Field(
        ge=0,
        description="无法完成语法解析的 Python 文件数量",
    )
