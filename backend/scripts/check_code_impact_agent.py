"""使用真实 DeepSeek 检查代码影响分析智能体。"""

from pathlib import Path

from app.schemas.requirement import RequirementAnalysis
from app.services.code_impact_agent import analyze_code_impact
from app.tools.repository_scanner import scan_repository

# 当前脚本位于 backend/scripts 目录。
# parents[2] 对应整个 ChangePilot-AI 项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 指定需要被分析的示例订单仓库。
SAMPLE_REPOSITORY_PATH = PROJECT_ROOT / "sample_repos" / "order_service"


def main() -> None:
    """扫描示例仓库并执行真实代码影响分析。"""

    print("正在扫描示例订单仓库……")

    # 读取订单仓库中的真实源代码、测试和项目文档。
    repository_snapshot = scan_repository(SAMPLE_REPOSITORY_PATH)

    print(f"仓库扫描完成：{repository_snapshot.scanned_file_count} 个文件")

    # 这里模拟第一个需求分析智能体已经生成的结构化结果。
    # 当前脚本的目标是单独验证第二个智能体。
    requirement = RequirementAnalysis(
        summary=(
            "为订单系统增加取消订单功能，用户只能取消尚未支付的订单，取消成功后必须记录审计日志"
        ),
        change_type="feature",
        affected_modules=[
            "订单模型",
            "订单业务服务",
            "审计日志",
            "自动化测试",
        ],
        acceptance_criteria=[
            "待支付订单可以被取消",
            "已经支付的订单不能被取消",
            "取消成功后订单状态变为已取消",
            "取消成功后记录订单取消审计日志",
        ],
        risks=[
            "支付和取消并发执行可能导致订单状态冲突",
            "重复取消可能产生重复审计记录",
        ],
    )

    print("正在调用代码影响分析智能体……\n")

    # 把结构化需求和真实仓库快照同时交给第二个智能体。
    impact_analysis = analyze_code_impact(
        requirement=requirement,
        repository=repository_snapshot,
    )

    print("代码影响分析结果：")

    # 将 Pydantic 对象输出为便于阅读的 JSON。
    print(impact_analysis.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
