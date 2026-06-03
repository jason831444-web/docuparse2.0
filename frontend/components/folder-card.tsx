import Link from "next/link";
import { ArrowRight, BellRing, CheckCircle2, FolderKanban, LoaderCircle, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { FolderSummary } from "@/types/document";

export function FolderCard({ folder, href, onDelete }: { folder: FolderSummary; href: string; onDelete?: () => void }) {
  const total = Math.max(folder.count, folder.needs_review + folder.confirmed + folder.processing, 1);
  const confirmedWidth = Math.min(100, Math.round((folder.confirmed / total) * 100));
  const reviewWidth = Math.min(100, Math.round((folder.needs_review / total) * 100));
  const processingWidth = Math.min(100, Math.round((folder.processing / total) * 100));

  return (
    <Card className="h-full min-w-0 overflow-hidden border-slate-200 bg-white transition hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-md">
      <CardContent className="space-y-5 p-5">
        <Link href={href} className="block">
          <div className="flex items-start justify-between gap-3">
            <div className="flex min-w-0 gap-3">
              <span className="mt-0.5 grid size-11 shrink-0 place-items-center rounded-lg bg-secondary text-primary">
                <FolderKanban className="size-5" />
              </span>
              <div className="min-w-0">
                <p className="line-clamp-2 break-words text-lg font-semibold leading-snug">{folder.label}</p>
                <p className="mt-1 text-sm text-muted-foreground">이 유형에 문서 {folder.count}건이 정리되었습니다</p>
              </div>
            </div>
            <ArrowRight className="mt-1 size-4 shrink-0 text-muted-foreground" />
          </div>
        </Link>

        <div className="grid grid-cols-3 gap-2">
          <div className="rounded-md border bg-slate-50 p-3">
            <p className="text-lg font-semibold">{folder.needs_review}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">검토 필요</p>
          </div>
          <div className="rounded-md border bg-slate-50 p-3">
            <p className="text-lg font-semibold">{folder.confirmed}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">확정 완료</p>
          </div>
          <div className="rounded-md border bg-slate-50 p-3">
            <p className="text-lg font-semibold">{folder.processing}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">처리 중</p>
          </div>
        </div>

        <div className="h-2 overflow-hidden rounded-full bg-muted">
          <div className="flex h-full">
            <span className="bg-emerald-500" style={{ width: `${confirmedWidth}%` }} />
            <span className="bg-amber-500" style={{ width: `${reviewWidth}%` }} />
            <span className="bg-primary" style={{ width: `${processingWidth}%` }} />
          </div>
        </div>

        <div className="grid gap-2 text-sm text-muted-foreground sm:grid-cols-3">
          <span className="flex min-w-0 items-center gap-2"><BellRing className="size-4 shrink-0 text-amber-600" /><span className="truncate">검토 필요</span></span>
          <span className="flex min-w-0 items-center gap-2"><CheckCircle2 className="size-4 shrink-0 text-emerald-600" /><span className="truncate">확정 완료</span></span>
          <span className="flex min-w-0 items-center gap-2"><LoaderCircle className="size-4 shrink-0 text-primary" /><span className="truncate">처리 중</span></span>
        </div>

        {onDelete && folder.custom && folder.count === 0 ? (
          <Button type="button" variant="outline" size="sm" onClick={onDelete}>
            <Trash2 className="size-4" />
            빈 유형 삭제
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}
