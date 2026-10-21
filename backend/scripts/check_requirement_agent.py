from app.services.requirement_agent import analyze_requirement


def main() -> None:
    # 这段需求故意没有规定审计日志字段、重复取消行为
    # 和并发处理方案，用于检查模型是否会擅自补充需求。
    request_text = """
    为订单系统增加取消订单功能。
    用户只能取消尚未支付的订单，已经支付的订单不能取消。
    取消成功后必须记录审计日志。
    """

    print("正在调用需求分析智能体……")
    print(f"原始需求：{request_text.strip()}\n")

    result = analyze_requirement(request_text)

    print("结构化分析结果：")
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
