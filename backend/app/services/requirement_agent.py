"""把用户的软件变更描述转换为可追踪的结构化需求。"""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.schemas.requirement import RequirementAnalysis
from app.services.llm import get_chat_model

SYSTEM_PROMPT = """
你是 ChangePilot AI 的需求分析智能体。

你的任务是把用户输入的软件变更需求转换成结构化结果，同时严格区分
用户明确提出的要求与智能体自行推断的内容。

必须遵守以下规则：

1. summary 只能概括用户明确提出的目标。
2. acceptance_criteria 只能包含用户原始文本直接支持的验收条件。
3. 不得擅自增加用户没有说明的数据字段、时间、权限、接口形式、
   技术方案、异常提示、并发策略或重复操作规则。
4. 如果某个未说明的信息会影响实现，应把它写入 assumptions，
   不能写入 acceptance_criteria。
5. 每个需要用户决定的重要 assumption，都应该在
   clarification_questions 中生成对应问题。
6. risks 只描述可能发生的问题，不代表用户已经要求解决这些问题。
7. affected_modules 只描述可能相关的业务范围，不能虚构具体代码文件。
8. 如果不存在需要确认的假设，assumptions 和
   clarification_questions 返回空列表。
9. 不要在任何字段中静默补全用户没有表达的需求。

请严格按照下面的格式返回，不要返回额外说明：

{format_instructions}
""".strip()


def analyze_requirement(
    request_text: str,
    model: BaseChatModel | None = None,
) -> RequirementAnalysis:
    """分析软件变更需求并返回可追踪的结构化结果。"""

    # 将连续空格和换行压缩成单个空格。
    normalized_request = " ".join(request_text.split())

    if not normalized_request:
        raise ValueError("变更需求不能为空。")

    # 解析器负责把模型 JSON 转换为 RequirementAnalysis。
    parser = PydanticOutputParser(pydantic_object=RequirementAnalysis)

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            (
                "human",
                "请分析以下软件变更需求：\n\n{request_text}",
            ),
        ]
    )

    # 测试时使用传入的假模型。
    # 正常运行时使用配置中的 DeepSeek 模型。
    selected_model = model or get_chat_model()

    chain = prompt | selected_model | parser

    return chain.invoke(
        {
            "request_text": normalized_request,
            "format_instructions": (parser.get_format_instructions()),
        }
    )
