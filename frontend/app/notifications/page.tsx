"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { BellRing, CheckCircle2, Clock3, TriangleAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/lib/api";
import { formatDateTime, titleCaseLabel } from "@/lib/utils";
import type { AppNotification } from "@/types/document";

function iconFor(kind: string) {
  if (kind === "failed") return TriangleAlert;
  if (kind === "review") return BellRing;
  if (kind === "processing") return Clock3;
  return CheckCircle2;
}

export default function NotificationsPage() {
  const [items, setItems] = useState<AppNotification[]>([]);

  useEffect(() => {
    api.notifications().then(setItems).catch(() => setItems([]));
  }, []);

  return (
    <main className="shell py-8">
      <div className="mb-6">
        <h1 className="text-3xl font-semibold tracking-normal">알림</h1>
        <p className="mt-2 text-muted-foreground">업로드, 처리 중, 검토 필요, 확정 완료 상태를 최근 순서로 확인합니다.</p>
      </div>

      {items.length ? (
        <div className="space-y-3">
          {items.map((item) => {
            const Icon = iconFor(item.kind);
            return (
              <Link key={item.id} href={item.action_url} className="block">
                <Card className="transition hover:border-primary/30 hover:shadow-md">
                  <CardContent className="flex min-w-0 items-start gap-4 p-5">
                    <span className="grid size-10 shrink-0 place-items-center rounded-md bg-secondary text-primary">
                      <Icon className="size-5" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="font-semibold">{item.title}</p>
                        {item.action_required ? <Badge className="bg-amber-100 text-amber-900">확인 필요</Badge> : null}
                        {item.category_label ? <Badge variant="outline">{item.category_label}</Badge> : null}
                      </div>
                      <p className="mt-1 line-clamp-2 break-words text-sm text-muted-foreground">{item.document_title || "제목 없는 문서"}</p>
                      <p className="mt-1 text-sm text-muted-foreground">{item.message}</p>
                      <p className="mt-2 text-xs text-muted-foreground">
                        {titleCaseLabel(item.processing_status)} · {formatDateTime(item.created_at)}
                      </p>
                    </div>
                  </CardContent>
                </Card>
              </Link>
            );
          })}
        </div>
      ) : (
        <Card>
          <CardContent className="p-10 text-center text-muted-foreground">아직 알림이 없습니다.</CardContent>
        </Card>
      )}
    </main>
  );
}
