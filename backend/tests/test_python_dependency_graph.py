"""Python 模块依赖图工具的自动化测试。"""

import pytest

from app.schemas.repository import (
    RepositoryFile,
    RepositorySnapshot,
)
from app.tools.python_dependency_graph import (
    build_python_dependency_graph,
    find_affected_modules,
)
from app.tools.python_symbol_indexer import index_python_symbols


def build_repository(
    files: list[RepositoryFile],
) -> RepositorySnapshot:
    """根据测试文件创建仓库扫描快照。"""

    return RepositorySnapshot(
        repository_name="order_service",
        files=files,
        scanned_file_count=len(files),
        truncated=False,
    )


def build_graph(
    files: list[RepositoryFile],
):
    """依次执行符号索引和依赖图构建。"""

    repository = build_repository(files)
    symbol_index = index_python_symbols(repository)

    return build_python_dependency_graph(symbol_index)


def python_file(
    path: str,
    content: str,
) -> RepositoryFile:
    """创建一个测试用 Python 仓库文件。"""

    return RepositoryFile(
        relative_path=path,
        language="python",
        size_bytes=len(content.encode("utf-8")),
        content=content,
    )


def test_graph_classifies_internal_and_external_dependencies() -> None:
    """依赖图应该区分仓库内部模块和外部模块。"""

    graph = build_graph(
        [
            python_file(
                "order_app/models.py",
                "class Order:\n    pass\n",
            ),
            python_file(
                "order_app/service.py",
                "from order_app.models import Order\n",
            ),
            python_file(
                "order_app/api.py",
                "from order_app.service import OrderService\n",
            ),
            python_file(
                "order_app/paths.py",
                "from pathlib import Path\n",
            ),
        ]
    )

    assert graph.repository_name == "order_service"
    assert len(graph.modules) == 4
    assert graph.internal_dependency_count == 2
    assert graph.external_dependency_count == 1
    assert graph.unresolved_internal_dependency_count == 0

    dependencies_by_source = {
        dependency.source_module: dependency for dependency in graph.dependencies
    }

    service_dependency = dependencies_by_source["order_app.service"]

    assert service_dependency.kind == "internal"
    assert service_dependency.target_module == "order_app.models"
    assert service_dependency.target_file == "order_app/models.py"
    assert service_dependency.imported_name == "Order"

    external_dependency = dependencies_by_source["order_app.paths"]

    assert external_dependency.kind == "external"
    assert external_dependency.target_module == "pathlib"
    assert external_dependency.target_file is None


def test_graph_resolves_relative_imports() -> None:
    """依赖图应该正确解析当前包和父包相对导入。"""

    graph = build_graph(
        [
            python_file(
                "order_app/__init__.py",
                "from .service import OrderService\n",
            ),
            python_file(
                "order_app/service.py",
                "class OrderService:\n    pass\n",
            ),
            python_file(
                "order_app/api/handlers.py",
                ("from ..service import OrderService\nfrom . import validators\n"),
            ),
            python_file(
                "order_app/api/validators.py",
                ("def validate_order() -> bool:\n    return True\n"),
            ),
        ]
    )

    internal_dependencies = [
        dependency for dependency in graph.dependencies if dependency.kind == "internal"
    ]

    assert graph.internal_dependency_count == 3
    assert {
        (
            dependency.source_module,
            dependency.target_module,
            dependency.relative_level,
        )
        for dependency in internal_dependencies
    } == {
        ("order_app", "order_app.service", 1),
        (
            "order_app.api.handlers",
            "order_app.service",
            2,
        ),
        (
            "order_app.api.handlers",
            "order_app.api.validators",
            1,
        ),
    }


def test_graph_marks_missing_repository_module_as_unresolved() -> None:
    """仓库命名空间内不存在的模块应该标记为未解析。"""

    graph = build_graph(
        [
            python_file(
                "order_app/service.py",
                ("from order_app.missing import MissingService\n"),
            )
        ]
    )

    assert graph.internal_dependency_count == 0
    assert graph.external_dependency_count == 0
    assert graph.unresolved_internal_dependency_count == 1

    dependency = graph.dependencies[0]

    assert dependency.kind == "unresolved_internal"
    assert dependency.target_module == "order_app.missing"
    assert dependency.target_file is None


def test_find_affected_modules_follows_transitive_dependents() -> None:
    """模块变更应该沿反向依赖链传播到间接调用方。"""

    graph = build_graph(
        [
            python_file(
                "order_app/models.py",
                "class Order:\n    pass\n",
            ),
            python_file(
                "order_app/service.py",
                "from order_app.models import Order\n",
            ),
            python_file(
                "order_app/api.py",
                "from order_app.service import OrderService\n",
            ),
            python_file(
                "tests/test_api.py",
                "from order_app.api import create_order\n",
            ),
        ]
    )

    impact = find_affected_modules(
        graph,
        ["order_app.models"],
    )

    assert impact.changed_modules == ["order_app.models"]
    assert impact.direct_dependents == ["order_app.service"]
    assert impact.transitive_dependents == [
        "order_app.api",
        "tests.test_api",
    ]
    assert impact.affected_modules == [
        "order_app.api",
        "order_app.models",
        "order_app.service",
        "tests.test_api",
    ]
    assert impact.affected_files == [
        "order_app/api.py",
        "order_app/models.py",
        "order_app/service.py",
        "tests/test_api.py",
    ]


def test_find_affected_modules_handles_dependency_cycles() -> None:
    """循环依赖不应该导致无限遍历或重复模块。"""

    graph = build_graph(
        [
            python_file(
                "package/a.py",
                "from package.b import function_b\n",
            ),
            python_file(
                "package/b.py",
                "from package.a import function_a\n",
            ),
        ]
    )

    impact = find_affected_modules(
        graph,
        ["package.a"],
    )

    assert impact.direct_dependents == ["package.b"]
    assert impact.transitive_dependents == []
    assert impact.affected_modules == [
        "package.a",
        "package.b",
    ]


def test_graph_rejects_duplicate_module_names() -> None:
    """一个模块名对应多个文件时应该拒绝生成歧义依赖图。"""

    repository = build_repository(
        [
            python_file(
                "package.py",
                "VALUE = 1\n",
            ),
            python_file(
                "package/__init__.py",
                "VALUE = 2\n",
            ),
        ]
    )
    symbol_index = index_python_symbols(repository)

    with pytest.raises(
        ValueError,
        match="Python 符号索引包含重复模块名称",
    ):
        build_python_dependency_graph(symbol_index)


def test_graph_accepts_empty_python_index() -> None:
    """没有 Python 文件时应该返回空依赖图。"""

    graph = build_graph([])

    assert graph.modules == []
    assert graph.dependencies == []
    assert graph.internal_dependency_count == 0
    assert graph.external_dependency_count == 0
    assert graph.unresolved_internal_dependency_count == 0


def test_find_affected_modules_rejects_unknown_module() -> None:
    """影响查询应该拒绝不在当前依赖图中的模块。"""

    graph = build_graph(
        [
            python_file(
                "order_app/service.py",
                "class OrderService:\n    pass\n",
            )
        ]
    )

    with pytest.raises(
        ValueError,
        match="依赖影响查询包含未知模块：order_app.missing",
    ):
        find_affected_modules(
            graph,
            ["order_app.missing"],
        )
