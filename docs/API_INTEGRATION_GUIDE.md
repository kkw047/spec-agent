# API 연동 가이드

이 문서는 SPEC Agent가 어떤 외부 API를 호출하고, 키를 어디에 넣고, 응답이 코드 안에서 어떻게 흐르는지 설명합니다.

## 1. 실제 호출 대상

현재 런타임에서 실제로 호출하는 대상:

- OpenAI API: 명세서 구조화 LLM 호출, embedding 생성
- PostgreSQL 17 + pgVector: 업로드 자료/참고자료 벡터 저장 및 검색
- KIPRISPlus REST API: 국내 특허·실용 공개·등록공보 선행기술 후보 검색
- 특허로 안내 페이지: 참고자료 인덱싱 시 공식 명세서 작성 안내 페이지 수집

현재 호출하지 않는 대상:

- 일반 인터넷 검색
- 구글 검색
- 논문 검색 API
- 자동 전자출원 API

## 2. 키를 넣는 파일

실제 키는 프로젝트 루트의 `.env`에 넣습니다.

```text
C:\Users\KKW\Documents\계절학기\spec-agent\.env
```

`.env.example`은 형식 예시입니다. 실제 키를 넣거나 GitHub에 올리면 안 됩니다.

## 3. OpenAI API

`.env`:

```env
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.4-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

사용 위치:

- `backend/app/services/spec_agent.py`
  - `ChatOpenAI(...).with_structured_output(...)`
  - 누적 자료, RAG 참고자료, KIPRIS 후보를 명세서 섹션/질문/검토 항목으로 구조화함.
- `backend/app/services/rag.py`
  - `OpenAIEmbeddings(...)`
  - 문서 청크를 벡터로 바꿔 pgVector에 저장/검색함.

전송되는 값:

- 사용자가 입력한 메시지
- 업로드 파일에서 추출한 텍스트
- 검색된 참고자료 발췌문
- KIPRISPlus 후보 요약

주의:

- 실제 비공개 발명자료를 넣으면 OpenAI API 호출에 텍스트가 포함될 수 있습니다.
- 발표에서는 `demo_materials/` 같은 공개 가능한 예시 자료 사용을 권장합니다.

## 4. PostgreSQL / pgVector

`.env`:

```env
POSTGRES_HOST=lab.studynest.kr
POSTGRES_PORT=45432
POSTGRES_USER=
POSTGRES_PASSWORD=
POSTGRES_DB=
PGVECTOR_COLLECTION=spec_agent_public_references
```

사용 위치:

- `backend/app/core/config.py`
  - `sqlalchemy_database_url`
  - `psycopg_params`
- `backend/app/services/rag.py`
  - `ensure_vector_extension()`
  - `add_documents_to_collection()`
  - `get_references_from_collection()`

저장되는 것:

- 사용자 메시지 청크
- 업로드 파일 추출 텍스트 청크
- 공용 참고자료 청크
- 각 청크의 embedding 벡터
- source, title, page, kind 같은 metadata

저장 위치:

```text
공용 참고자료 collection:
spec_agent_public_references

세션별 사건 자료 collection:
spec_agent_public_references_case_{session_id}
```

보안 한계:

- 세션별 collection으로 자료를 분리하지만, 로그인/권한 검사는 아직 없습니다.
- 여러 사용자가 쓰는 서비스라면 사용자별 DB 권한, 세션 소유권 검사, 자동 삭제 정책이 필요합니다.

## 5. KIPRISPlus API

`.env`:

```env
KIPRIS_SEARCH_ENABLED=true
KIPRIS_API_KEY=
KIPRIS_API_BASE_URL=https://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice
KIPRIS_RESULT_COUNT=5
KIPRIS_TIMEOUT_SECONDS=12
```

사용 위치:

- `backend/app/services/kipris.py`
  - `search_kipris()`: KIPRISPlus REST API 호출
  - `_parse_candidates()`: XML 응답 파싱
  - `_score_candidate()`: 자동 유사도 계산
- `backend/app/services/spec_agent.py`
  - RAG 이후 `KIPRIS 선행기술 후보 검색` 단계에서 호출
  - 결과를 `prior_art_candidates`와 `references`에 연결
- `frontend/src/App.jsx`
  - 오른쪽 `선행기술` 탭에서 원형 유사도와 후보 문헌 표시

호출 흐름:

```text
사용자 자료/질문
-> build_kipris_query()
-> KIPRISPlus getAdvancedSearch
-> XML 응답 파싱
-> PriorArtCandidate 목록
-> LLM 참고자료 + 프론트 선행기술 탭
```

표시 원칙:

- `자동 유사도`: 검색어 핵심어와 후보 문헌 제목/초록/IPC의 단순 겹침 점수입니다.
- `검토 주의도`: 자동 유사도를 낮음/보통/높음으로 바꾼 표시입니다.
- 이것은 특허성 확률이 아닙니다.
- 최종 신규성, 진보성, 등록 가능성 판단은 변리사 또는 담당자가 검토해야 합니다.

KIPRISPlus 확인 포인트:

- `GET /api/health`에서 `kipris_configured=true`인지 확인합니다.
- API 응답이 정상이어도 상품 신청/권한/검색 조건에 따라 결과가 0건일 수 있습니다.
- KIPRISPlus 공식 상태 페이지에서 REST API 상태를 확인할 수 있습니다.

## 6. 특허로 안내 페이지

`.env`:

```env
PATENT_GUIDE_URL=https://www.patent.go.kr/smart/jsp/ka/menu/guide/main/GuideMain0208.do
```

사용 위치:

- `backend/app/services/rag.py`
  - `load_patent_guide()`
  - `_clean_patent_guide_text()`
- `backend/app/main.py`
  - `POST /api/references/ingest`
- `backend/scripts/ingest_references.py`

역할:

- 명세서 항목 형식과 작성 안내를 RAG 참고자료로 넣습니다.
- 사용자 발명 내용을 대신 채우는 근거가 아닙니다.

## 7. 다운로드 API

생성된 파일은 세션별 폴더에 저장됩니다.

```text
local_data/outputs/{session_id}/명세서_양식_{사건명}.md
local_data/outputs/{session_id}/명세서_양식_{사건명}.docx
```

다운로드:

```http
GET /api/files/{session_id}/{filename}
```

세션 없는 과거 경로:

```http
GET /api/files/{filename}
```

위 경로는 보안상 차단합니다.

## 8. 상태 확인

```text
http://localhost:8000/api/health
```

확인 값:

- `openai_configured`
- `database_configured`
- `kipris_configured`
- `kipris_base_url_configured`
- `reference_dir`
- `output_dir`

## 9. 발표 때 설명 문장

```text
SPEC Agent는 사용자가 올린 자료를 세션별 벡터 DB에 저장하고, 공용 참고자료와 함께 RAG로 검색합니다. 이후 KIPRISPlus REST API로 국내 특허·실용 선행기술 후보를 조회하고, OpenAI 구조화 출력을 통해 명세서 초안, 보완 질문, 검토 항목, 체크리스트를 생성합니다.
```

```text
KIPRIS 후보의 원형 그래프는 특허성 확률이 아니라 자동 유사도입니다. 최종 신규성, 진보성, 청구범위 판단은 Human-in-the-loop 단계에서 사람이 검토합니다.
```
