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
import { documentSummaryDetailed, formatDateTime, primaryCategoryLabel, titleCaseLabel } from "@/lib/utils";
import type { DocumentRecord, DocumentUpdate, FolderSummary } from "@/types/document";

const detailTabs = ["original", "extracted", "ai"] as const;
type DetailTab = (typeof detailTabs)[number];

function toForm(document: DocumentRecord): DocumentUpdate & { tags_text: string } {
  return {
    title: document.title ?? "",
    raw_text: document.raw_text ?? "",
    extracted_date: document.extracted_date ?? "",
    extracted_amount: document.extracted_amount ?? "",
    subtotal: document.subtotal ?? "",
    tax: document.tax ?? "",
    currency: document.currency ?? "",
    merchant_name: document.merchant_name ?? "",
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
      .catch((error) => toast.error(error instanceof Error ? error.message : "Could not load document"))
      .finally(() => setLoading(false));
    api.categories().then(setCategories).catch(() => setCategories([]));
  }, [params.id, syncDocument]);

  async function onSubmit(values: DocumentUpdate & { tags_text: string }) {
    setSaving(true);
    const { tags_text, ...fields } = values;
    const payload: DocumentUpdate = {
      ...fields,
      title: values.title || null,
      raw_text: values.raw_text || null,
      extracted_date: values.extracted_date || null,
      extracted_amount: values.extracted_amount || null,
      subtotal: values.subtotal || null,
      tax: values.tax || null,
      currency: values.currency || null,
      merchant_name: values.merchant_name || null,
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
      toast.success("Document saved");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not save");
    } finally {
      setSaving(false);
    }
  }

  async function reprocess() {
    setSaving(true);
    try {
      const updated = await api.reprocess(params.id);
      syncDocument(updated);
      toast.success("Reprocessing started");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Reprocess failed");
    } finally {
      setSaving(false);
    }
  }

  async function confirmDocument() {
    setSaving(true);
    try {
      const updated = await api.confirm(params.id);
      syncDocument(updated);
      toast.success("Document confirmed");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Confirm failed");
    } finally {
      setSaving(false);
    }
  }

  async function markNeedsReview() {
    setSaving(true);
    try {
      const updated = await api.markNeedsReview(params.id);
      syncDocument(updated);
      toast.success("Moved to needs review");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not update review status");
    } finally {
      setSaving(false);
    }
  }

  async function toggleFavorite() {
    try {
      const updated = await api.toggleFavorite(params.id);
      syncDocument(updated);
      toast.success(updated.is_favorite ? "Pinned to favorites" : "Removed from favorites");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not update favorite");
    }
  }

  async function remove() {
    if (!window.confirm("Delete this document and uploaded file?")) return;
    try {
      await api.remove(params.id);
      toast.success("Document deleted");
      router.push(searchParams.get("from") || "/documents");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Delete failed");
    }
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
          <CardContent className="p-12 text-center text-muted-foreground">Document not found.</CardContent>
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

  return (
    <main className="shell py-8">
      <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-3xl">
          <div className="mb-3 flex flex-wrap gap-2">
            <StatusBadge status={document.processing_status} />
            <Badge className="bg-accent text-accent-foreground">{categoryLabel}</Badge>
            {document.source_file_type ? <Badge variant="outline">{titleCaseLabel(document.source_file_type)}</Badge> : null}
            {document.is_favorite ? <Badge className="border-amber-300 bg-amber-50 text-amber-800">Pinned</Badge> : null}
          </div>
          <h1 className="text-3xl font-semibold tracking-normal">{document.title || titleHint || document.original_filename}</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {document.original_filename} · Updated {formatDateTime(document.updated_at)}
          </p>
          <p className="mt-3 max-w-2xl text-sm text-muted-foreground">
            {documentSummaryDetailed(document, 700)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant={isConfirmed ? "secondary" : "default"} onClick={confirmDocument} disabled={saving || isConfirmed}>
            <CheckCheck className="size-4" />
            {isConfirmed ? "Confirmed" : "Confirm"}
          </Button>
          <Button variant="outline" onClick={markNeedsReview} disabled={saving}>
            <AlertTriangle className="size-4" />
            Needs Review
          </Button>
          <Button variant="outline" onClick={toggleFavorite}>
            <Star className={`size-4 ${document.is_favorite ? "fill-amber-400 text-amber-400" : ""}`} />
            {document.is_favorite ? "Pinned" : "Pin"}
          </Button>
          <Button variant="outline" onClick={reprocess} disabled={saving}>
            <RefreshCw className="size-4" />
            Reprocess
          </Button>
          <Button asChild variant="outline">
            <a href={api.exportJsonUrl(document.id)}>
              <Download className="size-4" />
              JSON
            </a>
          </Button>
          <Button variant="destructive" onClick={remove}>
            <Trash2 className="size-4" />
            Delete
          </Button>
        </div>
      </div>

      <div className="mb-6 grid gap-4 lg:grid-cols-3">
        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">Primary category</p>
            <p className="mt-1 font-semibold">{categoryLabel}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">Interpretation profile</p>
            <p className="mt-1 font-semibold">{categoryProfile ? titleCaseLabel(categoryProfile) : "Not surfaced"}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">Provider chain</p>
            <p className="mt-1 break-words text-sm font-semibold">{document.provider_chain || "Unavailable"}</p>
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
            {tab === "ai" ? "AI Result" : tab === "extracted" ? "Extracted Text" : "Original"}
          </button>
        ))}
      </div>

      <form onSubmit={form.handleSubmit(onSubmit)} className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
        <section className="space-y-6">
          {activeTab === "original" ? (
            <Card>
              <CardHeader>
                <CardTitle>Original document</CardTitle>
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
                      <a href={document.file_url}>Open original</a>
                    </Button>
                  </div>
                )}
                <InfoGrid
                  items={[
                    ["Source file type", titleCaseLabel(document.source_file_type || "unknown")],
                    ["Extraction method", document.extraction_method || "Unavailable"],
                    ["Uploaded", formatDateTime(document.created_at)],
                    ["Last updated", formatDateTime(document.updated_at)],
                  ]}
                />
              </CardContent>
            </Card>
          ) : null}

          {activeTab === "extracted" ? (
            <Card>
              <CardHeader>
                <CardTitle>Extracted text</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <Textarea className="min-h-[38rem] font-mono text-xs" {...form.register("raw_text")} />
                <InfoGrid
                  items={[
                    ["Extraction provider", document.extraction_provider || "Unavailable"],
                    ["Refinement provider", document.refinement_provider || "Not used"],
                    ["Confidence", document.ai_confidence_score ? `${Math.round(Number(document.ai_confidence_score) * 100)}%` : null],
                    ["Review recommendation", document.review_required ? "Recommended" : "Looks usable"],
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
                  AI result
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <InfoGrid
                  items={[
                    ["Primary visible label", categoryLabel],
                    ["Interpretation profile", categoryProfile ? titleCaseLabel(categoryProfile) : null],
                    ["Title hint", titleHint],
                  ]}
                />
                {document.ai_extraction_notes ? (
                  <div className="rounded-lg border bg-white p-4 text-sm text-muted-foreground whitespace-pre-line">
                    {document.ai_extraction_notes}
                  </div>
                ) : null}
                {surfacedFields.length ? (
                  <div className="rounded-lg border bg-white p-4">
                    <p className="mb-3 text-xs font-medium uppercase text-muted-foreground">Surfaced fields</p>
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
                    <p className="mb-3 text-xs font-medium uppercase text-muted-foreground">Field provenance</p>
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
                Review and organization
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {document.review_required ? (
                <div className="flex gap-2 rounded-lg border border-amber-300 bg-amber-100/60 p-3 text-sm text-amber-900">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                  Review is recommended before relying on this result. Confirm when it looks right, or keep it in the review queue.
                </div>
              ) : null}
              <InfoGrid
                items={[
                  ["Current folder", categoryLabel],
                  ["Status", titleCaseLabel(document.processing_status)],
                  ["Needs review", document.review_required ? "Yes" : "No"],
                  ["Follow-up", document.follow_up_required ? "Suggested" : "Not required"],
                ]}
              />
            </CardContent>
          </Card>
          <Card className="overflow-hidden">
            <CardHeader className="border-b bg-slate-50/70">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-medium uppercase tracking-normal text-muted-foreground">Correction workspace</p>
                  <CardTitle className="mt-1 flex items-center gap-2">
                    <ShieldCheck className="size-5 text-primary" />
                    Review and edit
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
                    <p className="text-sm font-semibold">Category folder</p>
                    <p className="text-xs text-muted-foreground">Move this document by meaning; search and filters use the normalized folder value.</p>
                  </div>
                </div>
                <CategorySelector
                  value={selectedCategory}
                  folders={categories}
                  onChange={(value) => form.setValue("category", value, { shouldDirty: true })}
                />
                <p className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
                  <Tag className="size-3.5" />
                  Changing the category moves this document into a different AI-organized folder.
                </p>
              </section>

              <section className="grid gap-4 rounded-lg border bg-slate-50/60 p-4">
                <div>
                  <p className="text-sm font-semibold">Editable extraction</p>
                  <p className="mt-1 text-xs text-muted-foreground">Correct the user-facing fields while keeping the original extraction available for audit.</p>
                </div>
                <label className="grid gap-2 text-sm font-medium">
                  Title
                  <Input {...form.register("title")} />
                </label>
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="grid gap-2 text-sm font-medium">
                    Extracted date
                    <Input type="date" {...form.register("extracted_date")} />
                  </label>
                  <label className="grid gap-2 text-sm font-medium">
                    Amount
                    <Input type="number" min="0" step="0.01" {...form.register("extracted_amount")} />
                  </label>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="grid gap-2 text-sm font-medium">
                    Subtotal
                    <Input type="number" min="0" step="0.01" {...form.register("subtotal")} />
                  </label>
                  <label className="grid gap-2 text-sm font-medium">
                    Tax
                    <Input type="number" min="0" step="0.01" {...form.register("tax")} />
                  </label>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="grid gap-2 text-sm font-medium">
                    Currency
                    <Input placeholder="USD" {...form.register("currency")} />
                  </label>
                  <label className="grid gap-2 text-sm font-medium">
                    Merchant / source
                    <Input {...form.register("merchant_name")} />
                  </label>
                </div>
              </section>

              <section className="grid gap-4 rounded-lg border bg-white p-4">
                <div>
                  <p className="text-sm font-semibold">Review notes</p>
                  <p className="mt-1 text-xs text-muted-foreground">Tags and summary make the corrected result easier to find later.</p>
                </div>
                <label className="grid gap-2 text-sm font-medium">
                  Tags
                  <Input placeholder="finance, spring-2026, review" {...form.register("tags_text")} />
                </label>
                <label className="grid gap-2 text-sm font-medium">
                  Summary
                  <Textarea className="min-h-28" {...form.register("summary")} />
                </label>
              </section>

              <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-slate-50 p-3">
                <Link href="/review" className="text-sm text-muted-foreground underline-offset-4 hover:underline">
                  Open review queue
                </Link>
                <Button type="submit" disabled={saving}>
                  {saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
                  Save corrected document
                </Button>
              </div>
            </CardContent>
          </Card>
        </section>
      </form>
    </main>
  );
}
