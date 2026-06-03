"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, ShieldCheck, TriangleAlert } from "lucide-react";

import { DocumentList } from "@/components/document-list";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { DocumentListResponse } from "@/types/document";

export default function ReviewPage() {
  const [data, setData] = useState<DocumentListResponse | null>(null);

  const load = useCallback(() => {
    api.review().then(setData).catch(() => setData(null));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <main className="shell py-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div className="max-w-3xl">
          <p className="text-sm font-medium uppercase tracking-normal text-muted-foreground">사람 검토 단계</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-normal">검토 필요</h1>
          <p className="mt-2 text-muted-foreground">수량, 단가, 합계금액 등 사람이 확인해야 하는 제조업 문서를 우선 검토합니다.</p>
        </div>
        <div className="rounded-lg border bg-white px-4 py-3 text-sm text-muted-foreground">
          <span className="font-semibold text-foreground">{data?.total ?? 0}</span>건 대기 중
        </div>
      </div>
      {data?.items.length ? (
        <div className="space-y-4">
          <div className="grid gap-3 md:grid-cols-3">
            <Card>
              <CardContent className="flex items-center justify-between p-4">
                <div>
                  <p className="text-sm text-muted-foreground">검토 대기</p>
                  <p className="mt-1 text-2xl font-semibold">{data.total}</p>
                </div>
                <TriangleAlert className="size-6 text-amber-600" />
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex items-center justify-between p-4">
                <div>
                  <p className="text-sm text-muted-foreground">작업</p>
                  <p className="mt-1 text-lg font-semibold">수정 또는 확정</p>
                </div>
                <ShieldCheck className="size-6 text-primary" />
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex items-center justify-between p-4">
                <div>
                  <p className="text-sm text-muted-foreground">결과</p>
                  <p className="mt-1 text-lg font-semibold">ERP 입력 준비</p>
                </div>
                <CheckCircle2 className="size-6 text-emerald-600" />
              </CardContent>
            </Card>
          </div>
          <DocumentList documents={data.items} onChanged={load} returnTo="/review" />
        </div>
      ) : (
        <Card>
          <CardContent className="grid gap-4 p-10 text-center text-muted-foreground">
            <TriangleAlert className="mx-auto size-10 text-amber-600" />
            <div>
              <p className="font-medium text-foreground">현재 검토가 필요한 문서가 없습니다.</p>
              <p className="mt-1">발주서, 견적서, 거래명세서, 납품서를 업로드하면 검토 필요 항목이 여기에 표시됩니다.</p>
            </div>
          </CardContent>
        </Card>
      )}
    </main>
  );
}
