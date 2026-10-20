from app.services.requirement_agent import analyze_requirement


def main() -> None:
    request_text = """
    为系统登录接口增加账号保护功能。
    同一账号连续登录失败三次后，需要锁定账号十五分钟。
    管理员可以提前解除账号锁定。
    """

    print("正在调用需求分析智能体……")
    print(f"原始需求：{request_text.strip()}\n")

    result = analyze_requirement(request_text)

    print("结构化分析结果：")
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
