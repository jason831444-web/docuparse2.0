# Docparse 제조업 문서 자동화 데모 가이드

이 문서는 Docparse를 한국 중소 제조업체의 구매/납품 문서 처리 MVP로 시연하기 위한 가이드입니다.

## Demo Setup

로컬 스택 실행:

```bash
docker compose --profile backend up --build
```

Open:

- Frontend: http://localhost:3001
- API docs: http://localhost:8001/docs

로컬 GGUF 해석을 사용할 경우 시작 전에 모델 파일을 준비합니다.

```text
models/gguf/gemma-3-4b-it-q4_0.gguf
```

## 추천 샘플 문서

데모에는 현실적인 한국 제조업 문서 샘플이 가장 좋습니다.

- 발주서 PDF 또는 이미지
- 견적서 엑셀 또는 PDF
- 거래명세서 PDF
- 납품서 이미지 또는 PDF

샘플 문서에는 다음 정보가 들어가면 좋습니다.

- 공급업체: 대한정밀부품 주식회사
- 고객사: 한빛모터스
- 발주번호: PO-2026-0603-001
- 발주일: 2026-06-03
- 납기일: 2026-06-17
- 품목: 브라켓 ASSY, SHAFT-2040, 알루미늄 하우징
- 품목 코드, 규격, 수량, 단가, 공급가액, 세액, 합계금액

## 핵심 데모 흐름

1. 사용자가 발주서 PDF 또는 이미지 파일을 업로드한다.
2. 시스템이 문서 유형을 발주서로 분류한다.
3. 공급업체, 고객사, 발주번호, 발주일, 납기일을 추출한다.
4. 품목 테이블에서 품목명, 품목 코드, 규격, 수량, 단가, 공급가액, 세액, 총액을 추출한다.
5. 신뢰도 낮은 필드는 “검토 필요”로 표시한다.
6. 사용자가 틀린 수량이나 단가를 수정한다.
7. 문서를 “확정 완료” 상태로 변경한다.
8. 최종 결과를 CSV, Excel, JSON 중 하나로 내보낸다.

## 화면별 시연 포인트

### 대시보드

- 총 문서 수
- 처리 중
- 검토 필요
- 확정 완료
- 처리 실패
- 최근 업로드 문서
- 제조업 문서 자동화 현황

### 업로드

- “제조업 문서 업로드” 영역에서 파일을 업로드한다.
- PDF, 이미지, 엑셀, 워드 문서 지원을 설명한다.
- 업로드 후 AI가 문서 유형과 핵심 업무 데이터를 자동 추출한다고 설명한다.

### 문서 상세/검토

다음 정보를 보여준다.

- 원본 문서
- 원문 텍스트
- AI 추출 결과
- 추출 경로 `provider_chain`
- 문서 유형
- 공급업체
- 고객사
- 문서번호
- 발행일
- 납기일
- 품목 정보
- 신뢰도 낮은 항목

품목 테이블에서 수량 또는 단가를 일부 수정한 뒤 “수정 저장”을 누른다.

### 검토 필요

- 품목 정보가 없거나 수량/단가/합계금액이 불확실한 문서가 `needs_review`로 이동하는 것을 보여준다.
- 사용자가 확인 후 “확정 처리”를 누르면 “확정 완료” 상태가 된다.

### 내보내기

- 문서 목록에서 CSV 또는 Excel로 내보낸다.
- 상세 화면에서 JSON으로 내보낸다.
- 확정된 데이터를 ERP나 엑셀 입력용으로 사용할 수 있다는 점을 설명한다.

## 기술 설명 포인트

짧은 설명:

> Docparse는 제조업 구매/납품 문서를 업로드하면 ingestion, OCR/text extraction, document routing, deterministic parser, optional local GGUF interpretation, quality gate를 거쳐 ERP/Excel-ready data로 변환합니다. AI 결과를 그대로 믿지 않고 provider_chain, field_sources, low_confidence_fields, needs_review 상태를 통해 사람이 검토할 수 있게 했습니다.

좋은 후속 질문:

- 왜 품목 행 `line_items`가 품질 게이트의 핵심인지
- OCR 결과가 약할 때 왜 `needs_review`로 보내는지
- `provider_chain`이 모델/OCR 회귀 디버깅에 어떻게 도움 되는지
- 실제 운영에서는 background job, auth, object storage, ERP API 연동을 어떻게 추가할지

## Demo Caveats

- CPU GGUF 추론은 첫 실행이 느릴 수 있습니다.
- 현재 ERP 직접 연동은 없고 CSV/Excel/JSON 내보내기를 우선 지원합니다.
- 인증 화면은 MVP shell이며 실제 계정 시스템은 별도 확장 영역입니다.
- 복잡한 병합 셀이나 저화질 스캔 문서는 검토 필요로 이동할 수 있습니다.
