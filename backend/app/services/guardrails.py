from typing import Literal

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.models.schemas import ReviewItem, ReviewSeverity


GuardrailRoute = Literal[
    "ALLOW_DRAFT",
    "ALLOW_IDEATION_ONLY",
    "ALLOW_KIPRIS_SEARCH",
    "BLOCK_FABRICATION",
    "BLOCK_PLAGIARISM",
    "BLOCK_FINAL_LEGAL_JUDGMENT",
    "BLOCK_UNSUPPORTED_SEARCH",
    "BLOCK_NON_PATENT",
]


# LLM Guardrail 분류 결과
# 키워드 guardrail 이후 요청 의도를 구조화해 LangGraph 라우팅에 사용함.
class GuardrailDecision(BaseModel):
    route: GuardrailRoute = Field(default="ALLOW_DRAFT")
    reason: str = Field(default="")
    safe_guidance: str = Field(default="")


#  특허 문맥 판단
# 특허 관련 요청인지 확인해 일반 글쓰기/문제풀이와 구분합니다.
PATENT_CONTEXT_TERMS = [
    "특허",
    "출원",
    "명세서",
    "청구항",
    "발명",
    "고안",
    "선행기술",
    "도면",
    "구성요소",
    "실시예",
]

#  허위자료 생성 차단 문구
# 가짜 실험, 없는 데이터, 근거 없는 내용을 만들라는 요청 제외
FALSE_CONTENT_PATTERNS = [
    "가짜",
    "허위",
    "거짓",
    "지어내",
    "없는거 만들어",
    "없는 거 만들어",
    "없는 실험",
    "실험이 없어도",
    "데이터를 만들어",
    "근거를 만들어",
    "임의로 수치",
    "대충 꾸며",
    "없는 내용을 넣어",
    "없는 내용",
    "꾸며",
]

# 표절/권리회피성 요청 차단
# 선행기술 비교는 허용하지만, 타인의 발명을 가져와 조금 바꾸라는 요청은 차단함.
PLAGIARISM_SOURCE_TERMS = [
    "다른사람",
    "다른 사람",
    "남의",
    "타인",
    "남이 만든",
    "기존 특허",
    "다른 특허",
]

PLAGIARISM_ACTION_TERMS = [
    "가져오",
    "베껴",
    "복사",
    "표절",
    "조금 바꿔",
    "조금바꿔",
    "살짝 바꿔",
    "살짝바꿔",
    "약간 바꿔",
    "약간바꿔",
    "비슷하게",
    "피해서",
    "회피",
]

# 전문가 영역 차단 문구
# Human-in-the-loop 구조중 인간이 판단을 내리는 기능들
FINAL_JUDGMENT_PATTERNS = [
    "특허성 판단",
    "특허 가능성 확정",
    "등록 가능성 확정",
    "청구항 확정",
    "청구범위 확정",
    "권리범위 판단",
    "자동 출원",
    "대신 출원",
    "출원까지 해",
    "출원 대신 해줘",
]

# 특허 외 작업 차단
# 특허 초안과 무관한 글쓰기/교정 요청
NON_PATENT_WRITING_PATTERNS = [
    "소설 써",
    "시 써",
    "자소서",
    "자기소개서",
    "블로그 글",
    "광고 문구",
    "마케팅 문구",
    "맞춤법 검사",
    "문장 교정만",
    "번역만",
]

# 무관한 문제풀이 차단 문구
#  수학/과학/코딩 문제풀이를 특허 Agent가 수행하지 않게
COMPLEX_PROBLEM_PATTERNS = [
    "수학 문제",
    "미분방정식",
    "적분 문제",
    "물리 문제",
    "화학 문제",
    "알고리즘 문제",
    "코딩테스트",
    "정답만 알려",
    "이 문제 텍스트화",
]

#  근거 없는 위임 표현
# "네가 정해", "알아서 채워"처럼 빈 정보를 Agent에게 맡기는 요청을 찾습니다.
FABRICATION_REQUEST_TERMS = [
    "임의로 정해",
    "알아서 정해",
    "알아서 채워",
    "너가 정해",
    "네가 정해",
    "니가 정해",
    "대충 정해",
    "적당히 정해",
    "만들어줘",
    "만들어 줘",
    "너가 제작",
    "임의로 제작",
]

#  필수항목 억지 완료 차단 조합
# "몰라 + 필수항목 + 다 채워" 조합을 잡아 체크리스트 허위 완료 방지
UNCERTAIN_DELEGATION_TERMS = [
    "몰라",
    "모르겠",
    "아무거나",
    "알아서",
    "대충",
    "임의로",
    "너가",
    "네가",
    "니가",
]

REQUIRED_FILL_TERMS = [
    "필수항목",
    "체크리스트",
    "누락항목",
    "부족항목",
    "빈칸",
]

FILL_ALL_TERMS = [
    "다채워",
    "다 채워",
    "다채줘",
    "다 채줘",
    "전부채워",
    "전부 채워",
    "모두채워",
    "모두 채워",
    "채줘",
    "채 줘",
    "채줘봐",
    "채줘바",
    "채워바",
    "채워봐",
    "채워줘",
    "완료로",
    "통과시켜",
]


# 수치, 실험, 문헌번호처럼 근거 없이는 만들면 안 되는 항목입니다.
SENSITIVE_MISSING_CONTENT_TERMS = [
    "임계값",
    "수치",
    "실험",
    "성능",
    "효과",
    "문헌번호",
    "공개번호",
    "등록번호",
    "선행문헌",
]

# 도면 창작 차단
# 도면이 없는데 만들어 달라는 요청 방지
MISSING_DRAWING_PATTERNS = [
    "도면은 없는데",
    "도면이 없는데",
    "도면 없어",
    "도면은 없어",
    "도면은 아직 제작중",
    "도면이 아직 제작중",
    "도면 제작중",
    "도안은 없는데",
    "도안이 없는데",
    "도안은 아직 제작중",
    "도안이 아직 제작중",
    "도안 제작중",
    "그리는중",
    "그리는 중",
    "도면 그리는중",
    "도면 그리는 중",
    "도안 그리는중",
    "도안 그리는 중",
    "너가 그려",
    "있다 치고 너가 그려",

]

# KIPRIS 선행기술 검색 허용 문구
# API 키가 설정된 경우 차단하지 않고 실제 KIPRISPlus 검색 도구로 넘김.
KIPRIS_RESEARCH_PATTERNS = [
    "유사한 발명",
    "비슷한 특허",
    "유사 특허",
    "국내 특허",
    "선행기술 찾아",
    "선행기술 검색",
    "선행문헌 찾아",
    "선행문헌 검색",
    "kipris 찾아",
    "키프리스 찾아",
    "공개번호 찾아",
    "등록번호 찾아",
]

# 미지원 외부검색 차단
# KIPRISPlus 외의 논문/일반 웹/구글 검색 요청은 검색한 척하지 않도록 차단함.
UNSUPPORTED_RESEARCH_PATTERNS = [
    "논문 찾아",
    "논문을 찾아",
    "논문 검색",
    "인터넷 검색",
    "웹 검색",
    "구글링",
    "실시간 검색",
    "너가 찾은",
    "네가 찾은",
    "니가 찾은",
]

#  근거 없는 해석 차단
# 사용자가 제공하지 않은 기술 내용을 Agent가 알아서 확정하지 않게
UNSUPPORTED_INTERPRETATION_PATTERNS = [
    "너가 알아서 해석",
    "네가 알아서 해석",
    "니가 알아서 해석",
    "너가 잘알",
    "네가 잘알",
    "니가 잘알",
    "너가 판단",
    "네가 판단",
    "당연한 소리",
    "당연한거",
]

# 추천값을 사실처럼 확정하는 요청 차단
RECOMMENDATION_AS_FACT_PATTERNS = [
    "너가 추천",
    "네가 추천",
    "니가 추천",
    "추천해줘",
    "추천해 줘",
]

TECHNICAL_FACT_TERMS = [
    "재질",
    "성분",
    "구조",
    "두께",
    "비율",
    "온도",
    "시간",
    "압력",
    "향",
    "향미",
    "층",
]


# 문자열 목록 중 포함되는 첫 패턴 반환함.
def _has_any(text: str, patterns: list[str]) -> str | None:
    normalized = text.replace(" ", "").lower()
    for pattern in patterns:
        if pattern.replace(" ", "").lower() in normalized:
            return pattern
    return None


# 특허 관련 단어가 있는 요청인지 판단함.
def _has_patent_context(text: str) -> bool:
    normalized = text.replace(" ", "").lower()
    return any(term.replace(" ", "").lower() in normalized for term in PATENT_CONTEXT_TERMS)


# 허위 생성 요청 감지
# 단일 문구뿐 아니라 "필수항목 + 다 채워 + 몰라" 같은 조합 조건 확인
def _detect_fabrication_request(text: str) -> str | None:
    normalized = text.replace(" ", "").lower()
    has_required_item = any(
        term.replace(" ", "").lower() in normalized for term in REQUIRED_FILL_TERMS
    )
    has_fill_all = any(term.replace(" ", "").lower() in normalized for term in FILL_ALL_TERMS)
    has_uncertain_delegation = any(
        term.replace(" ", "").lower() in normalized for term in UNCERTAIN_DELEGATION_TERMS
    )
    if has_required_item and has_fill_all and has_uncertain_delegation:
        return "부족한 필수항목 또는 체크리스트를 사용자 근거 없이 대신 채워 달라는 요청"

    has_fabrication_verb = any(
        term.replace(" ", "").lower() in normalized for term in FABRICATION_REQUEST_TERMS
    )
    if has_fabrication_verb and any(
        term.replace(" ", "").lower() in normalized for term in SENSITIVE_MISSING_CONTENT_TERMS
    ):
        return "근거가 필요한 수치, 실험자료, 효과 또는 선행문헌 정보를 임의로 정해 달라는 요청"

    has_missing_drawing = any(
        pattern.replace(" ", "").lower() in normalized for pattern in MISSING_DRAWING_PATTERNS
    )
    if has_missing_drawing and any(term in normalized for term in ["만들어", "그려", "작성해", "제작", "도와"]):
        return "사용자 자료에 없는 도면 또는 도안을 대신 만들어 달라는 요청"
    return None


# 표절/권리회피성 요청 감지
# "다른 사람 것 가져오고 조금 바꿔서" 같은 표현을 차단함.
def _detect_plagiarism_request(text: str) -> str | None:
    normalized = text.replace(" ", "").lower()
    has_source = any(term.replace(" ", "").lower() in normalized for term in PLAGIARISM_SOURCE_TERMS)
    has_action = any(term.replace(" ", "").lower() in normalized for term in PLAGIARISM_ACTION_TERMS)
    if has_source and has_action:
        return "타인의 발명 또는 선행기술을 가져와 일부만 바꾸어 초안을 만들라는 요청"
    if "표절" in normalized:
        return "표절 또는 표절에 가까운 방식의 초안 작성 요청"
    return None


# 외부 조사 요청 감지
# KIPRIS는 설정된 경우 허용하고, 논문/일반 웹 검색은 차단함.
def _detect_unsupported_research_request(text: str, allow_kipris_research: bool = False) -> str | None:
    if allow_kipris_research and _has_any(text, KIPRIS_RESEARCH_PATTERNS):
        return None
    if pattern := _has_any(text, UNSUPPORTED_RESEARCH_PATTERNS):
        return f"현재 앱은 논문 또는 일반 인터넷 검색을 수행하지 않습니다: {pattern}"
    return None


#  근거 없는 해석 요청 감지
# "너가 알아서 해석" 같은 표현 방지
def _detect_unsupported_interpretation_request(text: str) -> str | None:
    normalized = text.replace(" ", "").lower()
    if pattern := _has_any(text, UNSUPPORTED_INTERPRETATION_PATTERNS):
        return f"사용자 근거 없이 기술 내용을 해석하거나 확정해 달라는 요청입니다: {pattern}"
    has_recommendation = any(
        pattern.replace(" ", "").lower() in normalized for pattern in RECOMMENDATION_AS_FACT_PATTERNS
    )
    if has_recommendation and any(
        term.replace(" ", "").lower() in normalized for term in TECHNICAL_FACT_TERMS
    ):
        return "재질, 성분, 구조, 공정조건 같은 기술 내용을 Agent 추천값으로 확정해 달라는 요청입니다"
    return None


# Guardrail 최종 판정
# run_agent_turn()의 가장 앞에서 호출되며, 문자열을 반환하면 요청이 blocked
def detect_blocked_request(text: str, allow_kipris_research: bool = False) -> str | None:
    if reason := _detect_plagiarism_request(text):
        return (
            "표절 또는 부정한 권리화로 이어질 수 있는 요청입니다. "
            f"{reason}. 대신 KIPRISPlus 후보를 비교해 차이점과 보완 포인트를 검토하는 방식으로만 도와드릴 수 있습니다."
        )
    if reason := _detect_unsupported_research_request(text, allow_kipris_research):
        return f"지원하지 않는 외부 조사 요청입니다. {reason}. 직접 확인한 문헌이나 검색 결과를 올려 주면 반영할 수 있습니다."
    if reason := _detect_unsupported_interpretation_request(text):
        return f"근거 없는 해석 또는 확정 요청입니다. {reason}. 후보 제시는 검토 항목으로만 가능하며, 본문 확정값으로 넣을 수 없습니다."
    if reason := _detect_fabrication_request(text):
        return f"허위 또는 근거 없는 내용을 생성해 달라는 요청입니다: {reason}"
    if pattern := _has_any(text, FALSE_CONTENT_PATTERNS):
        return f"허위 또는 근거 없는 내용을 생성해 달라는 요청입니다: {pattern}"
    if pattern := _has_any(text, FINAL_JUDGMENT_PATTERNS):
        return f"전문가 최종 판단 또는 출원 대행 범위의 요청입니다: {pattern}"
    if (pattern := _has_any(text, NON_PATENT_WRITING_PATTERNS)) and not _has_patent_context(text):
        return f"특허 초안 작성과 무관한 글쓰기 또는 교정 요청입니다: {pattern}"
    if (pattern := _has_any(text, COMPLEX_PROBLEM_PATTERNS)) and not _has_patent_context(text):
        return f"특허 초안 작성과 무관한 복잡한 문제풀이 요청입니다: {pattern}"
    return None


# LLM Guardrail Classifier
# 단순 금칙어가 아니라 요청 의도를 분류해 차단/구체화/KIPRIS/초안 흐름으로 보냄.
def classify_request_with_llm(text: str, api_key: str | None, model: str) -> GuardrailDecision | None:
    if not api_key or not text.strip():
        return None

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """당신은 한국 특허 출원명세서 보조 Agent의 Guardrail Classifier입니다.

분류값:
- ALLOW_DRAFT: 사용자가 제공한 자료를 바탕으로 초안 작성 가능
- ALLOW_IDEATION_ONLY: 자료가 부족하지만 안전한 후보/선택지 제안은 가능. 본문 확정값 생성은 금지
- ALLOW_KIPRIS_SEARCH: KIPRISPlus 선행기술 후보 검색 요청
- BLOCK_FABRICATION: 없는 실험, 수치, 도면, 문헌번호, 구체 구성을 지어내라는 요청
- BLOCK_PLAGIARISM: 타인의 발명/특허를 가져와 조금 바꾸거나 표절/권리회피하려는 요청
- BLOCK_FINAL_LEGAL_JUDGMENT: 특허성, 등록 가능성, 청구범위, 출원 여부의 최종 판단 요청
- BLOCK_UNSUPPORTED_SEARCH: 논문, 일반 웹, 구글 등 현재 도구가 지원하지 않는 외부 검색 요청
- BLOCK_NON_PATENT: 특허 초안과 무관한 글쓰기/문제풀이 요청

중요 기준:
- "아이디어를 구체화해줘", "후보를 제안해줘"는 ALLOW_IDEATION_ONLY입니다.
- "지어내줘", "아무거나 넣어", "다른 사람 것 가져와 조금 바꿔"는 차단입니다.
- KIPRIS 선행기술 후보 검색은 허용하되 최종 판단은 금지입니다.
- 답변은 한국어로 짧게 작성합니다.
""",
            ),
            ("human", "사용자 요청:\n{text}"),
        ]
    )
    try:
        llm = ChatOpenAI(model=model, temperature=0, api_key=api_key).with_structured_output(GuardrailDecision)
        return (prompt | llm).invoke({"text": text[:3000]})
    except Exception:
        return None


# LLM 분류 결과를 차단 사유로 변환
# ALLOW 계열은 None을 반환하고 BLOCK 계열만 차단 문구를 만듦.
def blocked_reason_from_decision(decision: GuardrailDecision | None) -> str | None:
    if not decision or not decision.route.startswith("BLOCK"):
        return None
    labels = {
        "BLOCK_FABRICATION": "허위 또는 근거 없는 내용 생성 요청",
        "BLOCK_PLAGIARISM": "표절 또는 부정한 권리화 위험 요청",
        "BLOCK_FINAL_LEGAL_JUDGMENT": "전문가 최종 판단 범위 요청",
        "BLOCK_UNSUPPORTED_SEARCH": "지원하지 않는 외부 검색 요청",
        "BLOCK_NON_PATENT": "특허 초안 작성과 무관한 요청",
    }
    label = labels.get(decision.route, "수행 범위 밖 요청")
    detail = decision.reason or decision.safe_guidance
    return f"{label}입니다. {detail}".strip()


# 기본 검토 항목
# 모든 초안에 공통으로 붙는 책임 범위/근거 없는 내용 금지 안내입니다.
def default_boundary_items() -> list[ReviewItem]:
    return [
        ReviewItem(
            severity=ReviewSeverity.info,
            title="책임 범위",
            description=(
                "SPEC Agent는 출원명세서 검토용 초안 작성을 보조합니다. "
                "특허성 판단, 청구범위 확정, 자동 출원은 사람의 검토가 필요합니다."
            ),
            human_owner="변리사",
        ),
        ReviewItem(
            severity=ReviewSeverity.warning,
            title="근거 없는 내용 금지",
            description=(
                "사용자 자료에 없는 기술 구성, 실험 결과, 정량 효과는 본문에 단정적으로 넣지 않고 "
                "추가 질문 또는 검토 항목으로 분리합니다."
            ),
            human_owner="변리업 종사자",
        ),
    ]
