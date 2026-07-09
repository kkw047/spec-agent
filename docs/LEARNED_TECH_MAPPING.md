# 수업 기술 적용 매핑

이 문서는 `kkw047/study_ai_agent`에서 배운 내용을 SPEC Agent에 어떻게 적용했는지 정리합니다. 기준은 “이름만 붙인 구현”이 아니라 실제 사용자 흐름에 영향을 주는 기능입니다.

## 적용 완료

| 수업 기술 | SPEC Agent 적용 위치 | 실제 역할 |
|---|---|---|
| Prompt / structured output | `backend/app/services/spec_agent.py` `_call_llm()` | 사용자 자료를 `SpecSections`, 추가 질문, 누락 자료, 검토 항목으로 구조화 |
| Output parser / Pydantic schema | `backend/app/models/schemas.py`, `AgentStructuredOutput` | LLM 응답을 자유 텍스트가 아니라 정해진 객체로 받음 |
| Document loader | `backend/app/services/materials.py`, `backend/app/services/rag.py` | PDF/DOCX/TXT/이미지/참고자료를 LangChain `Document`로 변환 |
| Text splitter | `backend/app/services/rag.py` `split_documents()` | 긴 자료를 900자 청크와 120자 overlap으로 분할 |
| Embedding | `backend/app/services/rag.py` `OpenAIEmbeddings` | 청크를 벡터로 변환 |
| Vector DB / pgVector | `backend/app/services/rag.py` `PGVector` | 세션 자료와 공용 참고자료를 PostgreSQL pgVector에 저장/검색 |
| Retriever / similarity search | `get_case_references()`, `get_references()` | 현재 사건 자료와 공용 참고자료에서 관련 청크 검색 |
| RAG | `spec_agent.py` `_rag_node()` | 검색된 근거를 LLM 구조화 단계에 함께 제공 |
| Multi-turn memory | `backend/app/services/session_store.py` | 세션별 대화, 업로드 자료, 추출 텍스트를 유지 |
| Guardrail | `backend/app/services/guardrails.py` | 허위 수치, 없는 도면, 특허성 확정, 자동 출원, 무관한 글쓰기 차단 |
| LangGraph StateGraph | `backend/app/services/spec_agent.py` `_compiled_agent_graph()` | 한 턴을 노드 상태 그래프로 실행 |
| Tool use | `spec_agent.py` 각 노드, `rag.py`, `kipris.py`, `exporter.py` | 파일 추출, DB 저장, RAG, KIPRIS, Word 출력 도구 호출 |
| Human-in-the-loop | `_build_checklist()`, `_merge_questions()`, `review_items` | 부족한 자료를 질문하고 최종 판단을 사람에게 남김 |
| Self-review | `_self_review_node()`, `_review_sections_against_evidence()` | LLM 결과를 원문/RAG/KIPRIS 근거와 대조해 의심 항목을 검토로 분리 |
| External API tool | `backend/app/services/kipris.py` | KIPRISPlus로 국내 특허·실용 선행기술 후보 검색 |
| Export tool | `backend/app/services/markdown.py`, `exporter.py` | Markdown/Word 산출물 생성 |

## 현재 LangGraph 노드

```text
guardrail_node
receive_node
vectorize_node
memory_node
rag_node
kipris_node
structure_node
self_review_node
checklist_node
export_node
```

핵심 분기:

- `guardrail_node`에서 차단되면 초안 생성 없이 종료합니다.
- 통과한 요청만 자료 추출, 벡터 저장, RAG, KIPRIS, LLM 구조화로 이동합니다.
- `self_review_node`는 LLM 결과가 원문에 없는 수치나 표현을 포함하는지 다시 확인합니다.
- `checklist_node`는 부족한 항목을 질문으로 바꾸어 Human-in-the-loop를 만듭니다.

## A2A / Handoff 해석

수업의 A2A/Handoff 개념은 “여러 전문 Agent가 역할을 나눠 처리하고 다음 Agent에게 넘긴다”는 구조입니다.

현재 SPEC Agent에서는 별도 서버 Agent 여러 개를 띄우지는 않았습니다. 대신 LangGraph 노드가 아래 전문 역할을 수행합니다.

| 전문 역할 | 현재 구현 |
|---|---|
| Intake Agent | `receive_node`, `memory_node` |
| Search Agent | `rag_node`, `kipris_node` |
| Draft Agent | `structure_node` |
| Review Agent | `self_review_node`, `checklist_node` |
| Export Agent | `export_node` |

발표 답변:

> “독립 서버 간 A2A 프로토콜을 구현한 것은 아니지만, 수업의 handoff 개념은 LangGraph 노드 역할 분리로 적용했습니다. 실제 서비스가 커지면 각 노드를 별도 Agent 서비스로 분리해 A2A로 확장할 수 있습니다.”

## MCP 해석

수업의 MCP 개념은 “모델이 사용할 수 있는 도구를 표준 인터페이스로 노출하는 것”입니다.

현재 SPEC Agent는 외부 MCP 서버를 띄우지는 않습니다. 대신 Agent 내부 도구가 명확히 분리되어 있습니다.

- `materials.py`: 파일 추출 도구
- `rag.py`: 벡터 저장/검색 도구
- `kipris.py`: KIPRISPlus 검색 도구
- `markdown.py`: Markdown 생성 도구
- `exporter.py`: Word 생성 도구
- `guardrails.py`: 요청 검증 도구

발표 답변:

> “현재는 FastAPI 내부 도구 호출 구조입니다. MCP 서버를 억지로 띄우지는 않았습니다. 다만 도구 경계가 분리되어 있어, 이후 `rag.search`, `kipris.search`, `draft.export_word` 같은 MCP tool로 노출하기 쉽습니다.”

## 일부러 넣지 않은 것

| 수업 기술 | 제외 이유 |
|---|---|
| Claude SDK | 현재 프로젝트는 OpenAI API 기준으로 설계됨 |
| vLLM 로컬 서버 | 별도 GPU/모델 서버가 필요해 로컬 발표 안정성과 맞지 않음 |
| 웹 전체 실시간 검색 | 검색한 척하는 위험이 있어 KIPRISPlus만 허용 |
| 자동 특허성 확률 판단 | 법률 판단으로 오해될 수 있어 자동 유사도와 검토 주의도로 제한 |
| 자동 도면 창작 | 사용자 자료에 없는 구성요소를 만들 위험이 있어 차단 |

## 발표용 한 문장

SPEC Agent는 RAG와 pgVector만 붙인 문서 생성기가 아니라, LangGraph 상태 그래프가 Guardrail, 파일 파서, 세션 메모리, 벡터 검색, KIPRISPlus 검색, LLM 구조화, 자기검토, Human-in-the-loop 체크리스트, Word 출력 도구를 순서와 조건에 따라 호출하는 특허명세서 초안 Agent입니다.
