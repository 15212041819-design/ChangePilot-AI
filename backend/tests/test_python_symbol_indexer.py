"""Python 符号索引工具的自动化测试。"""

from app.schemas.repository import (
    RepositoryFile,
    RepositorySnapshot,
)
from app.tools.python_symbol_indexer import (
    build_module_name,
    index_python_symbols,
)


def build_repository(
    files: list[RepositoryFile],
) -> RepositorySnapshot:
    """根据给定文件创建测试仓库快照。"""

    return RepositorySnapshot(
        repository_name="order_service",
        files=files,
        scanned_file_count=len(files),
        truncated=False,
    )


def test_indexer_extracts_classes_functions_and_imports() -> None:
    """索引器应该提取类、函数、方法和导入关系。"""

    repository = build_repository(
        [
            RepositoryFile(
                relative_path="order_app/service.py",
                language="python",
                size_bytes=500,
                content="""
import os
from pathlib import Path as FilePath

class BaseService:
    pass

class OrderService(BaseService):
    \"\"\"订单业务服务。\"\"\"

    def cancel_order(
        self,
        order_id: str,
    ) -> bool:
        \"\"\"取消一个订单。\"\"\"

        return True

def build_service() -> OrderService:
    return OrderService()
""".strip(),
            )
        ]
    )

    result = index_python_symbols(repository)

    assert result.repository_name == "order_service"
    assert result.symbol_count == 4
    assert result.import_count == 2
    assert result.parse_error_count == 0

    file_index = result.files[0]

    assert file_index.file_path == ("order_app/service.py")
    assert file_index.module_name == ("order_app.service")
    assert file_index.parse_error is None

    symbols_by_name = {symbol.qualified_name: symbol for symbol in file_index.symbols}

    assert symbols_by_name["order_app.service.BaseService"].kind == "class"
    assert (
        symbols_by_name["order_app.service.OrderService"].signature
        == "class OrderService(BaseService)"
    )
    assert symbols_by_name["order_app.service.OrderService"].docstring == "订单业务服务。"

    cancel_order = symbols_by_name["order_app.service.OrderService.cancel_order"]

    assert cancel_order.kind == "method"
    assert cancel_order.line_start > 1
    assert "order_id: str" in cancel_order.signature
    assert cancel_order.docstring == "取消一个订单。"

    build_service = symbols_by_name["order_app.service.build_service"]

    assert build_service.kind == "function"
    assert build_service.signature == "def build_service() -> OrderService"

    first_import = file_index.imports[0]
    second_import = file_index.imports[1]

    assert first_import.module == "os"
    assert first_import.imported_name is None
    assert first_import.alias is None

    assert second_import.module == "pathlib"
    assert second_import.imported_name == "Path"
    assert second_import.alias == "FilePath"


def test_indexer_extracts_async_functions_and_methods() -> None:
    """索引器应该区分异步函数和异步方法。"""

    repository = build_repository(
        [
            RepositoryFile(
                relative_path="order_app/tasks.py",
                language="python",
                size_bytes=300,
                content="""
async def synchronize_orders() -> None:
    pass

class OrderWorker:
    async def run(self, order_id: str) -> None:
        pass
""".strip(),
            )
        ]
    )

    result = index_python_symbols(repository)

    symbols = result.files[0].symbols
    symbols_by_name = {symbol.qualified_name: symbol for symbol in symbols}

    assert symbols_by_name["order_app.tasks.synchronize_orders"].kind == "async_function"

    assert symbols_by_name["order_app.tasks.OrderWorker.run"].kind == "async_method"


def test_indexer_extracts_relative_imports() -> None:
    """索引器应该保留 Python 相对导入层级。"""

    repository = build_repository(
        [
            RepositoryFile(
                relative_path="order_app/service.py",
                language="python",
                size_bytes=100,
                content=("from .models import Order\nfrom ..shared import audit as audit_module\n"),
            )
        ]
    )

    result = index_python_symbols(repository)

    imports = result.files[0].imports

    assert len(imports) == 2

    assert imports[0].module == "models"
    assert imports[0].imported_name == "Order"
    assert imports[0].relative_level == 1

    assert imports[1].module == "shared"
    assert imports[1].imported_name == "audit"
    assert imports[1].alias == "audit_module"
    assert imports[1].relative_level == 2


def test_indexer_records_syntax_errors_without_stopping() -> None:
    """单个文件语法错误不应该阻止其他文件建立索引。"""

    repository = build_repository(
        [
            RepositoryFile(
                relative_path="order_app/broken.py",
                language="python",
                size_bytes=100,
                content="def broken(:\n    pass\n",
            ),
            RepositoryFile(
                relative_path="order_app/valid.py",
                language="python",
                size_bytes=100,
                content=('def valid_function() -> str:\n    return "ok"\n'),
            ),
        ]
    )

    result = index_python_symbols(repository)

    assert len(result.files) == 2
    assert result.parse_error_count == 1
    assert result.symbol_count == 1

    broken_file = result.files[0]
    valid_file = result.files[1]

    assert broken_file.parse_error is not None
    assert broken_file.symbols == []
    assert broken_file.imports == []

    assert valid_file.parse_error is None
    assert valid_file.symbols[0].name == "valid_function"


def test_indexer_ignores_non_python_files() -> None:
    """Python 索引器不应该尝试解析其他语言文件。"""

    repository = build_repository(
        [
            RepositoryFile(
                relative_path="src/index.ts",
                language="typescript",
                size_bytes=100,
                content=("export function main(): void {}"),
            ),
            RepositoryFile(
                relative_path="README.md",
                language="markdown",
                size_bytes=100,
                content="# Demo",
            ),
        ]
    )

    result = index_python_symbols(repository)

    assert result.files == []
    assert result.symbol_count == 0
    assert result.import_count == 0
    assert result.parse_error_count == 0


def test_build_module_name_handles_package_init_file() -> None:
    """包中的 __init__.py 应该映射为包模块名称。"""

    assert build_module_name("order_app/__init__.py") == "order_app"

    assert build_module_name("order_app/service.py") == "order_app.service"

    assert build_module_name("__init__.py") == "__root__"
