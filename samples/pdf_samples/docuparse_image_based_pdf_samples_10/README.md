# DocuParse image-based scanned PDF sample pack

이 묶음은 텍스트 레이어가 없는 이미지 기반 PDF입니다. 실제 현업 문서를 스캔한 것처럼 보이도록 PIL 이미지로 렌더링한 뒤 PDF로 저장했습니다. 따라서 일반 PDF text-layer 추출이 아니라 OCR 경로를 타는지 확인하기 좋습니다.

테스트 포인트:
- 발주서, 견적서, 인보이스/세금계산서, 거래명세서, 납품서 분류
- 가격 없는 납품서 Ready 정책
- Vendor SKU가 별도 품목으로 중복 생성되지 않는지
- 애매한 재질명(스텐판/Stainless Plate)은 자동확정하지 않고 후보확인으로 남는지
- 금액 컬럼 밀림, 수량 누락, 저품질 OCR에서 Needs Review/AI escalation이 발생하는지
- numeric/code field에 '확인 필요' 같은 표시 문구가 값으로 들어가지 않는지

파일 목록은 manifest.csv 참고.
