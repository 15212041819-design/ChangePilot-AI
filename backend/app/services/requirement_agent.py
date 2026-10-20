from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.schemas.requirement import RequirementAnalysis
from app.services.llm import get_chat_model

SYSTEM_PROMPT = """
你是 ChangePilot AI 的需求分析智能体。

你的任务是把用户输入的软件变更需求转换成结构化分析结果。

分析要求：
1. summary 必须准确概括需求，不要添加用户没有提出的功能。
2. change_type 必须从规定的类型中选择。
3. affected_modules 填写可能受到影响的软件模块。
4. acceptance_criteria 必须具体、可测试。
5. risks 只描述与本次变更直接相关的风险。

请严格按照下面的格式返回结果，不要返回额外说明：

{format_instructions}
""".strip()


def analyze_requirement(
    request_text: str,
    model: BaseChatModel | None = None,
) -> RequirementAnalysis:
    """分析软件变更需求并返回结构化结果。"""

    normalized_request = " ".join(request_text.split())

    if not normalized_request:
        raise ValueError("变更需求不能为空。")

    parser = PydanticOutputParser(pydantic_object=RequirementAnalysis)

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            ("human", "请分析以下软件变更需求：\n\n{request_text}"),
        ]
    )

    chain = prompt | (model or get_chat_model()) | parser

    return chain.invoke(
        {
            "request_text": normalized_request,
            "format_instructions": parser.get_format_instructions(),
        }
    )
