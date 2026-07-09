# SPEC Agent 처리 흐름

발표용 상세 흐름과 예상 질문은 `docs/PRESENTATION_MATERIAL.md`로 통합했습니다.

이 파일은 빠른 확인용 요약입니다.

## 핵심 흐름

```mermaid
flowchart TD
  A["사용자 메시지/파일"] --> B["Guardrail"]
  B -->|차단| C["Blocked 응답"]
  B -->|통과| D["자료 추출"]
  D --> E["세션 메모리"]
  D --> F["임베딩/pgVector 저장"]
  E --> G["RAG 검색"]
  G --> H["KIPRISPlus 후보 검색"]
  H --> I["LLM 구조화"]
  I --> J["자기검토"]
  J --> K["체크리스트/Human-in-the-loop"]
  K --> L["Markdown/Word 출력"]
```

## 실제 LangGraph 노드

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

## 발표 때 볼 문서

- `docs/PRESENTATION_MATERIAL.md`: 발표용 통합 설명, 구조 방어, 예상 질문 답변
- `docs/LEARNED_TECH_MAPPING.md`: 수업 기술이 실제 구현 어디에 들어갔는지 매핑
- `docs/API_INTEGRATION_GUIDE.md`: OpenAI, PostgreSQL/pgVector, KIPRISPlus 설정 방법
