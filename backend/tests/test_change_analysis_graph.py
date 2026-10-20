import pytest

from app.graph.change_analysis import build_change_analysis_graph
from app.schemas.requirement import RequirementAnalysis


def fake_requirement_analyzer(
    request_text: str,
) -> RequirementAnalysis:
    """用于测试的假需求分析智能体。"""

    return RequirementAnalysis(
        summary=f"分析结果：{request_text}",
        change_type="feature",
        affected_modules=["authentication"],
        acceptance_criteria=["连续三次登录失败后锁定账号"],
        risks=["正常用户可能被误锁"],
    )


def test_change_analysis_graph_analyzes_request() -> None:
    graph = build_change_analysis_graph(analyzer=fake_requirement_analyzer)

    result = graph.invoke(
        {
            "request_text": "  用户连续三次   登录失败后锁定账号  ",
            "normalized_request": "",
            "requirement_analysis": None,
            "status": "received",
        }
    )

    analysis = result["requirement_analysis"]

    assert result["normalized_request"] == "用户连续三次 登录失败后锁定账号"
    assert result["status"] == "requirement_analyzed"
    assert analysis is not None
    assert analysis.change_type == "feature"
    assert analysis.affected_modules == ["authentication"]


def test_change_analysis_graph_rejects_empty_request() -> None:
    graph = build_change_analysis_graph(analyzer=fake_requirement_analyzer)

    with pytest.raises(ValueError, match="变更需求不能为空"):
        graph.invoke(
            {
                "request_text": "   ",
                "normalized_request": "",
                "requirement_analysis": None,
                "status": "received",
            }
        )
