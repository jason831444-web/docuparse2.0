"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Database, Edit3, FileSpreadsheet, Loader2, Plus, Search, Tags, Upload } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/utils";
import type { CreateItemAliasPayload, CreateItemMasterPayload, ItemAliasRecord, ItemMasterRecord, ItemMasterStats } from "@/types/document";

const emptyItemForm: CreateItemMasterPayload = {
  internal_item_code: "",
  item_name: "",
  spec: "",
  unit: "EA",
  category: "",
  standard_price: "",
  active: true,
  aliases: [],
};

const emptyAliasForm: CreateItemAliasPayload = {
  alias_name: "",
  alias_spec: "",
  vendor_name: "",
  customer_name: "",
  source: "manual",
  confidence: "1",
  memo: "",
  active: true,
};

export default function ItemMasterPage() {
  const [items, setItems] = useState<ItemMasterRecord[]>([]);
  const [stats, setStats] = useState<ItemMasterStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [activeFilter, setActiveFilter] = useState("all");
  const [showItemForm, setShowItemForm] = useState(false);
  const [editingItem, setEditingItem] = useState<ItemMasterRecord | null>(null);
  const [itemForm, setItemForm] = useState<CreateItemMasterPayload>(emptyItemForm);
  const [aliasItem, setAliasItem] = useState<ItemMasterRecord | null>(null);
  const [editingAlias, setEditingAlias] = useState<ItemAliasRecord | null>(null);
  const [aliasForm, setAliasForm] = useState<CreateItemAliasPayload>(emptyAliasForm);

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

  function openCreateItem() {
    setShowItemForm(true);
    setEditingItem(null);
    setItemForm(emptyItemForm);
  }

  function openEditItem(item: ItemMasterRecord) {
    setShowItemForm(true);
    setEditingItem(item);
    setItemForm({
      internal_item_code: item.internal_item_code,
      item_name: item.item_name,
      spec: item.spec ?? "",
      unit: item.unit ?? "",
      category: item.category ?? "",
      standard_price: item.standard_price ?? "",
      active: item.active,
      aliases: item.aliases ?? [],
    });
  }

  async function saveItem(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      if (editingItem) {
        await api.itemMaster.update(editingItem.id, {
          item_name: itemForm.item_name,
          spec: itemForm.spec,
          unit: itemForm.unit,
          category: itemForm.category,
          standard_price: itemForm.standard_price,
          active: itemForm.active,
          aliases: itemForm.aliases,
        });
        toast.success("품목 정보를 수정했습니다");
      } else {
        await api.itemMaster.create(itemForm);
        toast.success("품목을 추가했습니다");
      }
      setShowItemForm(false);
      setEditingItem(null);
      setItemForm(emptyItemForm);
      await load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "품목을 저장하지 못했습니다");
    }
  }

  function openAliases(item: ItemMasterRecord) {
    setAliasItem(item);
    setEditingAlias(null);
    setAliasForm(emptyAliasForm);
  }

  function openEditAlias(alias: ItemAliasRecord) {
    setEditingAlias(alias);
    setAliasForm({
      alias_name: alias.alias_name,
      alias_spec: alias.alias_spec ?? "",
      vendor_name: alias.vendor_name ?? "",
      customer_name: alias.customer_name ?? "",
      source: alias.source ?? "manual",
      confidence: alias.confidence ?? "1",
      memo: alias.memo ?? "",
      active: alias.active,
    });
  }

  async function saveAlias(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!aliasItem) return;
    try {
      if (editingAlias) {
        await api.itemMaster.updateAlias(editingAlias.id, aliasForm);
        toast.success("별칭을 수정했습니다");
      } else {
        await api.itemMaster.createAlias(aliasItem.id, aliasForm);
        toast.success("별칭을 추가했습니다");
      }
      const refreshed = await api.itemMaster.get(aliasItem.id);
      setAliasItem(refreshed);
      setEditingAlias(null);
      setAliasForm(emptyAliasForm);
      await load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "별칭을 저장하지 못했습니다");
    }
  }

  async function deactivateAlias(alias: ItemAliasRecord) {
    if (!aliasItem) return;
    try {
      await api.itemMaster.removeAlias(alias.id);
      const refreshed = await api.itemMaster.get(aliasItem.id);
      setAliasItem(refreshed);
      toast.success("별칭을 비활성화했습니다");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "별칭을 비활성화하지 못했습니다");
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
        <Button type="button" onClick={openCreateItem}>
          <Plus className="size-4" />
          품목 추가
        </Button>
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
              ["alias 수", stats?.alias_count ?? 0],
              ["최근 업로드 시간", stats?.last_uploaded_at ? formatDateTime(stats.last_uploaded_at) : "없음"],
              ["최근 수정 시간", stats?.last_updated_at ? formatDateTime(stats.last_updated_at) : "없음"],
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

      {showItemForm ? (
        <Card className="mt-6 border-primary/30">
          <CardHeader>
            <CardTitle>{editingItem ? "품목 수정" : "품목 추가"}</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={saveItem} className="grid gap-4 md:grid-cols-3">
              <label className="grid gap-2 text-sm font-medium">
                내부 품목코드
                <Input value={itemForm.internal_item_code} disabled={!!editingItem} onChange={(event) => setItemForm({ ...itemForm, internal_item_code: event.target.value })} />
              </label>
              <label className="grid gap-2 text-sm font-medium">
                품목명
                <Input value={itemForm.item_name} onChange={(event) => setItemForm({ ...itemForm, item_name: event.target.value })} />
              </label>
              <label className="grid gap-2 text-sm font-medium">
                규격
                <Input value={itemForm.spec ?? ""} onChange={(event) => setItemForm({ ...itemForm, spec: event.target.value })} />
              </label>
              <label className="grid gap-2 text-sm font-medium">
                단위
                <Input value={itemForm.unit ?? ""} onChange={(event) => setItemForm({ ...itemForm, unit: event.target.value })} />
              </label>
              <label className="grid gap-2 text-sm font-medium">
                카테고리
                <Input value={itemForm.category ?? ""} onChange={(event) => setItemForm({ ...itemForm, category: event.target.value })} />
              </label>
              <label className="grid gap-2 text-sm font-medium">
                표준단가
                <Input type="number" min="0" step="0.01" value={itemForm.standard_price ?? ""} onChange={(event) => setItemForm({ ...itemForm, standard_price: event.target.value })} />
              </label>
              <label className="flex items-center gap-2 text-sm font-medium">
                <input type="checkbox" checked={itemForm.active ?? true} onChange={(event) => setItemForm({ ...itemForm, active: event.target.checked })} />
                활성 품목
              </label>
              <div className="flex items-end gap-2 md:col-span-2">
                <Button type="submit">저장</Button>
                <Button type="button" variant="outline" onClick={() => { setShowItemForm(false); setEditingItem(null); setItemForm(emptyItemForm); }}>취소</Button>
              </div>
            </form>
          </CardContent>
        </Card>
      ) : null}

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
                    {["내부 품목코드", "품목명", "정규화 품목명", "규격", "단위", "카테고리", "표준단가", "별칭", "활성 여부", "최근 수정일", ""].map((header) => (
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
                      <td className="px-4 py-3">{item.alias_records?.filter((alias) => alias.active).length ?? 0}</td>
                      <td className="px-4 py-3">
                        <Badge variant={item.active ? "default" : "outline"}>{item.active ? "활성" : "비활성"}</Badge>
                      </td>
                      <td className="px-4 py-3 text-muted-foreground">{formatDateTime(item.updated_at)}</td>
                      <td className="px-4 py-3">
                        <div className="flex justify-end gap-1">
                          <Button type="button" variant="outline" size="sm" onClick={() => openEditItem(item)}>
                            <Edit3 className="size-3.5" />
                            수정
                          </Button>
                          <Button type="button" variant="outline" size="sm" onClick={() => openAliases(item)}>
                            <Tags className="size-3.5" />
                            별칭
                          </Button>
                          <Button type="button" variant="outline" size="sm" disabled={!item.active} onClick={() => deactivate(item.id)}>비활성화</Button>
                        </div>
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

      {aliasItem ? (
        <Card className="mt-6 border-primary/30">
          <CardHeader>
            <CardTitle>별칭 관리: {aliasItem.internal_item_code}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-5">
            <div className="overflow-x-auto rounded-lg border">
              <table className="min-w-[760px] w-full text-sm">
                <thead className="bg-slate-50 text-left text-xs text-muted-foreground">
                  <tr>
                    {["별칭", "규격", "거래처", "고객사", "출처", "활성", "메모", ""].map((header) => (
                      <th key={header} className="px-3 py-2 font-medium">{header}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {aliasItem.alias_records?.length ? aliasItem.alias_records.map((alias) => (
                    <tr key={alias.id} className="border-t">
                      <td className="px-3 py-2 font-semibold">{alias.alias_name}</td>
                      <td className="px-3 py-2">{alias.alias_spec || "-"}</td>
                      <td className="px-3 py-2">{alias.vendor_name || "-"}</td>
                      <td className="px-3 py-2">{alias.customer_name || "-"}</td>
                      <td className="px-3 py-2">{alias.source || "-"}</td>
                      <td className="px-3 py-2">{alias.active ? "활성" : "비활성"}</td>
                      <td className="px-3 py-2 text-muted-foreground">{alias.memo || "-"}</td>
                      <td className="px-3 py-2">
                        <div className="flex justify-end gap-1">
                          <Button type="button" variant="outline" size="sm" onClick={() => openEditAlias(alias)}>수정</Button>
                          <Button type="button" variant="outline" size="sm" disabled={!alias.active} onClick={() => deactivateAlias(alias)}>비활성화</Button>
                        </div>
                      </td>
                    </tr>
                  )) : (
                    <tr>
                      <td className="px-3 py-8 text-center text-muted-foreground" colSpan={8}>등록된 별칭이 없습니다.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <form onSubmit={saveAlias} className="grid gap-3 md:grid-cols-3">
              <label className="grid gap-2 text-sm font-medium">
                별칭명
                <Input value={aliasForm.alias_name} onChange={(event) => setAliasForm({ ...aliasForm, alias_name: event.target.value })} />
              </label>
              <label className="grid gap-2 text-sm font-medium">
                별칭 규격
                <Input value={aliasForm.alias_spec ?? ""} onChange={(event) => setAliasForm({ ...aliasForm, alias_spec: event.target.value })} />
              </label>
              <label className="grid gap-2 text-sm font-medium">
                거래처
                <Input value={aliasForm.vendor_name ?? ""} onChange={(event) => setAliasForm({ ...aliasForm, vendor_name: event.target.value })} />
              </label>
              <label className="grid gap-2 text-sm font-medium">
                고객사
                <Input value={aliasForm.customer_name ?? ""} onChange={(event) => setAliasForm({ ...aliasForm, customer_name: event.target.value })} />
              </label>
              <label className="grid gap-2 text-sm font-medium">
                메모
                <Input value={aliasForm.memo ?? ""} onChange={(event) => setAliasForm({ ...aliasForm, memo: event.target.value })} />
              </label>
              <label className="flex items-center gap-2 text-sm font-medium">
                <input type="checkbox" checked={aliasForm.active ?? true} onChange={(event) => setAliasForm({ ...aliasForm, active: event.target.checked })} />
                활성 별칭
              </label>
              <div className="flex gap-2 md:col-span-3">
                <Button type="submit">{editingAlias ? "별칭 수정" : "별칭 추가"}</Button>
                <Button type="button" variant="outline" onClick={() => { setEditingAlias(null); setAliasForm(emptyAliasForm); }}>입력 초기화</Button>
                <Button type="button" variant="outline" onClick={() => setAliasItem(null)}>닫기</Button>
              </div>
            </form>
          </CardContent>
        </Card>
      ) : null}
    </main>
  );
}
