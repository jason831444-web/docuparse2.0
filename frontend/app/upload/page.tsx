import { UploadDropzone } from "@/components/upload-dropzone";
import { Card, CardContent } from "@/components/ui/card";

export default function UploadPage() {
  return (
    <main className="shell py-8">
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-primary">제조업 문서 업로드</p>
          <h1 className="mt-1 text-3xl font-semibold tracking-normal">발주서, 견적서, 거래명세서, 납품서를 업로드하세요</h1>
          <p className="mt-2 max-w-2xl text-muted-foreground">
            업로드 후 AI가 문서 유형과 거래처, 문서번호, 날짜, 납기일, 품목 테이블, 금액을 자동으로 추출합니다.
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-sm">
          {["업로드됨", "처리 중", "자동 추출 완료", "검토 필요", "확정 완료", "실패"].map((status) => (
            <span key={status} className="rounded-full border bg-white px-3 py-1 text-muted-foreground">
              {status}
            </span>
          ))}
        </div>
      </div>
      <div className="grid gap-6 xl:grid-cols-[1.35fr_0.65fr]">
        <UploadDropzone />
        <div className="grid gap-4">
          <Card>
            <CardContent className="p-5">
              <p className="text-sm font-semibold">업로드 후 처리 과정</p>
              <ol className="mt-4 space-y-4 text-sm text-muted-foreground">
                <li>
                  <span className="font-medium text-foreground">1. 문서를 정확히 읽습니다</span>
                  <p className="mt-1">PDF, 이미지, 엑셀, 워드 문서에 맞는 OCR/text extraction 경로를 선택합니다.</p>
                </li>
                <li>
                  <span className="font-medium text-foreground">2. 업무 데이터를 추출합니다</span>
                  <p className="mt-1">문서 유형, 공급업체, 고객사, 문서번호, 날짜, 품목 정보와 금액을 구조화합니다.</p>
                </li>
                <li>
                  <span className="font-medium text-foreground">3. 검토가 필요한 항목을 표시합니다</span>
                  <p className="mt-1">수량, 단가, 총액 등 신뢰도 낮은 항목은 검토 필요 상태로 보냅니다.</p>
                </li>
              </ol>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="grid gap-3 p-5 text-sm text-muted-foreground">
              <div className="rounded-lg border bg-white p-4">
                <p className="font-medium text-foreground">원본 문서</p>
                <p className="mt-1">업로드한 파일을 그대로 확인합니다.</p>
              </div>
              <div className="rounded-lg border bg-white p-4">
                <p className="font-medium text-foreground">원문 텍스트</p>
                <p className="mt-1">OCR/text extraction 결과를 확인하고 문제를 추적합니다.</p>
              </div>
              <div className="rounded-lg border bg-white p-4">
                <p className="font-medium text-foreground">추출된 업무 데이터</p>
                <p className="mt-1">문서 유형, 거래처, 품목 테이블, 금액, 검토 필요 항목을 수정합니다.</p>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </main>
  );
}
