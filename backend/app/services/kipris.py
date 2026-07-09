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
MIN_SIMILARITY_SCORE = 40

# 검색 제외어 #
# 너무 일반적인 단어는 유사도 계산에서 제외함.
STOPWORDS = {
    "아이디어",
    "기반",
    "기반해서",
    "바탕",
    "상담",
    "상담을",
    "받았어",
    "받았다",
    "이거",
    "이거야",
    "이거거든",
    "결국",
    "단점",
    "많이",
    "발생",
    "발생하게",
    "만들자",
    "만들어",
    "하자",
    "되는",
    "된다",
    "되며",
    "기계",
    "기계에",
    "들어가는",
    "들어간",
    "원가",
    "절감",
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
    "높은",
    "낮은",
}

TECHNICAL_PRIORITY_TERMS = {
    "볼트",
    "나사",
    "체결",
    "체결구",
    "선단",
    "선단부",
    "끝부분",
    "삼각",
    "삼각형",
    "단면",
    "회전",
    "마찰",
    "마찰열",
    "고열",
    "내열",
    "내열성",
    "재질",
    "합금",
    "용융",
    "나사부",
    "체결부",
}

TECHNICAL_PRIORITY_ORDER = [
    "볼트",
    "나사",
    "체결구",
    "체결",
    "삼각형",
    "삼각",
    "선단부",
    "선단",
    "끝부분",
    "단면",
    "내열",
    "내열성",
    "재질",
    "합금",
    "고열",
    "마찰열",
    "용융",
    "회전",
]
TECHNICAL_PRIORITY_INDEX = {term: index for index, term in enumerate(TECHNICAL_PRIORITY_ORDER)}


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
    for query_variant in _query_variants(query):
        for operation in [WORD_SEARCH_OPERATION, ADVANCED_SEARCH_OPERATION]:
            candidates.extend(_search_operation(settings, query_variant, count, operation))
        if len(_dedupe_candidates(candidates)) >= count * 2:
            break

    for candidate in candidates:
        _score_candidate(candidate, query)
    deduped = [
        candidate
        for candidate in _dedupe_candidates(candidates)
        if candidate.similarity_score >= MIN_SIMILARITY_SCORE
    ]
    if not deduped:
        return []
    deduped.sort(key=lambda item: item.similarity_score, reverse=True)
    return deduped[:count]


# 검색어 변형 #
# 긴 문장형 검색어가 0건일 때를 대비해 핵심어 조합을 자동으로 넓혀 검색함.
def _query_variants(query: str) -> list[str]:
    terms = _extract_terms(query)
    variants = []
    primary = " ".join(terms[:8]).strip() or query.strip()
    if primary:
        variants.append(primary)
    anchors = [term for term in terms if term in {"볼트", "나사", "체결구", "체결"}]
    if not anchors and terms:
        anchors = terms[:1]
    modifiers = [term for term in terms if term not in anchors]
    for anchor in anchors[:2]:
        for modifier in modifiers[:8]:
            variants.append(f"{anchor} {modifier}")
    for index in range(min(len(terms) - 1, 5)):
        variants.append(f"{terms[index]} {terms[index + 1]}")
    deduped = []
    seen = set()
    for variant in variants:
        cleaned = " ".join(variant.split())
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            deduped.append(cleaned)
    return deduped[:10]


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
    title = (candidate.title or "").lower()
    haystack = " ".join([candidate.title, candidate.abstract, candidate.ipc]).lower()
    matched = [term for term in query_terms if term.lower() in haystack]
    unmatched = [term for term in query_terms if term not in matched]
    if query_terms:
        score = round((len(matched) / len(query_terms)) * 100)
    else:
        score = 0
    if candidate.title and any(term.lower() in title for term in query_terms):
        score = min(100, score + 12)
    if "볼트" in query_terms and "삼각" in " ".join(query_terms):
        if "볼트" in title and "삼각" in title:
            score = min(100, score + 25)
        if any(noise in title for noise in ["전압", "킬로 볼트", "킬로볼트", "변압기", "전력"]):
            score = max(0, score - 35)
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
    for term in _expanded_terms(text) + raw_terms:
        normalized = _normalize_term(term)
        if len(normalized) < 2:
            continue
        if normalized in STOPWORDS or term in STOPWORDS:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        terms.append(normalized)
    terms.sort(key=_term_priority)
    return terms


# 검색어 확장 #
# 대화식 표현에서 특허 검색에 더 가까운 기술어 후보를 보강함.
def _expanded_terms(text: str) -> list[str]:
    normalized = re.sub(r"\s+", "", text or "").lower()
    expanded = []
    if "볼트" in normalized or "나사" in normalized:
        expanded.extend(["볼트", "체결구", "나사"])
    if "끝부분" in normalized or "끝" in normalized or "선단" in normalized:
        expanded.extend(["선단부", "선단"])
    if "삼각" in normalized:
        expanded.extend(["삼각형", "삼각", "단면"])
    if any(term in normalized for term in ["열", "발화점", "녹", "녹게", "고열"]):
        expanded.extend(["내열", "고열", "마찰열", "용융"])
    if "재질" in normalized or "제질" in normalized or "소재" in normalized:
        expanded.extend(["재질", "내열성", "합금"])
    if "회전" in normalized or "돌아" in normalized:
        expanded.append("회전")
    return expanded


# 검색어 정규화 #
# 조사/어미가 붙은 대화식 단어를 검색용 핵심어로 줄임.
def _normalize_term(term: str) -> str:
    normalized = (term or "").strip().lower()
    normalized = normalized.replace("제질", "재질")
    suffixes = [
        "입니다",
        "합니다",
        "했어",
        "았어",
        "었어",
        "인데요",
        "인데",
        "으로는",
        "으로",
        "로는",
        "에는",
        "에서",
        "에게",
        "까지",
        "부터",
        "라고",
        "이나",
        "거나",
        "이며",
        "되며",
        "하게",
        "한다",
        "되는",
        "된다",
        "되고",
        "하고",
        "하면",
        "은",
        "는",
        "이",
        "가",
        "을",
        "를",
        "에",
        "로",
    ]
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if normalized.endswith(suffix) and len(normalized) > len(suffix) + 1:
                normalized = normalized[: -len(suffix)]
                changed = True
                break
    return normalized


# 기술어 우선순위 #
# 대화어보다 구성/형상/재질/작동 관련 단어를 앞에 둠.
def _term_priority(term: str) -> tuple[int, int, str]:
    normalized = _normalize_term(term)
    if normalized in TECHNICAL_PRIORITY_INDEX:
        return (0, TECHNICAL_PRIORITY_INDEX[normalized], normalized)
    if normalized in TECHNICAL_PRIORITY_TERMS:
        return (0, 999, normalized)
    return (1, -len(normalized), normalized)
