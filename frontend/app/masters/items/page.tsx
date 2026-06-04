"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Database, FileSpreadsheet, Loader2, Search, Upload } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/utils";
import type { ItemMasterRecord, ItemMasterStats } from "@/types/document";

export default function ItemMasterPage() {
  const [items, setItems] = useState<ItemMasterRecord[]>([]);
  const [stats, setStats] = useState<ItemMasterStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [activeFilter, setActiveFilter] = useState("all");

  const categories = useMemo(() => Array.from(new Set(items.map((item) => item.category).filter(Boolean) as string[])).sort(), [items]);

  const load = useCallback(async () => {
    setLoading(true);
    const params = new URLSearchParams({ page: "1", page_size: "100" });
    if (search.trim()) params.set("search", search.trim());
    if (category) params.set("category", category);
    if (activeFilter !== "all") params.set("active", activeFilter === "active" ? "true" : "false");
    try {
      const [list, nextStats] = await Promise.all([api.itemMaster.list(params), api.itemMaster.stats()]);
      setItems(list.items);
      setStats(nextStats);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "품목마스터를 불러오지 못했습니다");
    } finally {
      setLoading(false);
    }
  }, [activeFilter, category, search]);

  useEffect(() => {
    load();
  }, [load]);

  async function uploadItemMaster(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      toast.error("업로드할 CSV 또는 Excel 파일을 선택하세요");
      return;
    }
    setUploading(true);
    try {
      const result = await api.itemMaster.upload(file);
      toast.success(`품목마스터 업로드 완료: 신규 ${result.inserted}건, 갱신 ${result.updated}건`);
      if (result.errors.length) toast.warning(result.errors.slice(0, 3).join("\n"));
      setFile(null);
      await load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "품목마스터 업로드에 실패했습니다");
    } finally {
      setUploading(false);
    }
  }

  async function deactivate(id: string) {
    try {
      await api.itemMaster.remove(id);
      toast.success("품목을 비활성화했습니다");
      await load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "품목을 비활성화하지 못했습니다");
    }
  }

  return (
    <main className="shell py-8">
      <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-3xl">
          <div className="mb-3 flex items-center gap-2 text-sm font-medium text-primary">
            <Database className="size-4" />
            내부 장부
          </div>
          <h1 className="text-3xl font-semibold tracking-normal">품목마스터</h1>
          <p className="mt-3 text-sm text-muted-foreground">
            회사 내부 품목코드 기준으로 문서 품목을 자동 매칭합니다. 발주서, 견적서, 거래명세서, 납품서, 인보이스에서 추출된 품목명을 내부 품목코드로 정규화합니다.
          </p>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[1fr_0.8fr]">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Upload className="size-5 text-primary" />
              품목마스터 업로드
            </CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={uploadItemMaster} className="grid gap-4">
              <div className="rounded-lg border bg-slate-50 p-4 text-sm text-muted-foreground">
                CSV 업로드를 지원합니다. Excel 파일은 서버 의존성이 준비된 경우 사용할 수 있습니다. 필수 컬럼은 internal_item_code, item_name이며 권장 컬럼은 spec, unit, category, standard_price, active입니다.
              </div>
              <Input
                type="file"
                accept=".csv,.txt,.xlsx,.xls"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
              <Button type="submit" disabled={uploading}>
                {uploading ? <Loader2 className="size-4 animate-spin" /> : <FileSpreadsheet className="size-4" />}
                업로드
              </Button>
            </form>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>등록 현황</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-2">
            {[
              ["전체 품목 수", stats?.total_items ?? 0],
              ["활성 품목 수", stats?.active_items ?? 0],
              ["비활성 품목 수", stats?.inactive_items ?? 0],
              ["최근 업로드 시간", stats?.last_uploaded_at ? formatDateTime(stats.last_uploaded_at) : "없음"],
            ].map(([label, value]) => (
              <div key={label} className="rounded-lg border bg-white p-4">
                <p className="text-xs text-muted-foreground">{label}</p>
                <p className="mt-1 text-lg font-semibold">{value}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>품목 검색/필터</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-[1fr_180px_160px_auto]">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-3.5 size-4 text-muted-foreground" />
            <Input className="pl-9" placeholder="품목코드, 품목명, 규격 검색" value={search} onChange={(event) => setSearch(event.target.value)} />
          </div>
          <select className="h-10 rounded-md border bg-white px-3 text-sm" value={category} onChange={(event) => setCategory(event.target.value)}>
            <option value="">전체 카테고리</option>
            {categories.map((value) => (
              <option key={value} value={value}>{value}</option>
            ))}
          </select>
          <select className="h-10 rounded-md border bg-white px-3 text-sm" value={activeFilter} onChange={(event) => setActiveFilter(event.target.value)}>
            <option value="all">전체 상태</option>
            <option value="active">활성</option>
            <option value="inactive">비활성</option>
          </select>
          <Button type="button" variant="outline" onClick={load}>검색</Button>
        </CardContent>
      </Card>

      <Card className="mt-6 overflow-hidden">
        <CardHeader className="border-b bg-slate-50/70">
          <CardTitle>품목 테이블</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {loading ? (
            <div className="p-12 text-center text-muted-foreground">품목마스터를 불러오는 중입니다.</div>
          ) : items.length ? (
            <div className="overflow-x-auto">
              <table className="min-w-[980px] w-full text-sm">
                <thead className="bg-slate-50 text-left text-xs text-muted-foreground">
                  <tr>
                    {["내부 품목코드", "품목명", "정규화 품목명", "규격", "단위", "카테고리", "표준단가", "활성 여부", "최근 수정일", ""].map((header) => (
                      <th key={header} className="px-4 py-3 font-medium">{header}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.id} className="border-t">
                      <td className="px-4 py-3 font-semibold">{item.internal_item_code}</td>
                      <td className="px-4 py-3">{item.item_name}</td>
                      <td className="px-4 py-3 text-muted-foreground">{item.normalized_item_name || "-"}</td>
                      <td className="px-4 py-3">{item.spec || "-"}</td>
                      <td className="px-4 py-3">{item.unit || "-"}</td>
                      <td className="px-4 py-3">{item.category || "-"}</td>
                      <td className="px-4 py-3">{item.standard_price ? Number(item.standard_price).toLocaleString("ko-KR") : "-"}</td>
                      <td className="px-4 py-3">
                        <Badge variant={item.active ? "default" : "outline"}>{item.active ? "활성" : "비활성"}</Badge>
                      </td>
                      <td className="px-4 py-3 text-muted-foreground">{formatDateTime(item.updated_at)}</td>
                      <td className="px-4 py-3 text-right">
                        <Button type="button" variant="outline" size="sm" disabled={!item.active} onClick={() => deactivate(item.id)}>비활성화</Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-12 text-center text-muted-foreground">등록된 품목마스터가 없습니다. CSV 파일을 업로드하세요.</div>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
