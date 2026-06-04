"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import {
  AlertTriangle,
  Bot,
  CheckCheck,
  Download,
  FileText,
  FolderKanban,
  Loader2,
  RefreshCw,
  Save,
  ShieldCheck,
  Sparkles,
  Star,
  Tag,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { StatusBadge } from "@/components/status-badge";
import { CategorySelector } from "@/components/category-selector";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { WorkflowPanel } from "@/components/workflow-panel";
import { api } from "@/lib/api";
import { businessFieldDate, documentFieldLabels, documentSummaryDetailed, extractionMethodLabel, formatDateTime, normalizedReviewIssues, primaryCategoryLabel, titleCaseLabel } from "@/lib/utils";
import type { DocumentRecord, DocumentUpdate, FolderSummary, ManufacturingLineItem } from "@/types/document";

const detailTabs = ["original", "extracted", "ai"] as const;
type DetailTab = (typeof detailTabs)[number];

const numericLineItemFields = new Set<keyof ManufacturingLineItem>(["quantity", "unit_price", "supply_amount", "tax_amount", "line_total"]);
const warningTextPattern = /(비어 있습니다|미확인|신뢰도 낮음|확인 필요|장부 매칭|검토 필요)/;

function cleanLineItemValue(field: keyof ManufacturingLineItem, value: unknown) {
  if (value === null || value === undefined) return "";
  const text = String(value).trim();
  if (!text || warningTextPattern.test(text)) return "";
  if (numericLineItemFields.has(field)) {
    const numeric = text.replace(/[,₩원\s]/g, "");
    return /^-?\d+(\.\d+)?$/.test(numeric) ? numeric : "";
  }
  if ((field === "item_code" || field === "internal_item_code") && warningTextPattern.test(text)) return "";
  return text;
}

function cleanLineItems(items: ManufacturingLineItem[]) {
  return (items || []).map((item) => ({
    ...item,
    item_name: cleanLineItemValue("item_name", item.item_name),
    item_code: cleanLineItemValue("item_code", item.item_code),
    source_item_name: item.source_item_name ?? item.item_name ?? null,
    source_item_code: item.source_item_code ?? item.item_code ?? null,
    internal_item_code: cleanLineItemValue("internal_item_code", item.internal_item_code),
    specification: cleanLineItemValue("specification", item.specification),
    quantity: cleanLineItemValue("quantity", item.quantity),
    unit: cleanLineItemValue("unit", item.unit),
    unit_price: cleanLineItemValue("unit_price", item.unit_price),
    supply_amount: cleanLineItemValue("supply_amount", item.supply_amount),
    tax_amount: cleanLineItemValue("tax_amount", item.tax_amount),
    line_total: cleanLineItemValue("line_total", item.line_total),
    item_master_match_status: item.item_master_match_status ?? null,
    item_master_match_confidence: item.item_master_match_confidence ?? null,
    item_master_candidates: item.item_master_candidates ?? [],
    item_master_match_reason: item.item_master_match_reason ?? null,
  }));
}

function itemMasterStatusLabel(status: string | null | undefined) {
  return {
    auto_matched: "자동 매칭됨",
    needs_review: "검토 필요",
    unmatched: "미매칭",
    skipped_no_item_master: "품목마스터 없음",
  }[status || ""] ?? "확인 전";
}

function itemMasterStatusClass(status: string | null | undefined) {
  if (status === "auto_matched") return "border-emerald-200 bg-emerald-50 text-emerald-800";
  if (status === "needs_review") return "border-amber-300 bg-amber-50 text-amber-800";
  if (status === "unmatched" || status === "skipped_no_item_master") return "border-slate-300 bg-slate-50 text-slate-700";
  return "border-slate-200 bg-white text-slate-600";
}

function toForm(document: DocumentRecord): DocumentUpdate & { tags_text: string } {
  const businessFields = (document.workflow_metadata?.business_fields ?? {}) as Record<string, unknown>;
  const transactionDate = typeof businessFields.transaction_date === "string" ? businessFields.transaction_date : document.extracted_date;
  const issueDate = document.document_type === "transaction_statement" ? transactionDate : document.issue_date;
  const roleDate = document.document_type === "transaction_statement" ? document.issue_date : businessFieldDate(document);
  return {
    title: document.title ?? "",
    raw_text: document.raw_text ?? "",
    extracted_date: document.extracted_date ?? "",
    extracted_amount: document.extracted_amount ?? "",
    subtotal: document.subtotal ?? "",
    tax: document.tax ?? "",
    currency: document.currency ?? "",
    merchant_name: document.merchant_name ?? "",
    vendor_name: document.vendor_name ?? "",
    customer_name: document.customer_name ?? "",
    document_number: document.document_number ?? "",
    issue_date: issueDate ?? "",
    due_date: roleDate ?? "",
    line_items: cleanLineItems(document.line_items ?? []),
    low_confidence_fields: document.low_confidence_fields ?? [],
    category: document.category ?? "",
    tags: document.tags,
    summary: document.summary ?? "",
    is_favorite: document.is_favorite,
    tags_text: document.tags.join(", "),
  } as DocumentUpdate & { tags_text: string };
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function readList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.trim().length > 0) : [];
}

function InfoGrid({ items }: { items: Array<[string, string | null | undefined]> }) {
  const present = items.filter(([, value]) => value);
  if (!present.length) return null;
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {present.map(([label, value]) => (
        <div key={label} className="rounded-lg border bg-white p-4">
          <p className="text-xs text-muted-foreground">{label}</p>
          <p className="mt-1 text-sm font-semibold">{value}</p>
        </div>
      ))}
    </div>
  );
}

export default function DocumentDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [document, setDocument] = useState<DocumentRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [activeTab, setActiveTab] = useState<DetailTab>("original");
  const [categories, setCategories] = useState<FolderSummary[]>([]);
  const form = useForm<DocumentUpdate & { tags_text: string }>();

  const syncDocument = useCallback((item: DocumentRecord) => {
    setDocument(item);
    form.reset(toForm(item));
  }, [form]);

  useEffect(() => {
    api
      .get(params.id)
      .then(syncDocument)
      .catch((error) => toast.error(error instanceof Error ? error.message : "문서를 불러오지 못했습니다"))
      .finally(() => setLoading(false));
    api.categories().then(setCategories).catch(() => setCategories([]));
  }, [params.id, syncDocument]);

  async function onSubmit(values: DocumentUpdate & { tags_text: string }) {
    setSaving(true);
    const { tags_text, ...fields } = values;
    const isTransactionStatement = document?.document_type === "transaction_statement";
    const payload: DocumentUpdate = {
      ...fields,
      title: values.title || null,
      raw_text: values.raw_text || null,
      extracted_date: values.issue_date || values.extracted_date || null,
      extracted_amount: values.extracted_amount || null,
      subtotal: values.subtotal || null,
      tax: values.tax || null,
      currency: values.currency || null,
      merchant_name: values.merchant_name || null,
      vendor_name: values.vendor_name || null,
      customer_name: values.customer_name || null,
      document_number: values.document_number || null,
      issue_date: isTransactionStatement ? values.due_date || values.issue_date || null : values.issue_date || null,
      due_date: isTransactionStatement ? null : values.due_date || null,
      line_items: cleanLineItems(values.line_items || []),
      low_confidence_fields: values.low_confidence_fields || [],
      category: values.category || null,
      summary: values.summary || null,
      tags: tags_text
        .split(",")
        .map((tag) => tag.trim())
        .filter(Boolean),
    };
    try {
      const updated = await api.update(params.id, payload);
      syncDocument(updated);
      toast.success("수정 내용을 저장했습니다");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "저장에 실패했습니다");
    } finally {
      setSaving(false);
    }
  }

  async function reprocess() {
    setSaving(true);
    try {
      const updated = await api.reprocess(params.id);
      syncDocument(updated);
      toast.success("다시 처리를 시작했습니다");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "다시 처리하지 못했습니다");
    } finally {
      setSaving(false);
    }
  }

  async function confirmDocument() {
    setSaving(true);
    try {
      const updated = await api.confirm(params.id);
      syncDocument(updated);
      toast.success("확정 완료로 변경했습니다");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "확정 처리에 실패했습니다");
    } finally {
      setSaving(false);
    }
  }

  async function markNeedsReview() {
    setSaving(true);
    try {
      const updated = await api.markNeedsReview(params.id);
      syncDocument(updated);
      toast.success("검토 필요 상태로 변경했습니다");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "검토 상태를 변경하지 못했습니다");
    } finally {
      setSaving(false);
    }
  }

  async function toggleFavorite() {
    try {
      const updated = await api.toggleFavorite(params.id);
      syncDocument(updated);
      toast.success(updated.is_favorite ? "즐겨찾기에 추가했습니다" : "즐겨찾기에서 제거했습니다");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "즐겨찾기 상태를 변경하지 못했습니다");
    }
  }

  async function remove() {
    if (!window.confirm("이 문서와 업로드한 원본 파일을 삭제할까요?")) return;
    try {
      await api.remove(params.id);
      toast.success("문서를 삭제했습니다");
      router.push(searchParams.get("from") || "/documents");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "삭제에 실패했습니다");
    }
  }

  function updateLineItem(index: number, field: keyof ManufacturingLineItem, value: string) {
    const items = [...(form.getValues("line_items") || [])];
    items[index] = { ...(items[index] || {}), [field]: cleanLineItemValue(field, value) };
    form.setValue("line_items", items, { shouldDirty: true });
  }

  function addLineItem() {
    const items = [...(form.getValues("line_items") || [])];
    items.push({
      item_name: "",
      item_code: "",
      internal_item_code: "",
      specification: "",
      quantity: "",
      unit: "",
      unit_price: "",
      supply_amount: "",
      tax_amount: "",
      line_total: "",
    });
    form.setValue("line_items", items, { shouldDirty: true });
  }

  function selectItemMasterCandidate(index: number, candidate: NonNullable<ManufacturingLineItem["item_master_candidates"]>[number]) {
    const items = [...(form.getValues("line_items") || [])];
    items[index] = {
      ...(items[index] || {}),
      internal_item_code: candidate.internal_item_code,
      item_master_match_status: "auto_matched",
      item_master_match_confidence: candidate.score,
      item_master_match_reason: "USER_SELECTED_CANDIDATE",
    };
    form.setValue("line_items", items, { shouldDirty: true });
  }

  function removeLineItem(index: number) {
    const items = [...(form.getValues("line_items") || [])];
    items.splice(index, 1);
    form.setValue("line_items", items, { shouldDirty: true });
  }

  const categoryInterpretation = useMemo(
    () => (document?.workflow_metadata?.category_interpretation ?? document?.ingestion_metadata?.category_interpretation ?? null) as Record<string, unknown> | null,
    [document]
  );

  if (loading) {
    return (
      <main className="shell py-8">
        <div className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
          <div className="h-[34rem] animate-pulse rounded-xl bg-muted" />
          <div className="h-[34rem] animate-pulse rounded-xl bg-muted" />
        </div>
      </main>
    );
  }

  if (!document) {
    return (
      <main className="shell py-8">
        <Card>
          <CardContent className="p-12 text-center text-muted-foreground">문서를 찾을 수 없습니다.</CardContent>
        </Card>
      </main>
    );
  }

  const isImage = document.mime_type.startsWith("image/");
  const categoryLabel = primaryCategoryLabel(document);
  const categoryProfile = readString(categoryInterpretation?.profile);
  const titleHint = readString(categoryInterpretation?.title_hint);
  const surfacedFields = readList(categoryInterpretation?.surfaced_fields);
  const isConfirmed = document.processing_status === "confirmed";
  const selectedCategory = form.watch("category") ?? "";
  const lineItems = form.watch("line_items") ?? [];
  const reviewIssues = normalizedReviewIssues(document);
  const lowConfidenceFields = document.low_confidence_fields ?? [];
  const fieldLabels = documentFieldLabels(document.document_type);

  return (
    <main className="shell py-8">
      <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-3xl">
          <div className="mb-3 flex flex-wrap gap-2">
            <StatusBadge status={document.processing_status} />
            <Badge className="bg-accent text-accent-foreground">{categoryLabel}</Badge>
            {document.source_file_type ? <Badge variant="outline">{titleCaseLabel(document.source_file_type)}</Badge> : null}
            {document.is_favorite ? <Badge className="border-amber-300 bg-amber-50 text-amber-800">즐겨찾기</Badge> : null}
          </div>
          <h1 className="text-3xl font-semibold tracking-normal">{document.title || titleHint || document.original_filename}</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {document.original_filename} · 최근 수정 {formatDateTime(document.updated_at)}
          </p>
          <p className="mt-3 max-w-2xl text-sm text-muted-foreground">
            {documentSummaryDetailed(document, 700)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant={isConfirmed ? "secondary" : "default"} onClick={confirmDocument} disabled={saving || isConfirmed}>
            <CheckCheck className="size-4" />
            {isConfirmed ? "확정 완료" : "확정 처리"}
          </Button>
          <Button variant="outline" onClick={markNeedsReview} disabled={saving}>
            <AlertTriangle className="size-4" />
            검토 필요
          </Button>
          <Button variant="outline" onClick={toggleFavorite}>
            <Star className={`size-4 ${document.is_favorite ? "fill-amber-400 text-amber-400" : ""}`} />
            {document.is_favorite ? "즐겨찾기" : "즐겨찾기"}
          </Button>
          <Button variant="outline" onClick={reprocess} disabled={saving}>
            <RefreshCw className="size-4" />
            다시 처리
          </Button>
          <Button asChild variant="outline">
            <a href={api.exportJsonUrl(document.id)}>
              <Download className="size-4" />
              JSON으로 내보내기
            </a>
          </Button>
          <Button variant="destructive" onClick={remove}>
            <Trash2 className="size-4" />
            삭제
          </Button>
        </div>
      </div>

      <div className="mb-6 grid gap-4 lg:grid-cols-3">
        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">문서 유형</p>
            <p className="mt-1 font-semibold">{categoryLabel}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">AI 분류 프로필</p>
            <p className="mt-1 font-semibold">{categoryProfile ? titleCaseLabel(categoryProfile) : "표시된 값 없음"}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">추출 경로</p>
            <p className="mt-1 break-words text-sm font-semibold">{extractionMethodLabel(document)}</p>
          </CardContent>
        </Card>
      </div>

      {document.processing_error ? (
        <Card className="mb-6 border-red-200 bg-red-50">
          <CardContent className="p-4 text-sm text-red-800">{document.processing_error}</CardContent>
        </Card>
      ) : null}

      <div className="mb-4 flex flex-wrap gap-2">
        {detailTabs.map((tab) => (
          <button
            key={tab}
            type="button"
            onClick={() => setActiveTab(tab)}
            className={`rounded-full border px-4 py-2 text-sm transition ${
              activeTab === tab ? "border-primary bg-primary text-primary-foreground" : "bg-white text-muted-foreground hover:border-primary/40"
            }`}
          >
            {tab === "ai" ? "AI 추출 결과" : tab === "extracted" ? "원문 텍스트" : "원본 문서"}
          </button>
        ))}
      </div>

      <form onSubmit={form.handleSubmit(onSubmit)} className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
        <section className="space-y-6">
          {activeTab === "original" ? (
            <Card>
              <CardHeader>
                <CardTitle>원본 문서</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                {isImage ? (
                  <div className="relative h-[42rem] max-h-[72vh] w-full rounded-lg border bg-white">
                    <Image src={document.file_url} alt={document.original_filename} fill unoptimized className="object-contain" />
                  </div>
                ) : (
                  <div className="flex min-h-72 flex-col items-center justify-center rounded-lg border bg-white p-8 text-center">
                    <FileText className="mb-3 size-10 text-primary" />
                    <p className="font-semibold">{document.original_filename}</p>
                    <p className="mt-1 text-sm text-muted-foreground">{document.mime_type}</p>
                    <Button asChild variant="outline" className="mt-4">
                      <a href={document.file_url}>원본 열기</a>
                    </Button>
                  </div>
                )}
                <InfoGrid
                  items={[
                    ["파일 형식", titleCaseLabel(document.source_file_type || "unknown")],
                    ["추출 방식", extractionMethodLabel(document)],
                    ["업로드 날짜", formatDateTime(document.created_at)],
                    ["최근 수정", formatDateTime(document.updated_at)],
                  ]}
                />
              </CardContent>
            </Card>
          ) : null}

          {activeTab === "extracted" ? (
            <Card>
              <CardHeader>
                <CardTitle>원문 텍스트</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <Textarea className="min-h-[38rem] font-mono text-xs" {...form.register("raw_text")} />
                <InfoGrid
                  items={[
                    ["추출 제공자", document.extraction_provider || "확인 불가"],
                    ["보정 제공자", document.refinement_provider || "사용 안 함"],
                    ["신뢰도", document.ai_confidence_score ? `${Math.round(Number(document.ai_confidence_score) * 100)}%` : null],
                    ["검토 상태", document.review_required ? "사람이 확인해야 합니다" : "자동 추출 완료"],
                  ]}
                />
              </CardContent>
            </Card>
          ) : null}

          {activeTab === "ai" ? (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Sparkles className="size-5 text-primary" />
                  AI 추출 결과
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <InfoGrid
                  items={[
                    ["문서 유형", categoryLabel],
                    ["분류 프로필", categoryProfile ? titleCaseLabel(categoryProfile) : null],
                    ["제목 후보", titleHint],
                  ]}
                />
                {document.ai_extraction_notes ? (
                  <div className="rounded-lg border bg-white p-4 text-sm text-muted-foreground whitespace-pre-line">
                    {document.ai_extraction_notes}
                  </div>
                ) : null}
                {surfacedFields.length ? (
                  <div className="rounded-lg border bg-white p-4">
                    <p className="mb-3 text-xs font-medium uppercase text-muted-foreground">검토 필요 항목</p>
                    <div className="flex flex-wrap gap-2">
                      {surfacedFields.map((field) => (
                        <Badge key={field} variant="outline">
                          {titleCaseLabel(field)}
                        </Badge>
                      ))}
                    </div>
                  </div>
                ) : null}
                {document.field_sources ? (
                  <div className="rounded-lg border bg-white p-4">
                    <p className="mb-3 text-xs font-medium uppercase text-muted-foreground">필드별 추출 출처</p>
                    <div className="grid gap-2 text-sm sm:grid-cols-2">
                      {Object.entries(document.field_sources).map(([field, source]) => (
                        <div key={field} className="flex items-center justify-between rounded-md border px-3 py-2">
                          <span>{titleCaseLabel(field)}</span>
                          <span className="text-muted-foreground">{source}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </CardContent>
            </Card>
          ) : null}
        </section>

        <section className="space-y-6">
          <WorkflowPanel document={document} />
          <Card className={document.review_required ? "border-amber-300 bg-amber-50/40" : ""}>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Bot className="size-5 text-primary" />
                문서 검토
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {document.review_required ? (
                <div className="flex gap-2 rounded-lg border border-amber-300 bg-amber-100/60 p-3 text-sm text-amber-900">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                  일부 항목은 확인이 필요합니다. 신뢰도 낮은 필드를 검토하고 수정한 뒤 확정 처리하세요.
                </div>
              ) : null}
              <InfoGrid
                items={[
                  ["문서 유형", categoryLabel],
                  ["처리 상태", titleCaseLabel(document.processing_status)],
                  ["검토 상태", document.review_required ? "사람이 확인해야 합니다" : "자동 추출 완료"],
                  ["검토 필요 항목", reviewIssues.length ? reviewIssues.map((issue) => issue.message_ko).join(", ") : "없음"],
                ]}
              />
            </CardContent>
          </Card>
          <Card className="overflow-hidden">
            <CardHeader className="border-b bg-slate-50/70">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-medium uppercase tracking-normal text-muted-foreground">추출된 업무 데이터</p>
                  <CardTitle className="mt-1 flex items-center gap-2">
                    <ShieldCheck className="size-5 text-primary" />
                    문서 검토 및 수정
                  </CardTitle>
                </div>
                <Badge variant="outline">{categoryLabel}</Badge>
              </div>
            </CardHeader>
            <CardContent className="grid gap-5 p-5">
              <section className="rounded-lg border bg-white p-4">
                <div className="mb-3 flex items-center gap-2">
                  <FolderKanban className="size-4 text-primary" />
                  <div>
                    <p className="text-sm font-semibold">문서 유형</p>
                    <p className="text-xs text-muted-foreground">발주서, 견적서, 거래명세서, 납품서 등 업무 문서 유형을 확인하세요.</p>
                  </div>
                </div>
                <CategorySelector
                  value={selectedCategory}
                  folders={categories}
                  onChange={(value) => form.setValue("category", value, { shouldDirty: true })}
                />
                <p className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
                  <Tag className="size-3.5" />
                  문서 유형은 검색, 필터, 내보내기 데이터에 함께 반영됩니다.
                </p>
              </section>

              <section className="grid gap-4 rounded-lg border bg-slate-50/60 p-4">
                <div>
                  <p className="text-sm font-semibold">문서 기본 정보</p>
                  <p className="mt-1 text-xs text-muted-foreground">공급업체, 고객사, 문서번호, 날짜, 금액을 ERP 입력 기준으로 수정하세요.</p>
                </div>
                <label className="grid gap-2 text-sm font-medium">
                  제목
                  <Input {...form.register("title")} />
                </label>
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="grid gap-2 text-sm font-medium">
                    {fieldLabels.issueDate}
                    <Input type="date" {...form.register("issue_date")} />
                  </label>
                  <label className="grid gap-2 text-sm font-medium">
                    {fieldLabels.dueDate}
                    <Input type="date" {...form.register("due_date")} />
                  </label>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="grid gap-2 text-sm font-medium">
                    공급업체
                    <Input {...form.register("vendor_name")} />
                  </label>
                  <label className="grid gap-2 text-sm font-medium">
                    고객사
                    <Input {...form.register("customer_name")} />
                  </label>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="grid gap-2 text-sm font-medium">
                    {fieldLabels.documentNumber}
                    <Input {...form.register("document_number")} />
                  </label>
                  <label className="grid gap-2 text-sm font-medium">
                    합계금액
                    <Input type="number" min="0" step="0.01" {...form.register("extracted_amount")} />
                  </label>
                </div>
                <div className="grid gap-4 sm:grid-cols-3">
                  <label className="grid gap-2 text-sm font-medium">
                    공급가액
                    <Input type="number" min="0" step="0.01" {...form.register("subtotal")} />
                  </label>
                  <label className="grid gap-2 text-sm font-medium">
                    세액
                    <Input type="number" min="0" step="0.01" {...form.register("tax")} />
                  </label>
                  <label className="grid gap-2 text-sm font-medium">
                    통화
                    <Input placeholder="KRW" {...form.register("currency")} />
                  </label>
                </div>
              </section>

              <section className="grid gap-4 rounded-lg border bg-white p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold">품목 정보</p>
                    <p className="mt-1 text-xs text-muted-foreground">품목명, 문서 품목코드, 내부 품목코드, 규격, 수량, 단가, 공급가액, 세액, 합계금액을 확인하세요.</p>
                  </div>
                  <Button type="button" variant="outline" size="sm" onClick={addLineItem}>품목 추가</Button>
                </div>
                {lineItems.length ? (
                  <div className="overflow-x-auto rounded-lg border">
                    <table className="min-w-[1180px] w-full text-sm">
                      <thead className="bg-slate-50 text-left text-xs text-muted-foreground">
                        <tr>
                          {["품목명", "문서 품목코드", "내부 품목코드", "규격", "수량", "단위", "단가", "공급가액", "세액", "합계금액", "매칭 상태", ""].map((header) => (
                            <th key={header} className="px-2 py-2 font-medium">{header}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {lineItems.map((item, index) => (
                          <tr key={index} className="border-t">
                            {([
                              ["item_name", "품목명"],
                              ["item_code", "문서 품목코드"],
                              ["internal_item_code", "내부 품목코드"],
                              ["specification", "규격"],
                              ["quantity", "수량"],
                              ["unit", "단위"],
                              ["unit_price", "단가"],
                              ["supply_amount", "공급가액"],
                              ["tax_amount", "세액"],
                              ["line_total", "합계금액"],
                            ] as Array<[keyof ManufacturingLineItem, string]>).map(([field, label]) => {
                              const itemCode = `item_${index + 1}`;
                              const structuredLowCodes = [
                                field === "item_code" ? `missing_item_code:${itemCode}` : null,
                                field === "internal_item_code" ? `item_master_match_required:${itemCode}` : null,
                                field === "internal_item_code" ? `item_master_unmatched:${itemCode}` : null,
                                field === "internal_item_code" ? "item_matching_skipped" : null,
                                field === "item_name" ? `missing_item_name:${itemCode}` : null,
                                field === "quantity" ? `missing_quantity:${itemCode}` : null,
                                field === "unit_price" || field === "line_total" ? `missing_price_or_total:${itemCode}` : null,
                              ].filter(Boolean);
                              const fieldIssues = reviewIssues.filter((issue) => issue.item_index === index && issue.field === `line_items.${field}`);
                              const low =
                                fieldIssues.length > 0 ||
                                structuredLowCodes.some((code) => lowConfidenceFields.includes(code as string)) ||
                                lowConfidenceFields.includes(`line_items[${index + 1}].${field}`) ||
                                (field === "line_total" && lowConfidenceFields.includes("missing_line_items"));
                              return (
                                <td key={field} className="px-2 py-2 align-top">
                                  <Input
                                    aria-label={`${index + 1}행 ${label}`}
                                    className={low ? "border-amber-400 bg-amber-50" : ""}
                                    value={String(item?.[field] ?? "")}
                                    onChange={(event) => updateLineItem(index, field, event.target.value)}
                                  />
                                  {fieldIssues.length ? (
                                    <div className="mt-1 flex flex-wrap gap-1">
                                      {fieldIssues.map((issue) => (
                                        <Badge key={`${issue.code}-${issue.message_ko}`} className="border-amber-300 bg-amber-50 text-[11px] text-amber-800">
                                          {issue.message_ko.replace(/^\d+번째 품목\s*/, "")}
                                        </Badge>
                                      ))}
                                    </div>
                                  ) : low ? <p className="mt-1 text-[11px] text-amber-700">확인 필요</p> : null}
                                </td>
                              );
                            })}
                            <td className="px-2 py-2 align-top">
                              <Badge variant="outline" className={itemMasterStatusClass(item.item_master_match_status)}>
                                {itemMasterStatusLabel(item.item_master_match_status)}
                              </Badge>
                              {item.item_master_match_confidence ? (
                                <p className="mt-1 text-[11px] text-muted-foreground">신뢰도 {Math.round(Number(item.item_master_match_confidence) * 100)}%</p>
                              ) : null}
                              {item.item_master_candidates?.length ? (
                                <div className="mt-2 grid gap-1">
                                  {item.item_master_candidates.slice(0, 3).map((candidate) => (
                                    <div key={candidate.internal_item_code} className="rounded-md border bg-white p-2 text-xs">
                                      <div className="flex items-center justify-between gap-2">
                                        <span className="font-semibold">{candidate.internal_item_code}</span>
                                        <Button type="button" variant="outline" size="sm" onClick={() => selectItemMasterCandidate(index, candidate)}>
                                          이 품목으로 선택
                                        </Button>
                                      </div>
                                      <p className="mt-1 text-muted-foreground">{candidate.item_name} · {candidate.spec || "규격 없음"} · {candidate.unit || "단위 없음"}</p>
                                      <p className="mt-1 text-muted-foreground">후보 신뢰도 {Math.round(Number(candidate.score) * 100)}%</p>
                                    </div>
                                  ))}
                                </div>
                              ) : null}
                            </td>
                            <td className="px-2 py-2 align-top">
                              <Button type="button" variant="outline" size="sm" onClick={() => removeLineItem(index)}>삭제</Button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
                    품목 정보가 추출되지 않았습니다. 사람이 확인해야 합니다.
                  </div>
                )}
              </section>

              <section className="grid gap-4 rounded-lg border bg-white p-4">
                <div>
                  <p className="text-sm font-semibold">검토 메모</p>
                  <p className="mt-1 text-xs text-muted-foreground">태그와 설명은 나중에 문서를 검색하고 업무 맥락을 확인하는 데 사용됩니다.</p>
                </div>
                <label className="grid gap-2 text-sm font-medium">
                  태그
                  <Input placeholder="발주서, 검토필요, 2026-06" {...form.register("tags_text")} />
                </label>
                <label className="grid gap-2 text-sm font-medium">
                  업무 메모
                  <Textarea className="min-h-28" {...form.register("summary")} />
                </label>
              </section>

              <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-slate-50 p-3">
                <Link href="/review" className="text-sm text-muted-foreground underline-offset-4 hover:underline">
                  검토 필요 목록 열기
                </Link>
                <Button type="submit" disabled={saving}>
                  {saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
                  수정 저장
                </Button>
              </div>
            </CardContent>
          </Card>
        </section>
      </form>
    </main>
  );
}
