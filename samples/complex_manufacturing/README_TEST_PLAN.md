# Docparse Complex Manufacturing Test Set

## 사용 순서

1. Docparse 실행
2. 사이드바 `내부 장부` 페이지로 이동
3. `samples/complex_manufacturing/item_master_complex.csv` 업로드
4. 문서 업로드 페이지에서 `01~10` txt 문서 업로드
5. 문서 상세 페이지에서 내부 품목코드 매칭, 검토 필요 항목, 금액 검증을 확인

## 핵심 테스트 포인트

- 거래처 품목명/별칭이 내부 품목코드로 매칭되는지
- 품목마스터에 없는 품목은 fake code 없이 unmatched/needs_review로 남는지
- ambiguous material, missing quantity, amount mismatch가 review issue로 뜨는지
- warning text가 quantity/item_code/input value에 들어가지 않는지
- 내부 품목마스터가 없을 때와 있을 때 결과가 달라지는지

## 권장 테스트

A. 내부 장부 업로드 전:
- 07_complex_item_master_matching_ambiguous_aliases.txt 업로드
- 품목마스터 없음/매칭 필요 warning 확인

B. 내부 장부 업로드 후:
- item_master_complex.csv 업로드
- 01, 02, 03, 08, 10 업로드
- internal_item_code가 자동/후보 매칭되는지 확인

C. Review 필요 케이스:
- 04: 스텐판 2T가 SUS304/SUS316 후보로 ambiguous인지 확인
- 05: 문서 총액과 품목 합계 불일치 확인
- 06: 수량 누락이 input value가 아니라 review issue로만 표시되는지 확인
- 09: Linear Guide Rail은 내부 품목마스터에 없으므로 fake code를 만들지 않는지 확인
