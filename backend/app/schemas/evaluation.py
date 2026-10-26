"""离线评测数据集的稳定契约。

这里描述的是“改动发生之前可见的输入”和“改动提交产生的答案”，
不包含任何模型输出。后续所有检索器和分析引擎都应复用同一批样本，
避免不同实验各自挑选有利提交。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvaluationSample:
    """一条不会把答案泄漏给分析器的 Git 历史样本。"""

    sample_id: str
    repository_relative_path: str
    request_text: str
    analysis_commit: str
    answer_commit: str
    changed_files: tuple[str, ...]
    source_files: tuple[str, ...]
    test_files: tuple[str, ...]
    existing_source_files: tuple[str, ...] = ()
    new_source_files: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """在构造期阻止不完整或自相矛盾的评测样本。"""

        if not self.sample_id.strip():
            raise ValueError("sample_id 不能为空。")

        if not self.request_text.strip():
            raise ValueError("request_text 不能为空。")

        if self.analysis_commit == self.answer_commit:
            raise ValueError("analysis_commit 不能等于 answer_commit。")

        for field_name, commit in (
            ("analysis_commit", self.analysis_commit),
            ("answer_commit", self.answer_commit),
        ):
            if len(commit) != 40 or any(
                character not in "0123456789abcdef" for character in commit
            ):
                raise ValueError(f"{field_name} 必须是 40 位小写 commit SHA。")

        if not self.changed_files:
            raise ValueError("changed_files 不能为空。")

        if tuple(sorted(set(self.changed_files))) != self.changed_files:
            raise ValueError("changed_files 必须去重并稳定排序。")

        if tuple(sorted(set(self.source_files))) != self.source_files:
            raise ValueError("source_files 必须去重并稳定排序。")

        if tuple(sorted(set(self.test_files))) != self.test_files:
            raise ValueError("test_files 必须去重并稳定排序。")

        if tuple(sorted(set(self.existing_source_files))) != self.existing_source_files:
            raise ValueError("existing_source_files 必须去重并稳定排序。")

        if tuple(sorted(set(self.new_source_files))) != self.new_source_files:
            raise ValueError("new_source_files 必须去重并稳定排序。")

        # 兼容契约升级前由测试或第三方代码手工构造的样本。
        # 真实 Git 数据集必须显式传入 existing/new 分类；
        # 旧调用未分类时，保守地把 source_files 视为既有文件。
        if self.source_files and not self.existing_source_files and not self.new_source_files:
            object.__setattr__(
                self,
                "existing_source_files",
                self.source_files,
            )

        changed = set(self.changed_files)

        if set(self.source_files) | set(self.test_files) != changed:
            raise ValueError("source_files 与 test_files 必须完整划分 changed_files。")

        if set(self.source_files) & set(self.test_files):
            raise ValueError("同一路径不能同时属于 source_files 和 test_files。")

        existing = set(self.existing_source_files)
        new = set(self.new_source_files)

        if existing & new:
            raise ValueError("源码路径不能同时属于既有文件和新增文件。")

        if existing | new != set(self.source_files):
            raise ValueError(
                "existing_source_files 与 new_source_files 必须完整划分 source_files。"
            )


@dataclass(frozen=True)
class EvaluationDataset:
    """一次确定性筛选得到的数据集及可审计统计。"""

    repository_relative_path: str
    requested_ref: str
    resolved_ref: str
    scanned_commit_count: int
    samples: tuple[EvaluationSample, ...]
    rejected_root_count: int
    rejected_merge_count: int
    rejected_short_message_count: int
    rejected_file_count_count: int
    rejected_pure_test_count: int

    @property
    def accepted_sample_count(self) -> int:
        """返回最终可用于评测的样本数量。"""

        return len(self.samples)

    @property
    def rejected_commit_count(self) -> int:
        """返回所有已扫描但未被接纳的提交数量。"""

        return (
            self.rejected_root_count
            + self.rejected_merge_count
            + self.rejected_short_message_count
            + self.rejected_file_count_count
            + self.rejected_pure_test_count
        )

    def __post_init__(self) -> None:
        """校验统计量和样本顺序没有互相矛盾。"""

        if self.scanned_commit_count < 0:
            raise ValueError("scanned_commit_count 不能为负数。")

        counters = (
            self.rejected_root_count,
            self.rejected_merge_count,
            self.rejected_short_message_count,
            self.rejected_file_count_count,
            self.rejected_pure_test_count,
        )

        if any(counter < 0 for counter in counters):
            raise ValueError("拒绝统计不能为负数。")

        if self.accepted_sample_count + self.rejected_commit_count != self.scanned_commit_count:
            raise ValueError("接受数、拒绝数与扫描提交数不一致。")

        sample_ids = tuple(sample.sample_id for sample in self.samples)

        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("样本 ID 不能重复。")
