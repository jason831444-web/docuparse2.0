"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";

import { DocumentList } from "@/components/document-list";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/lib/api";
import { titleCaseLabel } from "@/lib/utils";
import type { DocumentListResponse } from "@/types/document";

export default function FileTypeFolderPage() {
  const params = useParams<{ type: string }>();
  const [data, setData] = useState<DocumentListResponse | null>(null);
  const type = useMemo(() => decodeURIComponent(params.type), [params.type]);

  const load = useCallback(() => {
    const query = new URLSearchParams();
    query.set("source_file_type", type);
    query.set("sort_by", "updated_at");
    query.set("order", "desc");
    api.list(query).then(setData).catch(() => setData(null));
  }, [type]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <main className="shell py-8">
      <div className="mb-6">
        <h1 className="text-3xl font-semibold tracking-normal">{titleCaseLabel(type)}</h1>
        <p className="mt-2 text-muted-foreground">원본 파일 형식과 추출 경로 기준으로 묶인 문서입니다.</p>
      </div>
      {data?.items.length ? (
        <DocumentList documents={data.items} onChanged={load} returnTo={`/file-types/${encodeURIComponent(type)}`} />
      ) : (
        <Card><CardContent className="p-10 text-center text-muted-foreground">이 파일 형식의 문서가 아직 없습니다.</CardContent></Card>
      )}
    </main>
  );
}
