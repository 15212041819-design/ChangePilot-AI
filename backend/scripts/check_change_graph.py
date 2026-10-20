from app.graph.change_analysis import change_analysis_graph


def main() -> None:
    request_text = """
    为订单系统增加取消订单功能。
    用户只能取消尚未支付的订单。
    订单取消后需要记录操作日志。
    """

    print("正在运行 ChangePilot AI 变更分析工作流……\n")

    result = change_analysis_graph.invoke(
        {
            "request_text": request_text,
            "normalized_request": "",
            "requirement_analysis": None,
            "status": "received",
        }
    )

    analysis = result["requirement_analysis"]

    if analysis is None:
        raise RuntimeError("工作流没有生成需求分析结果。")

    print(f"工作流状态：{result['status']}")
    print(f"规范化需求：{result['normalized_request']}\n")
    print("需求分析结果：")
    print(analysis.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
