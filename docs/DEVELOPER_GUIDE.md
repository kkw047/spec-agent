# SPEC Agent 개발자 가이드

이 문서는 신규 개발자가 `spec-agent` 폴더를 열었을 때 어디에 무엇이 있고, 어떤 값을 어디에 추가해야 하는지 바로 이해하기 위한 문서입니다.

## 1. 프로젝트 목적

SPEC Agent는 특허 출원명세서 검토용 초안을 만드는 대화형 LLM Agent입니다.

사용자는 회의록, 아이디어 메모, 도면 설명, 상담 기록, PDF/DOCX/TXT 파일을 보냅니다. Agent는 자료를 읽고 필수항목을 구조화한 뒤, 부족한 내용을 채팅으로 질문하고, Markdown/Word 초안을 생성합니다.

중요 원칙:

- 자료에 없는 실험 수치, 임계값, 효과, 선행문헌 번호, 도면은 만들지 않습니다.
- 특허성 판단, 청구범위 확정, 자동 출원은 하지 않습니다.
- 체크리스트 `완료`는 “자료에서 해당 항목이 확인됨”이라는 뜻입니다. 법적 검토 완료가 아닙니다.

## 2. 실행 명령

최상위 폴더에서 실행합니다.

```powershell
npm run dev:backend
npm run dev
```

검증:

```powershell
npm run lint
npm run build
npm run check:backend
```

## 3. 최상위 파일

### `.env.example`

환경변수 예시 파일입니다. 실제 비밀값은 `.env`에 넣습니다.

주요 값:

- `OPENAI_API_KEY`: OpenAI API 키
- `OPENAI_MODEL`: 구조화 답변 생성 모델
- `OPENAI_EMBEDDING_MODEL`: 벡터 검색용 임베딩 모델
- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`: PostgreSQL 접속 정보
- `PGVECTOR_COLLECTION`: 공용 참고자료 컬렉션 이름
- `REFERENCE_SOURCE_DIR`: 참고자료 원본 폴더
- `DRAFT_OUTPUT_DIR`: 산출물 저장 폴더

값을 추가하려면:

- 새 환경변수가 필요하면 `.env.example`에 이름을 추가합니다.
- 실제 값은 `.env`에만 넣습니다.
- Python에서 읽으려면 `backend/app/core/config.py`의 `Settings` 클래스에 필드를 추가합니다.

### `.gitignore`

Git에 올리지 않을 파일을 정합니다.

보통 제외해야 하는 것:

- `.env`
- `local_data/`
- `backend/.venv/`
- `frontend/node_modules/`
- `frontend/dist/`

새로 생성되는 비밀 파일이나 대용량 산출물이 있으면 여기에 추가합니다.

### `package.json`

최상위 실행 스크립트입니다.

스크립트:

- `npm run dev`: 프론트엔드 실행
- `npm run dev:backend`: 백엔드 실행
- `npm run build`: 프론트엔드 빌드
- `npm run lint`: 프론트엔드 lint
- `npm run check:backend`: 백엔드 문법 검사

새 개발 명령을 만들려면 `scripts`에 추가합니다.

### `README.md`

프로젝트 소개와 실행 방법입니다.

사용자 또는 발표자가 먼저 보는 문서입니다. 개발 내부 상세는 이 파일보다 `docs/DEVELOPER_GUIDE.md`, `docs/AGENT_FLOW.md`에 적습니다.

## 4. 백엔드 구조

백엔드는 FastAPI 기반입니다.

```text
backend/
|-- app/
|   |-- main.py
|   |-- core/config.py
|   |-- models/schemas.py
|   `-- services/
|       |-- spec_agent.py
|       |-- guardrails.py
|       |-- materials.py
|       |-- rag.py
|       |-- session_store.py
|       |-- markdown.py
|       `-- exporter.py
|-- scripts/
|   |-- ingest_references.py
|   `-- copy_user_references.ps1
|-- templates/명세서_양식.docx
`-- requirements.txt
```

### `backend/requirements.txt`

백엔드 Python 의존성입니다.

주요 라이브러리:

- `fastapi`, `uvicorn`: API 서버
- `langchain-openai`: OpenAI LLM/임베딩 호출
- `langchain-postgres`: pgVector 연동
- `psycopg`: PostgreSQL 연결
- `python-docx`: Word 생성
- `pypdf`, `PyMuPDF`: PDF 텍스트 추출/OCR 보조
- `beautifulsoup4`, `requests`: 특허로 안내 페이지 수집

새 백엔드 라이브러리를 추가하면 이 파일에 버전을 고정합니다.

### `backend/app/main.py`

FastAPI 진입점입니다.

API:

- `GET /api/health`: OpenAI/DB 설정 확인
- `POST /api/agent/message`: 채팅형 Agent 실행
- `POST /api/drafts`: 예전 폼 방식 호환
- `POST /api/references/ingest`: 참고자료를 pgVector에 인덱싱
- `GET /api/files/{session_id}/{filename}`: 세션별 생성 md/docx 다운로드
- `GET /api/files/{filename}`: 구 다운로드 경로이며 보안상 차단

수정 위치:

- 새 API를 만들려면 이 파일에 route를 추가합니다.
- 요청 필드를 늘리려면 `agent_message()`의 `Form(...)` 인자를 추가하고 `run_agent_turn()`에 넘깁니다.
- 참고자료 인덱싱 형식을 바꾸려면 이 파일이 아니라 `services/rag.py`의 `load_reference_file_documents()`를 수정합니다.
- 다운로드 방식을 바꾸려면 `get_output_file()`의 세션/파일명 검증을 같이 수정합니다.

### `backend/app/core/config.py`

환경변수 설정 파일입니다.

중요 클래스:

- `Settings`: 프로젝트 루트의 `.env` 값을 읽는 설정 객체
- `sqlalchemy_database_url`: LangChain PGVector용 DB URL 생성
- `psycopg_params`: 직접 psycopg 연결용 dict 생성
- `resolved_reference_dir`: 참고자료 폴더 경로
- `resolved_output_dir`: 산출물 폴더 경로
- `kipris_api_base_url`: KIPRISPlus REST API 서비스 루트

값을 추가하려면:

1. `.env.example`에 환경변수 이름을 추가합니다.
2. `Settings` 클래스에 `Field(..., alias="환경변수명")`을 추가합니다.
3. 다른 파일에서는 `settings.필드명`으로 사용합니다.

예:

```python
max_reference_results: int = Field(default=8, alias="MAX_REFERENCE_RESULTS")
```

### `backend/app/models/schemas.py`

API 데이터 구조입니다.

주요 모델:

- `SpecSections`: 명세서 섹션
- `FollowUpQuestion`: 부족한 항목 질문
- `ReviewItem`: 사람 검토 항목
- `ReferenceItem`: 근거자료 검색 결과
- `PriorArtCandidate`: KIPRISPlus 선행기술 자동 검색 후보
- `ChecklistItem`: 필수항목 체크리스트
- `AgentStep`: 왼쪽 처리 흐름
- `AgentResponse`: `/api/agent/message` 응답 전체

새 필드를 추가하려면:

- 응답에 새 값을 내려야 하면 `AgentResponse`에 필드를 추가합니다.
- 명세서 항목을 늘리려면 `SpecSections`에 필드를 추가하고, `spec_agent.py`, `markdown.py`, `exporter.py`에도 연결합니다.
- 체크리스트 상태를 늘리려면 `ChecklistStatus` enum에 값을 추가하고, 프론트 `statusLabel`과 CSS도 추가합니다.

### `backend/app/services/guardrails.py`

수행 금지 요청을 차단합니다.

현재 차단 범위:

- 가짜 실험자료 생성
- 허위 수치 작성
- 임계값을 Agent가 임의로 정하는 요청
- 자료에 없는 도면/도안 생성 요청
- “몰라, 알아서 필수항목 다 채워”처럼 부족한 체크리스트를 근거 없이 완료시키라는 요청
- 실시간 논문/일반 인터넷 검색 요청
- “너가 알아서 해석”, “당연한 소리”처럼 근거 없이 기술 내용을 확정하라는 요청
- 재질, 성분, 구조, 공정조건을 Agent 추천값으로 확정하라는 요청
- 특허성 판단 확정
- 청구범위 확정
- 자동 출원
- 특허 외 글쓰기/맞춤법 검사
- 무관한 수학/과학 문제풀이

추가할 수 있는 변수:

- `PATENT_CONTEXT_TERMS`: 특허 관련 문맥으로 인정할 단어
- `FALSE_CONTENT_PATTERNS`: 허위 내용 생성 요청 패턴
- `FINAL_JUDGMENT_PATTERNS`: 전문가 판단/출원 대행 요청 패턴
- `NON_PATENT_WRITING_PATTERNS`: 특허 외 글쓰기 요청 패턴
- `COMPLEX_PROBLEM_PATTERNS`: 특허 외 문제풀이 요청 패턴
- `FABRICATION_REQUEST_TERMS`: “네가 정해”, “대충 만들어” 같은 생성 강요 표현
- `UNCERTAIN_DELEGATION_TERMS`: “몰라”, “알아서”, “대충”처럼 근거 없는 위임 표현
- `REQUIRED_FILL_TERMS`: “필수항목”, “체크리스트”, “누락항목” 같은 체크리스트 대상 표현
- `FILL_ALL_TERMS`: “다 채워”, “완료로”, “통과시켜” 같은 완료 강요 표현
- `SENSITIVE_MISSING_CONTENT_TERMS`: 수치/실험/문헌번호처럼 근거가 필요한 항목
- `MISSING_DRAWING_PATTERNS`: 도면이 없는데 만들어 달라는 표현
- `KIPRIS_RESEARCH_PATTERNS`: KIPRISPlus 설정 시 허용할 국내 특허/선행기술 검색 표현
- `UNSUPPORTED_RESEARCH_PATTERNS`: 논문, 일반 인터넷 검색 요청 표현
- `UNSUPPORTED_INTERPRETATION_PATTERNS`: 근거 없는 해석/판단 강요 표현
- `RECOMMENDATION_AS_FACT_PATTERNS`: 추천값을 본문 확정값으로 넣으려는 표현
- `TECHNICAL_FACT_TERMS`: 재질, 성분, 구조, 두께, 온도, 압력 등 기술 사실 항목

새 차단 규칙을 넣으려면:

1. 단순 문구 차단이면 위 리스트 중 하나에 문구를 추가합니다.
2. 조합 조건이 필요하면 `_detect_fabrication_request()`, `_detect_unsupported_research_request()`, `_detect_unsupported_interpretation_request()`에 조건을 추가합니다.
3. 차단 사유 문구는 `detect_blocked_request()`의 반환값을 수정합니다.

예:

```python
FALSE_CONTENT_PATTERNS.append("존재하지 않는 논문")
```

### `backend/app/services/spec_agent.py`

Agent 핵심 흐름입니다.

주요 함수:

- `run_agent_turn()`: 한 번의 채팅 턴 전체 실행
- `_base_steps()`: 왼쪽 처리 흐름 단계 정의
- `_extract_materials()`: 업로드 자료 추출 호출
- `_call_llm()`: OpenAI 구조화 출력 호출
- `_fallback_sections()`: LLM 실패 시 라벨 기반 추출
- `_build_checklist()`: 필수항목 체크리스트 생성
- `_audit_checklist_with_llm()`: 규칙 기반 체크리스트를 AI Review Agent가 다시 엄격히 판정
- `_merge_questions()`: 부족한 체크리스트를 질문으로 변환
- `_append_follow_up_questions()`: 채팅 답변 끝에 질문 붙이기

수정 위치:

- 필수항목을 추가하려면 `REQUIRED_ITEMS`에 항목을 추가합니다.
- 체크리스트 기본 판단 기준을 바꾸려면 `_build_checklist()`와 `_is_meaningful()`을 수정합니다.
- 체크리스트가 너무 쉽게 완료되는 문제를 줄이려면 `_audit_checklist_with_llm()`의 system prompt와 `ChecklistAuditOutput` 구조를 수정합니다.
- 효과/실험 수치 주장을 검토 필요로 돌리는 기준은 `QUANTIFIED_EFFECT_PATTERNS`, `EFFECT_CLAIM_TERMS`, `_unverified_effect_claim()`을 수정합니다.
- LLM 프롬프트를 바꾸려면 `_call_llm()`의 system prompt를 수정합니다.
- 왼쪽 처리 단계명을 바꾸려면 `_base_steps()`를 수정합니다.
- fallback 라벨을 늘리려면 `_fallback_sections()`의 `_extract_labeled_value()` label 목록을 추가합니다.

주의:

- 사용자 자료에 없는 수치나 도면은 여기서 만들어 넣으면 안 됩니다.
- LLM이 실패해도 `fallback_sections()`가 최소 초안을 만들 수 있어야 합니다.

### `backend/app/services/materials.py`

업로드 파일을 텍스트로 변환합니다.

지원 형식:

- TXT, MD, CSV, JSON, LOG
- PDF
- DOCX
- PNG, JPG, JPEG, WEBP, BMP, TIF, TIFF

주요 함수:

- `safe_filename()`: 파일명 안전화
- `extract_upload()`: 업로드 저장 + 텍스트 추출 + 청크 생성
- `message_to_documents()`: 사용자 메시지를 벡터 저장용 문서로 변환
- `_extract_pdf()`, `_extract_docx()`, `_extract_image()`: 형식별 추출
- `_image_material_text()`: 이미지 파일을 도면 후보 자료로 설명하는 텍스트 생성

새 파일 형식을 지원하려면:

1. 확장자 집합을 추가합니다.
2. `extract_upload()`의 분기문에 처리 로직을 추가합니다.
3. 필요한 라이브러리는 `requirements.txt`에 추가합니다.

예:

```python
TEXT_SUFFIXES.add(".yaml")
```

이미지 처리:

- 이미지 파일은 `kind="image"`로 저장됩니다.
- OCR 텍스트가 없어도 “도면 또는 도안 후보로 업로드됨”이라는 자료 텍스트를 생성합니다.
- 현재 시스템은 이미지를 보고 구성요소와 부호를 자동 확정하지 않습니다.
- 이미지 시각 해석을 추가하려면 `_extract_image()`가 아니라 별도의 vision 모델 호출 단계를 `spec_agent.py` 앞쪽에 추가하는 편이 좋습니다.

### `backend/app/services/rag.py`

RAG와 DB 연결 파일입니다.

DB 연결:

- `ensure_vector_extension()`이 PostgreSQL에 `vector` 확장을 준비합니다.
- `add_documents_to_collection()`이 OpenAI 임베딩을 만들고 `PGVector.from_documents()`로 저장합니다.
- `get_references_from_collection()`이 `PGVector.similarity_search()`로 유사 문장을 검색합니다.

저장되는 컬렉션:

- 공용 참고자료: `.env`의 `PGVECTOR_COLLECTION`
- 사건별 자료: `{PGVECTOR_COLLECTION}_case_{session_id}`

검색 대상:

- 사용자가 올린 현재 사건 자료
- `local_data/references/`에 넣고 인덱싱한 공용 참고자료
- 일반 인터넷 실시간 검색은 하지 않습니다.
- KIPRISPlus 검색은 `services/kipris.py`에서 별도 수행합니다.

보안상 중요한 점:

- 사용자가 올린 자료는 세션별 컬렉션에 저장됩니다.
- 현재 구현은 같은 DB 안에 세션별 컬렉션을 분리하지만, 사용자 계정별 권한 분리는 아직 없습니다.
- 발표/수업용 단일 사용자 프로젝트라면 충분하지만, 실제 서비스라면 계정/조직별 DB 분리, 암호화, 보존기간 삭제 정책이 필요합니다.

수정 위치:

- 참고자료 파일 형식을 늘리려면 `load_reference_file_documents()`를 수정합니다.
- 특허로 페이지 잡음 제거는 `_clean_patent_guide_text()`, `_is_noisy_reference()`를 수정합니다.
- 검색 개수는 `spec_agent.py`에서 `get_case_references(..., k=5)`, `get_references(..., k=4)` 값을 바꿉니다.
- 청크 크기는 `split_documents()`의 `chunk_size`, `chunk_overlap`을 바꿉니다.

### `backend/app/services/kipris.py`

KIPRISPlus 국내 특허·실용 공개·등록공보 후보 검색 파일입니다.

주요 함수:

- `kipris_is_configured()`: `.env`의 KIPRIS 설정이 모두 있는지 확인합니다.
- `build_kipris_query()`: 누적 자료에서 너무 긴 문장을 줄여 검색어를 만듭니다.
- `search_kipris()`: KIPRISPlus REST API를 호출합니다.
- `_parse_candidates()`: XML 응답을 `PriorArtCandidate`로 바꿉니다.
- `_score_candidate()`: 제목/초록/IPC와 검색어 핵심어의 겹침으로 자동 유사도를 계산합니다.

수정 위치:

- 검색 결과 개수는 `.env`의 `KIPRIS_RESULT_COUNT`를 바꿉니다.
- 타임아웃은 `.env`의 `KIPRIS_TIMEOUT_SECONDS`를 바꿉니다.
- KIPRISPlus 상품 URL이 바뀌면 `.env`의 `KIPRIS_API_BASE_URL`을 바꿉니다.
- 유사도 산식은 `_score_candidate()`를 수정합니다.

주의:

- 자동 유사도는 특허성 확률이 아닙니다.
- 결과가 0건이면 키 권한, 상품 신청 상태, 검색어, KIPRISPlus API 상태를 확인해야 합니다.

### `backend/app/services/session_store.py`

대화 세션을 로컬 JSON으로 저장합니다.

저장 위치:

```text
local_data/sessions/{session_id}/state.json
local_data/sessions/{session_id}/uploads/
```

주요 함수:

- `normalize_session_id()`: 세션 ID 생성/검증
- `session_dir()`, `upload_dir()`, `state_path()`: 세션 경로 계산
- `append_user_turn()`: 사용자 메시지와 자료 저장
- `append_assistant_turn()`: Agent 답변 저장

수정 위치:

- 세션 보존기간을 넣으려면 이 파일에 삭제 로직을 추가합니다.
- 저장 필드를 늘리려면 `load_state()` 기본 dict와 append 함수들을 수정합니다.

### `backend/app/services/markdown.py`

Markdown 초안 생성 파일입니다. 다운로드 파일과 화면의 `초안` 탭에는 명세서 본문만 표시합니다.

수정 위치:

- Markdown 섹션 제목을 바꾸려면 `build_markdown()`을 수정합니다.

주의:

- 체크리스트, 검토 항목, KIPRIS 후보, 근거 자료는 Markdown 본문에 넣지 않고 웹 화면의 별도 탭에서 표시합니다.
- Word 생성은 별도 파일 `exporter.py`가 담당합니다.

### `backend/app/services/exporter.py`

Word와 Markdown 파일을 저장합니다.

주요 함수:

- `export_draft()`: 산출물 저장 전체
- `_fill_after_heading()`: Word 양식의 특정 제목 아래 내용을 채움
- `_claim_draft()`: 검토용 청구항 초안 생성
- `_xml_safe()`: Word XML에 들어갈 수 없는 제어문자 제거

수정 위치:

- Word 파일명 규칙은 `_safe_name()`을 수정합니다.
- Word 양식의 제목 매핑은 `fill_map`을 수정합니다.
- Word 템플릿을 바꾸려면 `backend/templates/명세서_양식.docx`를 교체합니다.

주의:

- `명세서_양식.docx` 안의 제목 텍스트와 `fill_map`의 key가 맞아야 자동 채움이 됩니다.
- Word 다운로드는 명세서 초안 본문만 포함합니다. 체크리스트와 검토 확인표는 웹 화면에서 확인합니다.

### `backend/scripts/ingest_references.py`

참고자료를 pgVector에 넣는 CLI 스크립트입니다.

사용:

```powershell
cd backend
.venv\Scripts\python.exe scripts\ingest_references.py
```

옵션:

- `--html-only`: 특허로 안내 페이지만 인덱싱
- `--no-reset`: 기존 컬렉션을 지우지 않고 추가

자료를 더 넣으려면:

1. `local_data/references/`에 PDF, TXT, MD, DOCX 등을 넣습니다.
2. 이 스크립트를 실행합니다.
3. `collection=spec_agent_public_references`가 출력되면 공용 참고자료가 갱신된 것입니다.

### `backend/scripts/copy_user_references.ps1`

다운로드 폴더에 있는 수업 PDF를 `local_data/references/`로 복사하는 보조 스크립트입니다.

새 PDF를 자동 복사 목록에 넣고 싶으면 `$files` 배열에 경로를 추가합니다.

### `backend/templates/명세서_양식.docx`

Word 출력 템플릿입니다.

수정 시 주의:

- `【발명의 명칭】`, `【기술분야】` 같은 제목 텍스트가 유지되어야 `exporter.py`가 내용을 채울 수 있습니다.
- 제목을 바꾸면 `exporter.py`의 `fill_map`도 같이 바꿔야 합니다.

## 5. 프론트엔드 구조

프론트엔드는 React + Vite입니다.

```text
frontend/
|-- index.html
|-- vite.config.js
|-- eslint.config.js
|-- package.json
`-- src/
    |-- main.jsx
    |-- App.jsx
    `-- styles.css
```

### `frontend/package.json`

프론트엔드 의존성과 스크립트입니다.

주요 의존성:

- `react`, `react-dom`: UI
- `vite`: 개발 서버/빌드
- `lucide-react`: 아이콘

새 UI 라이브러리를 추가하면 이 파일에 기록됩니다.

### `frontend/index.html`

Vite 진입 HTML입니다.

대부분 수정하지 않습니다. 앱은 `src/main.jsx`에서 마운트됩니다.

### `frontend/src/main.jsx`

React 앱 마운트 파일입니다.

역할:

- `App.jsx`를 `root`에 렌더링
- `styles.css` 로드

### `frontend/src/App.jsx`

전체 화면과 사용자 입력 흐름입니다.

주석 기준:

- `0. API 설정`
- `1. 사용자 입력 기본값`
- `2. 입력 페이지 상태`
- `3. 사용자 파일 입력 처리`
- `4. 사용자 입력 전송`
- `UI: 좌측 처리 흐름`
- `UI: 채팅 입력페이지`
- `UI: 오른쪽 결과/체크리스트`

주요 상태:

- `sessionId`: 현재 대화 세션
- `caseName`: 사건명
- `useRag`: 참고자료 보강 여부
- `message`: 입력창 내용
- `queuedFiles`: 전송 대기 파일
- `messages`: 채팅 말풍선
- `result`: Agent 응답 전체
- `activeTab`: 오른쪽 탭

수정 위치:

- 시작 안내 문구는 `starterMessage.content`를 수정합니다.
- 필수항목 기본 목록은 `defaultChecklist`를 수정합니다.
- 파일 전송은 `sendTurn()`의 `FormData` 구성을 수정합니다.
- 오른쪽 탭을 추가하려면 `activeTab` 버튼과 조건부 렌더링 블록을 추가합니다.
- 체크리스트 의미 안내 문구는 `checklist-note` 문단을 수정합니다.

### `frontend/src/styles.css`

화면 스타일입니다.

주석 기준:

- `UI: 색상 토큰`
- `UI: 기본 리셋`
- `UI: 전체 페이지 레이아웃`
- `UI: 좌측 처리 흐름`
- `UI: 공통 버튼`
- `UI: 중앙 채팅/오른쪽 결과 배치`
- `UI: 채팅 입력페이지`
- `UI: 사건명/상세 옵션`
- `UI: 상세 옵션 메뉴`
- `UI: 대화 메시지`
- `UI: 첨부파일 칩`
- `UI: 메시지 입력창`
- `UI: 오른쪽 결과/체크리스트`

수정 위치:

- 전체 색상은 `:root` 변수를 수정합니다.
- 좌측 패널은 `.side-panel`, `.step-list`, `.step`을 수정합니다.
- 채팅창 크기는 `.chat-panel`, `.message-list`, `.composer`를 수정합니다.
- 긴 파일명 처리는 `.file-chip`, `.file-chip span`, `.attachment-tray`를 수정합니다.
- 오른쪽 체크리스트는 `.summary-panel`, `.inline-checklist`, `.mini-check`를 수정합니다.
- 모바일 대응은 하단 `@media`를 수정합니다.

### `frontend/vite.config.js`

Vite 설정입니다.

수정 위치:

- 개발 서버 포트나 프록시 설정이 필요하면 이 파일을 수정합니다.

### `frontend/eslint.config.js`

ESLint 설정입니다.

수정 위치:

- lint rule을 추가하거나 완화하려면 이 파일을 수정합니다.

## 6. 자료와 DB 동작

### 사용자가 파일을 올리면

1. 프론트가 파일을 `/api/agent/message`로 보냅니다.
2. 백엔드가 파일을 `local_data/sessions/{session_id}/uploads/`에 저장합니다.
3. 텍스트를 추출합니다.
4. 텍스트를 청크로 나눕니다.
5. 청크를 OpenAI embedding으로 벡터화합니다.
6. PostgreSQL pgVector의 세션별 컬렉션에 저장합니다.
7. 같은 세션의 다음 질문에서 이 자료를 다시 검색할 수 있습니다.

### DB에 저장되는 것

- 파일에서 추출한 텍스트 청크
- 사용자 메시지 청크
- 파일명, source, title 같은 metadata
- 임베딩 벡터

### 인터넷 검색 여부

현재 일반 인터넷 검색은 하지 않습니다.

단, 참고자료 인덱싱을 실행할 때 특허로 안내 페이지 URL을 한 번 수집해 공용 참고자료로 저장할 수 있습니다.

KIPRISPlus는 일반 웹 검색이 아니라 공식 특허·실용 공개·등록공보 API 후보 검색입니다. `.env`에서 `KIPRIS_SEARCH_ENABLED=true`이고 API 키가 있으면 채팅 처리 중 `services/kipris.py`가 선행기술 후보를 조회합니다.

### 무엇과 비교하는가

RAG 검색은 두 곳에서 합니다.

1. 세션별 자료

   사용자가 현재 사건에서 올린 회의록, 메모, 도면 설명, 메시지입니다.

2. 공용 참고자료

   `local_data/references/`에 넣고 인덱싱한 PDF/TXT/DOCX와 특허로 안내 페이지입니다.

### 아이디어 유출/보안

현재 구현은 수업/발표용 단일 사용자 환경에 가깝습니다.

보안상 현재 상태:

- 자료는 로컬 `local_data/sessions/`와 PostgreSQL pgVector에 저장됩니다.
- 세션별 컬렉션으로 분리됩니다.
- 일반 인터넷 검색으로 자료를 보내지는 않습니다.
- KIPRISPlus에는 검색어가 전송됩니다.
- OpenAI API 호출과 embedding 생성에는 텍스트가 전송됩니다.

실서비스로 강화하려면:

- 사용자/조직별 DB 또는 컬렉션 권한 분리
- 세션별 자료 자동 삭제 정책
- pgVector 저장 텍스트 암호화 또는 민감정보 마스킹
- OpenAI API 전송 전 비밀정보 제거
- 관리자/타사용자 검색 권한 차단
- 로그에 원문 저장 금지

## 7. 체크리스트 의미

오른쪽 `8/10`은 “10개 필수항목 중 8개가 사용자 자료에서 확인됨”이라는 뜻입니다.
선행기술문헌은 일반 출원명세서의 필수 체크리스트에서 제외하고, KIPRISPlus 후보 검토 영역에서 별도로 다룹니다.

예:

- `완료`: 자료에서 해당 항목을 찾았고 초안에 반영할 수 있음
- `부족`: 자료가 없어 질문이 필요함
- `검토`: 자료는 있으나 도면부호, 근거, 안전성 등 사람 확인이 필요함
- `차단`: 가드레일에 걸린 요청

중요:

- `완료`는 특허 등록 가능성을 뜻하지 않습니다.
- `완료`는 청구범위 확정이 아닙니다.
- `완료`는 변리사 검토 완료가 아닙니다.

## 8. 발표용 데모 자료

위치:

```text
demo_materials/
```

폴더:

- `complete_case/`: 회전각 고정 레버 장치
- `food_shape_case/`: 새로운 음식 형태
- `ergonomic_chair_case/`: 인체공학 의자
- `ai_software_case/`: AI 소프트웨어 개발

`food_shape_case/01_회의녹취록.txt`만 올리면 도면 설명과 부호의 설명이 부족할 수 있습니다. 선행기술 키워드/문헌은 필수항목 점수에서 제외하고, 사용자가 “선행기술 찾아줘”라고 요청하거나 KIPRISPlus 설정이 켜져 있을 때 별도 후보로 보여줍니다.

## 9. 자주 바꾸는 요구사항별 수정 위치

가드레일을 추가하고 싶다:

- `backend/app/services/guardrails.py`

필수 체크리스트를 바꾸고 싶다:

- `backend/app/services/spec_agent.py`의 `REQUIRED_ITEMS`
- `frontend/src/App.jsx`의 `defaultChecklist`

명세서 섹션을 추가하고 싶다:

- `backend/app/models/schemas.py`의 `SpecSections`
- `backend/app/services/spec_agent.py`
- `backend/app/services/markdown.py`
- `backend/app/services/exporter.py`

참고자료 파일 형식을 늘리고 싶다:

- `backend/app/services/rag.py`의 `load_reference_file_documents()`

Word 양식을 바꾸고 싶다:

- `backend/templates/명세서_양식.docx`
- `backend/app/services/exporter.py`의 `fill_map`

화면 탭을 바꾸고 싶다:

- `frontend/src/App.jsx`의 `.tabs` 영역
- `frontend/src/styles.css`의 `.tabs`

긴 파일명 UI를 바꾸고 싶다:

- `frontend/src/styles.css`의 `.file-chip`, `.file-chip span`, `.attachment-tray`

이미지 도면 업로드 처리를 바꾸고 싶다:

- `backend/app/services/materials.py`의 `_image_material_text()`
- Word에 이미지 첨부 방식을 바꾸려면 `backend/app/services/exporter.py`의 `업로드 이미지 자료` 섹션
- 이미지 내용을 자동 판독하려면 `backend/app/services/spec_agent.py` 앞단에 vision 분석 단계를 새로 추가

DB 접속 정보를 바꾸고 싶다:

- `.env`
- 필요하면 `.env.example`
- `backend/app/core/config.py`

## 10. 현재 미구현 또는 다음 개선 후보

- 사용자 계정/조직별 권한 분리
- 세션 자동 삭제
- 체크리스트별 근거 문장 하이라이트
- 도면 파일 업로드 시 이미지 미리보기
- Word 템플릿 세부 서식 고도화
- 이미지 vision 해석 단계 추가
- 내부 도구를 MCP 서버로 외부 노출
- LangGraph 노드를 독립 Agent 서비스로 분리해 A2A/Handoff 확장
- 테스트 코드 정식 추가
