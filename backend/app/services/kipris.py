from __future__ import annotations

import re
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

import requests

from app.core.config import Settings
from app.models.schemas import PriorArtCandidate, ReferenceItem


# KIPRISPlus 기본 오퍼레이션 #
# 특허·실용 공개·등록공보 REST API의 항목별 전체검색 오퍼레이션임.
ADVANCED_SEARCH_OPERATION = "getAdvancedSearch"
WORD_SEARCH_OPERATION = "getWordSearch"

# 검색 제외어 #
# 너무 일반적인 단어는 유사도 계산에서 제외함.
STOPWORDS = {
    "특허",
    "발명",
    "고안",
    "장치",
    "방법",
    "시스템",
    "구조",
    "사용",
    "포함",
    "구비",
    "상기",
    "대한",
    "관련",
    "위한",
    "통한",
    "그리고",
    "또는",
    "있는",
    "하는",
}


# KIPRIS 사용 가능 여부 #
# 설정값이 모두 있고 검색 스위치가 켜져 있을 때만 외부 API를 호출함.
def kipris_is_configured(settings: Settings) -> bool:
    return bool(
        settings.kipris_search_enabled
        and settings.kipris_api_key
        and settings.kipris_api_base_url
    )


# 검색 질의 압축 #
# 누적 자료 전체를 보내지 않고 제목/구성 중심의 짧은 검색어로 줄임.
def build_kipris_query(text: str, limit: int = 90) -> str:
    terms = _extract_terms(text)
    if not terms:
        return " ".join((text or "").split())[:limit]
    return " ".join(terms[:8])[:limit]


# KIPRISPlus 검색 호출 #
# 전체검색과 고급검색을 모두 시도하고 XML 결과를 후보 목록으로 바꿈.
def search_kipris(settings: Settings, query: str, limit: int | None = None) -> list[PriorArtCandidate]:
    if not kipris_is_configured(settings) or not query.strip():
        return []

    count = max(1, min(limit or settings.kipris_result_count, 10))
    candidates: list[PriorArtCandidate] = []
    for operation in [WORD_SEARCH_OPERATION, ADVANCED_SEARCH_OPERATION]:
        candidates.extend(_search_operation(settings, query, count, operation))
        if len(candidates) >= count:
            break

    deduped = [candidate for candidate in _dedupe_candidates(candidates) if candidate.similarity_score > 0]
    deduped.sort(key=lambda item: item.similarity_score, reverse=True)
    return deduped[:count]


# KIPRISPlus 단일 오퍼레이션 호출 #
# getWordSearch와 getAdvancedSearch의 파라미터 차이를 흡수함.
def _search_operation(
    settings: Settings,
    query: str,
    count: int,
    operation: str,
) -> list[PriorArtCandidate]:
    params = {
        "ServiceKey": settings.kipris_api_key,
        "word": query,
        "pageNo": "1",
        "numOfRows": str(count),
    }
    if operation == WORD_SEARCH_OPERATION:
        params["year"] = "0"

    for endpoint in _operation_urls(settings.kipris_api_base_url, operation):
        try:
            response = requests.get(
                endpoint,
                params=params,
                timeout=settings.kipris_timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException:
            continue
        if not _response_is_success(response.text):
            continue
        candidates = _parse_candidates(response.text, query, count)
        for candidate in candidates:
            candidate.source_url = f"{endpoint}?{urlencode({k: v for k, v in params.items() if k != 'ServiceKey'})}"
        if candidates:
            return candidates
    return []


# KIPRIS 결과를 RAG 참고자료 형태로 변환 #
# LLM 프롬프트와 Markdown/Word 참고자료에 함께 넣기 위한 변환임.
def candidates_to_references(candidates: list[PriorArtCandidate]) -> list[ReferenceItem]:
    references = []
    for candidate in candidates:
        number = candidate.publication_number or candidate.application_number or candidate.registration_number
        source = "KIPRISPlus"
        if number:
            source = f"KIPRISPlus {number}"
        excerpt = " ".join(
            part
            for part in [
                f"자동 유사도 {candidate.similarity_score}%",
                f"검토 주의도 {candidate.risk_level}",
                candidate.abstract,
            ]
            if part
        )
        references.append(
            ReferenceItem(
                title=candidate.title or "KIPRISPlus 선행기술 후보",
                source=source,
                excerpt=excerpt[:700],
            )
        )
    return references


# 오퍼레이션 URL 조립 #
# base가 서비스 루트면 operation을 붙이고, 이미 오퍼레이션이면 교체해서 사용함.
def _operation_url(base_url: str, operation: str = ADVANCED_SEARCH_OPERATION) -> str:
    cleaned = (base_url or "").strip().rstrip("/")
    for known_operation in [ADVANCED_SEARCH_OPERATION, WORD_SEARCH_OPERATION]:
        if cleaned.endswith(known_operation):
            return cleaned[: -len(known_operation)].rstrip("/") + f"/{operation}"
    return f"{cleaned}/{operation}"


# 오퍼레이션 URL 후보 #
# KIPRISPlus가 HTTPS 연결을 끊는 경우가 있어 HTTP도 fallback으로 시도함.
def _operation_urls(base_url: str, operation: str) -> list[str]:
    primary = _operation_url(base_url, operation)
    urls = [primary]
    if primary.startswith("https://"):
        urls.append("http://" + primary[len("https://") :])
    elif primary.startswith("http://"):
        urls.append("https://" + primary[len("http://") :])
    return urls


# KIPRISPlus 성공 응답 확인 #
# HTTP 200이어도 resultCode가 00이 아니면 후보로 쓰지 않음.
def _response_is_success(xml_text: str) -> bool:
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError:
        return False
    result_code = ""
    success = ""
    for element in root.iter():
        tag = _strip_namespace(element.tag)
        text = (element.text or "").strip()
        if tag == "resultCode":
            result_code = text
        elif tag == "successYN":
            success = text
    return result_code in {"", "00"} and success in {"", "Y"}


# 후보 중복 제거 #
# 같은 출원/공개/등록번호가 여러 검색 오퍼레이션에서 나오면 하나만 남김.
def _dedupe_candidates(candidates: list[PriorArtCandidate]) -> list[PriorArtCandidate]:
    deduped = []
    seen = set()
    for candidate in candidates:
        key = (
            candidate.application_number
            or candidate.publication_number
            or candidate.registration_number
            or f"{candidate.title}:{candidate.abstract[:40]}"
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


# XML 후보 파싱 #
# KIPRISPlus XML의 item/result 하위 태그명을 유연하게 읽음.
def _parse_candidates(xml_text: str, query: str, limit: int) -> list[PriorArtCandidate]:
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError:
        return []

    records = _record_elements(root)
    candidates = []
    for record in records[:limit]:
        data = _element_dict(record)
        title = _pick(data, "inventionTitle", "title", "발명의명칭", "inventTitle")
        abstract = _pick(data, "astrtCont", "abstract", "초록", "summary")
        candidate = PriorArtCandidate(
            title=title,
            application_number=_pick(data, "applicationNumber", "applNo", "출원번호"),
            publication_number=_pick(data, "publicationNumber", "openNumber", "공개번호"),
            registration_number=_pick(data, "registrationNumber", "registerNumber", "등록번호"),
            applicant=_pick(data, "applicantName", "applicant", "출원인"),
            ipc=_pick(data, "ipcNumber", "ipc", "IPC"),
            abstract=abstract,
        )
        _score_candidate(candidate, query)
        if candidate.title or candidate.abstract or candidate.application_number:
            candidates.append(candidate)
    candidates.sort(key=lambda item: item.similarity_score, reverse=True)
    return candidates[:limit]


# 결과 레코드 탐색 #
# 응답 구조가 item/items/result 등으로 달라져도 반복 레코드를 찾음.
def _record_elements(root: ET.Element) -> list[ET.Element]:
    records = [element for element in root.iter() if _strip_namespace(element.tag).lower() in {"item", "result"}]
    if records:
        return records
    children = list(root)
    return children if children else [root]


# XML 요소를 dict로 평탄화 #
# 같은 태그가 여러 번 나오면 마지막 값만 사용함.
def _element_dict(element: ET.Element) -> dict[str, str]:
    data = {}
    for child in element.iter():
        tag = _strip_namespace(child.tag)
        text = (child.text or "").strip()
        if tag and text:
            data[tag] = text
    return data


# 네임스페이스 제거 #
# {namespace}tag 형태를 tag로 바꿈.
def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


# 후보 필드 선택 #
# 여러 가능한 태그명 중 먼저 발견되는 값을 반환함.
def _pick(data: dict[str, str], *names: str) -> str:
    lowered = {key.lower(): value for key, value in data.items()}
    for name in names:
        if name in data:
            return data[name]
        if name.lower() in lowered:
            return lowered[name.lower()]
    return ""


# 자동 유사도 계산 #
# 질의 핵심어가 제목/초록/IPC에 얼마나 겹치는지 기반의 단순 점수임.
def _score_candidate(candidate: PriorArtCandidate, query: str) -> None:
    query_terms = _extract_terms(query)
    haystack = " ".join([candidate.title, candidate.abstract, candidate.ipc]).lower()
    matched = [term for term in query_terms if term.lower() in haystack]
    unmatched = [term for term in query_terms if term not in matched]
    if query_terms:
        score = round((len(matched) / len(query_terms)) * 100)
    else:
        score = 0
    if candidate.title and any(term.lower() in candidate.title.lower() for term in query_terms):
        score = min(100, score + 12)
    candidate.similarity_score = max(0, min(score, 100))
    candidate.matched_terms = matched[:8]
    if candidate.similarity_score >= 65:
        candidate.risk_level = "높음"
    elif candidate.similarity_score >= 35:
        candidate.risk_level = "보통"
    else:
        candidate.risk_level = "낮음"
    matched_text = ", ".join(matched[:5]) or "없음"
    unmatched_text = ", ".join(unmatched[:5]) or "별도 검토"
    candidate.note = (
        f"유사 후보로만 검토합니다. 일치 키워드: {matched_text}. "
        f"차이점 검토 키워드: {unmatched_text}."
    )


# 핵심어 추출 #
# 한글/영문/숫자 2글자 이상 토큰을 뽑고 일반어는 제외함.
def _extract_terms(text: str) -> list[str]:
    raw_terms = re.findall(r"[가-힣A-Za-z0-9]{2,}", text or "")
    terms = []
    seen = set()
    for term in raw_terms:
        normalized = term.lower()
        if normalized in STOPWORDS or term in STOPWORDS:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        terms.append(term)
    return terms
