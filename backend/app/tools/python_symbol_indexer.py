"""使用 Python AST 为仓库中的 Python 文件建立符号索引。"""

import ast
from pathlib import PurePosixPath
from typing import Literal

from app.schemas.code_index import (
    CodeSymbol,
    ImportReference,
    PythonFileIndex,
    RepositorySymbolIndex,
    SymbolKind,
)
from app.schemas.repository import RepositorySnapshot

ScopeKind = Literal["class", "function"]


def build_module_name(
    file_path: str,
) -> str:
    """根据仓库相对路径生成 Python 模块名称。"""

    normalized_path = file_path.replace("\\", "/")
    path = PurePosixPath(normalized_path)

    # 移除 .py 扩展名。
    module_parts = list(path.with_suffix("").parts)

    # 包目录中的 __init__.py 对应包本身，
    # 不需要在模块名末尾保留 __init__。
    if module_parts and module_parts[-1] == "__init__":
        module_parts.pop()

    # 仓库根目录中的 __init__.py 没有明确包名，
    # 使用 __root__ 作为稳定的索引标识。
    if not module_parts:
        return "__root__"

    return ".".join(module_parts)


def build_function_signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str:
    """根据函数 AST 节点生成容易阅读的声明签名。"""

    arguments = ast.unparse(node.args)

    return_annotation = ""

    if node.returns is not None:
        return_annotation = f" -> {ast.unparse(node.returns)}"

    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"

    return f"{prefix} {node.name}({arguments}){return_annotation}"


def build_class_signature(
    node: ast.ClassDef,
) -> str:
    """根据类 AST 节点生成容易阅读的声明签名。"""

    base_expressions = [ast.unparse(base) for base in node.bases]

    keyword_expressions = [
        (
            f"{keyword.arg}={ast.unparse(keyword.value)}"
            if keyword.arg is not None
            else f"**{ast.unparse(keyword.value)}"
        )
        for keyword in node.keywords
    ]

    class_arguments = base_expressions + keyword_expressions

    if not class_arguments:
        return f"class {node.name}"

    return f"class {node.name}({', '.join(class_arguments)})"


class PythonSymbolVisitor(ast.NodeVisitor):
    """访问 Python AST 并收集符号和导入关系。"""

    def __init__(
        self,
        *,
        file_path: str,
        module_name: str,
    ) -> None:
        """初始化当前文件的索引上下文。"""

        self.file_path = file_path
        self.module_name = module_name

        self.symbols: list[CodeSymbol] = []
        self.imports: list[ImportReference] = []

        # 保存当前所在的类和函数作用域。
        # 例如 OrderService.cancel_order。
        self.scope_stack: list[tuple[str, ScopeKind]] = []

    def build_qualified_name(
        self,
        symbol_name: str,
    ) -> str:
        """生成包含模块和父级作用域的完整符号名称。"""

        scope_names = [scope_name for scope_name, _ in self.scope_stack]

        return ".".join(
            [
                self.module_name,
                *scope_names,
                symbol_name,
            ]
        )

    def visit_ClassDef(
        self,
        node: ast.ClassDef,
    ) -> None:
        """记录类定义，并继续分析类中的方法。"""

        self.symbols.append(
            CodeSymbol(
                name=node.name,
                qualified_name=(self.build_qualified_name(node.name)),
                kind="class",
                file_path=self.file_path,
                line_start=node.lineno,
                line_end=(node.end_lineno or node.lineno),
                signature=build_class_signature(node),
                docstring=ast.get_docstring(
                    node,
                    clean=True,
                ),
            )
        )

        self.scope_stack.append((node.name, "class"))

        self.generic_visit(node)

        self.scope_stack.pop()

    def visit_FunctionDef(
        self,
        node: ast.FunctionDef,
    ) -> None:
        """记录普通函数或同步方法。"""

        self.visit_function(
            node,
            is_async=False,
        )

    def visit_AsyncFunctionDef(
        self,
        node: ast.AsyncFunctionDef,
    ) -> None:
        """记录异步函数或异步方法。"""

        self.visit_function(
            node,
            is_async=True,
        )

    def visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        is_async: bool,
    ) -> None:
        """记录函数节点并继续分析嵌套作用域。"""

        parent_is_class = bool(self.scope_stack and self.scope_stack[-1][1] == "class")

        if parent_is_class:
            symbol_kind: SymbolKind = "async_method" if is_async else "method"
        else:
            symbol_kind = "async_function" if is_async else "function"

        self.symbols.append(
            CodeSymbol(
                name=node.name,
                qualified_name=(self.build_qualified_name(node.name)),
                kind=symbol_kind,
                file_path=self.file_path,
                line_start=node.lineno,
                line_end=(node.end_lineno or node.lineno),
                signature=build_function_signature(node),
                docstring=ast.get_docstring(
                    node,
                    clean=True,
                ),
            )
        )

        self.scope_stack.append((node.name, "function"))

        self.generic_visit(node)

        self.scope_stack.pop()

    def visit_Import(
        self,
        node: ast.Import,
    ) -> None:
        """记录 import module 形式的导入。"""

        for imported_module in node.names:
            self.imports.append(
                ImportReference(
                    module=imported_module.name,
                    imported_name=None,
                    alias=imported_module.asname,
                    relative_level=0,
                    line_number=node.lineno,
                )
            )

        self.generic_visit(node)

    def visit_ImportFrom(
        self,
        node: ast.ImportFrom,
    ) -> None:
        """记录 from module import name 形式的导入。"""

        module_name = node.module or ""

        for imported_name in node.names:
            self.imports.append(
                ImportReference(
                    module=module_name,
                    imported_name=(imported_name.name),
                    alias=imported_name.asname,
                    relative_level=node.level,
                    line_number=node.lineno,
                )
            )

        self.generic_visit(node)


def index_python_file(
    *,
    file_path: str,
    content: str,
) -> PythonFileIndex:
    """为单个 Python 文件建立符号索引。"""

    normalized_file_path = file_path.replace(
        "\\",
        "/",
    )
    module_name = build_module_name(normalized_file_path)

    try:
        syntax_tree = ast.parse(
            content,
            filename=normalized_file_path,
        )
    except SyntaxError as exc:
        line_number = str(exc.lineno) if exc.lineno is not None else "未知"

        return PythonFileIndex(
            file_path=normalized_file_path,
            module_name=module_name,
            symbols=[],
            imports=[],
            parse_error=(f"{exc.msg}，第 {line_number} 行"),
        )

    visitor = PythonSymbolVisitor(
        file_path=normalized_file_path,
        module_name=module_name,
    )
    visitor.visit(syntax_tree)

    return PythonFileIndex(
        file_path=normalized_file_path,
        module_name=module_name,
        symbols=visitor.symbols,
        imports=visitor.imports,
        parse_error=None,
    )


def index_python_symbols(
    repository: RepositorySnapshot,
) -> RepositorySymbolIndex:
    """为仓库快照中的所有 Python 文件建立符号索引。"""

    indexed_files: list[PythonFileIndex] = []

    for repository_file in repository.files:
        # 当前工具只处理 Python。
        # JavaScript、TypeScript 等语言后续使用对应解析器扩展。
        if repository_file.language != "python":
            continue

        indexed_files.append(
            index_python_file(
                file_path=(repository_file.relative_path),
                content=repository_file.content,
            )
        )

    symbol_count = sum(len(indexed_file.symbols) for indexed_file in indexed_files)

    import_count = sum(len(indexed_file.imports) for indexed_file in indexed_files)

    parse_error_count = sum(indexed_file.parse_error is not None for indexed_file in indexed_files)

    return RepositorySymbolIndex(
        repository_name=repository.repository_name,
        files=indexed_files,
        symbol_count=symbol_count,
        import_count=import_count,
        parse_error_count=parse_error_count,
    )
