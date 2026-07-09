from pathlib import Path
import re

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.core.config import get_settings
from app.models.schemas import AgentResponse, DraftRequest, DraftResponse
from app.services.rag import build_vectorstore, load_patent_guide, load_reference_file_documents, split_documents
from app.services.spec_agent import IncomingUpload, run_agent_turn, run_spec_agent


settings = get_settings()

app = FastAPI(
    title="SPEC Agent API",
    description="Patent specification draft assistant API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 상태 확인 API #
# 프론트/개발자가 OpenAI, DB, KIPRIS 설정이 읽혔는지만 빠르게 확인함.
# 이 API가 없어도 Agent 본 기능은 동작하지만, 실행 전 점검과 발표 시 상태 확인이 어려워짐.
@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "openai_configured": bool(settings.openai_api_key),
        "database_configured": bool(settings.sqlalchemy_database_url),
        "kipris_configured": bool(settings.kipris_search_enabled and settings.kipris_api_key),
        "kipris_base_url_configured": bool(settings.kipris_api_base_url),
        "reference_dir": str(settings.resolved_reference_dir),
        "output_dir": str(settings.resolved_output_dir),
    }



# 예전 폼 방식 호환 #
# 과거 JSON 폼 입력을 받는 API임. 새 채팅 세션 유지 기능은 /api/agent/message가 담당함.
@app.post("/api/drafts", response_model=DraftResponse)
def create_draft(payload: DraftRequest) -> DraftResponse:
    return run_spec_agent(payload, settings)


# 채팅 Agent API #
# message, session_id, case_name, use_rag, files를 multipart/form-data로 받음.
# run_agent_turn이 Guardrail -> 추출 -> DB 저장 -> RAG/KIPRIS -> LLM -> 출력 순서로 처리함.
@app.post("/api/agent/message", response_model=AgentResponse)
async def agent_message(
    message: str = Form(default=""),
    session_id: str = Form(default=""),
    case_name: str = Form(default="새 출원 준비 건"),
    use_rag: bool = Form(default=True),
    files: list[UploadFile] | None = File(default=None),
) -> AgentResponse:
    uploads = []
    for file in files or []:
        content = await file.read()
        uploads.append(IncomingUpload(filename=file.filename or "material", content=content))

    return run_agent_turn(
        message=message,
        uploads=uploads,
        settings=settings,
        session_id=session_id or None,
        case_name=case_name,
        use_rag=use_rag,
    )


# 공용 참고자료 인덱싱 API #
# local_data/references 파일과 특허로 안내 페이지를 읽어 pgVector 공용 컬렉션에 저장함.
@app.post("/api/references/ingest")
def ingest_references(reset: bool = False) -> dict:
    documents = []

    settings.resolved_reference_dir.mkdir(parents=True, exist_ok=True)
    for file_path in sorted(settings.resolved_reference_dir.iterdir()):
        if file_path.is_file():
            documents.extend(load_reference_file_documents(file_path))

    try:
        documents.extend(load_patent_guide(settings.patent_guide_url))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"특허청 안내 페이지 수집 실패: {exc}") from exc

    splits = split_documents(documents)
    try:
        indexed = build_vectorstore(settings, splits, reset=reset)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"참고자료 인덱싱 실패: {exc}") from exc

    return {
        "source_documents": len(documents),
        "chunks_indexed": indexed,
        "collection": settings.pgvector_collection,
    }


# 산출물 다운로드 API #
# 생성된 Markdown/Word 파일을 세션 폴더 안에서만 내려줌.
# filename만으로 다른 세션 산출물을 추측 다운로드하는 문제를 줄이기 위함.
@app.get("/api/files/{session_id}/{filename}")
def get_output_file(session_id: str, filename: str) -> FileResponse:
    safe_session_id = _safe_path_part(session_id)
    safe_filename = _safe_filename(filename)
    output_dir = (settings.resolved_output_dir / safe_session_id).resolve()
    file_path = (output_dir / safe_filename).resolve()
    if output_dir not in file_path.parents and file_path != output_dir:
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=Path(file_path), filename=file_path.name)


# 구 다운로드 경로 차단 #
# 세션 없는 파일 다운로드는 다른 사람 산출물 열람 위험이 있어 더 이상 허용하지 않음.
@app.get("/api/files/{filename}")
def get_output_file_without_session(filename: str) -> None:
    raise HTTPException(status_code=400, detail="session_id가 포함된 다운로드 경로를 사용해야 합니다.")


# URL 경로값 검증 #
# 세션 ID는 영문/숫자/_/-만 허용함.
def _safe_path_part(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", value or ""):
        raise HTTPException(status_code=400, detail="Invalid session id")
    return value


# 파일명 검증 #
# 경로 이동 문자를 제거하고 md/docx만 허용함.
def _safe_filename(value: str) -> str:
    name = Path(value or "").name
    if not name or Path(name).suffix.lower() not in {".md", ".docx"}:
        raise HTTPException(status_code=400, detail="Invalid file name")
    return name
