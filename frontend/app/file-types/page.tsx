"use client";

import { useEffect, useState } from "react";

import { FolderCard } from "@/components/folder-card";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { FolderSummary } from "@/types/document";

export default function FileTypesPage() {
  const [folders, setFolders] = useState<FolderSummary[]>([]);

  useEffect(() => {
    api.fileTypes().then(setFolders).catch(() => setFolders([]));
  }, []);

  return (
    <main className="shell py-8">
      <div className="mb-6">
        <h1 className="text-3xl font-semibold tracking-normal">파일 형식</h1>
        <p className="mt-2 text-muted-foreground">PDF, 이미지, 엑셀, 워드 등 원본 파일 형식과 추출 경로별로 문서를 확인합니다.</p>
      </div>
      {folders.length ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {folders.map((folder) => <FolderCard key={folder.value} folder={folder} href={`/file-types/${folder.value}`} />)}
        </div>
      ) : (
        <Card><CardContent className="p-10 text-center text-muted-foreground">문서가 업로드되면 파일 형식별 목록이 표시됩니다.</CardContent></Card>
      )}
    </main>
  );
}
