from pathlib import Path
import json
import re
from uuid import uuid4

from app.core.config import Settings
from app.models.schemas import ChatMessage, MaterialSource


# 세션 ID 처리
# 프론트가 session_id를 보내면 이어 쓰고, 없거나 이상하면 새 ID를 제작
def normalize_session_id(session_id: str | None) -> str:
    if session_id and re.fullmatch(r"[a-zA-Z0-9_-]{8,80}", session_id):
        return session_id
    return uuid4().hex[:16]


# 세션 폴더
# local_data/sessions/{session_id} 아래에 업로드 파일과 state.json을 저장
def session_dir(settings: Settings, session_id: str) -> Path:
    return settings.resolved_output_dir.parent / "sessions" / session_id


# 업로드 저장 위치
# 사용자가 올린 원본 파일이 들어가는 폴더
def upload_dir(settings: Settings, session_id: str) -> Path:
    return session_dir(settings, session_id) / "uploads"


# 상태 파일 위치
# 대화 기록, 읽은 자료, 추출 텍스트가 state.json에 누적
def state_path(settings: Settings, session_id: str) -> Path:
    return session_dir(settings, session_id) / "state.json"


# 상태 읽기
# 새 세션이면 빈 대화/자료 상태를 반환
def load_state(settings: Settings, session_id: str) -> dict:
    path = state_path(settings, session_id)
    if not path.exists():
        return {
            "case_name": "새 출원 준비 건",
            "messages": [],
            "materials": [],
            "material_texts": [],
        }
    return json.loads(path.read_text(encoding="utf-8"))


# 상태 저장
# 다음 턴에서 이어서 분석할 수 있도록 state.json을 갱신
def save_state(settings: Settings, session_id: str, state: dict) -> None:
    path = state_path(settings, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# 사용자 턴 누적
# message, 업로드 파일 메타데이터, 추출 텍스트를 세션에 저장
def append_user_turn(
    settings: Settings,
    session_id: str,
    message: str,
    materials: list[MaterialSource],
    material_texts: list[str],
) -> dict:
    state = load_state(settings, session_id)
    if message.strip():
        state["messages"].append(ChatMessage(role="user", content=message).model_dump())
    state["materials"].extend(material.model_dump() for material in materials)
    state["material_texts"].extend(text for text in material_texts if text.strip())
    save_state(settings, session_id, state)
    return state


#  Agent 답변 누적
# 채팅창에 보이는 assistant 답변을 세션 대화 기록에 저장
def append_assistant_turn(settings: Settings, session_id: str, reply: str) -> dict:
    state = load_state(settings, session_id)
    if reply.strip():
        state["messages"].append(ChatMessage(role="assistant", content=reply).model_dump())
    save_state(settings, session_id, state)
    return state
