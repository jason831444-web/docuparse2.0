"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { BookOpen, Edit3, Loader2, Plus, Search, Tags } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/utils";
import type {
  CreateDomainDictionaryAliasPayload,
  CreateDomainDictionaryEntryPayload,
  DomainDictionaryAliasRecord,
  DomainDictionaryEntryRecord,
  DomainDictionaryStats,
} from "@/types/document";

const dictionaryTypeLabels: Record<string, string> = {
  field_label: "라벨",
  party: "거래처",
  item: "품목",
  spec: "규격",
  value: "값",
};

const emptyEntryForm: CreateDomainDictionaryEntryPayload = {
  dictionary_type: "field_label",
  canonical_value: "",
  field: "",
  source: "manual",
  memo: "",
  active: true,
  aliases: [],
};

const emptyAliasForm: CreateDomainDictionaryAliasPayload = {
  alias_value: "",
  source: "manual",
  confidence: "1",
  active: true,
};

export default function DomainDictionaryPage() {
  const [entries, setEntries] = useState<DomainDictionaryEntryRecord[]>([]);
  const [stats, setStats] = useState<DomainDictionaryStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [dictionaryType, setDictionaryType] = useState("");
  const [activeFilter, setActiveFilter] = useState("active");
  const [showEntryForm, setShowEntryForm] = useState(false);
  const [editingEntry, setEditingEntry] = useState<DomainDictionaryEntryRecord | null>(null);
  const [entryForm, setEntryForm] = useState<CreateDomainDictionaryEntryPayload>(emptyEntryForm);
  const [aliasEntry, setAliasEntry] = useState<DomainDictionaryEntryRecord | null>(null);
  const [editingAlias, setEditingAlias] = useState<DomainDictionaryAliasRecord | null>(null);
  const [aliasForm, setAliasForm] = useState<CreateDomainDictionaryAliasPayload>(emptyAliasForm);

  const typeOptions = useMemo(() => Object.entries(dictionaryTypeLabels), []);

  const load = useCallback(async () => {
    setLoading(true);
    const params = new URLSearchParams({ page: "1", page_size: "200" });
    if (search.trim()) params.set("query", search.trim());
    if (dictionaryType) params.set("dictionary_type", dictionaryType);
    if (activeFilter !== "all") params.set("active", activeFilter === "active" ? "true" : "false");
    try {
      const [list, nextStats] = await Promise.all([api.domainDictionary.list(params), api.domainDictionary.stats()]);
      setEntries(list.items);
      setStats(nextStats);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "도메인 사전을 불러오지 못했습니다");
    } finally {
      setLoading(false);
    }
  }, [activeFilter, dictionaryType, search]);

  useEffect(() => {
    load();
  }, [load]);

  function openCreateEntry() {
    setShowEntryForm(true);
    setEditingEntry(null);
    setEntryForm(emptyEntryForm);
  }

  function openEditEntry(entry: DomainDictionaryEntryRecord) {
    setShowEntryForm(true);
    setEditingEntry(entry);
    setEntryForm({
      dictionary_type: entry.dictionary_type,
      canonical_value: entry.canonical_value,
      field: entry.field ?? "",
      source: entry.source,
      memo: entry.memo ?? "",
      active: entry.active,
      aliases: [],
    });
  }

  async function saveEntry(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      if (editingEntry) {
        await api.domainDictionary.update(editingEntry.id, {
          canonical_value: entryForm.canonical_value,
          field: entryForm.field,
          source: entryForm.source,
          memo: entryForm.memo,
          active: entryForm.active,
        });
        toast.success("사전 항목을 수정했습니다");
      } else {
        await api.domainDictionary.create({
          ...entryForm,
          aliases: splitAliases(entryForm.aliases?.join(",") ?? ""),
        });
        toast.success("사전 항목을 추가했습니다");
      }
      setShowEntryForm(false);
      setEditingEntry(null);
      setEntryForm(emptyEntryForm);
      await load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "사전 항목을 저장하지 못했습니다");
    }
  }

  function openAliases(entry: DomainDictionaryEntryRecord) {
    setAliasEntry(entry);
    setEditingAlias(null);
    setAliasForm(emptyAliasForm);
  }

  function openEditAlias(alias: DomainDictionaryAliasRecord) {
    setEditingAlias(alias);
    setAliasForm({
      alias_value: alias.alias_value,
      source: alias.source,
      confidence: alias.confidence ?? "1",
      active: alias.active,
    });
  }

  async function saveAlias(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!aliasEntry) return;
    try {
      if (editingAlias) {
        await api.domainDictionary.updateAlias(editingAlias.id, aliasForm);
        toast.success("별칭을 수정했습니다");
      } else {
        await api.domainDictionary.createAlias(aliasEntry.id, aliasForm);
        toast.success("별칭을 추가했습니다");
      }
      const refreshed = await api.domainDictionary.get(aliasEntry.id);
      setAliasEntry(refreshed);
      setEditingAlias(null);
      setAliasForm(emptyAliasForm);
      await load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "별칭을 저장하지 못했습니다");
    }
  }

  async function deactivateEntry(id: string) {
    try {
      await api.domainDictionary.remove(id);
      toast.success("사전 항목을 비활성화했습니다");
      await load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "사전 항목을 비활성화하지 못했습니다");
    }
  }

  async function deactivateAlias(alias: DomainDictionaryAliasRecord) {
    if (!aliasEntry) return;
    try {
      await api.domainDictionary.removeAlias(alias.id);
      const refreshed = await api.domainDictionary.get(aliasEntry.id);
      setAliasEntry(refreshed);
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
            <BookOpen className="size-4" />
            제조업 사전
          </div>
          <h1 className="text-3xl font-semibold tracking-normal">도메인 사전</h1>
          <p className="mt-3 text-sm text-muted-foreground">
            확정 문서와 품목마스터에 더해 직접 관리하는 라벨, 거래처, 품목, 규격 별칭을 검토 추천에 사용합니다.
          </p>
        </div>
        <Button type="button" onClick={openCreateEntry}>
          <Plus className="size-4" />
          항목 추가
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>사전 현황</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
          {[
            ["전체 항목", stats?.total_entries ?? 0],
            ["활성 항목", stats?.active_entries ?? 0],
            ["비활성 항목", stats?.inactive_entries ?? 0],
            ["별칭 수", stats?.alias_count ?? 0],
            ["피드백", stats?.feedback_count ?? 0],
            ["거절", stats?.rejected_count ?? 0],
          ].map(([label, value]) => (
            <div key={label} className="rounded-lg border bg-white p-4">
              <p className="text-xs text-muted-foreground">{label}</p>
              <p className="mt-1 text-lg font-semibold">{value}</p>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>검색/필터</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-[1fr_180px_160px_auto]">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-3.5 size-4 text-muted-foreground" />
            <Input className="pl-9" placeholder="정식값, 필드, 메모 검색" value={search} onChange={(event) => setSearch(event.target.value)} />
          </div>
          <select className="h-10 rounded-md border bg-white px-3 text-sm" value={dictionaryType} onChange={(event) => setDictionaryType(event.target.value)}>
            <option value="">전체 유형</option>
            {typeOptions.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
          <select className="h-10 rounded-md border bg-white px-3 text-sm" value={activeFilter} onChange={(event) => setActiveFilter(event.target.value)}>
            <option value="active">활성</option>
            <option value="inactive">비활성</option>
            <option value="all">전체 상태</option>
          </select>
          <Button type="button" variant="outline" onClick={load}>검색</Button>
        </CardContent>
      </Card>

      {showEntryForm ? (
        <Card className="mt-6 border-primary/30">
          <CardHeader>
            <CardTitle>{editingEntry ? "사전 항목 수정" : "사전 항목 추가"}</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={saveEntry} className="grid gap-4 md:grid-cols-3">
              <label className="grid gap-2 text-sm font-medium">
                유형
                <select className="h-10 rounded-md border bg-white px-3 text-sm" value={entryForm.dictionary_type} disabled={!!editingEntry} onChange={(event) => setEntryForm({ ...entryForm, dictionary_type: event.target.value })}>
                  {typeOptions.map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </label>
              <label className="grid gap-2 text-sm font-medium">
                정식값
                <Input value={entryForm.canonical_value} onChange={(event) => setEntryForm({ ...entryForm, canonical_value: event.target.value })} />
              </label>
              <label className="grid gap-2 text-sm font-medium">
                업무 필드
                <Input placeholder="vendor_name, key, item_name" value={entryForm.field ?? ""} onChange={(event) => setEntryForm({ ...entryForm, field: event.target.value })} />
              </label>
              <label className="grid gap-2 text-sm font-medium">
                출처
                <Input value={entryForm.source ?? "manual"} onChange={(event) => setEntryForm({ ...entryForm, source: event.target.value })} />
              </label>
              <label className="grid gap-2 text-sm font-medium">
                메모
                <Input value={entryForm.memo ?? ""} onChange={(event) => setEntryForm({ ...entryForm, memo: event.target.value })} />
              </label>
              {!editingEntry ? (
                <label className="grid gap-2 text-sm font-medium">
                  초기 별칭
                  <Input placeholder="쉼표로 여러 개 입력" value={entryForm.aliases?.join(", ") ?? ""} onChange={(event) => setEntryForm({ ...entryForm, aliases: splitAliases(event.target.value) })} />
                </label>
              ) : null}
              <label className="flex items-center gap-2 text-sm font-medium">
                <input type="checkbox" checked={entryForm.active ?? true} onChange={(event) => setEntryForm({ ...entryForm, active: event.target.checked })} />
                활성 항목
              </label>
              <div className="flex items-end gap-2 md:col-span-2">
                <Button type="submit">저장</Button>
                <Button type="button" variant="outline" onClick={() => { setShowEntryForm(false); setEditingEntry(null); setEntryForm(emptyEntryForm); }}>취소</Button>
              </div>
            </form>
          </CardContent>
        </Card>
      ) : null}

      <Card className="mt-6 overflow-hidden">
        <CardHeader className="border-b bg-slate-50/70">
          <CardTitle>사전 테이블</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {loading ? (
            <div className="flex items-center justify-center gap-2 p-12 text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              도메인 사전을 불러오는 중입니다.
            </div>
          ) : entries.length ? (
            <div className="overflow-x-auto">
              <table className="min-w-[980px] w-full text-sm">
                <thead className="bg-slate-50 text-left text-xs text-muted-foreground">
                  <tr>
                    {["유형", "정식값", "정규화", "업무 필드", "출처", "별칭", "상태", "최근 수정", ""].map((header) => (
                      <th key={header} className="px-4 py-3 font-medium">{header}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {entries.map((entry) => (
                    <tr key={entry.id} className="border-t">
                      <td className="px-4 py-3"><Badge>{dictionaryTypeLabels[entry.dictionary_type] ?? entry.dictionary_type}</Badge></td>
                      <td className="px-4 py-3 font-semibold">{entry.canonical_value}</td>
                      <td className="px-4 py-3 text-muted-foreground">{entry.normalized_value || "-"}</td>
                      <td className="px-4 py-3">{entry.field || "-"}</td>
                      <td className="px-4 py-3">{entry.source}</td>
                      <td className="px-4 py-3">{entry.aliases.filter((alias) => alias.active).length}</td>
                      <td className="px-4 py-3"><Badge variant={entry.active ? "default" : "outline"}>{entry.active ? "활성" : "비활성"}</Badge></td>
                      <td className="px-4 py-3 text-muted-foreground">{formatDateTime(entry.updated_at)}</td>
                      <td className="px-4 py-3">
                        <div className="flex justify-end gap-1">
                          <Button type="button" variant="outline" size="sm" onClick={() => openEditEntry(entry)}>
                            <Edit3 className="size-3.5" />
                            수정
                          </Button>
                          <Button type="button" variant="outline" size="sm" onClick={() => openAliases(entry)}>
                            <Tags className="size-3.5" />
                            별칭
                          </Button>
                          <Button type="button" variant="outline" size="sm" disabled={!entry.active} onClick={() => deactivateEntry(entry.id)}>비활성화</Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-12 text-center text-muted-foreground">등록된 사전 항목이 없습니다.</div>
          )}
        </CardContent>
      </Card>

      {aliasEntry ? (
        <Card className="mt-6 border-primary/30">
          <CardHeader>
            <CardTitle>별칭 관리: {aliasEntry.canonical_value}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-5">
            <div className="overflow-x-auto rounded-lg border">
              <table className="min-w-[720px] w-full text-sm">
                <thead className="bg-slate-50 text-left text-xs text-muted-foreground">
                  <tr>
                    {["별칭", "정규화", "출처", "신뢰도", "활성", ""].map((header) => (
                      <th key={header} className="px-3 py-2 font-medium">{header}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {aliasEntry.aliases.length ? aliasEntry.aliases.map((alias) => (
                    <tr key={alias.id} className="border-t">
                      <td className="px-3 py-2 font-semibold">{alias.alias_value}</td>
                      <td className="px-3 py-2 text-muted-foreground">{alias.normalized_alias_value || "-"}</td>
                      <td className="px-3 py-2">{alias.source}</td>
                      <td className="px-3 py-2">{alias.confidence ?? "-"}</td>
                      <td className="px-3 py-2">{alias.active ? "활성" : "비활성"}</td>
                      <td className="px-3 py-2">
                        <div className="flex justify-end gap-1">
                          <Button type="button" variant="outline" size="sm" onClick={() => openEditAlias(alias)}>수정</Button>
                          <Button type="button" variant="outline" size="sm" disabled={!alias.active} onClick={() => deactivateAlias(alias)}>비활성화</Button>
                        </div>
                      </td>
                    </tr>
                  )) : (
                    <tr>
                      <td className="px-3 py-8 text-center text-muted-foreground" colSpan={6}>등록된 별칭이 없습니다.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <form onSubmit={saveAlias} className="grid gap-3 md:grid-cols-4">
              <label className="grid gap-2 text-sm font-medium">
                별칭
                <Input value={aliasForm.alias_value} onChange={(event) => setAliasForm({ ...aliasForm, alias_value: event.target.value })} />
              </label>
              <label className="grid gap-2 text-sm font-medium">
                출처
                <Input value={aliasForm.source ?? "manual"} onChange={(event) => setAliasForm({ ...aliasForm, source: event.target.value })} />
              </label>
              <label className="grid gap-2 text-sm font-medium">
                신뢰도
                <Input type="number" min="0" max="1" step="0.001" value={aliasForm.confidence ?? ""} onChange={(event) => setAliasForm({ ...aliasForm, confidence: event.target.value })} />
              </label>
              <label className="flex items-center gap-2 text-sm font-medium">
                <input type="checkbox" checked={aliasForm.active ?? true} onChange={(event) => setAliasForm({ ...aliasForm, active: event.target.checked })} />
                활성 별칭
              </label>
              <div className="flex gap-2 md:col-span-4">
                <Button type="submit">{editingAlias ? "별칭 수정" : "별칭 추가"}</Button>
                <Button type="button" variant="outline" onClick={() => { setEditingAlias(null); setAliasForm(emptyAliasForm); }}>입력 초기화</Button>
                <Button type="button" variant="outline" onClick={() => setAliasEntry(null)}>닫기</Button>
              </div>
            </form>
          </CardContent>
        </Card>
      ) : null}
    </main>
  );
}

function splitAliases(value: string) {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}
