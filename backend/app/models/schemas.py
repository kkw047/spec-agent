from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


# 검토 심각도 #
# 검토 항목을 정보/주의/차단으로 구분함.
class ReviewSeverity(str, Enum):
    info = "info"
    warning = "warning"
    blocked = "blocked"


# 체크리스트 상태 #
# 오른쪽 필수항목 패널에서 완료/부족/검토/차단을 표시함.
class ChecklistStatus(str, Enum):
    complete = "complete"
    missing = "missing"
    needs_review = "needs_review"
    blocked = "blocked"


# 처리 단계 상태 #
# 왼쪽 처리 흐름에서 대기/진행/완료/주의/차단을 표시함.
class StepStatus(str, Enum):
    pending = "pending"
    running = "running"
    complete = "complete"
    warning = "warning"
    blocked = "blocked"


# 예전 폼 입력 #
# 채팅 UI 이전에 쓰던 JSON 폼 입력 구조임.
# /api/drafts가 이 구조를 받고 내부에서 채팅 메시지로 변환함.
class InventionInput(BaseModel):
    title: str = Field(default="", description="발명 명칭 또는 임시 제목")
    idea: str = Field(default="", description="발명 아이디어 설명")
    problem: str = Field(default="", description="기존 방식의 문제점")
    components: str = Field(default="", description="구성요소")
    operation: str = Field(default="", description="작동 방식")
    effects: str = Field(default="", description="기대 효과")
    drawings: str = Field(default="", description="도면 또는 도면 설명")
    experiment_data: str = Field(default="", description="실험 또는 성능 자료")
    consultation_memo: str = Field(default="", description="상담 메모")
    prior_art_keywords: str = Field(default="", description="선행기술 검색 키워드")


# 예전 폼 요청 #
# /api/drafts 전용 요청임. 현재 기본 UI는 /api/agent/message를 사용함.
class DraftRequest(BaseModel):
    case_name: str = Field(default="새 출원 준비 건")
    invention: InventionInput
    use_rag: bool = True


# 출원명세서 섹션 #
# LLM 또는 fallback 추출기가 채우는 Markdown/Word 본문 필드임.
class SpecSections(BaseModel):
    invention_title: str = ""
    technical_field: str = ""
    background_art: str = ""
    prior_art_documents: str = ""
    problem_to_solve: str = ""
    solution: str = ""
    advantageous_effects: str = ""
    embodiment: str = ""
    drawing_description: str = ""
    reference_signs: str = ""
    industrial_applicability: str = ""


# 보완 질문 #
# 부족한 필수항목을 채팅 질문으로 다시 물을 때 사용함.
class FollowUpQuestion(BaseModel):
    field: str
    question: str
    reason: str


# 검토 항목 #
# 사람 확인이 필요한 위험/주의 지점을 본문과 분리해 표시함.
class ReviewItem(BaseModel):
    severity: ReviewSeverity = ReviewSeverity.warning
    title: str
    description: str
    human_owner: str = "변리업 종사자 또는 변리사"


# 참고자료 문장 #
# RAG가 찾은 공용 참고자료/세션 자료의 근거 문장임.
class ReferenceItem(BaseModel):
    title: str
    source: str
    excerpt: str = ""


# 선행기술 후보 #
# KIPRISPlus 검색 결과를 후보로만 표시함. 최종 신규성/진보성 판단 아님.
class PriorArtCandidate(BaseModel):
    title: str = ""
    application_number: str = ""
    publication_number: str = ""
    registration_number: str = ""
    applicant: str = ""
    ipc: str = ""
    abstract: str = ""
    source_url: str = ""
    similarity_score: int = 0
    risk_level: Literal["낮음", "보통", "높음"] = "낮음"
    matched_terms: list[str] = Field(default_factory=list)
    note: str = "자동 검색 후보입니다. 최종 특허성 판단은 사람 검토가 필요합니다."


# 필수항목 체크리스트 #
# 10개 명세서 필수항목의 자료 확인 상태를 표시함.
class ChecklistItem(BaseModel):
    key: str
    label: str
    status: ChecklistStatus = ChecklistStatus.missing
    evidence: str = ""
    question: str = ""


# 업로드 자료 #
# 파일명, 종류, 추출 글자 수, 저장 경로를 프론트와 Word 부록으로 전달함.
class MaterialSource(BaseModel):
    name: str
    kind: str = "text"
    status: str = "processed"
    char_count: int = 0
    chunk_count: int = 0
    note: str = ""
    stored_path: str = ""


# 처리 단계 시각화 #
# 왼쪽 사이드바에서 자료 수신 -> DB 저장 -> RAG -> KIPRIS -> LLM -> 체크리스트 -> 산출물을 표시함.
class AgentStep(BaseModel):
    key: str
    title: str
    status: StepStatus = StepStatus.pending
    detail: str = ""
    tool: str = ""


# 채팅 메시지 #
# state.json과 프론트 말풍선이 공유하는 최소 메시지 구조임.
class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


# 초안 응답 #
# /api/drafts와 /api/agent/message가 공통으로 쓰는 산출물 중심 응답임.
class DraftResponse(BaseModel):
    case_name: str
    sections: SpecSections
    follow_up_questions: list[FollowUpQuestion]
    missing_materials: list[str]
    review_items: list[ReviewItem]
    references: list[ReferenceItem]
    prior_art_candidates: list[PriorArtCandidate] = Field(default_factory=list)
    markdown: str
    markdown_path: Optional[str] = None
    docx_path: Optional[str] = None
    blocked: bool = False
    blocked_reason: Optional[str] = None


# 채팅 Agent 응답 #
# /api/agent/message 전용 응답임. 세션, reply, 체크리스트, 처리 흐름이 추가됨.
class AgentResponse(DraftResponse):
    session_id: str
    reply: str
    checklist: list[ChecklistItem] = Field(default_factory=list)
    materials: list[MaterialSource] = Field(default_factory=list)
    steps: list[AgentStep] = Field(default_factory=list)
    messages: list[ChatMessage] = Field(default_factory=list)
