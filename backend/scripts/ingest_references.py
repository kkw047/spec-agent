from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import get_settings
from app.services.rag import build_vectorstore, load_patent_guide, load_reference_file_documents, split_documents


#  참고자료 인덱싱 스크립트
# local_data/references 파일과 특허로 안내 페이지를 청크화해 공용 pgVector 컬렉션에 저장
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html-only", action="store_true")
    parser.add_argument("--no-reset", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    settings.resolved_reference_dir.mkdir(parents=True, exist_ok=True)

    #  로컬 참고자료 읽기
    # --html-only가 아니면 PDF/TXT/DOCX 등 사용자가 넣은 공용 참고자료 읽기
    documents = []
    if not args.html_only:
        for file_path in sorted(settings.resolved_reference_dir.iterdir()):
            if file_path.is_file():
                documents.extend(load_reference_file_documents(file_path))

    # 특허로 안내 페이지 읽기
    # requests.get(PATENT_GUIDE_URL)로 공식 안내 페이지를 가져옴
    documents.extend(load_patent_guide(settings.patent_guide_url))

    #  토큰화 -> DB 저장
    # 청크를 OpenAI embedding으로 바꾼 뒤 pgVector 공용 컬렉션에 저장
    splits = split_documents(documents)
    indexed = build_vectorstore(settings, splits, reset=not args.no_reset)

    print(f"source_documents={len(documents)}")
    print(f"chunks_indexed={indexed}")
    print(f"collection={settings.pgvector_collection}")


if __name__ == "__main__":
    main()
