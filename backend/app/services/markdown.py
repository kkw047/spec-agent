from app.models.schemas import DraftResponse


# Markdown 산출물 생성 #
# 다운로드/초안 미리보기용으로 명세서 본문만 만듦.
def build_markdown(response: DraftResponse) -> str:
    sections = response.sections

    return f"""# {response.case_name} 출원명세서 검토용 초안

> 이 문서는 SPEC Agent가 생성한 검토용 초안입니다. 최종 특허성 판단, 권리범위 판단, 청구항 확정, 출원은 사람의 검토가 필요합니다.

## 1. 발명(고안)의 명칭

{sections.invention_title or "작성 필요"}

## 2. 기술분야

{sections.technical_field or "작성 필요"}

## 3. 발명(고안)의 배경이 되는 기술

{sections.background_art or "작성 필요"}

## 4. 선행기술문헌

{sections.prior_art_documents or "필요 시 작성"}

## 5. 해결하려는 과제

{sections.problem_to_solve or "작성 필요"}

## 6. 과제의 해결 수단

{sections.solution or "작성 필요"}

## 7. 발명(고안)의 효과

{sections.advantageous_effects or "작성 필요"}

## 8. 발명(고안)을 실시하기 위한 구체적인 내용

{sections.embodiment or "작성 필요"}

## 9. 도면의 간단한 설명

{sections.drawing_description or "작성 필요"}

## 10. 부호의 설명

{sections.reference_signs or "작성 필요"}

## 11. 산업상 이용가능성

{sections.industrial_applicability or "필요 시 작성"}
"""
