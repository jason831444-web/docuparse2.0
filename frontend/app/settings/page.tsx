"use client";

import { useEffect, useState } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { documentGroupingLabels, loadDocumentGroupingMode, saveDocumentGroupingMode, type DocumentGroupingMode } from "@/lib/settings";

export default function SettingsPage() {
  const [grouping, setGrouping] = useState<DocumentGroupingMode>("none");

  useEffect(() => {
    setGrouping(loadDocumentGroupingMode());
  }, []);

  function updateGrouping(value: DocumentGroupingMode) {
    setGrouping(value);
    saveDocumentGroupingMode(value);
  }

  return (
    <main className="shell py-8">
      <div className="mb-6">
        <h1 className="text-3xl font-semibold tracking-normal">설정</h1>
        <p className="mt-2 text-muted-foreground">문서 목록과 검토 업무의 기본 표시 방식을 설정합니다.</p>
      </div>
      <div className="grid gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>문서 목록 설정</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4">
            <label className="grid gap-2 text-sm font-medium">
              기본 정렬
              <select className="h-10 rounded-md border bg-white px-3 text-sm">
                <option>최근 업로드 날짜순</option>
                <option>오래된 업로드 날짜순</option>
                <option>최근 수정순</option>
                <option>제목 가나다순</option>
              </select>
            </label>
            <label className="grid gap-2 text-sm font-medium">
              문서 그룹 방식
              <select className="h-10 rounded-md border bg-white px-3 text-sm" value={grouping} onChange={(event) => updateGrouping(event.target.value as DocumentGroupingMode)}>
                {Object.entries(documentGroupingLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
              <span className="text-xs font-normal text-muted-foreground">
                거래처별 → 문서 유형별을 선택하면 회사 폴더 아래에 발주서, 납품서, 세금계산서처럼 다시 나눠서 볼 수 있습니다.
              </span>
            </label>
            <label className="grid gap-2 text-sm font-medium">
              기본 보기 방식
              <select className="h-10 rounded-md border bg-white px-3 text-sm">
                <option>카드 보기</option>
                <option>목록 보기</option>
              </select>
            </label>
            <label className="flex items-center justify-between rounded-lg border bg-white px-4 py-3 text-sm font-medium">
              자동 문서 유형 분류
              <input type="checkbox" defaultChecked className="size-4" />
            </label>
            <label className="flex items-center justify-between rounded-lg border bg-white px-4 py-3 text-sm font-medium">
              처리 후 업로드 문서 열기
              <input type="checkbox" defaultChecked className="size-4" />
            </label>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>사용자 정보</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4">
            <label className="grid gap-2 text-sm font-medium">
              표시 이름
              <Input placeholder="Docparse 사용자" />
            </label>
            <label className="grid gap-2 text-sm font-medium">
              이메일
              <Input type="email" placeholder="you@company.com" />
            </label>
            <label className="grid gap-2 text-sm font-medium">
              작업공간 이름
              <Input placeholder="구매팀, 생산관리팀, 품질팀" />
            </label>
            <div className="rounded-lg border bg-white p-4 text-sm text-muted-foreground">
              현재 MVP에서는 계정 기능이 가볍게 구성되어 있으며, 이 화면은 업무 기본값을 관리하는 공간입니다.
            </div>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
