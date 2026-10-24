"""校验代码影响分析结果是否与真实仓库快照一致。"""

from pathlib import PurePosixPath, PureWindowsPath

from app.schemas.code_impact import (
    CodeImpactAnalysis,
    ImpactedFile,
)
from app.schemas.repository import RepositorySnapshot


class CodeImpactValidationError(ValueError):
    """代码影响分析结果没有通过真实性校验。"""


def normalize_repository_path(
    path: str,
    *,
    field_name: str,
) -> str:
    """把模型返回的仓库路径转换为安全、统一的相对路径。"""

    original_path = path
    stripped_path = path.strip()

    if not stripped_path:
        raise CodeImpactValidationError(f"{field_name} 包含空文件路径。")

    # 仓库内部统一使用正斜杠。
    # 这样 Windows 和 POSIX 风格路径可以使用同一种方式比较。
    candidate_path = stripped_path.replace("\\", "/")

    # 禁止空字符，避免路径被底层文件系统截断或错误解析。
    if "\x00" in candidate_path:
        raise CodeImpactValidationError(f"{field_name} 包含非法空字符：{original_path!r}")

    # 同时检查 POSIX 和 Windows 绝对路径。
    # AI 只能返回相对于仓库根目录的路径。
    if PurePosixPath(candidate_path).is_absolute() or PureWindowsPath(stripped_path).is_absolute():
        raise CodeImpactValidationError(f"{field_name} 不能包含绝对路径：{original_path}")

    raw_parts = candidate_path.split("/")

    # 禁止使用 .. 逃离仓库根目录。
    if ".." in raw_parts:
        raise CodeImpactValidationError(f"{field_name} 包含目录穿越路径：{original_path}")

    # 冒号可能表示 Windows 盘符或 URI。
    # 仓库相对路径中不允许出现这种形式。
    if ":" in candidate_path:
        raise CodeImpactValidationError(f"{field_name} 包含不安全路径：{original_path}")

    # PurePosixPath 会移除多余的 ./ 和重复分隔符。
    normalized_path = PurePosixPath(candidate_path).as_posix()

    if normalized_path in {"", "."}:
        raise CodeImpactValidationError(f"{field_name} 包含无效文件路径：{original_path}")

    return normalized_path


def find_duplicate_paths(
    paths: list[str],
) -> list[str]:
    """返回路径列表中重复出现的项目。"""

    seen_paths: set[str] = set()
    duplicate_paths: set[str] = set()

    for path in paths:
        if path in seen_paths:
            duplicate_paths.add(path)
        else:
            seen_paths.add(path)

    return sorted(duplicate_paths)


def validate_code_impact_analysis(
    analysis: CodeImpactAnalysis,
    repository: RepositorySnapshot,
) -> CodeImpactAnalysis:
    """验证并规范化代码影响分析中的所有文件路径。"""

    # 首先规范化仓库快照中的真实路径。
    # 仓库快照是后续所有真实性判断的依据。
    existing_paths = {
        normalize_repository_path(
            repository_file.relative_path,
            field_name="repository.files",
        )
        for repository_file in repository.files
    }

    # 规范化 impacted_files 中的路径，同时保留其他分析字段。
    normalized_impacted_files: list[ImpactedFile] = [
        impacted_file.model_copy(
            update={
                "path": normalize_repository_path(
                    impacted_file.path,
                    field_name="impacted_files",
                )
            }
        )
        for impacted_file in analysis.impacted_files
    ]

    normalized_impacted_paths = [impacted_file.path for impacted_file in normalized_impacted_files]

    normalized_new_files = [
        normalize_repository_path(
            path,
            field_name="new_files",
        )
        for path in analysis.new_files
    ]

    normalized_test_files = [
        normalize_repository_path(
            path,
            field_name="test_files",
        )
        for path in analysis.test_files
    ]

    # 同一个列表中不应该重复出现相同文件。
    duplicate_impacted_paths = find_duplicate_paths(normalized_impacted_paths)

    if duplicate_impacted_paths:
        duplicate_text = ", ".join(duplicate_impacted_paths)

        raise CodeImpactValidationError(f"impacted_files 包含重复文件：{duplicate_text}")

    duplicate_new_paths = find_duplicate_paths(normalized_new_files)

    if duplicate_new_paths:
        duplicate_text = ", ".join(duplicate_new_paths)

        raise CodeImpactValidationError(f"new_files 包含重复文件：{duplicate_text}")

    duplicate_test_paths = find_duplicate_paths(normalized_test_files)

    if duplicate_test_paths:
        duplicate_text = ", ".join(duplicate_test_paths)

        raise CodeImpactValidationError(f"test_files 包含重复文件：{duplicate_text}")

    # impacted_files 表示需要修改的现有文件，
    # 因此每一个路径都必须出现在仓库扫描快照中。
    unknown_impacted_paths = sorted(set(normalized_impacted_paths) - existing_paths)

    if unknown_impacted_paths:
        unknown_text = ", ".join(unknown_impacted_paths)

        raise CodeImpactValidationError(f"impacted_files 包含仓库中不存在的文件：{unknown_text}")

    # 当前数据模型把 test_files 定义为需要修改的现有测试文件，
    # 因此它们同样必须出现在仓库快照中。
    unknown_test_paths = sorted(set(normalized_test_files) - existing_paths)

    if unknown_test_paths:
        unknown_text = ", ".join(unknown_test_paths)

        raise CodeImpactValidationError(f"test_files 包含仓库中不存在的测试文件：{unknown_text}")

    # new_files 表示尚未存在、建议新建的文件。
    # 如果文件已经存在，说明模型把现有文件错误地归类为新文件。
    conflicting_new_paths = sorted(set(normalized_new_files) & existing_paths)

    if conflicting_new_paths:
        conflict_text = ", ".join(conflicting_new_paths)

        raise CodeImpactValidationError(f"new_files 包含仓库中已经存在的文件：{conflict_text}")

    # 返回经过路径规范化的新对象，不直接修改模型原始结果。
    return analysis.model_copy(
        update={
            "impacted_files": normalized_impacted_files,
            "new_files": normalized_new_files,
            "test_files": normalized_test_files,
        }
    )
