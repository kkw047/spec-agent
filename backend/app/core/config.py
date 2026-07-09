from functools import lru_cache
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[3]
load_dotenv(ROOT_DIR / ".env")


# 환경변수 설정 #
# OpenAI, DB, KIPRIS, 참고자료/산출물 경로를 한 객체에서 관리함.
class Settings(BaseSettings):
    # OpenAI API #
    # 초안 구조화와 임베딩 생성에 사용함.
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-5.4-mini", alias="OPENAI_MODEL")
    openai_embedding_model: str = Field(
        default="text-embedding-3-small", alias="OPENAI_EMBEDDING_MODEL"
    )

    # PostgreSQL / pgVector #
    # 사용자가 올린 자료와 공용 참고자료 청크를 벡터 DB에 저장함.
    postgres_host: str = Field(default="lab.studynest.kr", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=45432, alias="POSTGRES_PORT")
    postgres_user: str = Field(default="", alias="POSTGRES_USER")
    postgres_password: str = Field(default="", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="", alias="POSTGRES_DB")
    database_url: str = Field(default="", alias="DATABASE_URL")
    pgvector_collection: str = Field(
        default="spec_agent_public_references", alias="PGVECTOR_COLLECTION"
    )

    # 프론트 연결 / 공식 참고자료 #
    # CORS 허용 주소와 특허로 명세서 작성 안내 페이지 주소임.
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173", alias="CORS_ORIGINS"
    )
    patent_guide_url: str = Field(
        default="https://www.patent.go.kr/smart/jsp/ka/menu/guide/main/GuideMain0208.do",
        alias="PATENT_GUIDE_URL",
    )

    # KIPRISPlus API #
    # 국내 특허/실용 공개·등록공보 REST API 검색 후보를 가져올 때 사용함.
    kipris_search_enabled: bool = Field(default=False, alias="KIPRIS_SEARCH_ENABLED")
    kipris_api_key: str = Field(default="", alias="KIPRIS_API_KEY")
    kipris_api_base_url: str = Field(
        default="https://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice",
        alias="KIPRIS_API_BASE_URL",
    )
    kipris_result_count: int = Field(default=5, alias="KIPRIS_RESULT_COUNT")
    kipris_timeout_seconds: int = Field(default=12, alias="KIPRIS_TIMEOUT_SECONDS")

    # 로컬 자료 경로 #
    # local_data는 git에 올리지 않는 실행 중 생성/참고자료 폴더임.
    reference_source_dir: str = Field(
        default="local_data/references", alias="REFERENCE_SOURCE_DIR"
    )
    draft_output_dir: str = Field(default="local_data/outputs", alias="DRAFT_OUTPUT_DIR")

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    # CORS 주소 목록 #
    # "a,b,c" 문자열을 FastAPI가 쓰는 list[str]로 바꿈.
    @property
    def allowed_origins(self) -> List[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    # 참고자료 절대경로 #
    # local_data/references 같은 상대경로를 프로젝트 기준 절대경로로 바꿈.
    @property
    def resolved_reference_dir(self) -> Path:
        return (ROOT_DIR / self.reference_source_dir).resolve()

    # 산출물 절대경로 #
    # Markdown/Word 결과가 저장될 local_data/outputs 절대경로를 만듦.
    @property
    def resolved_output_dir(self) -> Path:
        return (ROOT_DIR / self.draft_output_dir).resolve()

    # SQLAlchemy DB URL #
    # DATABASE_URL이 있으면 우선 사용하고, 없으면 POSTGRES_* 값으로 조립함.
    @property
    def sqlalchemy_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        if not all([self.postgres_user, self.postgres_password, self.postgres_db]):
            return ""
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # psycopg 직접 연결값 #
    # vector 확장 생성처럼 LangChain 밖에서 DB에 직접 붙을 때 사용함.
    @property
    def psycopg_params(self) -> dict:
        return {
            "host": self.postgres_host,
            "port": self.postgres_port,
            "dbname": self.postgres_db,
            "user": self.postgres_user,
            "password": self.postgres_password,
        }


# 설정 캐시 #
# 요청마다 .env를 다시 읽지 않도록 Settings 객체를 1회 생성해 재사용함.
@lru_cache
def get_settings() -> Settings:
    return Settings()
