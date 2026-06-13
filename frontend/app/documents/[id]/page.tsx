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
import { TaxonomyBadges } from "@/components/taxonomy-badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { WorkflowPanel } from "@/components/workflow-panel";
import { api } from "@/lib/api";
import { cleanLineItemValue, cleanLineItems, numericLineItemFields } from "@/lib/line-items";
import { blockingReviewIssues, businessFieldDate, documentDisplayTitle, documentFieldLabels, documentProfileLabel, documentReviewMetadata, documentSubtypeLabel, documentSummaryDetailed, documentTaxonomy, extractionMethodLabel, formatDateTime, getDocumentScheduleDate, getErpReadinessStatus, getErpReadinessSummary, groupedReviewIssues, informationalReviewIssues, layoutDebugMetadata, layoutProfileLabel, primaryCategoryLabel, profileLabelForDocument, reviewIssueAmountLines, reviewIssueDescription, reviewIssueProgressCounts, reviewIssueSummary, reviewIssueSummaryItems, taxonomyPolicyLines, titleCaseLabel } from "@/lib/utils";
import type { DocumentRecord, DocumentUpdate, FolderSummary, ManufacturingLineItem } from "@/types/document";

const detailTabs = ["extracted", "ai"] as const;
type DetailTab = (typeof detailTabs)[number];

function itemMasterStatusLabel(status: string | null | undefined) {
  return {
    auto_matched: "자동 매칭됨",
    direct_code_match: "직접 코드 매칭",
    alias_matched: "별칭 매칭됨",
    user_selected: "사용자 선택",
    manual_confirmed: "사용자 확정",
    ambiguous: "후보 확인 필요",
    needs_review: "검토 필요",
    unmatched: "미매칭",
    skipped_no_item_master: "품목마스터 없음",
  }[status || ""] ?? "확인 전";
}

function itemMasterStatusClass(status: string | null | undefined) {
  if (status === "auto_matched" || status === "direct_code_match" || status === "alias_matched" || status === "user_selected" || status === "manual_confirmed") return "border-emerald-200 bg-emerald-50 text-emerald-800";
  if (status === "needs_review" || status === "ambiguous") return "border-amber-300 bg-amber-50 text-amber-800";
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

function TaxonomyPolicyCard({ document }: { document: DocumentRecord }) {
  const taxonomy = documentTaxonomy(document);
  const subtype = documentSubtypeLabel(taxonomy.document_subtype);
  const profileLabels = (taxonomy.document_profiles || []).map(documentProfileLabel).filter((label): label is string => Boolean(label));
  const layout = layoutProfileLabel(taxonomy.layout_profile);
  const policyLines = taxonomyPolicyLines(taxonomy);
  if (!subtype && !profileLabels.length && !layout && !policyLines.length) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="size-5 text-primary" />
          업무 분류 / 처리 정책
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <InfoGrid
          items={[
            ["업무 분류", subtype],
            ["처리 정책", profileLabels.join(", ") || null],
            ["레이아웃", layout],
          ]}
        />
        {policyLines.length ? (
          <div className="rounded-lg border bg-slate-50 p-4 text-sm text-slate-700">
            <p className="mb-2 text-xs font-medium uppercase text-muted-foreground">안내</p>
            <ul className="space-y-1">
              {policyLines.map((line) => <li key={line}>{line}</li>)}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ErpReadinessBanner({
  document,
  openIssueCount,
}: {
  document: DocumentRecord;
  openIssueCount: number;
}) {
  const readiness = getErpReadinessStatus(document);
  const summary = getErpReadinessSummary(document);
  const schedule = getDocumentScheduleDate(document);
  const layoutDebug = layoutDebugMetadata(document);
  const toneClass = {
    success: "border-emerald-300 bg-emerald-50 text-emerald-950",
    warning: "border-amber-300 bg-amber-50 text-amber-950",
    danger: "border-red-300 bg-red-50 text-red-950",
    processing: "border-blue-300 bg-blue-50 text-blue-950",
  }[readiness.tone];
  return (
    <Card className={`${toneClass} mb-6`}>
      <CardContent className="grid gap-4 p-5 lg:grid-cols-[1fr_auto] lg:items-center">
        <div>
          <p className="text-sm font-medium">ERP/엑셀 입력 준비 상태</p>
          <h2 className="mt-1 text-2xl font-semibold tracking-normal">{readiness.title}</h2>
          <p className="mt-2 text-sm">{summary}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Badge variant="outline">{document.processing_status}</Badge>
            {document.review_required ? <Badge className="border-amber-400 bg-white text-amber-800">검토 필요 {openIssueCount}건</Badge> : <Badge className="border-emerald-400 bg-white text-emerald-800">검토 항목 없음</Badge>}
            {schedule ? <Badge variant="outline">{schedule.label}: {schedule.date}</Badge> : <Badge variant="outline">일정 날짜 없음</Badge>}
            {layoutDebug?.bbox_candidate_summary?.uncertain_count ? <Badge className="border-amber-400 bg-white text-amber-800">OCR 위치 기반 후보 {layoutDebug.bbox_candidate_summary.uncertain_count}건</Badge> : null}
          </div>
        </div>
        <div className="flex flex-wrap gap-2 lg:justify-end">
          <Button asChild variant="outline">
            <a href={api.exportExcelUrl(new URLSearchParams({ document_ids: document.id }))}>
              <Download className="size-4" />
              Excel 내보내기
            </a>
          </Button>
          <Button asChild variant="outline">
            <a href={api.exportCsvUrl(new URLSearchParams({ document_ids: document.id }))}>
              <Download className="size-4" />
              CSV 내보내기
            </a>
          </Button>
          <Button asChild variant="outline">
            <a href={api.exportJsonUrl(document.id)}>
              <Download className="size-4" />
              JSON 보기
            </a>
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function OriginalPreviewCard({ document, isImage }: { document: DocumentRecord; isImage: boolean }) {
  const isPdf = document.mime_type === "application/pdf";
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle>원본 문서</CardTitle>
        <Button asChild variant="outline" size="sm">
          <a href={document.file_url} target="_blank" rel="noreferrer">원본 열기</a>
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {isImage ? (
          <div className="relative h-[48rem] max-h-[78vh] w-full rounded-lg border bg-white">
            <Image src={document.file_url} alt={document.original_filename} fill unoptimized className="object-contain" />
          </div>
        ) : isPdf ? (
          <iframe
            src={document.file_url}
            title={document.original_filename}
            className="h-[48rem] max-h-[78vh] w-full rounded-lg border bg-white"
          />
        ) : (
          <div className="flex min-h-72 flex-col items-center justify-center rounded-lg border bg-white p-8 text-center">
            <FileText className="mb-3 size-10 text-primary" />
            <p className="font-semibold">{document.original_filename}</p>
            <p className="mt-1 text-sm text-muted-foreground">{document.mime_type}</p>
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
  );
}

function InfoIssueDetails({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <details className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
      <summary className="cursor-pointer text-xs font-medium text-slate-600">참고 정보 {items.length}건</summary>
      <ul className="mt-2 space-y-1 text-xs">
        {items.map((item) => <li key={item}>{item}</li>)}
      </ul>
    </details>
  );
}

function ProcessingMetadataDetails({ document }: { document: DocumentRecord }) {
  const metadata = (document.workflow_metadata ?? {}) as Record<string, unknown>;
  const ingestion = (document.ingestion_metadata ?? {}) as Record<string, unknown>;
  const quality = (metadata.quality ?? {}) as Record<string, unknown>;
  const escalation = (metadata.ai_escalation_decision ?? ingestion.ai_escalation_decision ?? null) as Record<string, unknown> | null;
  const providerDiagnostics = (metadata.ai_provider_diagnostics ?? ingestion.ai_provider_diagnostics ?? null) as Record<string, unknown> | null;
  const fileMetadata = (ingestion.file_metadata ?? {}) as Record<string, unknown>;
  const interpretation = (metadata.category_interpretation ?? ingestion.category_interpretation ?? null) as Record<string, unknown> | null;
  const diagnostics = (interpretation?.diagnostics ?? {}) as Record<string, unknown>;
  const rows = ([
    ["추출 방식", document.extraction_method],
    ["추출 제공자", document.extraction_provider],
    ["보정 제공자", document.refinement_provider],
    ["Provider chain", document.provider_chain],
    ["PDF 페이지 수", fileMetadata.page_count],
    ["파일 크기", fileMetadata.size_bytes],
    ["텍스트 레이어", fileMetadata.text_layer_exists],
    ["이미지 전용 PDF", fileMetadata.image_only],
    ["OCR 엔진", fileMetadata.ocr_engine],
    ["OCR 신뢰도", ingestion.ocr_confidence ?? metadata.ocr_confidence],
    ["OCR provider attempted", fileMetadata.ocr_provider_attempted],
    ["OCR provider succeeded", fileMetadata.ocr_provider_succeeded],
    ["OCR provider failed reason", fileMetadata.ocr_provider_failed_reason],
    ["표 신뢰도", fileMetadata.table_confidence ?? (fileMetadata.quality as Record<string, unknown> | undefined)?.table_confidence],
    ["Line item completeness", (escalation?.signals as Record<string, unknown> | undefined)?.line_item_completeness],
    ["AI 보정 필요", escalation?.should_escalate],
    ["AI 보정 사유", Array.isArray(escalation?.reasons) ? escalation?.reasons.join(", ") : escalation?.reasons],
    ["AI 보정 신호", escalation?.signals],
    ["AI 시도", diagnostics.ai_attempted ?? diagnostics.ai_assisted ?? interpretation?.ai_assisted],
    ["AI 성공", diagnostics.ai_succeeded],
    ["AI 실패 사유", diagnostics.ai_failed_reason],
    ["AI 출력 없음", diagnostics.ai_output_empty],
    ["Document AI provider", providerDiagnostics],
    ["병합 충돌", metadata.merge_conflicts ?? diagnostics.merge_conflicts],
    ["프로필 정규화", interpretation?.profile ? `${interpretation.profile} → ${document.document_type}` : null],
    ["추출 품질", quality],
  ] as Array<[string, unknown]>).filter((row): row is [string, unknown] => row[1] !== undefined && row[1] !== null && row[1] !== "");
  if (!rows.length) return null;
  return (
    <details className="rounded-lg border bg-slate-50 p-4 text-sm">
      <summary className="cursor-pointer font-medium text-slate-700">처리 정보 자세히 보기</summary>
      <div className="mt-3 grid gap-2">
        {rows.map(([label, value]) => (
          <div key={label} className="grid gap-1 rounded-md border bg-white px-3 py-2 sm:grid-cols-[10rem_1fr]">
            <span className="text-xs font-medium text-muted-foreground">{label}</span>
            <span className="break-words text-xs text-slate-700">
              {typeof value === "object" ? JSON.stringify(value, null, 2) : String(value)}
            </span>
          </div>
        ))}
      </div>
    </details>
  );
}

export default function DocumentDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [document, setDocument] = useState<DocumentRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [activeTab, setActiveTab] = useState<DetailTab>("extracted");
  const [categories, setCategories] = useState<FolderSummary[]>([]);
  const [aliasSaveRows, setAliasSaveRows] = useState<Record<number, boolean>>({});
  const [approvalNote, setApprovalNote] = useState("");
  const form = useForm<DocumentUpdate & { tags_text: string }>();

  const syncDocument = useCallback((item: DocumentRecord) => {
    setDocument(item);
    form.reset(toForm(item));
    setApprovalNote(documentReviewMetadata(item).approval_note || "");
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
      const updated = await api.confirm(params.id, { approval_note: approvalNote || null });
      syncDocument(updated);
      toast.success("확정 완료로 변경했습니다");
    } catch (error) {
      toast.error(error instanceof Error ? `확정 처리 실패: ${error.message}` : "확정 처리에 실패했습니다");
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

  async function setReviewIssueStatus(key: string, status: "open" | "resolved" | "ignored" | "blocked") {
    setSaving(true);
    try {
      const updated = await api.updateReviewIssue(params.id, { key, status });
      syncDocument(updated);
      toast.success(status === "resolved" ? "검토 항목을 해결됨으로 표시했습니다" : status === "ignored" ? "검토 항목을 무시로 표시했습니다" : "검토 항목 상태를 변경했습니다");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "검토 항목 상태를 변경하지 못했습니다");
    } finally {
      setSaving(false);
    }
  }

  async function setReviewIssueGroupStatus(keys: string[], status: "resolved" | "ignored") {
    if (!keys.length) return;
    setSaving(true);
    try {
      let updated: DocumentRecord | null = null;
      for (const key of keys) {
        updated = await api.updateReviewIssue(params.id, { key, status });
      }
      if (updated) syncDocument(updated);
      toast.success(status === "resolved" ? "검토 항목을 해결됨으로 표시했습니다" : "검토 항목을 무시로 표시했습니다");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "검토 항목 상태를 변경하지 못했습니다");
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

  async function selectItemMasterCandidate(index: number, candidate: NonNullable<ManufacturingLineItem["item_master_candidates"]>[number]) {
    const items = [...(form.getValues("line_items") || [])];
    const currentItem = items[index] || {};
    items[index] = {
      ...currentItem,
      internal_item_code: candidate.internal_item_code,
      item_master_match_status: "user_selected",
      item_master_match_confidence: "1",
      item_master_match_reason: "USER_SELECTED_CANDIDATE",
    };
    form.setValue("line_items", items, { shouldDirty: true });
    if ((aliasSaveRows[index] ?? true) && candidate.item_master_id && currentItem.item_name) {
      try {
        await api.itemMaster.createAlias(candidate.item_master_id, {
          alias_name: String(currentItem.item_name),
          alias_spec: currentItem.specification ? String(currentItem.specification) : null,
          vendor_name: document?.vendor_name ?? document?.merchant_name ?? null,
          customer_name: document?.customer_name ?? null,
          source: "document_selection",
          confidence: "1",
          memo: `문서 ${document?.document_number || document?.original_filename || ""}에서 선택됨`.trim(),
          active: true,
        });
        toast.success("후보를 선택하고 별칭으로 저장했습니다");
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "별칭 저장에 실패했습니다. 선택 내용은 문서에 반영되었습니다");
      }
    } else {
      toast.success("선택한 내부 품목코드를 문서에 반영했습니다");
    }
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
  const categoryProfileLabel = profileLabelForDocument(document);
  const titleHint = readString(categoryInterpretation?.title_hint);
  const surfacedFields = readList(categoryInterpretation?.surfaced_fields);
  const isConfirmed = document.processing_status === "confirmed";
  const selectedCategory = form.watch("category") ?? "";
  const lineItems = form.watch("line_items") ?? [];
  const blockingIssues = blockingReviewIssues(document);
  const blockingIssueSummaryItems = reviewIssueSummaryItems(blockingIssues);
  const groupedBlockingIssueItems = groupedReviewIssues(blockingIssues);
  const infoIssues = informationalReviewIssues(document);
  const lowConfidenceFields = document.low_confidence_fields ?? [];
  const fieldLabels = documentFieldLabels(document.document_type);
  const displayTitle = documentDisplayTitle(document);
  const reviewMetadata = documentReviewMetadata(document);
  const reviewIssueProgress = reviewIssueProgressCounts(reviewMetadata, blockingIssues.length);
  const openIssueCount = reviewIssueProgress.open;
  const resolvedIssueCount = reviewIssueProgress.resolved;
  const exportNotice = document.review_required && !reviewMetadata.approved
    ? "검토 필요 상태입니다. 내보내기 파일에는 review_required와 경고 정보가 함께 포함됩니다."
    : isConfirmed
      ? "확정 완료된 문서입니다. 내보내기 파일에 승인 정보가 함께 포함됩니다."
      : null;

  return (
    <main className="shell py-8">
      <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-3xl">
          <div className="mb-3 flex flex-wrap gap-2">
            <StatusBadge status={document.processing_status} />
            <Badge className="bg-accent text-accent-foreground">{categoryLabel}</Badge>
            <TaxonomyBadges document={document} />
            {document.source_file_type ? <Badge variant="outline">{titleCaseLabel(document.source_file_type)}</Badge> : null}
            {document.is_favorite ? <Badge className="border-amber-300 bg-amber-50 text-amber-800">즐겨찾기</Badge> : null}
          </div>
          <h1 className="text-3xl font-semibold tracking-normal">{displayTitle}</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {document.original_filename} · 최근 수정 {formatDateTime(document.updated_at)}
          </p>
          <p className="mt-3 max-w-2xl text-sm text-muted-foreground">
            {documentSummaryDetailed(document, 700)}
          </p>
        </div>
        <div className="max-w-md space-y-2">
          <div className="flex flex-wrap justify-end gap-2">
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
            {document.document_type === "invoice" ? (
              <Button asChild variant="outline">
                <a href={api.exportTaxInvoiceXmlUrl(document.id)}>
                  <Download className="size-4" />
                  XML 초안
                </a>
              </Button>
            ) : null}
            <Button variant="destructive" onClick={remove}>
              <Trash2 className="size-4" />
              삭제
            </Button>
          </div>
          {exportNotice ? <p className="text-right text-xs text-muted-foreground">{exportNotice}</p> : null}
        </div>
      </div>

      <ErpReadinessBanner document={document} openIssueCount={openIssueCount} />

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
            <p className="mt-1 font-semibold">{categoryProfileLabel}</p>
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

      <form onSubmit={form.handleSubmit(onSubmit)} className="grid gap-6 xl:grid-cols-[1.05fr_0.95fr]">
        <section className="space-y-6">
          <OriginalPreviewCard document={document} isImage={isImage} />

          <div className="flex flex-wrap gap-2">
            {detailTabs.map((tab) => (
              <button
                key={tab}
                type="button"
                onClick={() => setActiveTab(tab)}
                className={`rounded-full border px-4 py-2 text-sm transition ${
                  activeTab === tab ? "border-primary bg-primary text-primary-foreground" : "bg-white text-muted-foreground hover:border-primary/40"
                }`}
              >
                {tab === "ai" ? "AI 추출 결과" : "원문 텍스트"}
              </button>
            ))}
          </div>

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
                    ["분류 프로필", categoryProfileLabel],
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
                <ProcessingMetadataDetails document={document} />
              </CardContent>
            </Card>
          ) : null}
        </section>

        <section className="space-y-6">
          <TaxonomyPolicyCard document={document} />
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
                  ["업무 분류", documentSubtypeLabel(documentTaxonomy(document).document_subtype)],
                  ["처리 상태", titleCaseLabel(document.processing_status)],
                  ["검토 상태", document.review_required ? "사람이 확인해야 합니다" : "자동 추출 완료"],
                  ["검토 진행", `${openIssueCount}개 열림 / ${resolvedIssueCount}개 해결`],
                  ["승인 일시", reviewMetadata.approved_at ? formatDateTime(reviewMetadata.approved_at) : null],
                  ["검토 필요 항목", blockingIssueSummaryItems.length ? blockingIssueSummaryItems.join(", ") : "없음"],
                ]}
              />
              <div className="grid gap-2 rounded-lg border bg-white p-3">
                <label className="grid gap-2 text-sm font-medium">
                  승인 메모
                  <Textarea
                    className="min-h-20"
                    placeholder="예: 원본 PDF와 대조 후 ERP 입력 전 확인 완료"
                    value={approvalNote}
                    onChange={(event) => setApprovalNote(event.target.value)}
                  />
                </label>
                <p className="text-xs text-muted-foreground">검토 완료 버튼을 누르면 이 메모가 workflow metadata에 저장됩니다.</p>
              </div>
              {blockingIssues.length ? (
                <div className="grid gap-2">
                  {groupedBlockingIssueItems.map((group) => {
                    const issueKeys = group.issues.map((issue) => issue.key).filter((key): key is string => Boolean(key));
                    return (
                    <div key={`${group.summary}-${group.description}`} className="rounded-lg border border-amber-200 bg-white p-3 text-sm">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div>
                          <p className="font-medium text-amber-900">
                            {group.summary}
                            {group.count > 1 ? <span className="ml-1 text-xs text-amber-700">({group.count}건)</span> : null}
                          </p>
                          <p className="mt-1 text-xs text-amber-800">{group.description}</p>
                        </div>
                        {issueKeys.length ? (
                          <div className="flex gap-1">
                            <Button type="button" variant="outline" size="sm" disabled={saving} onClick={() => issueKeys.length === 1 ? setReviewIssueStatus(issueKeys[0], "resolved") : setReviewIssueGroupStatus(issueKeys, "resolved")}>해결</Button>
                            <Button type="button" variant="outline" size="sm" disabled={saving} onClick={() => issueKeys.length === 1 ? setReviewIssueStatus(issueKeys[0], "ignored") : setReviewIssueGroupStatus(issueKeys, "ignored")}>무시</Button>
                          </div>
                        ) : null}
                      </div>
                    </div>
                  );
                  })}
                </div>
              ) : null}
              <InfoIssueDetails items={infoIssues.map((issue) => `${reviewIssueSummary(issue)}: ${reviewIssueDescription(issue)}`)} />
              {blockingIssues.some((issue) => reviewIssueAmountLines(issue).length) ? (
                <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
                  {blockingIssues.filter((issue) => reviewIssueAmountLines(issue).length).map((issue) => (
                    <div key={`${issue.code}-${issue.message_ko}`}>
                      <p className="font-medium">{reviewIssueSummary(issue)}</p>
                      <p className="mt-1 text-xs">{reviewIssueDescription(issue)}</p>
                      <div className="mt-2 grid gap-1 text-xs sm:grid-cols-3">
                        {reviewIssueAmountLines(issue).map((line) => <span key={line}>{line}</span>)}
                      </div>
                    </div>
                  ))}
                </div>
              ) : null}
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
                    <span className="text-xs font-normal text-muted-foreground">캘린더에 표시됨</span>
                  </label>
                  <label className="grid gap-2 text-sm font-medium">
                    {fieldLabels.dueDate}
                    <Input type="date" {...form.register("due_date")} />
                    <span className="text-xs font-normal text-muted-foreground">알림/일정 카드에 반영됨</span>
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
                              const fieldBlockingIssues = blockingIssues.filter((issue) => issue.item_index === index && issue.field === `line_items.${field}`);
                              const fieldInfoIssues = infoIssues.filter((issue) => issue.item_index === index && issue.field === `line_items.${field}`);
                              const low =
                                fieldBlockingIssues.length > 0 ||
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
                                  {fieldBlockingIssues.length || fieldInfoIssues.length ? (
                                    <div className="mt-1 flex flex-wrap gap-1">
                                      {fieldBlockingIssues.map((issue) => (
                                        <Badge key={`${issue.code}-${issue.message_ko}`} className="border-amber-300 bg-amber-50 text-[11px] text-amber-800">
                                          {numericLineItemFields.has(field) ? "확인 필요" : issue.message_ko.replace(/^\d+번째 품목\s*/, "")}
                                        </Badge>
                                      ))}
                                      {fieldInfoIssues.map((issue) => (
                                        <Badge key={`${issue.code}-${issue.message_ko}`} variant="outline" className="bg-white text-[11px] text-slate-600">
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
                                  <label className="flex items-center gap-1 text-[11px] text-muted-foreground">
                                    <input
                                      type="checkbox"
                                      checked={aliasSaveRows[index] ?? true}
                                      onChange={(event) => setAliasSaveRows({ ...aliasSaveRows, [index]: event.target.checked })}
                                    />
                                    이 선택을 별칭으로 저장
                                  </label>
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
