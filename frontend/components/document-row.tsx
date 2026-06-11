import Link from "next/link";
import { Star } from "lucide-react";

import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { documentDisplayTitle, documentSummaryShort, formatDateTime, primaryCategoryLabel, titleCaseLabel } from "@/lib/utils";
import type { DocumentRecord } from "@/types/document";

export function DocumentRow({ document, selected = false, onSelect, returnTo }: { document: DocumentRecord; selected?: boolean; onSelect?: (checked: boolean) => void; returnTo?: string }) {
  const href = returnTo ? `/documents/${document.id}?from=${encodeURIComponent(returnTo)}` : `/documents/${document.id}`;
  const displayTitle = documentDisplayTitle(document);
  return (
    <div className="grid min-w-0 gap-3 overflow-hidden rounded-lg border bg-white px-4 py-4 shadow-sm shadow-slate-200/50 transition hover:border-primary/40 hover:shadow-md lg:grid-cols-[auto_minmax(0,2.2fr)_minmax(0,1fr)_minmax(0,0.9fr)_auto]">
      {onSelect ? (
        <input
          aria-label={`${displayTitle} 선택`}
          type="checkbox"
          className="mt-1 size-4"
          checked={selected}
          onChange={(event) => onSelect(event.target.checked)}
        />
      ) : null}
      <Link href={href} className="min-w-0">
        <div className="flex min-w-0 items-start gap-2">
          <p className="line-clamp-2 break-words font-semibold leading-snug">{displayTitle}</p>
          {document.is_favorite ? <Star className="mt-0.5 size-4 shrink-0 fill-amber-400 text-amber-400" /> : null}
        </div>
        <p className="mt-1 line-clamp-2 break-words text-sm leading-5 text-muted-foreground">{documentSummaryShort(document, 180)}</p>
        <p className="mt-1 truncate text-xs text-muted-foreground">
          {document.vendor_name || "공급업체 미확인"} · {document.document_number || "문서번호 없음"} · 품목 {document.line_items?.length ?? 0}건
        </p>
      </Link>
      <div className="flex min-w-0 flex-wrap gap-2 lg:justify-self-start">
        <Badge className="bg-accent text-accent-foreground">{primaryCategoryLabel(document)}</Badge>
        {document.source_file_type ? <Badge variant="outline">{titleCaseLabel(document.source_file_type)}</Badge> : null}
      </div>
      <div className="min-w-0 text-sm text-muted-foreground">
        <p>{formatDateTime(document.updated_at)}</p>
        <p className="mt-1 truncate" title={document.original_filename}>{document.original_filename}</p>
      </div>
      <div className="flex items-start justify-between gap-3 lg:justify-self-end">
        <StatusBadge status={document.processing_status} />
      </div>
    </div>
  );
}
