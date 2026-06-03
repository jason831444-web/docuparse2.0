"use client";

import { useCallback, useEffect, useState } from "react";

import { DocumentList } from "@/components/document-list";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { DocumentListResponse } from "@/types/document";

export default function FavoritesPage() {
  const [data, setData] = useState<DocumentListResponse | null>(null);

  const load = useCallback(() => {
    api.favorites().then(setData).catch(() => setData(null));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <main className="shell py-8">
      <div className="mb-6">
        <h1 className="text-3xl font-semibold tracking-normal">즐겨찾기</h1>
        <p className="mt-2 text-muted-foreground">자주 검토하거나 내보내야 하는 제조업 문서를 모아둡니다.</p>
      </div>
      {data?.items.length ? (
        <DocumentList documents={data.items} onChanged={load} returnTo="/favorites" />
      ) : (
        <Card><CardContent className="p-10 text-center text-muted-foreground">문서 상세 화면에서 즐겨찾기를 선택하면 여기에 표시됩니다.</CardContent></Card>
      )}
    </main>
  );
}
