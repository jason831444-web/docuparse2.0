import Link from "next/link";
import { Calendar, DollarSign, FileType2, Star, Tag } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { StatusBadge } from "@/components/status-badge";
import { documentSummaryShort, formatDate, formatMoney, primaryCategoryLabel, titleCaseLabel } from "@/lib/utils";
import type { DocumentRecord } from "@/types/document";

export function DocumentCard({ document, selected = false, onSelect, returnTo }: { document: DocumentRecord; selected?: boolean; onSelect?: (checked: boolean) => void; returnTo?: string }) {
  const href = returnTo ? `/documents/${document.id}?from=${encodeURIComponent(returnTo)}` : `/documents/${document.id}`;
  return (
    <Card className="h-full min-w-0 overflow-hidden transition hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-md">
      <CardContent className="space-y-4 p-5">
        <div className="flex min-w-0 items-start gap-3">
          {onSelect ? (
            <input
              aria-label={`${document.title || document.original_filename} 선택`}
              type="checkbox"
              className="mt-1 size-4"
              checked={selected}
              onChange={(event) => onSelect(event.target.checked)}
            />
          ) : null}
          <Link href={href} className="min-w-0 flex-1">
            <div className="flex min-w-0 items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex min-w-0 items-start gap-2">
                  <h3 className="line-clamp-2 break-words font-semibold leading-snug">{document.title || document.original_filename}</h3>
                  {document.is_favorite ? <Star className="mt-0.5 size-4 shrink-0 fill-amber-400 text-amber-400" /> : null}
                </div>
                <p className="mt-1 line-clamp-2 break-words text-sm leading-5 text-muted-foreground">{documentSummaryShort(document, 120)}</p>
              </div>
              <div className="shrink-0"><StatusBadge status={document.processing_status} /></div>
            </div>
          </Link>
        </div>
          <div className="flex flex-wrap gap-2">
            <Badge className="bg-accent text-accent-foreground">{primaryCategoryLabel(document)}</Badge>
            {document.source_file_type ? <Badge variant="outline">{document.source_file_type.toUpperCase()}</Badge> : null}
          </div>
          <div className="grid gap-2 text-sm text-muted-foreground sm:grid-cols-3">
            {document.issue_date || document.extracted_date ? <span className="flex min-w-0 items-center gap-2"><Calendar className="size-4 shrink-0" /><span className="truncate">{formatDate(document.issue_date || document.extracted_date)}</span></span> : null}
            <span className="flex min-w-0 items-center gap-2"><DollarSign className="size-4 shrink-0" /><span className="truncate">{formatMoney(document.extracted_amount, document.currency || "KRW")}</span></span>
            <span className="flex min-w-0 items-center gap-2"><FileType2 className="size-4 shrink-0" /><span className="truncate">{titleCaseLabel(document.source_file_type || document.mime_type)}</span></span>
          </div>
          <div className="flex min-w-0 items-center gap-2 text-sm text-muted-foreground"><Tag className="size-4 shrink-0" /><span className="truncate">{document.vendor_name || document.customer_name || document.document_number || "거래처 정보 없음"}</span></div>
      </CardContent>
    </Card>
  );
}
