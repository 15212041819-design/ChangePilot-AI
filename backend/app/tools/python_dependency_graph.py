"""根据 Python 符号索引构建模块依赖图并查询变更影响范围。

本工具只使用 AST 索引中已经提取出的 import 信息，不执行用户代码，
也不调用大模型，因此结果稳定、可测试，并且可以作为 AI 分析的事实证据。
"""

from collections import defaultdict, deque

from app.schemas.code_index import (
    ImportReference,
    PythonFileIndex,
    RepositorySymbolIndex,
)
from app.schemas.dependency_graph import (
    DependencyImpact,
    DependencyKind,
    ModuleDependency,
    PythonModuleNode,
    RepositoryDependencyGraph,
)


def build_module_lookup(
    symbol_index: RepositorySymbolIndex,
) -> dict[str, PythonFileIndex]:
    """建立模块名称到文件索引的唯一映射。"""

    module_lookup: dict[str, PythonFileIndex] = {}

    for indexed_file in symbol_index.files:
        module_name = indexed_file.module_name

        # package.py 与 package/__init__.py 可能产生相同模块名。
        # 如果静默覆盖，后续依赖边会指向不确定文件，因此直接拒绝。
        if module_name in module_lookup:
            previous_file = module_lookup[module_name]

            raise ValueError(
                "Python 符号索引包含重复模块名称："
                f"{module_name} 同时对应 "
                f"{previous_file.file_path} 和 {indexed_file.file_path}。"
            )

        module_lookup[module_name] = indexed_file

    return module_lookup


def get_source_package_parts(
    indexed_file: PythonFileIndex,
) -> list[str]:
    """返回相对导入计算所需的当前包路径。"""

    module_parts = indexed_file.module_name.split(".")
    normalized_file_path = indexed_file.file_path.replace("\\", "/")

    # __init__.py 的 module_name 已经是包名称，
    # 因此整个模块名都属于当前包路径。
    if normalized_file_path == "__init__.py" or normalized_file_path.endswith("/__init__.py"):
        return module_parts

    # 普通 .py 文件的最后一段是模块自身名称，
    # 相对导入应该从它所在的包开始计算。
    return module_parts[:-1]


def resolve_import_base_module(
    *,
    indexed_file: PythonFileIndex,
    import_reference: ImportReference,
) -> str | None:
    """把绝对或相对导入转换成候选基础模块名。"""

    if import_reference.relative_level == 0:
        return import_reference.module or None

    package_parts = get_source_package_parts(indexed_file)

    # from .xxx 表示当前包，level=1 时不向上移。
    # from ..xxx 表示父包，level=2 时向上移一层。
    parent_steps = import_reference.relative_level - 1

    if parent_steps > len(package_parts):
        # 相对导入已经越过仓库内可推导的顶层包。
        return None

    if parent_steps:
        package_parts = package_parts[:-parent_steps]

    imported_module_parts = [part for part in import_reference.module.split(".") if part]
    resolved_parts = [
        *package_parts,
        *imported_module_parts,
    ]

    if not resolved_parts:
        return None

    return ".".join(resolved_parts)


def build_internal_candidates(
    *,
    base_module: str | None,
    imported_name: str | None,
) -> list[str]:
    """生成可能对应仓库内模块的候选名称。"""

    candidates: list[str] = []

    # from package import submodule 可能指向 package.submodule。
    # 优先检查更具体的子模块，再检查 package 本身。
    if imported_name and imported_name != "*":
        imported_candidate = f"{base_module}.{imported_name}" if base_module else imported_name
        candidates.append(imported_candidate)

    if base_module:
        candidates.append(base_module)

    # 使用 dict 保序去重。
    return list(dict.fromkeys(candidates))


def classify_unresolved_dependency(
    *,
    base_module: str | None,
    import_reference: ImportReference,
    internal_roots: set[str],
) -> DependencyKind:
    """区分外部依赖与仓库内未解析依赖。"""

    # 相对导入天然表示当前包或父包中的内容。
    # 无法解析时应标记为仓库内缺失，而不是第三方依赖。
    if import_reference.relative_level > 0:
        return "unresolved_internal"

    if base_module:
        root_module = base_module.split(".", maxsplit=1)[0]

        if root_module in internal_roots:
            return "unresolved_internal"

    return "external"


def build_dependency(
    *,
    indexed_file: PythonFileIndex,
    import_reference: ImportReference,
    module_lookup: dict[str, PythonFileIndex],
    internal_roots: set[str],
) -> ModuleDependency:
    """把一条 AST 导入记录转换成依赖图中的边。"""

    base_module = resolve_import_base_module(
        indexed_file=indexed_file,
        import_reference=import_reference,
    )
    candidates = build_internal_candidates(
        base_module=base_module,
        imported_name=import_reference.imported_name,
    )

    for candidate in candidates:
        target_file_index = module_lookup.get(candidate)

        if target_file_index is None:
            continue

        return ModuleDependency(
            source_module=indexed_file.module_name,
            source_file=indexed_file.file_path,
            target_module=candidate,
            target_file=target_file_index.file_path,
            imported_name=import_reference.imported_name,
            kind="internal",
            relative_level=import_reference.relative_level,
            line_number=import_reference.line_number,
        )

    dependency_kind = classify_unresolved_dependency(
        base_module=base_module,
        import_reference=import_reference,
        internal_roots=internal_roots,
    )

    # 无法解析的相对导入仍保留原始点号，
    # 让开发人员能够看出失败发生在哪种导入表达式上。
    if base_module:
        target_module = base_module
    else:
        target_module = (
            "." * import_reference.relative_level
            + import_reference.module
            + (f".{import_reference.imported_name}" if import_reference.imported_name else "")
        )

    return ModuleDependency(
        source_module=indexed_file.module_name,
        source_file=indexed_file.file_path,
        target_module=target_module,
        target_file=None,
        imported_name=import_reference.imported_name,
        kind=dependency_kind,
        relative_level=import_reference.relative_level,
        line_number=import_reference.line_number,
    )


def build_python_dependency_graph(
    symbol_index: RepositorySymbolIndex,
) -> RepositoryDependencyGraph:
    """根据仓库 Python 符号索引构建确定性的模块依赖图。"""

    module_lookup = build_module_lookup(symbol_index)

    modules = [
        PythonModuleNode(
            module_name=indexed_file.module_name,
            file_path=indexed_file.file_path,
            has_parse_error=indexed_file.parse_error is not None,
        )
        for indexed_file in sorted(
            symbol_index.files,
            key=lambda item: item.module_name,
        )
    ]

    internal_roots = {
        module_name.split(".", maxsplit=1)[0]
        for module_name in module_lookup
        if module_name != "__root__"
    }

    dependencies: list[ModuleDependency] = []

    for indexed_file in sorted(
        symbol_index.files,
        key=lambda item: item.module_name,
    ):
        for import_reference in indexed_file.imports:
            dependencies.append(
                build_dependency(
                    indexed_file=indexed_file,
                    import_reference=import_reference,
                    module_lookup=module_lookup,
                    internal_roots=internal_roots,
                )
            )

    internal_dependency_count = sum(dependency.kind == "internal" for dependency in dependencies)
    external_dependency_count = sum(dependency.kind == "external" for dependency in dependencies)
    unresolved_internal_dependency_count = sum(
        dependency.kind == "unresolved_internal" for dependency in dependencies
    )

    return RepositoryDependencyGraph(
        repository_name=symbol_index.repository_name,
        modules=modules,
        dependencies=dependencies,
        internal_dependency_count=internal_dependency_count,
        external_dependency_count=external_dependency_count,
        unresolved_internal_dependency_count=(unresolved_internal_dependency_count),
    )


def find_affected_modules(
    graph: RepositoryDependencyGraph,
    changed_modules: list[str],
) -> DependencyImpact:
    """反向遍历依赖图，查询模块变更可能影响的所有上游模块。"""

    known_modules = {module.module_name: module.file_path for module in graph.modules}
    normalized_changed_modules = sorted(set(changed_modules))

    unknown_modules = [
        module_name
        for module_name in normalized_changed_modules
        if module_name not in known_modules
    ]

    if unknown_modules:
        raise ValueError("依赖影响查询包含未知模块：" + "、".join(unknown_modules) + "。")

    # 依赖边方向为 source -> target。
    # 影响分析需要反向查询：target 变更后，哪些 source 会受到影响。
    reverse_dependencies: dict[str, set[str]] = defaultdict(set)

    for dependency in graph.dependencies:
        if dependency.kind != "internal":
            continue

        reverse_dependencies[dependency.target_module].add(dependency.source_module)

    changed_module_set = set(normalized_changed_modules)
    direct_dependents: set[str] = set()

    for changed_module in normalized_changed_modules:
        direct_dependents.update(reverse_dependencies[changed_module])

    # 循环依赖可能让变更模块重新出现在其依赖方集合中。
    direct_dependents.difference_update(changed_module_set)

    visited = set(changed_module_set)
    all_dependents: set[str] = set()
    pending_modules = deque(sorted(direct_dependents))

    while pending_modules:
        current_module = pending_modules.popleft()

        if current_module in visited:
            continue

        visited.add(current_module)
        all_dependents.add(current_module)

        for dependent_module in sorted(reverse_dependencies[current_module]):
            if dependent_module not in visited:
                pending_modules.append(dependent_module)

    transitive_dependents = all_dependents - direct_dependents
    affected_modules = sorted(changed_module_set | all_dependents)
    affected_files = sorted(known_modules[module_name] for module_name in affected_modules)

    return DependencyImpact(
        changed_modules=normalized_changed_modules,
        direct_dependents=sorted(direct_dependents),
        transitive_dependents=sorted(transitive_dependents),
        affected_modules=affected_modules,
        affected_files=affected_files,
    )
