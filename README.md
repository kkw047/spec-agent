# SPEC Agent

SPEC Agent는 한국 특허 출원명세서 검토용 초안을 만드는 대화형 LLM Agent다.

사용자는 회의록, 아이디어 메모, 도면 설명, 상담 기록, PDF/DOCX/TXT/이미지 파일을 업로드한다. Agent는 자료를 읽고 필수항목을 구조화한 뒤, 부족한 항목을 채팅으로 다시 묻고, Markdown/Word 초안과 체크리스트를 생성한다.

중요 원칙:

- 자료에 없는 실험 수치, 임계값, 효과, 선행문헌 번호, 도면은 생성하지 않음
- KIPRISPlus 후보는 자동 검색 후보이며, 최종 신규성/진보성/등록 가능성 판단이 아님
- 특허성 판단, 청구범위 확정, 자동 출원은 사람 검토 영역

## 로컬 실행

최상위 폴더에서 실행.

```powershell
cd C:\Users\KKW\Documents\계절학기\spec-agent
npm run dev:backend
npm run dev
```

접속 주소:

```text
프론트엔드: http://localhost:5173
백엔드 API: http://localhost:8000
상태 확인: http://localhost:8000/api/health
```

검증 명령:

```powershell
npm run check:backend
npm run lint
npm run build
```

## 최초 설치

```powershell
cd C:\Users\KKW\Documents\계절학기\spec-agent\backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

cd ..\frontend
npm install
```

## 환경변수

실제 값은 프로젝트 루트의 `.env`에 입력.

`.env.example`은 형식 예시다. 실제 API 키와 DB 비밀번호는 기록하지 않음.

필수 구성:

```env
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.4-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

POSTGRES_HOST=lab.studynest.kr
POSTGRES_PORT=45432
POSTGRES_USER=
POSTGRES_PASSWORD=
POSTGRES_DB=
PGVECTOR_COLLECTION=spec_agent_public_references

CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
PATENT_GUIDE_URL=https://www.patent.go.kr/smart/jsp/ka/menu/guide/main/GuideMain0208.do

KIPRIS_SEARCH_ENABLED=true
KIPRIS_API_KEY=
KIPRIS_API_BASE_URL=https://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice
KIPRIS_RESULT_COUNT=5
KIPRIS_TIMEOUT_SECONDS=12

REFERENCE_SOURCE_DIR=local_data/references
DRAFT_OUTPUT_DIR=local_data/outputs
```

## 폴더 구조

```text
backend/
  app/
    main.py                 FastAPI API 입구
    core/config.py          .env, DB, OpenAI, KIPRIS 설정
    models/schemas.py       API 요청/응답 데이터 구조
    services/
      spec_agent.py         Agent 핵심 처리 흐름
      guardrails.py         허위정보/범위초과 요청 차단
      materials.py          업로드 파일 추출
      rag.py                pgVector 저장/검색
      kipris.py             KIPRISPlus 선행기술 후보 검색
      session_store.py      세션 대화/자료 저장
      markdown.py           Markdown 산출물 생성
      exporter.py           Word 산출물 생성
  scripts/
    ingest_references.py    참고자료 pgVector 인덱싱
    copy_user_references.ps1 다운로드 폴더 자료 복사 보조
  templates/
    명세서_양식.docx         Word 출력 템플릿

frontend/
  src/App.jsx               채팅 UI, 체크리스트, 다운로드, 선행기술 후보 표시
  src/styles.css            화면 스타일
  src/main.jsx              React 진입점

docs/
  DEVELOPER_GUIDE.md        개발자 상세 가이드
  AGENT_FLOW.md             Agent 처리 흐름과 발표용 설명
  API_INTEGRATION_GUIDE.md  OpenAI/DB/KIPRIS API 연동 가이드
  LEARNED_TECH_MAPPING.md   수업에서 배운 기술과 실제 구현 위치
  PRESENTATION_MATERIAL.md  발표용 흐름/예상 질문 답변
```

## Git에서 제외되는 폴더

아래 항목은 로컬 실행 중 생성되거나 비밀값/대용량 자료가 들어가므로 Git 제외.

```text
.env
.env.local
backend/.venv/
frontend/node_modules/
frontend/dist/
local_data/
tmp/
*.log
```

## 데모 자료

발표 전 체험용 자료 위치: `demo_materials/`

```text
demo_materials/complete_case/
demo_materials/complete_flying_car_case/
demo_materials/complete_baby_chair_case/
demo_materials/food_shape_case/
demo_materials/ergonomic_chair_case/
demo_materials/ai_software_case/
```

사용 방법:

1. 프론트 화면에서 폴더 버튼 선택
2. 위 폴더 중 하나를 통째로 업로드
3. 아래 문장 전송

```text
첨부 자료를 모두 분석해서 출원명세서 검토용 초안을 작성해줘.
부족한 항목이 있으면 답장 안에서 먼저 질문하고, 오른쪽 체크리스트에도 표시해줘.
근거 없는 실험 결과나 수치는 만들지 말고, 자료에 있는 내용만 반영해줘.
```

## 참고자료 인덱싱

공용 참고자료는 `local_data/references/`에 넣고 pgVector에 인덱싱.

```powershell
cd C:\Users\KKW\Documents\계절학기\spec-agent\backend
.venv\Scripts\python.exe scripts\ingest_references.py
```

특허로 안내 페이지만 넣는 경우:

```powershell
.venv\Scripts\python.exe scripts\ingest_references.py --html-only
```

참고자료는 초안의 빈 내용을 대신 꾸며 넣는 용도가 아님. 명세서 형식, 용어, 사용자 자료와 연결되는 근거 문장을 확인하기 위한 자료.

## 다운로드 보안

생성된 Markdown/Word 파일 저장 위치: `local_data/outputs/{session_id}/`

프론트 다운로드 경로:

```text
GET /api/files/{session_id}/{filename}
```

세션 ID 없이 파일명만으로 받는 구 방식은 차단. 현재 앱은 로그인/사용자 권한 분리가 없는 로컬 단일 사용자용 구조다. 여러 사람이 쓰는 서비스로 만들려면 계정 인증, 세션 소유권 검사, 보존기간 삭제 정책이 추가로 필요.

## 현재 실제 Agent 도구

- LangGraph: Guardrail, 자료 수신, 벡터 저장, RAG, KIPRIS, LLM, 자기검토, 체크리스트, 출력 노드 실행
- Guardrail: 허위정보, 범위초과 요청 차단
- File parser: PDF/DOCX/TXT/이미지 OCR 후보 처리
- Session memory: 한 세션 안에서 대화와 자료 누적
- Embedding: OpenAIEmbeddings로 청크를 벡터화
- Vector DB: PostgreSQL 17 + pgVector 저장/검색
- RAG: 세션 자료와 공용 참고자료 검색
- KIPRISPlus: 국내 특허·실용 공개·등록공보 후보 검색
- LLM structured output: OpenAI 모델로 명세서 섹션, 질문, 검토 항목 구조화
- Self-review: LLM 결과를 원문/RAG/KIPRIS 근거와 대조해 의심 항목을 검토로 분리
- Human-in-the-loop checklist: 부족/검토 필요 항목을 사람에게 되묻기
- Exporter: Markdown/Word 산출물 생성
