import { Badge } from "@/components/ui/badge";
import type { ProcessingStatus } from "@/types/document";

const colors: Record<ProcessingStatus, string> = {
  uploaded: "border-amber-300 bg-amber-50 text-amber-800",
  queued: "border-sky-300 bg-sky-50 text-sky-800",
  processing: "border-blue-300 bg-blue-50 text-blue-800",
  ready: "border-emerald-300 bg-emerald-50 text-emerald-800",
  needs_review: "border-amber-300 bg-amber-50 text-amber-800",
  confirmed: "border-violet-300 bg-violet-50 text-violet-800",
  completed: "border-emerald-300 bg-emerald-50 text-emerald-800",
  failed: "border-red-300 bg-red-50 text-red-800"
};

const labels: Record<ProcessingStatus, string> = {
  uploaded: "업로드됨",
  queued: "대기 중",
  processing: "처리 중",
  ready: "자동 추출 완료",
  needs_review: "검토 필요",
  confirmed: "확정 완료",
  completed: "자동 추출 완료",
  failed: "실패"
};

export function StatusBadge({ status }: { status: ProcessingStatus }) {
  return <Badge className={colors[status]}>{labels[status]}</Badge>;
}
