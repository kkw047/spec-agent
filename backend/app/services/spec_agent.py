from dataclasses import dataclass
from functools import lru_cache
import json
import re
from typing import Any, Literal, TypedDict

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.models.schemas import (
    AgentResponse,
    AgentStep,
    ChatMessage,
    ChecklistItem,
    ChecklistStatus,
    DraftRequest,
    DraftResponse,
    FollowUpQuestion,
    MaterialSource,
    PriorArtCandidate,
    ReferenceItem,
    ReviewItem,
    ReviewSeverity,
    SpecSections,
    StepStatus,
)
from app.services.exporter import export_draft
from app.services.guardrails import (
    blocked_reason_from_decision,
    classify_request_with_llm,
    default_boundary_items,
    detect_blocked_request,
)
from app.services.kipris import (
    build_kipris_query,
    candidates_to_references,
    kipris_is_configured,
    search_kipris,
)
from app.services.markdown import build_markdown
from app.services.materials import extract_upload, message_to_documents
from app.services.rag import (
    add_documents_to_collection,
    case_collection_name,
    get_case_references,
    get_references,
)
from app.services.session_store import (
    append_assistant_turn,
    append_user_turn,
    load_state,
    normalize_session_id,
    save_state,
    upload_dir,
)


@dataclass
class IncomingUpload:
    # 입력 파일 #
    # FastAPI UploadFile을 Agent 내부에서 쓰기 쉬운 bytes 형태로 바꾼 값입니다.
    filename: str
    content: bytes


# LLM 구조화 출력 #
# OpenAI가 reply, 명세서 섹션, 추가 질문, 누락 자료, 검토 항목으로 나누어 반환합니다.
class AgentStructuredOutput(BaseModel):
    reply: str = Field(default="")
    case_name: str = Field(default="새 출원 준비 건")
    sections: SpecSections = Field(default_factory=SpecSections)
    follow_up_questions: list[FollowUpQuestion] = Field(default_factory=list)
    missing_materials: list[str] = Field(default_factory=list)
    review_items: list[ReviewItem] = Field(default_factory=list)


# AI 체크리스트 항목 #
# 규칙 기반 체크리스트를 Review Agent가 다시 검토한 결과임.
class ChecklistAuditItem(BaseModel):
    key: str = Field(default="")
    status: Literal["complete", "missing", "needs_review"] = "needs_review"
    evidence: str = Field(default="")
    question: str = Field(default="")
    reason: str = Field(default="")


# AI 체크리스트 결과 #
# 10개 필수항목을 한 번의 LLM 호출로 엄격 재판정함.
class ChecklistAuditOutput(BaseModel):
    items: list[ChecklistAuditItem] = Field(default_factory=list)


# 안전한 아이디어 구체화 후보 묶음 #
# 자료가 부족할 때 LLM이 본문 확정 대신 선택지를 만들기 위한 구조임.
class SafeIdeationGroup(BaseModel):
    label: str = Field(default="")
    options: list[str] = Field(default_factory=list)
    reason: str = Field(default="")


# 안전한 아이디어 구체화 결과 #
# 낯선 발명 주제도 하드코딩된 키워드가 아니라 LLM이 후보/질문을 만들게 함.
class SafeIdeationOutput(BaseModel):
    invention_summary: str = Field(default="")
    option_groups: list[SafeIdeationGroup] = Field(default_factory=list)
    questions: list[FollowUpQuestion] = Field(default_factory=list)


# 필수항목 체크리스트 #
# 오른쪽 패널의 10개 필수 항목이며, 선행기술은 보조 검토 항목으로 별도 표시합니다.
REQUIRED_ITEMS = [
    ("title", "발명 명칭", "invention_title", "발명의 핵심을 드러내는 명칭을 알려 주세요."),
    ("field", "기술분야", "technical_field", "어떤 기술분야의 발명인지 알려 주세요."),
    ("background", "배경기술/종래 문제", "background_art", "기존 방식의 문제점이나 불편을 알려 주세요."),
    ("problem", "해결하려는 과제", "problem_to_solve", "이 발명이 해결하려는 과제를 알려 주세요."),
    ("solution", "구성요소와 해결수단", "solution", "핵심 구성요소와 서로 연결되는 방식을 알려 주세요."),
    ("operation", "작동 방식/실시예", "embodiment", "실제로 어떻게 작동하거나 실시되는지 순서대로 알려 주세요."),
    ("effects", "효과와 근거", "advantageous_effects", "기대 효과와 그 근거를 알려 주세요. 수치가 없으면 정성 효과로 표시합니다."),
    ("drawings", "도면 설명", "drawing_description", "도면이 있다면 각 도면이 무엇을 보여주는지 알려 주세요."),
    ("reference_signs", "부호의 설명", "reference_signs", "도면부호와 각 부호가 가리키는 구성요소를 알려 주세요."),
    ("industry", "산업상 이용가능성", "industrial_applicability", "사용 또는 생산 가능한 분야를 알려 주세요."),
]

WEAK_VALUES = {"", "아", "없음", "모름", "작성 필요", "필요 시 작성", "확인 필요", "자료 확인 필요"}

QUANTIFIED_EFFECT_PATTERNS = [
    re.compile(r"\d+\s*명(?:\s*의\s*[^중\s]{0,12})?\s*중\s*\d+\s*명"),
    re.compile(r"\d+\s*명의\s*[^중]{0,12}중\s*\d+\s*명"),
    re.compile(r"\d+\s*/\s*\d+"),
    re.compile(r"\d+\s*%"),
]

EFFECT_CLAIM_TERMS = [
    "효과",
    "성능",
    "실험",
    "테스트",
    "관찰",
    "검증",
    "개선",
    "감소",
    "증가",
    "수면",
    "잠",
    "재웠",
    "잘자",
    "잘 자",
    "안전",
]


# 예전 폼 요청 연결 #
# /api/drafts JSON 입력을 채팅형 message로 바꿔 같은 run_agent_turn 흐름으로 보냅니다.
def run_spec_agent(request: DraftRequest, settings: Settings) -> DraftResponse:
    message = "\n".join(
        value
        for value in [
            f"사건명: {request.case_name}",
            f"발명 명칭: {request.invention.title}",
            f"아이디어: {request.invention.idea}",
            f"해결 과제: {request.invention.problem}",
            f"구성요소: {request.invention.components}",
            f"작동 방식: {request.invention.operation}",
            f"효과: {request.invention.effects}",
            f"도면 설명: {request.invention.drawings}",
            f"실험/성능 자료: {request.invention.experiment_data}",
            f"상담 메모: {request.invention.consultation_memo}",
            f"선행기술 키워드: {request.invention.prior_art_keywords}",
        ]
        if value.strip()
    )
    return run_agent_turn(
        message=message,
        uploads=[],
        settings=settings,
        session_id=None,
        case_name=request.case_name,
        use_rag=request.use_rag,
    )


# LangGraph 실행 상태 #
# 한 턴 동안 Agent 노드가 주고받는 작업 메모임. 프론트로 직접 노출하지 않음.
class AgentRuntimeState(TypedDict, total=False):
    message: str
    uploads: list[IncomingUpload]
    settings: Settings
    session_id: str
    active_case_name: str
    use_rag: bool
    session_state: dict[str, Any]
    steps: list[AgentStep]
    materials: list[MaterialSource]
    documents: list[Any]
    material_texts: list[str]
    corpus: str
    query: str
    weak_corpus: bool
    references: list[ReferenceItem]
    prior_art_candidates: list[PriorArtCandidate]
    structured: AgentStructuredOutput | None
    llm_error: str | None
    sections: SpecSections
    response_case_name: str
    follow_up_questions: list[FollowUpQuestion]
    missing_materials: list[str]
    review_items: list[ReviewItem]
    checklist: list[ChecklistItem]
    reply: str
    weak_delegation_request: bool
    guardrail_route: str
    guardrail_reason: str
    response: AgentResponse


# 1. Agent 한 턴 처리 #
# 프론트가 보낸 메시지/파일 1회분을 LangGraph 노드 흐름으로 실행합니다.
def run_agent_turn(
    message: str,
    uploads: list[IncomingUpload],
    settings: Settings,
    session_id: str | None = None,
    case_name: str | None = None,
    use_rag: bool = True,
) -> AgentResponse:
    initial_state: AgentRuntimeState = {
        "message": message,
        "uploads": uploads,
        "settings": settings,
        "session_id": session_id or "",
        "active_case_name": case_name or "",
        "use_rag": use_rag,
    }
    final_state = _compiled_agent_graph().invoke(initial_state)
    return final_state["response"]


# LangGraph 컴파일 #
# 수업의 StateGraph처럼 각 처리 단계를 노드로 연결함.
@lru_cache(maxsize=1)
def _compiled_agent_graph():
    graph = StateGraph(AgentRuntimeState)
    graph.add_node("guardrail", _guardrail_node)
    graph.add_node("receive", _receive_node)
    graph.add_node("vectorize", _vectorize_node)
    graph.add_node("memory", _memory_node)
    graph.add_node("rag", _rag_node)
    graph.add_node("kipris", _kipris_node)
    graph.add_node("structure", _structure_node)
    graph.add_node("self_review", _self_review_node)
    graph.add_node("checklist_step", _checklist_node)
    graph.add_node("export_step", _export_node)
    graph.add_edge(START, "guardrail")
    graph.add_conditional_edges(
        "guardrail",
        _route_after_guardrail,
        {"blocked": END, "continue": "receive"},
    )
    graph.add_edge("receive", "vectorize")
    graph.add_edge("vectorize", "memory")
    graph.add_edge("memory", "rag")
    graph.add_edge("rag", "kipris")
    graph.add_edge("kipris", "structure")
    graph.add_edge("structure", "self_review")
    graph.add_edge("self_review", "checklist_step")
    graph.add_edge("checklist_step", "export_step")
    graph.add_edge("export_step", END)
    return graph.compile()


# Guardrail 노드 #
# 금지 요청이면 여기서 응답을 만들고 그래프를 종료함.
def _guardrail_node(state: AgentRuntimeState) -> AgentRuntimeState:
    settings = state["settings"]
    session_id = normalize_session_id(state.get("session_id"))
    session_state = load_state(settings, session_id)
    active_case_name = state.get("active_case_name") or session_state.get("case_name") or "새 출원 준비 건"
    steps = _base_steps()
    blocked_reason = detect_blocked_request(
        state.get("message", ""),
        allow_kipris_research=kipris_is_configured(settings),
    )
    guardrail_decision = None
    if not blocked_reason:
        guardrail_decision = classify_request_with_llm(
            state.get("message", ""),
            api_key=settings.openai_api_key,
            model=settings.openai_model,
        )
        blocked_reason = blocked_reason_from_decision(guardrail_decision)
    if blocked_reason:
        return {
            "session_id": session_id,
            "session_state": session_state,
            "active_case_name": active_case_name,
            "steps": steps,
            "response": _blocked_response(session_id, active_case_name, blocked_reason, steps),
        }
    return {
        "session_id": session_id,
        "session_state": session_state,
        "active_case_name": active_case_name,
        "steps": steps,
        "guardrail_route": guardrail_decision.route if guardrail_decision else "ALLOW_DRAFT",
        "guardrail_reason": guardrail_decision.reason if guardrail_decision else "",
    }


# Guardrail 분기 #
# blocked 응답이 있으면 바로 END, 없으면 자료 수신 노드로 이동함.
def _route_after_guardrail(state: AgentRuntimeState) -> str:
    return "blocked" if state.get("response") else "continue"


# 자료 수신 노드 #
# 파일/메시지를 LangChain Document와 화면 표시용 MaterialSource로 변환함.
def _receive_node(state: AgentRuntimeState) -> AgentRuntimeState:
    settings = state["settings"]
    session_id = state["session_id"]
    message = state.get("message", "")
    uploads = state.get("uploads", [])
    steps = state["steps"]
    materials, documents, material_texts = _extract_materials(settings, session_id, uploads)
    material_message = _material_message_text(message)
    if material_message:
        material_texts.append(material_message)
        documents.extend(message_to_documents(material_message, session_id))
    steps[0].status = StepStatus.complete
    steps[0].detail = f"메시지와 파일 {len(uploads)}개를 수신했습니다."
    return {"materials": materials, "documents": documents, "material_texts": material_texts, "steps": steps}


# 벡터 저장 노드 #
# Document 청크를 임베딩해 세션 전용 pgVector 컬렉션에 저장함.
def _vectorize_node(state: AgentRuntimeState) -> AgentRuntimeState:
    settings = state["settings"]
    session_id = state["session_id"]
    documents = state.get("documents", [])
    steps = state["steps"]
    if documents:
        try:
            indexed_count = add_documents_to_collection(
                settings=settings,
                documents=documents,
                collection_name=case_collection_name(settings, session_id),
                reset=False,
            )
            steps[1].status = StepStatus.complete
            steps[1].detail = f"{indexed_count}개 청크를 세션 벡터 DB에 저장했습니다."
        except Exception as exc:
            steps[1].status = StepStatus.warning
            steps[1].detail = f"벡터 저장은 실패했지만 텍스트 분석은 계속합니다: {_safe_error(exc)}"
    else:
        steps[1].status = StepStatus.warning
        steps[1].detail = "새로 분석할 자료가 없습니다."
    return {"steps": steps}


# 세션 메모리 노드 #
# 이번 턴 입력을 state.json에 누적하고 다음 노드가 쓸 corpus/query를 만듦.
def _memory_node(state: AgentRuntimeState) -> AgentRuntimeState:
    settings = state["settings"]
    session_id = state["session_id"]
    active_case_name = state["active_case_name"]
    message = state.get("message", "")
    session_state = append_user_turn(
        settings,
        session_id,
        message,
        state.get("materials", []),
        state.get("material_texts", []),
    )
    session_state["case_name"] = active_case_name
    save_state(settings, session_id, session_state)
    corpus = _build_corpus(session_state)
    query = _query_text(message, corpus)
    corpus_too_weak = _corpus_is_too_weak(corpus)
    weak_delegation_request = _is_weak_delegation_request(message, corpus)
    if state.get("guardrail_route") == "ALLOW_IDEATION_ONLY" and corpus_too_weak:
        weak_delegation_request = True
    return {
        "session_state": session_state,
        "corpus": corpus,
        "query": query,
        "weak_corpus": corpus_too_weak or weak_delegation_request,
        "weak_delegation_request": weak_delegation_request,
    }


# RAG 노드 #
# 세션 벡터DB와 공용 참고자료 벡터DB에서 관련 청크를 검색함.
def _rag_node(state: AgentRuntimeState) -> AgentRuntimeState:
    settings = state["settings"]
    session_id = state["session_id"]
    query = state.get("query", "")
    steps = state["steps"]
    references: list[ReferenceItem] = []
    if state.get("weak_corpus"):
        steps[2].status = StepStatus.warning
        steps[2].detail = "자료가 너무 짧아 RAG 검색을 건너뛰었습니다."
    elif state.get("use_rag", True) and query.strip():
        references = get_case_references(settings, session_id, query, k=5)
        references.extend(get_references(settings, query, k=4))
        steps[2].status = StepStatus.complete if references else StepStatus.warning
        steps[2].detail = f"관련 참고자료 {len(references)}건을 찾았습니다." if references else "검색된 참고자료가 없습니다."
    else:
        steps[2].status = StepStatus.warning
        steps[2].detail = "RAG 검색을 건너뛰었습니다."
    return {"references": references, "steps": steps}


# KIPRIS 도구 노드 #
# KIPRISPlus API가 켜져 있으면 국내 선행기술 후보를 검색해 참고자료에 합침.
def _kipris_node(state: AgentRuntimeState) -> AgentRuntimeState:
    settings = state["settings"]
    query = state.get("query", "")
    corpus = state.get("corpus", "")
    message = state.get("message", "")
    steps = state["steps"]
    references = list(state.get("references", []))
    prior_art_candidates: list[PriorArtCandidate] = []
    requests_kipris = _message_requests_kipris(message) or state.get("guardrail_route") == "ALLOW_KIPRIS_SEARCH"
    kipris_source = _kipris_query_source(message, query, corpus)
    kipris_query = build_kipris_query(kipris_source)
    if state.get("weak_corpus") and not requests_kipris:
        steps[3].status = StepStatus.warning
        steps[3].detail = "자료가 너무 짧아 KIPRIS 검색을 건너뛰었습니다."
    elif not kipris_query.strip():
        steps[3].status = StepStatus.warning
        steps[3].detail = "KIPRIS 검색어를 만들 수 없어 후보 검색을 건너뛰었습니다."
    elif kipris_is_configured(settings):
        prior_art_candidates = search_kipris(settings, kipris_query)
        references.extend(candidates_to_references(prior_art_candidates))
        steps[3].status = StepStatus.complete if prior_art_candidates else StepStatus.warning
        steps[3].detail = (
            f"KIPRISPlus에서 선행기술 후보 {len(prior_art_candidates)}건을 찾았습니다."
            if prior_art_candidates
            else "KIPRISPlus 검색 결과가 없거나 호출에 실패했습니다."
        )
    else:
        steps[3].status = StepStatus.warning
        steps[3].detail = "KIPRIS API 키 또는 검색 설정이 없어 선행기술 후보 검색을 건너뛰었습니다."
    return {"references": references, "prior_art_candidates": prior_art_candidates, "steps": steps}


# LLM 구조화 노드 #
# corpus/RAG/KIPRIS 결과를 OpenAI 구조화 출력으로 변환함.
def _structure_node(state: AgentRuntimeState) -> AgentRuntimeState:
    settings = state["settings"]
    steps = state["steps"]
    corpus = state.get("corpus", "")
    active_case_name = state["active_case_name"]
    if state.get("weak_corpus"):
        structured, llm_error = None, None
    else:
        structured, llm_error = _call_llm(
            settings=settings,
            case_name=active_case_name,
            corpus=corpus,
            references=state.get("references", []),
            messages=[ChatMessage(**item) for item in state.get("session_state", {}).get("messages", [])],
        )

    if structured:
        steps[4].status = StepStatus.complete
        steps[4].detail = "LLM이 자료를 명세서 항목으로 구조화했습니다."
        sections = _fill_sections_from_corpus(structured.sections, corpus)
        return {
            "structured": structured,
            "llm_error": None,
            "sections": sections,
            "response_case_name": structured.case_name or active_case_name,
            "follow_up_questions": structured.follow_up_questions,
            "missing_materials": structured.missing_materials,
            "review_items": default_boundary_items() + structured.review_items,
            "reply": structured.reply,
            "steps": steps,
        }

    steps[4].status = StepStatus.warning
    steps[4].detail = "LLM 구조화 호출이 실패해 규칙 기반 분석으로 대체했습니다."
    review_items = default_boundary_items()
    if llm_error:
        review_items.append(
            ReviewItem(
                severity=ReviewSeverity.warning,
                title="LLM 호출 확인 필요",
                description=f"구조화 호출 실패: {llm_error}",
                human_owner="개발자",
            )
        )
    return {
        "structured": None,
        "llm_error": llm_error,
        "sections": _fill_sections_from_corpus(_fallback_sections(corpus), corpus),
        "response_case_name": active_case_name,
        "follow_up_questions": [],
        "missing_materials": [],
        "review_items": review_items,
        "reply": "",
        "steps": steps,
    }


# 자기검토 노드 #
# LLM이 채운 본문을 원문/RAG 근거와 다시 대조해 검토 항목을 추가함.
def _self_review_node(state: AgentRuntimeState) -> AgentRuntimeState:
    steps = state["steps"]
    review_items = list(state.get("review_items", []))
    review_items.extend(
        _review_sections_against_evidence(
            state.get("sections", SpecSections()),
            state.get("corpus", ""),
            state.get("references", []),
        )
    )
    steps[5].status = StepStatus.complete
    steps[5].detail = "LLM 결과를 원문 및 검색 근거와 대조했습니다."
    return {"review_items": review_items, "steps": steps}


# 체크리스트 노드 #
# 명세서 10개 필수항목의 완료/부족/검토 필요를 판정함.
def _checklist_node(state: AgentRuntimeState) -> AgentRuntimeState:
    settings = state["settings"]
    corpus = state.get("corpus", "")
    steps = state["steps"]
    sections = _attach_prior_art_candidates(
        state.get("sections", SpecSections()),
        state.get("prior_art_candidates", []),
    )
    checklist = _build_checklist(sections, corpus)
    missing_from_checklist = [item.label for item in checklist if item.status == ChecklistStatus.missing]
    missing_materials = list(state.get("missing_materials", []))
    existing_missing = set(missing_materials)
    missing_materials.extend(item for item in missing_from_checklist if item not in existing_missing)
    follow_up_questions = list(state.get("follow_up_questions", []))
    review_items = list(state.get("review_items", []))
    if not state.get("weak_corpus"):
        checklist, audit_questions, audit_reviews = _audit_checklist_with_llm(
            settings=settings,
            sections=sections,
            corpus=corpus,
            references=state.get("references", []),
            checklist=checklist,
        )
        follow_up_questions.extend(audit_questions)
        review_items.extend(audit_reviews)
        missing_from_checklist = [item.label for item in checklist if item.status == ChecklistStatus.missing]
        existing_missing = set(missing_materials)
        missing_materials.extend(item for item in missing_from_checklist if item not in existing_missing)
    unverified_claim = _unverified_effect_claim(corpus)
    if unverified_claim:
        if "효과 또는 실험/관찰 결과 증빙 자료" not in missing_materials:
            missing_materials.append("효과 또는 실험/관찰 결과 증빙 자료")
        review_items.append(
            ReviewItem(
                severity=ReviewSeverity.warning,
                title="효과 주장 증빙 필요",
                description=(
                    f"'{unverified_claim}' 같은 수치 또는 관찰 주장은 원자료 확인 전에는 "
                    "정량 효과로 확정하지 않습니다. 시험조건, 관찰기록, 대상 수, 기간, 비교군을 확인해야 합니다."
                ),
                human_owner="변리업 종사자 또는 변리사",
            )
        )
    prior_art_candidates = state.get("prior_art_candidates", [])
    if prior_art_candidates and not any(item.title == "KIPRISPlus 후보 차이점 검토" for item in review_items):
        top_titles = ", ".join((candidate.title or "제목 확인 필요") for candidate in prior_art_candidates[:3])
        review_items.append(
            ReviewItem(
                severity=ReviewSeverity.warning,
                title="KIPRISPlus 후보 차이점 검토",
                description=(
                    f"자동 검색된 선행기술 후보와 본 발명의 공통점/차이점을 사람이 검토해야 합니다. "
                    f"우선 검토 후보: {top_titles}"
                ),
                human_owner="변리업 종사자 또는 변리사",
            )
        )
    needs_human_answer = any(
        item.status in {ChecklistStatus.missing, ChecklistStatus.needs_review, ChecklistStatus.blocked}
        for item in checklist
    )
    follow_up_questions = _merge_questions(follow_up_questions, checklist) if needs_human_answer else []
    steps[6].status = StepStatus.complete if not missing_from_checklist else StepStatus.warning
    steps[6].detail = (
        "필수항목이 모두 채워졌습니다."
        if not missing_from_checklist
        else f"필수항목 {len(missing_from_checklist)}개가 부족합니다."
    )

    reply = state.get("reply", "")
    if state.get("weak_corpus"):
        sections = SpecSections()
        checklist = _build_checklist(sections, "")
        missing_materials = [item.label for item in checklist if item.status == ChecklistStatus.missing]
        if state.get("weak_delegation_request"):
            reply, follow_up_questions = _build_safe_ideation_reply(
                settings=settings,
                message=state.get("message", ""),
                corpus=corpus,
            )
            review_items.append(
                ReviewItem(
                    severity=ReviewSeverity.warning,
                    title="근거 부족 위임 요청",
                    description=(
                        "사용자가 '너가 해줘'처럼 작성을 위임했지만, 현재 자료에는 발명의 목적, "
                        "구성요소, 작동 방식, 차별점이 부족합니다. Agent가 없는 기술 내용을 만들어 넣지 않도록 보완 질문으로 전환했습니다."
                    ),
                    human_owner="사용자",
                )
            )
        else:
            reply = (
                "지금 자료는 출원명세서 초안을 만들기에는 너무 짧습니다. "
                "발명의 목적, 구성요소, 작동 방식, 효과 중 최소 2~3가지를 더 알려 주세요."
            )
            follow_up_questions = _merge_questions([], checklist)
    elif not reply:
        reply = _build_reply(checklist, state.get("references", []))
    kipris_note = _build_kipris_reply_note(
        settings=settings,
        message=state.get("message", ""),
        candidates=state.get("prior_art_candidates", []),
    )
    if kipris_note and kipris_note not in reply:
        reply = f"{reply.rstrip()}\n\n{kipris_note}"
    reply = _append_follow_up_questions(reply, follow_up_questions)
    return {
        "sections": sections,
        "checklist": checklist,
        "missing_materials": missing_materials,
        "follow_up_questions": follow_up_questions,
        "review_items": review_items,
        "reply": reply,
        "steps": steps,
    }


# 출력 노드 #
# AgentResponse를 만들고 Markdown/Word 파일을 세션 폴더에 저장함.
def _export_node(state: AgentRuntimeState) -> AgentRuntimeState:
    settings = state["settings"]
    session_id = state["session_id"]
    steps = state["steps"]
    session_state = state.get("session_state", {})
    response = AgentResponse(
        session_id=session_id,
        case_name=state.get("response_case_name") or state.get("active_case_name") or "새 출원 준비 건",
        reply=state.get("reply", ""),
        sections=state.get("sections", SpecSections()),
        follow_up_questions=state.get("follow_up_questions", []),
        missing_materials=state.get("missing_materials", []),
        review_items=state.get("review_items", []),
        references=_dedupe_references(state.get("references", [])),
        prior_art_candidates=state.get("prior_art_candidates", []),
        checklist=state.get("checklist", []),
        materials=[MaterialSource(**item) for item in session_state.get("materials", [])],
        steps=steps,
        messages=[ChatMessage(**item) for item in session_state.get("messages", [])],
        markdown="",
    )
    response.markdown = build_markdown(response)
    markdown_path, docx_path = export_draft(response, settings.resolved_output_dir / session_id)
    response.markdown_path = str(markdown_path)
    response.docx_path = str(docx_path)
    steps[7].status = StepStatus.complete
    steps[7].detail = "Markdown과 Word 초안을 생성했습니다."
    append_assistant_turn(settings, session_id, response.reply)
    response.messages = [ChatMessage(**item) for item in load_state(settings, session_id).get("messages", [])]
    return {"response": response, "steps": steps}


# 처리 흐름 정의 #
# 왼쪽 사이드바에 표시되는 단계명과 사용 도구입니다.
def _base_steps() -> list[AgentStep]:
    return [
        AgentStep(key="receive", title="자료 수신", status=StepStatus.running, tool="FastAPI Upload"),
        AgentStep(key="vectorize", title="토큰화 및 벡터 저장", tool="LangChain + pgVector"),
        AgentStep(key="rag", title="RAG 참고자료 검색", tool="pgVector similarity search"),
        AgentStep(key="kipris", title="KIPRIS 선행기술 후보 검색", tool="KIPRISPlus REST API"),
        AgentStep(key="structure", title="명세서 항목 구조화", tool="OpenAI LLM"),
        AgentStep(key="self_review", title="자기검토 및 근거 대조", tool="SPEC self-review"),
        AgentStep(key="checklist", title="필수항목 체크리스트 점검", tool="SPEC checklist"),
        AgentStep(key="export", title="md / Word 산출물 생성", tool="python-docx"),
    ]


# 파일 추출 호출 #
# uploads -> 저장 파일 -> Document 청크 -> materials 표시 데이터로 바꿉니다.
def _extract_materials(
    settings: Settings,
    session_id: str,
    uploads: list[IncomingUpload],
) -> tuple[list[MaterialSource], list, list[str]]:
    materials = []
    documents = []
    texts = []
    target_dir = upload_dir(settings, session_id)
    for upload in uploads:
        material, chunks, text = extract_upload(upload.filename, upload.content, target_dir)
        materials.append(material)
        documents.extend(chunks)
        if text.strip():
            texts.append(text)
    return materials, documents, texts


# OpenAI LLM 호출 #
# API 호출 대상: OpenAI ChatOpenAI. 보내는 값은 사건명, 최근 대화, 누적 자료, RAG 참고자료입니다.
def _call_llm(
    settings: Settings,
    case_name: str,
    corpus: str,
    references: list[ReferenceItem],
    messages: list[ChatMessage],
) -> tuple[AgentStructuredOutput | None, str | None]:
    if not settings.openai_api_key or not corpus.strip():
        return None, None

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """당신은 한국 특허 출원명세서 검토용 초안을 돕는 SPEC Agent입니다.

역할:
- 사용자가 보낸 회의록, 아이디어, 도면 설명, 상담 메모를 명세서 항목으로 구조화합니다.
- 사용자가 이전에 업로드한 회의록이나 자료 내용을 보여 달라고 하면, 초안 작성만 반복하지 말고 누적 자료를 바탕으로 먼저 대화형으로 답합니다.
- 자료가 부족하면 초안을 억지로 채우지 말고 follow_up_questions와 missing_materials에 분리합니다.
- 사용자 자료에 없는 기술 구성, 실험 결과, 정량 효과, 선행기술문헌 번호를 만들지 않습니다.
- 사용자가 임계값, 실험 수치, 성능 수치, 선행문헌 번호, 도면 또는 도안을 임의로 정하거나 만들어 달라고 해도 본문에 넣지 않습니다.
- 도면 파일이나 도면 설명이 없으면 도면을 창작하지 말고 필요한 도면 종류와 부호 설명을 질문합니다.
- 이미지 파일이 업로드되어도 구성요소와 부호를 눈으로 본 것처럼 단정하지 않습니다. 이미지가 도면 후보임을 언급하고 도면 설명/부호 확인을 질문합니다.
- 논문 또는 일반 인터넷 검색을 수행한 것처럼 말하지 않습니다. KIPRISPlus 결과는 prior_art_candidates에 있는 자동 후보만 근거로 삼습니다.
- 재질, 성분, 층 구조, 향미 효과, 공정조건을 추천값으로 본문에 확정하지 않습니다. 후보는 검토 항목 또는 추가 질문으로만 둡니다.
- 사용자가 제공한 위험할 수 있는 작동 방식은 안전장치와 전문가 검토 항목으로 분리하고, 안전성이 확인된 것처럼 단정하지 않습니다.
- 특허성 판단, 청구범위 확정, 자동 출원은 하지 않습니다.
- 답변은 한국어로 작성합니다.

출력:
- 사용자가 바로 이해할 수 있는 reply
- 출원명세서 섹션 초안
- 추가 질문
- 누락 자료
- 전문가 검토 항목
""",
            ),
            (
                "human",
                """사건명: {case_name}

대화 기록:
{messages_json}

사용자가 제공한 누적 자료:
{corpus}

검색된 참고자료:
{references_json}
""",
            ),
        ]
    )

    try:
        # 4.2 OpenAI 요청 본문 #
        # case_name, messages_json, corpus, references_json이 프롬프트 변수로 들어갑니다.
        llm = ChatOpenAI(
            model=settings.openai_model,
            temperature=0.2,
            api_key=settings.openai_api_key,
        ).with_structured_output(AgentStructuredOutput)
        chain = prompt | llm
        return (
            chain.invoke(
                {
                    "case_name": case_name,
                    "messages_json": json.dumps(
                        [message.model_dump() for message in messages[-8:]],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "corpus": corpus[-14000:],
                    "references_json": json.dumps(
                        [reference.model_dump() for reference in references[:8]],
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            ),
            None,
        )
    except Exception as exc:
        return None, _safe_error(exc)


# LLM 실패 시 규칙 기반 추출 #
# 라벨이 있는 입력만 간단히 뽑습니다. 없는 정보는 만들지 않습니다.
def _fallback_sections(corpus: str) -> SpecSections:
    if _corpus_is_too_weak(corpus):
        return SpecSections()
    title = _extract_labeled_value(corpus, ["발명 명칭", "제목", "아이디어명"]) or _extract_section_block(
        corpus, ["발명(고안)의 명칭", "발명의 명칭"]
    )
    problem = _extract_labeled_value(corpus, ["해결하려는 과제", "해결 과제", "문제", "불편"]) or _extract_section_block(
        corpus, ["해결하려는 과제", "해결하고자 하는 과제"]
    )
    components = _extract_labeled_value(corpus, ["구성요소", "구성", "부품"])
    operation = _extract_labeled_value(corpus, ["작동 방식", "동작", "실시예"]) or _extract_section_block(
        corpus, ["발명을 실시하기 위한 구체적인 내용", "실시예"]
    )
    effects = _extract_labeled_value(corpus, ["발명의 효과", "효과", "장점"]) or _extract_section_block(
        corpus, ["발명(고안)의 효과", "발명의 효과"]
    )
    drawings = _extract_labeled_value(corpus, ["도면 설명", "도면", "스케치"]) or _extract_section_block(
        corpus, ["도면의 간단한 설명"]
    )
    technical_field = _extract_labeled_value(corpus, ["기술분야", "분야"]) or _extract_section_block(
        corpus, ["기술분야"]
    )
    background = _extract_section_block(corpus, ["발명(고안)의 배경이 되는 기술", "발명의 배경이 되는 기술", "배경기술"])
    prior_art = _extract_labeled_value(
        corpus, ["선행기술 키워드", "선행기술문헌", "선행기술", "선행문헌", "검색 키워드"]
    ) or _extract_section_block(corpus, ["선행기술문헌", "선행기술 문헌"])
    industry = _extract_labeled_value(corpus, ["산업상 이용가능성", "사용 분야", "활용 분야"]) or _extract_section_block(
        corpus, ["산업상 이용가능성"]
    )
    reference_signs = _extract_labeled_value(corpus, ["부호의 설명", "부호", "도면부호"]) or _extract_section_block(
        corpus, ["부호의 설명"]
    )
    solution = _extract_section_block(corpus, ["과제의 해결 수단", "해결 수단"]) or "\n\n".join(
        part for part in [components, operation] if part
    )

    return SpecSections(
        invention_title=title,
        technical_field=technical_field,
        background_art=background or problem,
        prior_art_documents=prior_art,
        problem_to_solve=problem,
        solution=solution,
        advantageous_effects=effects,
        embodiment=operation,
        drawing_description=drawings,
        reference_signs=reference_signs,
        industrial_applicability=industry,
    )


# LLM 결과 후처리 #
# LLM이 놓친 표준 명세서 제목 블록을 원문에서 보충함.
def _fill_sections_from_corpus(sections: SpecSections, corpus: str) -> SpecSections:
    fallback = _fallback_sections(corpus)
    data = sections.model_dump()
    for key, value in fallback.model_dump().items():
        if not _is_meaningful(str(data.get(key, ""))) and _is_meaningful(str(value)):
            data[key] = value
    return SpecSections(**data)


# KIPRIS 후보 본문 자동 삽입 방지 #
# 선행기술은 일반 명세서 필수 입력이 아니므로 자동 후보를 본문 섹션에 넣지 않고 사이트 탭에만 둠.
def _attach_prior_art_candidates(
    sections: SpecSections,
    candidates: list[PriorArtCandidate],
) -> SpecSections:
    return sections


# 자기검토: 본문-근거 대조 #
# LLM이 만든 섹션에 원문/참고자료에서 찾기 어려운 수치나 표현이 있으면 검토 항목으로 분리함.
def _review_sections_against_evidence(
    sections: SpecSections,
    corpus: str,
    references: list[ReferenceItem],
) -> list[ReviewItem]:
    evidence_text = "\n".join([corpus, *[item.excerpt for item in references]])
    evidence_tokens = set(_evidence_tokens(evidence_text))
    review_items = []
    section_labels = {
        "invention_title": "발명 명칭",
        "technical_field": "기술분야",
        "background_art": "배경기술",
        "prior_art_documents": "선행기술문헌",
        "problem_to_solve": "해결하려는 과제",
        "solution": "해결수단",
        "advantageous_effects": "효과",
        "embodiment": "실시예",
        "drawing_description": "도면 설명",
        "reference_signs": "부호 설명",
        "industrial_applicability": "산업상 이용가능성",
    }
    for field_name, label in section_labels.items():
        value = getattr(sections, field_name, "")
        if not _is_meaningful(value) or "KIPRISPlus 자동 검색 후보" in value:
            continue
        missing_numbers = [number for number in re.findall(r"\d+(?:\.\d+)?", value) if number not in evidence_text]
        if missing_numbers:
            review_items.append(
                ReviewItem(
                    severity=ReviewSeverity.warning,
                    title=f"{label} 수치 근거 확인 필요",
                    description=(
                        f"{label}에 '{', '.join(missing_numbers[:4])}' 같은 수치가 있으나 "
                        "누적 자료 또는 검색 근거에서 직접 확인되지 않았습니다."
                    ),
                    human_owner="변리업 종사자 또는 변리사",
                )
            )
            continue
        value_tokens = set(_evidence_tokens(value))
        if len(value_tokens) >= 5 and evidence_tokens and not value_tokens.intersection(evidence_tokens):
            review_items.append(
                ReviewItem(
                    severity=ReviewSeverity.warning,
                    title=f"{label} 원문 근거 확인 필요",
                    description=(
                        f"{label} 섹션에 원문과 직접 맞물리지 않는 표현이 포함될 수 있습니다. "
                        "본문 확정 전 사용자 자료 또는 도면 설명으로 확인해야 합니다."
                    ),
                    human_owner="변리업 종사자 또는 변리사",
                )
            )
        if len(review_items) >= 4:
            break
    return review_items


# 자기검토 토큰 추출 #
# 조사/일반어를 줄이고 명사성 단어 중심으로 근거 대조에 사용함.
def _evidence_tokens(text: str) -> list[str]:
    stopwords = {
        "본발명",
        "발명은",
        "관한",
        "것으로",
        "있다",
        "한다",
        "대한",
        "또는",
        "그리고",
        "사용자",
        "자료",
        "검토",
        "필요",
        "확인",
        "구성",
        "방식",
        "기술",
        "효과",
    }
    compact = re.sub(r"\s+", "", text or "")
    tokens = re.findall(r"[가-힣A-Za-z0-9]{3,}", compact)
    return [token.lower() for token in tokens if token.lower() not in stopwords]


# 표준 제목 블록 추출 #
# 【해결하려는 과제】 또는 "## 해결하려는 과제" 아래 본문을 다음 제목 전까지 읽음.
def _extract_section_block(text: str, headings: list[str]) -> str:
    if not text:
        return ""
    normalized_headings = [re.escape(heading) for heading in headings]
    heading_pattern = "|".join(normalized_headings)
    start_pattern = re.compile(
        rf"(?:【\s*(?:{heading_pattern})\s*】|##+\s*(?:{heading_pattern})|(?:{heading_pattern})\s*\n)",
        re.IGNORECASE,
    )
    match = start_pattern.search(text)
    if not match:
        return ""
    rest = text[match.end() :]
    end_match = re.search(r"\n\s*(?:【[^】]{2,60}】|##+\s+.+)\s*\n", rest)
    block = rest[: end_match.start()] if end_match else rest
    block = re.sub(r"^\s*[：:]\s*", "", block.strip())
    return block[:1800]


# 라벨 값 추출 #
# "구성요소: ..." 같은 줄에서 오른쪽 값을 잘라냅니다.
def _extract_labeled_value(text: str, labels: list[str]) -> str:
    for label in labels:
        pattern = re.compile(rf"{re.escape(label)}\s*[:：]\s*(.+)", re.IGNORECASE)
        match = pattern.search(text)
        if match:
            return match.group(1).strip()[:1200]
    return ""


# 체크리스트 판정 #
# 완료는 "자료에서 해당 항목이 확인됨"이고, 법률 검토 완료가 아닙니다.
def _build_checklist(sections: SpecSections, corpus: str = "") -> list[ChecklistItem]:
    items = []
    compact_corpus = re.sub(r"\s+", "", corpus or "").lower()
    has_drawing_material = any(term in compact_corpus for term in ["도면", "도1", "도2", "부호", "스케치", "도안"])
    has_uploaded_image = "이미지자료업로드" in compact_corpus
    unverified_claim = _unverified_effect_claim(corpus)
    for key, label, section_name, question in REQUIRED_ITEMS:
        value = getattr(sections, section_name, "")
        if _is_meaningful(value):
            status = ChecklistStatus.complete
            evidence = value.strip()[:160]
        else:
            status = ChecklistStatus.missing
            evidence = ""
        items.append(
            ChecklistItem(
                key=key,
                label=label,
                status=status,
                evidence=evidence,
                question=question,
            )
        )

    if not has_drawing_material:
        for item in items:
            if item.key in {"drawings", "reference_signs"}:
                item.status = ChecklistStatus.missing
                item.evidence = ""
                item.question = "도면 또는 도안이 있다면 파일로 올리거나, 각 도면과 부호 설명을 알려 주세요."

    if has_uploaded_image and not _is_meaningful(sections.drawing_description):
        for item in items:
            if item.key == "drawings":
                item.status = ChecklistStatus.needs_review
                item.evidence = "이미지 파일이 도면 또는 도안 후보로 업로드되었습니다."
                item.question = "이미지는 업로드되었지만 도면 내용을 자동 확정할 수 없습니다. 도면 명칭과 각 도면의 설명을 알려 주세요."
            if item.key == "reference_signs":
                item.status = ChecklistStatus.needs_review
                item.evidence = "이미지 파일이 도면 또는 도안 후보로 업로드되었습니다."
                item.question = "이미지의 구성요소를 임의로 확정할 수 없습니다. 도면부호와 구성요소 대응표를 알려 주세요."

    if has_drawing_material and _is_meaningful(sections.drawing_description) and not _is_meaningful(sections.reference_signs):
        for item in items:
            if item.key == "reference_signs":
                item.status = ChecklistStatus.needs_review
                item.question = "도면 설명은 있으나 도면부호가 부족합니다. 부호와 구성요소 대응표를 알려 주세요."
    if unverified_claim:
        for item in items:
            if item.key == "effects":
                item.status = ChecklistStatus.needs_review
                item.evidence = f"효과 또는 수치 주장 증빙 확인 필요: {unverified_claim[:120]}"
                item.question = "효과를 주장하려면 관찰기록, 시험조건, 대상 수, 기간, 비교군 등 증빙 자료를 알려 주세요."
    return items


# AI 체크리스트 재검토 #
# 규칙 기반 체크리스트가 너무 쉽게 완료되는 문제를 LLM Review Agent로 한 번 더 검사함.
def _audit_checklist_with_llm(
    settings: Settings,
    sections: SpecSections,
    corpus: str,
    references: list[ReferenceItem],
    checklist: list[ChecklistItem],
) -> tuple[list[ChecklistItem], list[FollowUpQuestion], list[ReviewItem]]:
    if not settings.openai_api_key or not corpus.strip():
        return checklist, [], []

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """당신은 한국 특허 출원명세서 초안의 필수항목을 엄격히 검토하는 Review Agent입니다.

판정 기준:
- complete: 초안 작성에 필요한 구체 정보가 자료 안에 명시되어 있음.
- needs_review: 항목은 있으나 구체성, 근거, 결합관계, 도면부호, 효과 증빙, 안전성 검토가 부족함.
- missing: 해당 항목을 작성할 자료가 사실상 없음.

주의:
- 단순 키워드, 짧은 대화, 추상적 아이디어만으로 complete 처리하지 않습니다.
- 인터뷰/회의록만 있어도 구성요소, 작동순서, 도면 설명, 도면부호가 구체적이면 complete가 가능합니다.
- 선행기술문헌은 일반 명세서 필수항목으로 판정하지 않고, KIPRISPlus 후보 검토 항목으로만 다룹니다.
- 최종 특허성 판단, 청구범위 확정, 출원 가능성은 complete 판정 대상이 아니며 사람 검토 영역입니다.
- 각 항목별 질문은 missing 또는 needs_review일 때만 작성합니다.
- 답변은 한국어로 작성합니다.
""",
            ),
            (
                "human",
                """필수항목:
{required_items}

규칙 기반 체크리스트:
{checklist_json}

명세서 섹션:
{sections_json}

누적 원자료:
{corpus}

검색 근거:
{references_json}
""",
            ),
        ]
    )
    try:
        llm = ChatOpenAI(
            model=settings.openai_model,
            temperature=0,
            api_key=settings.openai_api_key,
        ).with_structured_output(ChecklistAuditOutput)
        audit = (prompt | llm).invoke(
            {
                "required_items": json.dumps(
                    [{"key": key, "label": label, "section": section} for key, label, section, _ in REQUIRED_ITEMS],
                    ensure_ascii=False,
                    indent=2,
                ),
                "checklist_json": json.dumps(
                    [item.model_dump() for item in checklist],
                    ensure_ascii=False,
                    indent=2,
                ),
                "sections_json": json.dumps(sections.model_dump(), ensure_ascii=False, indent=2),
                "corpus": corpus[-12000:],
                "references_json": json.dumps(
                    [reference.model_dump() for reference in references[:8]],
                    ensure_ascii=False,
                    indent=2,
                ),
            }
        )
    except Exception:
        return checklist, [], []

    by_key = {item.key: item for item in checklist}
    label_by_key = {key: label for key, label, _, _ in REQUIRED_ITEMS}
    audit_questions = []
    audit_reviews = []
    for audit_item in audit.items:
        target = by_key.get(audit_item.key)
        if not target:
            continue
        if audit_item.status == "complete":
            target.status = ChecklistStatus.complete
            if audit_item.evidence.strip():
                target.evidence = audit_item.evidence.strip()[:220]
            continue
        target.status = ChecklistStatus.missing if audit_item.status == "missing" else ChecklistStatus.needs_review
        if audit_item.evidence.strip():
            target.evidence = audit_item.evidence.strip()[:220]
        if audit_item.question.strip():
            target.question = audit_item.question.strip()
            audit_questions.append(
                FollowUpQuestion(
                    field=label_by_key.get(audit_item.key, target.label),
                    question=audit_item.question.strip(),
                    reason=audit_item.reason.strip() or "AI 체크리스트 재검토에서 보완이 필요하다고 판단된 항목입니다.",
                )
            )
        if audit_item.reason.strip():
            audit_reviews.append(
                ReviewItem(
                    severity=ReviewSeverity.warning,
                    title=f"AI 체크리스트 검토: {target.label}",
                    description=audit_item.reason.strip(),
                    human_owner="변리업 종사자 또는 변리사",
                )
            )
    return checklist, audit_questions, audit_reviews


# 수치 효과 주장 감지 #
# 예: "10명 중 10명" 같은 표현은 증빙 확인 전까지 검토 필요로 돌립니다.
def _unverified_effect_claim(corpus: str) -> str:
    if not corpus:
        return ""
    normalized = corpus.lower()
    if not any(term in normalized for term in EFFECT_CLAIM_TERMS):
        return ""
    for pattern in QUANTIFIED_EFFECT_PATTERNS:
        match = pattern.search(corpus)
        if match:
            return " ".join(match.group(0).split())
    return ""


# 의미 있는 값 판단 #
# 너무 짧거나 "확인 필요" 같은 placeholder는 완료로 보지 않습니다.
def _is_meaningful(value: str) -> bool:
    normalized = re.sub(r"\s+", "", value or "")
    if normalized in WEAK_VALUES:
        return False
    if len(normalized) < 5:
        return False
    weak_phrases = ["확인필요", "작성필요", "자료확인", "검토필요", "검토가필요"]
    if any(phrase in normalized for phrase in weak_phrases):
        return False
    return True


# 부족 항목 -> 질문 변환 #
# 체크리스트 missing/needs_review 항목을 채팅 질문으로 바꿉니다.
def _merge_questions(
    questions: list[FollowUpQuestion],
    checklist: list[ChecklistItem],
) -> list[FollowUpQuestion]:
    existing = {question.field for question in questions}
    merged = list(questions)
    for item in checklist:
        if item.status in {ChecklistStatus.missing, ChecklistStatus.needs_review} and item.label not in existing:
            merged.append(
                FollowUpQuestion(
                    field=item.label,
                    question=item.question,
                    reason="출원명세서 필수항목 체크리스트에서 부족하거나 검토가 필요한 항목입니다.",
                )
            )
    return merged


# 기본 답변 생성 #
# LLM reply가 비었을 때 체크리스트 상태를 기준으로 간단 답변을 만듭니다.
def _build_reply(checklist: list[ChecklistItem], references: list[ReferenceItem]) -> str:
    missing = [item for item in checklist if item.status == ChecklistStatus.missing]
    needs_review = [item for item in checklist if item.status == ChecklistStatus.needs_review]
    if missing:
        top = ", ".join(item.label for item in missing[:4])
        return f"자료를 분석했습니다. 다만 {top} 항목이 부족해서 먼저 보완 질문을 드립니다. 부족한 부분은 체크리스트에 표시했습니다."
    if needs_review:
        top = ", ".join(item.label for item in needs_review[:3])
        return f"초안 작성은 가능하지만 {top} 항목은 사람 검토가 필요합니다. md와 Word 초안을 함께 만들었습니다."
    if references:
        return "자료와 참고자료를 반영해 출원명세서 검토용 초안을 작성했습니다. 마지막 체크리스트도 모두 확인했습니다."
    return "사용자 자료를 기준으로 출원명세서 검토용 초안을 작성했습니다. 참고자료는 추가로 연결하면 더 보강할 수 있습니다."


# 질문을 답변 하단에 표시 #
# 최대 6개까지 채팅창에 보여주고 나머지는 체크리스트에서 보게 합니다.
def _append_follow_up_questions(reply: str, questions: list[FollowUpQuestion]) -> str:
    if not questions:
        return reply
    lines = ["", "제가 먼저 확인하고 싶은 질문입니다."]
    for index, question in enumerate(questions[:6], 1):
        lines.append(f"{index}. {question.question}")
    if len(questions) > 6:
        lines.append(f"...외 {len(questions) - 6}개 항목은 오른쪽 체크리스트에 표시했습니다.")
    return f"{reply.rstrip()}\n" + "\n".join(lines)


# 안전한 구체화 후보 답변 #
# LLM이 주제별 후보를 동적으로 만들고, 실패하면 규칙 기반 후보로 대체함.
def _build_safe_ideation_reply(
    settings: Settings,
    message: str,
    corpus: str,
) -> tuple[str, list[FollowUpQuestion]]:
    ideation = _call_safe_ideation_llm(settings, message, corpus)
    if ideation and ideation.option_groups:
        reply = _format_safe_ideation_reply(ideation)
        questions = ideation.questions[:4] or _minimum_invention_questions(f"{corpus}\n{message}")
        return reply, questions
    return _fallback_safe_ideation_reply(message, corpus), _minimum_invention_questions(f"{corpus}\n{message}")


# 안전한 구체화 LLM 호출 #
# 본문 확정 없이 선택 후보와 확인 질문만 만들도록 구조화 출력으로 제한함.
def _call_safe_ideation_llm(
    settings: Settings,
    message: str,
    corpus: str,
) -> SafeIdeationOutput | None:
    if not settings.openai_api_key:
        return None
    source = f"{corpus}\n{message}".strip()
    if not source:
        return None

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """당신은 한국 특허 출원명세서 초안 작성 전 단계의 아이디어 구체화 Agent입니다.

목표:
- 사용자의 짧거나 모호한 아이디어를 발명 후보 선택지로 넓혀 줍니다.
- 단, 선택지를 실제 발명 사실처럼 확정하지 않습니다.
- 없는 실험값, 성능 수치, 도면부호, 선행문헌 번호, 구체 재질/치수는 만들지 않습니다.
- 타인의 발명 일부 변경, 표절, 권리회피 전략은 제안하지 않습니다.
- 사용자가 바로 고를 수 있게 발명 주제에 맞는 구체 후보를 만듭니다.

작성 기준:
- option_groups는 3~5개입니다.
- 각 group의 label은 '착용 구조 후보', '집기 구조 후보'처럼 주제 맞춤형으로 씁니다.
- 각 group의 options는 3~5개이며, 너무 일반적인 입력부/처리부/제어부 표현만 반복하지 않습니다.
- questions는 3~5개이며, 사용자가 답하면 명세서 필수항목을 채울 수 있어야 합니다.
- 답변은 한국어로 씁니다.
""",
            ),
            (
                "human",
                """사용자 아이디어와 누적 자료:
{source}
""",
            ),
        ]
    )
    try:
        llm = ChatOpenAI(
            model=settings.openai_model,
            temperature=0.3,
            api_key=settings.openai_api_key,
        ).with_structured_output(SafeIdeationOutput)
        return (prompt | llm).invoke({"source": source[-6000:]})
    except Exception:
        return None


# 안전한 후보 답변 포맷 #
# LLM 후보를 사용자에게 확정값이 아닌 선택지로 보여줌.
def _format_safe_ideation_reply(ideation: SafeIdeationOutput) -> str:
    lines = [
        "자료가 부족해서 제가 없는 구성을 명세서 본문에 확정해서 넣지는 않겠습니다.",
        "대신 지금 아이디어를 발명으로 좁혀 갈 수 있도록 선택 후보를 정리했습니다. 아래 내용은 확정 사실이 아니라 사용자가 고를 수 있는 후보입니다.",
    ]
    if ideation.invention_summary.strip():
        lines.extend(["", f"현재 이해한 방향: {ideation.invention_summary.strip()}"])
    lines.append("")
    for group in ideation.option_groups[:5]:
        label = group.label.strip() or "후보"
        options = [option.strip() for option in group.options if option.strip()]
        if not options:
            continue
        lines.append(f"- {label}: {', '.join(options[:5])}")
        if group.reason.strip():
            lines.append(f"  검토 이유: {group.reason.strip()}")
    lines.extend(
        [
            "",
            "위 후보 중 실제 의도와 맞는 것을 골라 주면, 선택한 범위 안에서만 초안을 구체화하겠습니다.",
        ]
    )
    return "\n".join(lines)


# 규칙 기반 구체화 후보 답변 #
# LLM 호출이 실패했을 때만 쓰는 fallback임.
def _fallback_safe_ideation_reply(message: str, corpus: str) -> str:
    topic = _topic_key(f"{corpus}\n{message}")
    options = {
        "ergonomic_chair": [
            "감지부 후보: 압력센서, 하중센서, 굴곡센서, 기울기센서 중 선택",
            "조절부 후보: 요추 지지패드 이동, 좌판 틸트 조절, 등받이 각도 조절, 높이 조절 중 선택",
            "구동부 후보: 전동모터, 공압/유압 실린더, 탄성 복귀 구조 중 선택",
            "알림 후보: 진동, LED, 앱 알림, 소리 알림 중 선택",
        ],
        "flying_car": [
            "비행부 후보: 접이식 로터, 덕트팬, 접이식 날개, 틸트 추진부 중 선택",
            "전환부 후보: 팬 수납/전개 링크, 주행-비행 모드 전환 제어, 잠금 확인 센서 중 선택",
            "안전부 후보: 충돌 감지, 비상 착륙 제어, 추락 완화 장치, 이륙 제한 조건 중 선택",
            "동력부 후보: 배터리 전기식, 하이브리드, 별도 비행 전원 중 선택",
        ],
        "food": [
            "층 구조 후보: 외피층, 중간 차단층, 내용물층, 향미층 중 선택",
            "재료 후보: 전분계 필름, 젤라틴계 필름, 알지네이트계 필름 등 검토 후보로 선택",
            "제조 후보: 성형, 압착, 열압착, 냉각, 포장 공정 중 선택",
            "효과 후보: 식감 유지, 수분 이동 억제, 보관 편의성 같은 정성 효과 중 선택",
        ],
        "baby_chair": [
            "받침 구조 후보: 슬라이드식 받침시트, 롤식 커버, 힌지식 보조받침 중 선택",
            "접힘 구조 후보: 핀 결합, 회전축, 래치, 스토퍼 중 선택",
            "안전 구조 후보: 끼임 방지, 전도 방지, 잠금 확인부, 모서리 라운딩 중 선택",
            "사용 효과 후보: 세척 편의, 보관 편의, 받침면 보호 같은 정성 효과 중 선택",
        ],
        "ai_software": [
            "입력 데이터 후보: 문서, 이미지, 로그, 사용자 대화, 센서 데이터 중 선택",
            "처리부 후보: 전처리, 임베딩, 검색, 분류, 생성, 검증 모듈 중 선택",
            "출력 후보: 보고서, 추천 결과, 경고, 자동 분류값, 초안 문서 중 선택",
            "기술 효과 후보: 처리 시간 단축, 누락 탐지, 일관성 향상, 검토 비용 절감 중 선택",
        ],
        "gaming_chopsticks": [
            "착용 구조 후보: 손가락 고정부, 손등 밴드, 손목 밴드, 컨트롤러 간섭 방지 클립 중 선택",
            "집기 구조 후보: 젓가락형 두 갈래 집게, 핀셋형 집게, 탄성 복귀 집게, 교체형 팁 중 선택",
            "작동 후보: 손가락 굽힘 연동, 레버 조작, 버튼 조작, 탄성 복귀식 개폐 중 선택",
            "위생/편의 후보: 탈착 세척, 미끄럼 방지, 과자 부스러기 낙하 방지, 한손 사용 중 선택",
        ],
        "generic": [
            "구성요소 후보: 입력부, 처리부, 제어부, 출력부, 고정부, 감지부 중 필요한 것을 선택",
            "작동 후보: 감지 -> 판단 -> 제어 -> 출력 순서로 흐름을 선택",
            "효과 후보: 편의성, 안전성, 정확성, 유지관리성, 비용 절감 중 실제 목적에 맞는 것을 선택",
            "검토 후보: 수치, 실험값, 도면부호, 선행문헌 번호는 사용자가 확인해야 함",
        ],
    }
    lines = [
        "자료가 부족해서 제가 없는 구성을 명세서 본문에 확정해서 넣지는 않겠습니다.",
        "다만 아이디어를 구체화할 수 있도록 아래처럼 선택 후보를 제안할 수 있습니다. 아래 내용은 발명 사실이 아니라 사용자가 고를 수 있는 후보입니다.",
        "",
    ]
    lines.extend(f"- {item}" for item in options.get(topic, options["generic"]))
    lines.extend(
        [
            "",
            "위 후보 중 실제로 의도한 것 2~3개만 골라 주면, 그 선택 범위 안에서 초안을 더 구체적으로 작성하겠습니다.",
        ]
    )
    return "\n".join(lines)


# 최소 보완 질문 #
# 개념만 있고 작성 위임이 들어온 경우 반복 질문 대신 핵심 4개만 묻습니다.
def _minimum_invention_questions(corpus: str) -> list[FollowUpQuestion]:
    topic = _topic_key(corpus)
    if topic == "ergonomic_chair":
        return [
            FollowUpQuestion(field="감지 방식", question="자세나 하중을 어떤 센서로 감지할까요? 예: 압력센서, 굴곡센서, 기울기센서", reason="감지부가 발명의 핵심 구성입니다."),
            FollowUpQuestion(field="조절 구조", question="의자의 어느 부분을 움직이나요? 예: 요추 지지부, 좌판, 등받이, 높이 조절부", reason="해결수단과 실시예를 쓰기 위한 정보입니다."),
            FollowUpQuestion(field="제어/알림", question="감지 후 자동 조절만 하나요, 사용자 알림도 하나요?", reason="작동 흐름을 구체화하기 위한 정보입니다."),
            FollowUpQuestion(field="차별점", question="일반 인체공학 의자와 다르게 강조하고 싶은 점은 무엇인가요?", reason="선행기술 대비 차이 검토가 필요합니다."),
        ]
    if topic == "flying_car":
        return [
            FollowUpQuestion(field="비행 방식", question="자동차가 하늘을 나는 핵심 방식은 무엇인가요? 예: 로터, 접이식 날개, 덕트팬", reason="발명의 핵심 구성요소입니다."),
            FollowUpQuestion(field="전환 구조", question="도로 주행 상태에서 비행 상태로 전환되는 구조나 순서를 알려 주세요.", reason="작동 방식과 실시예를 쓰기 위한 정보입니다."),
            FollowUpQuestion(field="안전장치", question="추락 방지, 비상착륙, 충돌 회피, 이륙 제한 같은 안전장치가 있나요?", reason="위험한 기술분야에서는 안전구성이 중요합니다."),
            FollowUpQuestion(field="차별점", question="기존 자동차, 드론, 항공기와 비교해 가장 다르게 만들고 싶은 점은 무엇인가요?", reason="선행기술 대비 차이를 검토해야 합니다."),
        ]
    if topic == "food":
        return [
            FollowUpQuestion(field="식품 구조", question="식품은 몇 개의 층 또는 부분으로 구성되나요?", reason="구성요소를 구체화해야 합니다."),
            FollowUpQuestion(field="재료", question="각 층 또는 부분의 재료 후보가 있나요?", reason="없는 재료를 본문에 확정할 수 없습니다."),
            FollowUpQuestion(field="제조 방식", question="성형, 압착, 가열, 냉각, 포장 중 어떤 공정이 들어가나요?", reason="실시예 작성에 필요합니다."),
            FollowUpQuestion(field="효과", question="유지하려는 식감, 보관성, 편의성 중 무엇이 핵심 효과인가요?", reason="효과를 과장하지 않기 위한 확인입니다."),
        ]
    if topic == "gaming_chopsticks":
        return [
            FollowUpQuestion(field="착용 위치", question="손가락, 손등, 손목 중 어디에 착용하는 구조인가요?", reason="착용부와 고정부를 특정하기 위한 정보입니다."),
            FollowUpQuestion(field="집기 방식", question="과자를 집는 부분은 젓가락형 두 갈래, 집게형, 핀셋형 중 어느 쪽에 가깝나요?", reason="핵심 구성요소와 작동 방식을 쓰기 위한 정보입니다."),
            FollowUpQuestion(field="조작 방식", question="손가락 굽힘, 레버, 버튼, 탄성 복귀 중 어떤 방식으로 열고 닫나요?", reason="실시예 작성에 필요합니다."),
            FollowUpQuestion(field="차별점", question="기존 젓가락, 집게, 손가락 장갑형 간식 도구와 다르게 만들고 싶은 점은 무엇인가요?", reason="선행기술 대비 차이를 검토해야 합니다."),
        ]
    return [
        FollowUpQuestion(field="발명 목적", question="이 발명으로 해결하려는 가장 큰 불편이나 문제는 무엇인가요?", reason="해결 과제를 쓰기 위한 최소 정보입니다."),
        FollowUpQuestion(field="핵심 구성", question="꼭 들어가야 하는 구성요소 2~3개를 골라 주세요.", reason="구성요소를 임의로 만들 수 없기 때문입니다."),
        FollowUpQuestion(field="작동 흐름", question="사용자가 쓰면 어떤 순서로 작동하나요?", reason="실시예 작성에 필요합니다."),
        FollowUpQuestion(field="차별점", question="기존 제품 또는 방식과 다르게 만들고 싶은 점은 무엇인가요?", reason="선행기술 대비 차이를 검토해야 합니다."),
    ]


# 발명 주제 분류 #
# 짧은 아이디어에서도 질문과 후보를 주제에 맞게 바꾸기 위한 단순 분류임.
def _topic_key(text: str) -> str:
    normalized = re.sub(r"\s+", "", text or "").lower()
    if any(term in normalized for term in ["게이밍", "게임", "젓가락", "과자", "간식", "컨트롤러", "착용형집게"]):
        return "gaming_chopsticks"
    if any(term in normalized for term in ["유아", "아기", "받침", "시트", "접이식"]):
        return "baby_chair"
    if any(term in normalized for term in ["인체공학", "의자", "자세", "요추", "좌판", "등받이"]):
        return "ergonomic_chair"
    if any(term in normalized for term in ["하늘", "비행", "자동차", "로터", "덕트팬", "날개"]):
        return "flying_car"
    if any(term in normalized for term in ["식품", "음식", "디저트", "성형", "식감", "포장"]):
        return "food"
    if any(term in normalized for term in ["ai", "인공지능", "소프트웨어", "모델", "데이터", "알고리즘"]):
        return "ai_software"
    return "generic"


# 사용자 메시지 자료성 판단 #
# 명령만 있는 문장은 대화에는 저장하되 RAG corpus와 벡터 DB에는 넣지 않음.
def _message_has_material_content(message: str) -> bool:
    normalized = re.sub(r"\s+", "", message or "").lower()
    if not normalized:
        return False
    command_only_patterns = [
        "너가해줘",
        "네가해줘",
        "니가해줘",
        "너가해",
        "네가해",
        "알아서해줘",
        "알아서해",
        "해줘",
        "해줘봐",
        "해줘바",
        "작성해줘",
        "초안작성해줘",
        "만들어줘",
    ]
    if normalized in command_only_patterns:
        return False
    material_terms = [
        "발명",
        "특허",
        "자동차",
        "의자",
        "장치",
        "시스템",
        "방법",
        "구성",
        "센서",
        "모터",
        "제어",
        "비행",
        "작동",
        "효과",
        "문제",
        "해결",
        "게임",
        "젓가락",
        "과자",
        "간식",
        "착용",
        "집게",
        "유아",
        "받침",
        "시트",
        "접이식",
        "슬라이드",
    ]
    return len(normalized) >= 8 or any(term in normalized for term in material_terms)


# 자료용 메시지 정리 #
# "하늘을 나는 자동차로 너가 해줘"에서 작성 명령은 빼고 발명 내용만 RAG 자료로 남김.
def _material_message_text(message: str) -> str:
    if not _message_has_material_content(message):
        return ""
    cleaned = message.strip()
    command_patterns = [
        r"\s*너\s*가\s*해\s*줘\s*",
        r"\s*네\s*가\s*해\s*줘\s*",
        r"\s*니\s*가\s*해\s*줘\s*",
        r"\s*너가해줘\s*",
        r"\s*네가해줘\s*",
        r"\s*니가해줘\s*",
        r"\s*알아서\s*해\s*줘\s*",
        r"\s*초안\s*작성\s*해\s*줘\s*",
        r"\s*작성\s*해\s*줘\s*",
        r"\s*만들어\s*줘\s*",
    ]
    for pattern in command_patterns:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.!?;:：")
    return cleaned if _message_has_material_content(cleaned) else ""


# 근거 부족 위임 판단 #
# 개념 수준 자료만 있는 상태에서 "너가 해줘"가 들어오면 LLM 생성으로 넘기지 않음.
def _is_weak_delegation_request(message: str, corpus: str) -> bool:
    normalized_message = re.sub(r"\s+", "", message or "").lower()
    delegation_terms = [
        "너가해줘",
        "네가해줘",
        "니가해줘",
        "너가해",
        "네가해",
        "니가해",
        "알아서해줘",
        "알아서해",
        "알아서작성",
        "다채워",
        "다채줘",
        "모두채워",
        "전부채워",
        "기반으로해줘",
        "바탕으로해줘",
        "기반으로만들어줘",
        "바탕으로만들어줘",
        "초안만들어줘",
        "초안작성해줘",
        "작성해줘",
        "만들어줘",
    ]
    if not any(term in normalized_message for term in delegation_terms):
        return False
    return _invention_detail_score(corpus) < 2


# 발명 상세도 점수 #
# 목적/문제, 구성요소, 작동 방식, 효과/차별점 중 몇 종류가 있는지 대략 판단함.
def _invention_detail_score(corpus: str) -> int:
    normalized = re.sub(r"\s+", "", corpus or "").lower()
    groups = [
        ["목적", "문제", "불편", "과제", "해결하려는", "못먹", "못 먹"],
        [
            "구성",
            "구성요소",
            "부품",
            "센서",
            "모터",
            "로터",
            "프로펠러",
            "날개",
            "배터리",
            "제어부",
            "젓가락",
            "집게",
            "고정부",
            "착용",
            "받침시트",
            "슬라이드",
            "레일",
            "핀",
            "잠금부",
            "보조받침부",
        ],
        ["작동", "동작", "전환", "제어", "순서", "실시예", "주행", "비행", "사용", "착용", "인출", "수납", "접힘", "슬라이드", "먹"],
        ["효과", "장점", "차별", "개선", "안전", "감소", "증가", "편의", "위생", "보관", "세척", "달라"],
    ]
    return sum(1 for terms in groups if any(term in normalized for term in terms))


# 누적 자료 합치기 #
# state.json의 최근 material_texts를 하나의 corpus로 합칩니다.
def _build_corpus(state: dict) -> str:
    parts = []
    for text in state.get("material_texts", [])[-24:]:
        if text and text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


# RAG 검색 질의 만들기 #
# 최신 메시지를 우선 query로 쓰고, 없으면 누적 자료 끝부분을 사용합니다.
def _query_text(message: str, corpus: str) -> str:
    if message.strip():
        return message[:1200]
    return corpus[-1200:]


# KIPRIS 요청 감지 #
# 사용자가 선행기술/유사특허 검색을 요구했는지 판단함.
def _message_requests_kipris(message: str) -> bool:
    normalized = re.sub(r"\s+", "", message or "").lower()
    terms = [
        "선행기술",
        "선행문헌",
        "유사특허",
        "비슷한특허",
        "유사한발명",
        "국내특허",
        "kipris",
        "키프리스",
        "공개번호",
        "등록번호",
    ]
    return any(term in normalized for term in terms)


# KIPRIS 일반 명령 판단 #
# "선행기술 찾아줘"처럼 발명 내용 없는 검색 명령인지 판단함.
def _is_generic_kipris_request(message: str) -> bool:
    normalized = re.sub(r"\s+", "", message or "").lower()
    generic_terms = [
        "선행기술찾아줘",
        "선행기술검색",
        "선행문헌찾아줘",
        "유사특허찾아줘",
        "비슷한특허찾아줘",
        "국내특허찾아줘",
        "kipris찾아줘",
        "키프리스찾아줘",
    ]
    return any(term in normalized for term in generic_terms)


# KIPRIS 검색 원문 선택 #
# 짧은 검색 명령이면 최신 메시지가 아니라 누적 발명 자료를 검색 원문으로 사용함.
def _kipris_query_source(message: str, query: str, corpus: str) -> str:
    if _is_generic_kipris_request(message):
        return corpus
    if _message_requests_kipris(message):
        return f"{message}\n{corpus[-3000:]}"
    return query or corpus


# KIPRIS 답변 보강 #
# 선행기술 요청 시 실제 API 후보 또는 API 상태를 채팅 답변에 명확히 추가함.
def _build_kipris_reply_note(
    settings: Settings,
    message: str,
    candidates: list[PriorArtCandidate],
) -> str:
    if not _message_requests_kipris(message):
        return ""
    if candidates:
        lines = ["KIPRISPlus 자동 검색 후보를 확인했습니다. 최종 신규성/진보성 판단은 사람 검토가 필요합니다."]
        for index, candidate in enumerate(candidates[:3], 1):
            number = candidate.publication_number or candidate.application_number or candidate.registration_number or "번호 확인 필요"
            title = candidate.title or "제목 확인 필요"
            lines.append(
                f"{index}. {title} / {number} / 자동 유사도 {candidate.similarity_score}% / 검토 주의도 {candidate.risk_level}"
            )
        return "\n".join(lines)
    if kipris_is_configured(settings):
        return (
            "KIPRISPlus를 누적 발명 자료 기준으로 호출했지만 후보가 없거나 API가 빈 결과를 반환했습니다. "
            "검색어를 더 구체화하거나 KIPRISPlus 서비스 권한/응답 상태를 확인할 필요가 있습니다."
        )
    return "KIPRISPlus 검색 설정이 꺼져 있어 실제 선행기술 후보를 불러오지 못했습니다. KIPRIS_SEARCH_ENABLED와 KIPRIS_API_KEY 설정이 필요합니다."


# 너무 짧은 입력 방지 #
# 자료가 짧으면 RAG/LLM을 억지로 돌리지 않고 보완 질문을 합니다.
def _corpus_is_too_weak(corpus: str) -> bool:
    normalized = re.sub(r"\s+", "", corpus or "")
    if len(normalized) < 30:
        return True
    if len(set(normalized)) <= 4 and len(normalized) < 120:
        return True
    return False


# 차단 응답 #
# Guardrail에 걸리면 초안 생성 없이 blocked 응답만 반환합니다.
def _blocked_response(
    session_id: str,
    case_name: str,
    blocked_reason: str,
    steps: list[AgentStep],
) -> AgentResponse:
    steps[0].status = StepStatus.blocked
    steps[0].detail = blocked_reason
    review_items = [
        ReviewItem(
            severity=ReviewSeverity.blocked,
            title="Guardrail 차단",
            description=blocked_reason,
            human_owner="사용자",
        )
    ]
    return AgentResponse(
        session_id=session_id,
        case_name=case_name,
        reply=f"이 요청은 SPEC Agent의 수행 범위를 벗어나서 진행하지 않습니다. {blocked_reason}",
        sections=SpecSections(),
        follow_up_questions=[],
        missing_materials=[],
        review_items=review_items,
        references=[],
        prior_art_candidates=[],
        checklist=[
            ChecklistItem(
                key="guardrail",
                label="수행 가능 범위",
                status=ChecklistStatus.blocked,
                evidence=blocked_reason,
            )
        ],
        materials=[],
        steps=steps,
        messages=[],
        markdown="",
        blocked=True,
        blocked_reason=blocked_reason,
    )


# 참고자료 중복 제거 #
# 같은 제목/출처/발췌는 한 번만 오른쪽 근거 자료에 보여줍니다.
def _dedupe_references(references: list[ReferenceItem]) -> list[ReferenceItem]:
    seen = set()
    deduped = []
    for item in references:
        key = (item.title, item.source, item.excerpt[:80])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:8]


# 오류 메시지 안전 처리 #
# API 키 같은 민감정보가 예외 메시지에 섞여도 화면에 그대로 노출되지 않게 합니다.
def _safe_error(exc: Exception) -> str:
    message = str(exc)
    message = re.sub(r"sk-[A-Za-z0-9_-]+", "[redacted]", message)
    message = re.sub(r"sk-proj-[A-Za-z0-9_-]+", "[redacted]", message)
    return f"{type(exc).__name__}: {message[:260]}"
