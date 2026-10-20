import pytest

from app.graph.change_analysis import change_analysis_graph


def test_change_analysis_graph_normalizes_request() -> None:
    result = change_analysis_graph.invoke(
        {
            "request_text": "  用户连续三次   登录失败后锁定账号  ",
            "normalized_request": "",
            "status": "received",
        }
    )

    assert result["normalized_request"] == "用户连续三次 登录失败后锁定账号"
    assert result["status"] == "ready_for_requirement_analysis"


def test_change_analysis_graph_rejects_empty_request() -> None:
    with pytest.raises(ValueError, match="变更需求不能为空"):
        change_analysis_graph.invoke(
            {
                "request_text": "   ",
                "normalized_request": "",
                "status": "received",
            }
        )
