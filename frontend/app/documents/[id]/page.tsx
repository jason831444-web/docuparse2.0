"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent, SyntheticEvent } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import {
  AlertTriangle,
  Bot,
  CheckCheck,
  ChevronLeft,
  ChevronRight,
  Download,
  FileText,
  FolderKanban,
  Loader2,
  Plus,
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
import { api, documentFileUrl } from "@/lib/api";
import { cleanLineItemValue, cleanLineItems, numericLineItemFields } from "@/lib/line-items";
import { isLiveProcessingStatus, useDocumentsChanged } from "@/lib/realtime";
import { blockingReviewIssues, businessColumnLabel, businessFieldDate, documentDisplayTitle, documentFieldLabels, documentProfileLabel, documentReviewMetadata, documentSubtypeLabel, documentSummaryDetailed, documentTaxonomy, extractionMethodLabel, formatDateTime, getDocumentScheduleDate, getErpReadinessStatus, getErpReadinessSummary, groupedReviewIssues, informationalReviewIssues, layoutDebugMetadata, layoutProfileLabel, primaryCategoryLabel, profileLabelForDocument, reviewIssueAmountLines, reviewIssueDescription, reviewIssueProgressCounts, reviewIssueSummary, reviewIssueSummaryItems, taxonomyPolicyLines, titleCaseLabel } from "@/lib/utils";
import type { AiParsedDocument, AiParsedField, AiParsedSection, AiParsedTableRow, DocumentListResponse, DocumentRecord, DocumentUpdate, ExportTemplateRecord, FolderSummary, ManufacturingLineItem, PosSettlementSummary, ReviewCandidate } from "@/types/document";

const detailTabs = ["extracted", "ai"] as const;
type DetailTab = (typeof detailTabs)[number];
type DocumentListItem = DocumentListResponse["items"][number];
type DocumentReviewForm = DocumentUpdate & { tags_text: string };

function toForm(document: DocumentRecord): DocumentReviewForm {
  const businessFields = (document.workflow_metadata?.business_fields ?? {}) as Record<string, unknown>;
  const transactionDate = typeof businessFields.transaction_date === "string" ? businessFields.transaction_date : document.extracted_date;
  const issueDate = document.document_type === "transaction_statement" ? transactionDate : document.issue_date;
  const roleDate = document.document_type === "transaction_statement" ? document.issue_date : businessFieldDate(document);
  return {
    title: document.title ?? "",
    raw_text: document.raw_text ?? "",
    extracted_date: document.extracted_date ?? "",
    extracted_amount: document.extracted_amount ?? semanticFieldValue(document, ["document_total", "payment_total", "estimated_total", "total_amount"]) ?? "",
    subtotal: document.subtotal ?? semanticFieldValue(document, ["supply_amount", "subtotal"]) ?? "",
    tax: document.tax ?? semanticFieldValue(document, ["tax_amount", "vat"]) ?? "",
    currency: document.currency ?? "",
    merchant_name: document.merchant_name ?? "",
    vendor_name: document.vendor_name ?? "",
    customer_name: document.customer_name ?? "",
    document_number: document.document_number ?? "",
    issue_date: issueDate ?? "",
    due_date: roleDate ?? "",
    line_items: rawLineItemsFromOfficialTables(document).length ? rawLineItemsFromOfficialTables(document) : cleanLineItems(document.line_items ?? []),
    reviewed_key_values: rawKeyValueEntries(document),
    low_confidence_fields: document.low_confidence_fields ?? [],
    category: document.category ?? "",
    tags: document.tags,
    summary: document.summary ?? "",
    is_favorite: document.is_favorite,
    tags_text: document.tags.join(", "),
  } as DocumentReviewForm;
}

function buildDocumentUpdatePayload(values: DocumentReviewForm, documentType?: string): DocumentUpdate {
  const { tags_text, ...fields } = values;
  const isTransactionStatement = documentType === "transaction_statement";
  return {
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
    reviewed_key_values: Array.isArray(values.reviewed_key_values) ? values.reviewed_key_values : null,
    low_confidence_fields: values.low_confidence_fields || [],
    category: values.category || null,
    summary: values.summary || null,
    tags: tags_text
      .split(",")
      .map((tag) => tag.trim())
      .filter(Boolean),
  };
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function readList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.trim().length > 0) : [];
}

function readRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function semanticFieldValue(document: DocumentRecord, keys: string[]): string | null {
  const metadata = readRecord(document.workflow_metadata);
  const confirmed = readRecord(metadata.confirmed_semantic_mapping);
  const raw = readRecord(metadata.raw_semantic_mapping);
  for (const mapping of [confirmed, raw]) {
    const fields = { ...mapping, ...readRecord(mapping.fields) };
    for (const key of keys) {
      const value = fields[key];
      if (typeof value === "string" && value.trim()) return value;
      if (typeof value === "number" && Number.isFinite(value)) return String(value);
    }
  }
  return null;
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function confidenceLabel(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return `${Math.round(numeric * 100)}%`;
}

function aiStatusLabel(status: unknown): string {
  return {
    candidate: "검토 후보",
    unmapped: "미매핑",
    blocked: "차단됨",
  }[String(status || "")] ?? (status ? titleCaseLabel(String(status)) : "검토 후보");
}

function aiStatusClass(status: unknown): string {
  if (status === "blocked") return "border-red-200 bg-red-50 text-red-800";
  if (status === "unmapped") return "border-slate-300 bg-slate-50 text-slate-700";
  return "border-blue-200 bg-blue-50 text-blue-800";
}

function aiParsedDocumentMetadata(document: DocumentRecord): AiParsedDocument | null {
  const metadata = readRecord(document.workflow_metadata);
  const parsed = readRecord(metadata.ai_parsed_document);
  if (!Object.keys(parsed).length) return null;
  return parsed as AiParsedDocument;
}

function aiParsedSections(document: AiParsedDocument | null, type: string): AiParsedSection[] {
  return Array.isArray(document?.sections) ? document.sections.filter((section) => section?.type === type) : [];
}

function aiParsedFields(value: unknown): AiParsedField[] {
  return Array.isArray(value) ? value.map((field) => readRecord(field) as AiParsedField).filter((field) => Object.keys(field).length) : [];
}

function aiParsedRows(value: unknown): AiParsedTableRow[] {
  return Array.isArray(value) ? value.map((row) => readRecord(row) as AiParsedTableRow).filter((row) => Object.keys(row).length) : [];
}

function aiParsedColumns(section: AiParsedSection, rows: AiParsedTableRow[]): string[] {
  if (Array.isArray(section.columns) && section.columns.length) return section.columns.map(String);
  const columns = new Set<string>();
  rows.forEach((row) => Object.keys(readRecord(row.cells)).forEach((column) => columns.add(column)));
  return Array.from(columns);
}

function workflowCandidates(document: DocumentRecord, key: string): ReviewCandidate[] {
  const metadata = readRecord(document.workflow_metadata);
  const value = metadata[key];
  return Array.isArray(value) ? value.map((candidate) => readRecord(candidate) as ReviewCandidate).filter((candidate) => Object.keys(candidate).length) : [];
}

function posSettlementSummary(document: DocumentRecord): PosSettlementSummary | null {
  const summary = readRecord(readRecord(document.workflow_metadata).pos_settlement_summary);
  return Object.keys(summary).length ? summary as PosSettlementSummary : null;
}

function CandidateList({ title, description, candidates }: { title: string; description: string; candidates: ReviewCandidate[] }) {
  if (!candidates.length) return null;
  return (
    <details open className="rounded-lg border bg-white p-4">
      <summary className="cursor-pointer text-sm font-semibold">
        {title}
        <span className="ml-2 text-xs font-normal text-muted-foreground">확정값 아님</span>
      </summary>
      <p className="mt-2 text-xs text-muted-foreground">{description}</p>
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {candidates.map((candidate, index) => (
          <div key={`${candidate.field}-${candidate.value}-${index}`} className="rounded-md border bg-slate-50 p-3 text-sm">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <p className="text-xs text-muted-foreground">{candidate.source_label || candidate.field || candidate.role || "후보"}</p>
                <p className="mt-1 break-words font-medium">{displayValue(candidate.normalized_value ?? candidate.value)}</p>
              </div>
              <Badge variant="outline" className={aiStatusClass(candidate.status)}>{aiStatusLabel(candidate.status)}</Badge>
            </div>
            <div className="mt-2 flex flex-wrap gap-1 text-[11px] text-muted-foreground">
              {candidate.field ? <span className="rounded bg-white px-1.5 py-0.5">field: {candidate.field}</span> : null}
              {candidate.role ? <span className="rounded bg-white px-1.5 py-0.5">role: {candidate.role}</span> : null}
              {candidate.source ? <span className="rounded bg-white px-1.5 py-0.5">{candidate.source}</span> : null}
              {confidenceLabel(candidate.confidence) ? <span className="rounded bg-white px-1.5 py-0.5">{confidenceLabel(candidate.confidence)}</span> : null}
            </div>
            {candidate.evidence ? <p className="mt-2 break-words text-xs text-muted-foreground">근거: {candidate.evidence}</p> : null}
            {candidate.reason ? <p className="mt-1 text-xs text-muted-foreground">사유: {candidate.reason}</p> : null}
          </div>
        ))}
      </div>
    </details>
  );
}

async function loadDocumentNeighbors(currentId: string): Promise<DocumentNeighbors> {
  const documents: DocumentListItem[] = [];
  let page = 1;
  let total = Infinity;
  while (documents.length < Math.min(total, 500)) {
    const params = new URLSearchParams();
    params.set("page", String(page));
    params.set("page_size", "100");
    params.set("sort_by", "created_at");
    params.set("order", "desc");
    const data = await api.list(params);
    documents.push(...data.items);
    total = data.total;
    const index = documents.findIndex((item) => item.id === currentId);
    if (index >= 0 && index < documents.length - 1) break;
    if (!data.items.length || documents.length >= data.total) break;
    page += 1;
  }
  const index = documents.findIndex((item) => item.id === currentId);
  if (index < 0) return { previous: null, next: null };
  return {
    previous: documents[index - 1] ? toDocumentNeighbor(documents[index - 1]) : null,
    next: documents[index + 1] ? toDocumentNeighbor(documents[index + 1]) : null,
  };
}

function toDocumentNeighbor(document: DocumentListItem): DocumentNeighbor {
  return {
    id: document.id,
    label: document.document_number || document.title || null,
    filename: document.original_filename,
  };
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

function officialTableLabel(value: unknown): string {
  const key = String(value || "").trim();
  return {
    incoming_inspection: "입고검사 표",
    inspection_report: "검사성적서 표",
    delivery_note: "납품서 표",
    purchase_order: "발주서 표",
    quotation: "견적서 표",
    transaction_statement: "거래명세서 표",
    invoice: "인보이스/세금계산서 표",
  }[key] ?? (key ? titleCaseLabel(key) : "표 구조");
}

function officialTableEntries(document: DocumentRecord): Array<Record<string, unknown>> {
  const metadata = readRecord(document.workflow_metadata);
  const candidates = Array.isArray(metadata.vl_candidates) ? metadata.vl_candidates : [];
  const tables: Array<Record<string, unknown>> = [];
  for (const candidate of candidates) {
    const candidateRecord = readRecord(candidate);
    const candidateTables = Array.isArray(candidateRecord.tables) ? candidateRecord.tables : [];
    for (const table of candidateTables) {
      const tableRecord = readRecord(table);
      if (tableRecord.source === "paddleocrvl_official_table_html") {
        tables.push(tableRecord);
      }
    }
    const structuredCandidate = readRecord(candidateRecord.structured_candidate);
    const compactTables = Array.isArray(structuredCandidate.tables) ? structuredCandidate.tables : [];
    for (const table of compactTables) {
      const tableRecord = readRecord(table);
      if (tableRecord.source === "paddleocrvl_official_table_html") {
        tables.push(tableRecord);
      }
    }
  }
  const seen = new Set<string>();
  return tables.filter((table) => {
    const key = JSON.stringify([
      table.table_type,
      table.source,
      table.row_count,
      readRecord(table.provenance).block_bbox ?? table.block_bbox,
    ]);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function rawExtractionTableEntries(document: DocumentRecord): Array<Record<string, unknown>> {
  const metadata = readRecord(document.workflow_metadata);
  const rawExtraction = readRecord(metadata.raw_extraction);
  return Array.isArray(rawExtraction.tables)
    ? rawExtraction.tables.map((table) => readRecord(table)).filter((table) => Array.isArray(table.rows) && table.source !== "user_reviewed_line_items")
    : [];
}

function rawEditableTableEntries(document: DocumentRecord): Array<Record<string, unknown>> {
  const rawTables = rawExtractionTableEntries(document);
  return rawTables.length ? rawTables : officialTableEntries(document);
}

function officialTableRowCount(table: Record<string, unknown>): number {
  if (typeof table.row_count === "number") return table.row_count;
  return Array.isArray(table.rows) ? table.rows.length : 0;
}

function rawOfficialTableRows(table: Record<string, unknown>): Array<Record<string, unknown>> {
  const rows = Array.isArray(table.rows) ? table.rows : [];
  return rows
    .map((row) => readRecord(row))
    .filter((row) => Object.keys(row).length);
}

function rawOfficialTableColumns(table: Record<string, unknown>, rows: Array<Record<string, unknown>>): string[] {
  const rawColumns = Array.isArray(table.raw_columns) ? table.raw_columns.map(String).filter(Boolean) : [];
  const columns = Array.isArray(table.columns) ? table.columns.map(String).filter(Boolean) : [];
  if (rawColumns.length) return rawColumns;
  if (columns.length) return columns;
  const keys = new Set<string>();
  for (const row of rows) {
    const rawCells = readRecord(row.raw_cells);
    for (const key of Object.keys(rawCells).length ? Object.keys(rawCells) : Object.keys(row)) {
      if (!key.startsWith("_") && key !== "raw_cells" && key !== "validation_warnings" && key !== "review_flags") keys.add(key);
    }
  }
  return Array.from(keys);
}

function rawOfficialTableCell(row: Record<string, unknown>, column: string): string {
  const rawCells = readRecord(row.raw_cells);
  if (rawCells[column] !== undefined) return displayValue(rawCells[column]);
  if (row[column] !== undefined) return displayValue(row[column]);
  const normalizedKey = {
    No: "no",
    품목명: "item_name",
    "규격/코드": "document_item_code",
    품목코드: "document_item_code",
    내부코드: "document_item_code",
    수량: "quantity",
    단위: "unit",
    단가: "unit_price",
    금액: "line_total",
    합계금액: "line_total",
    공급가액: "supply_amount",
    세액: "tax_amount",
    판정: "inspection_result",
    비고: "note",
  }[column];
  return normalizedKey ? displayValue(row[normalizedKey]) : "-";
}

function rawTableFieldForColumn(column: string): string {
  const normalizedKey = {
    No: "line_number",
    번호: "line_number",
    품목명: "item_name",
    품명: "item_name",
    Description: "item_name",
    "규격/코드": "document_item_code",
    품목코드: "document_item_code",
    내부코드: "document_item_code",
    "HS/Code": "document_item_code",
    규격: "specification",
    "Lot No": "lot_code",
    "Lot/Code": "lot_code",
    수량: "quantity",
    Qty: "quantity",
    단위: "unit",
    Unit: "unit",
    단가: "unit_price",
    "Unit Price": "unit_price",
    공급가액: "supply_amount",
    세액: "tax_amount",
    금액: "line_total",
    합계금액: "line_total",
    Amount: "line_total",
    판정: "inspection_result",
    검사판정: "inspection_result",
    검사항목: "inspection_item",
    비고: "note",
    이동사유: "note",
  }[column];
  return normalizedKey ?? (column.trim().replace(/\s+/g, "_").replace(/[^A-Za-z0-9가-힣_]/g, "") || column);
}

function rawLineItemsFromOfficialTables(document: DocumentRecord): ManufacturingLineItem[] {
  const table = rawEditableTableEntries(document)[0];
  if (!table) return [];
  const rows = rawOfficialTableRows(table);
  const columns = rawOfficialTableColumns(table, rows);
  return rows.map((row) => {
    const item: ManufacturingLineItem = {};
    for (const column of columns) {
      const field = rawTableFieldForColumn(column);
      const value = rawOfficialTableCell(row, column);
      if (value !== "-") (item as Record<string, unknown>)[field] = value;
    }
    return item;
  }).filter((item) => Object.keys(item).length);
}

function rawEditorColumns(document: DocumentRecord, items: ManufacturingLineItem[]): string[] {
  const table = rawEditableTableEntries(document)[0];
  if (table) {
    const rows = rawOfficialTableRows(table);
    const columns = rawOfficialTableColumns(table, rows);
    if (columns.length) return columns;
  }
  const fields = new Set<string>();
  for (const item of items) {
    for (const field of Object.keys(item || {})) {
      if (!field.startsWith("_") && field !== "item_master_candidates") fields.add(field);
    }
  }
  const preferred = ["item_name", "document_item_code", "specification", "lot_code", "quantity", "unit", "unit_price", "supply_amount", "tax_amount", "line_total", "inspection_result", "note"];
  return preferred.filter((field) => fields.has(field)).concat([...fields].filter((field) => !preferred.includes(field)).sort());
}

function rawKeyValueEntries(document: DocumentRecord): Array<Record<string, unknown>> {
  const metadata = readRecord(document.workflow_metadata);
  const rawExtraction = readRecord(metadata.raw_extraction);
  return Array.isArray(rawExtraction.key_values)
    ? rawExtraction.key_values
      .map((item) => readRecord(item))
      .filter((item) => item.key && item.value !== undefined)
      .map((item) => ({ ...item, _review_identity: keyValueBackendIdentity(item) }))
    : [];
}

function dictionarySuggestions(document: DocumentRecord): Array<Record<string, unknown>> {
  const metadata = readRecord(document.workflow_metadata);
  const dictionary = readRecord(metadata.dictionary_suggestions);
  return Array.isArray(dictionary.suggestions)
    ? dictionary.suggestions.map((item) => readRecord(item)).filter((item) => item.target === "raw_key_value" && item.suggested_value)
    : [];
}

function tableDictionarySuggestions(document: DocumentRecord): Array<Record<string, unknown>> {
  const metadata = readRecord(document.workflow_metadata);
  const dictionary = readRecord(metadata.dictionary_suggestions);
  return Array.isArray(dictionary.suggestions)
    ? dictionary.suggestions.map((item) => readRecord(item)).filter((item) => item.target === "raw_table_cell" && item.suggested_value)
    : [];
}

function keyValueBackendIdentity(entry: Record<string, unknown>): string {
  return [entry.key, entry.source, entry.role, entry.section].map((value) => String(value ?? "")).join("|");
}

function keyValueIdentity(entry: Record<string, unknown>, index: number): string {
  return [entry._review_identity, entry.key, entry.source, entry.role, entry.section, index].map((value) => String(value ?? "")).join("|");
}

function RawKeyValueEditor({
  documentId,
  entries,
  suggestions,
  saving,
  onChange,
}: {
  documentId: string;
  entries: Array<Record<string, unknown>>;
  suggestions: Array<Record<string, unknown>>;
  saving: boolean;
  onChange: (index: number, field: "key" | "value", value: string) => void;
}) {
  if (!entries.length) return null;
  const suggestionsByIndex = suggestions.reduce<Record<number, Array<Record<string, unknown>>>>((acc, suggestion) => {
    const index = Number(suggestion.index);
    if (Number.isInteger(index) && index >= 0) {
      acc[index] = [...(acc[index] || []), suggestion];
    }
    return acc;
  }, {});
  return (
    <div className="grid gap-3 rounded-lg border border-blue-200 bg-blue-50/40 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-blue-950">추출 원형 정보</p>
          <p className="mt-1 text-xs text-blue-800">추출된 key-value를 그대로 확인하고 필요한 key와 value를 수정하세요.</p>
        </div>
        <Badge variant="outline" className="bg-white text-blue-900">
          {entries.length}개
        </Badge>
      </div>
      <div className="grid gap-3 rounded-md border bg-white p-3 md:grid-cols-2 xl:grid-cols-3">
        {entries.map((entry, index) => {
          const entrySuggestions = suggestionsByIndex[index] || [];
          return (
            <div key={keyValueIdentity(entry, index)} className="grid gap-2 rounded-md border border-slate-100 bg-slate-50/60 p-2">
              <label className="grid gap-1 text-xs font-medium text-slate-600">
                <span>Key</span>
                <Input className="h-8 bg-white text-xs" value={displayValue(entry.key) === "-" ? "" : displayValue(entry.key)} disabled={saving} onChange={(event) => onChange(index, "key", event.target.value)} />
              </label>
              <label className="grid gap-1 text-xs font-medium text-slate-600">
                <span>Value</span>
                <Input className="h-8 bg-white text-xs" value={displayValue(entry.value) === "-" ? "" : displayValue(entry.value)} disabled={saving} onChange={(event) => onChange(index, "value", event.target.value)} />
              </label>
              {entrySuggestions.length ? (
                <div className="grid gap-1 border-t border-slate-200 pt-2">
                  {entrySuggestions.map((suggestion, suggestionIndex) => {
                    const field = suggestion.field === "key" ? "key" : "value";
                    const confidence = Number(suggestion.confidence);
                    return (
                      <div key={`${field}-${suggestion.suggested_value}-${suggestionIndex}`} className="grid gap-1 rounded border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-950">
                        <button
                          type="button"
                          disabled={saving}
                          onClick={() => {
                            onChange(index, field, String(suggestion.suggested_value ?? ""));
                            void recordDictionaryFeedback(documentId, suggestion, "accepted");
                          }}
                          className="text-left transition hover:text-amber-800 disabled:opacity-60"
                        >
                          <span className="font-semibold">추천 {field === "key" ? "Key" : "Value"}:</span> {displayValue(suggestion.suggested_value)}
                          {Number.isFinite(confidence) ? <span className="ml-1 text-amber-700">{Math.round(confidence * 100)}%</span> : null}
                        </button>
                        <button
                          type="button"
                          disabled={saving}
                          onClick={() => {
                            void recordDictionaryFeedback(documentId, suggestion, "rejected");
                            toast.success("이 추천을 거절로 기록했습니다");
                          }}
                          className="w-fit rounded border border-amber-200 bg-white px-1.5 py-0.5 text-[10px] text-amber-800 transition hover:bg-amber-100 disabled:opacity-60"
                        >
                          거절
                        </button>
                      </div>
                    );
                  })}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

async function recordDictionaryFeedback(documentId: string, suggestion: Record<string, unknown>, action: "accepted" | "rejected" | "ignored") {
  try {
    await api.domainDictionary.feedback({
      document_id: documentId,
      target: String(suggestion.target ?? "raw_key_value"),
      field: suggestion.field ? String(suggestion.field) : null,
      original_value: String(suggestion.original_value ?? ""),
      suggested_value: String(suggestion.suggested_value ?? ""),
      action,
      dictionary_type: suggestion.dictionary_type ? String(suggestion.dictionary_type) : null,
      metadata: {
        source: suggestion.source ?? null,
        dictionary_type: suggestion.dictionary_type ?? null,
        confidence: suggestion.confidence ?? null,
      },
    });
  } catch {
    // Feedback is advisory; editing the review form should not fail because logging failed.
  }
}

function classificationCandidates(document: DocumentRecord): Array<Record<string, unknown>> {
  const metadata = readRecord(document.workflow_metadata);
  const preMapping = readRecord(metadata.classification_pre_mapping);
  return Array.isArray(preMapping.candidates) ? preMapping.candidates.map((item) => readRecord(item)).filter((item) => item.category || item.document_type) : [];
}

function ClassificationCandidatePanel({ document }: { document: DocumentRecord }) {
  const candidates = classificationCandidates(document);
  if (!candidates.length) return null;
  return (
    <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-semibold text-slate-700">문서 유형 후보</p>
        <Badge variant="outline" className="bg-white">{candidates.length}개</Badge>
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        {candidates.map((candidate, index) => (
          <Badge key={`${candidate.category}-${index}`} variant="outline" className="bg-white text-slate-700">
            {displayValue(candidate.category || candidate.document_type)}
            {candidate.score ? <span className="ml-1 text-slate-400">{Math.round(Number(candidate.score) * 100)}%</span> : null}
          </Badge>
        ))}
      </div>
    </div>
  );
}

function EditableRawExtractedTable({
  document,
  items,
  suggestions,
  saving,
  onChange,
  onDelete,
  onAdd,
}: {
  document: DocumentRecord;
  items: ManufacturingLineItem[];
  suggestions: Array<Record<string, unknown>>;
  saving: boolean;
  onChange: (index: number, field: string, value: string) => void;
  onDelete: (index: number) => void;
  onAdd: () => void;
}) {
  const columns = rawEditorColumns(document, items);
  const suggestionsByCell = suggestions.reduce<Record<string, Array<Record<string, unknown>>>>((acc, suggestion) => {
    const rowIndex = Number(suggestion.row_index);
    const column = String(suggestion.column ?? "");
    if (Number.isInteger(rowIndex) && rowIndex >= 0 && column) {
      const key = `${rowIndex}|${rawTableFieldForColumn(column)}`;
      acc[key] = [...(acc[key] || []), suggestion];
    }
    return acc;
  }, {});
  return (
    <div className="grid gap-3 rounded-lg border border-blue-200 bg-blue-50/40 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-blue-950">추출 원형 표</p>
          <p className="mt-1 text-xs text-blue-800">표에서 바로 수정하고, 필요 없는 행은 삭제하세요.</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="bg-white text-blue-900">{items.length}행</Badge>
          <Button type="button" variant="outline" size="sm" onClick={onAdd} disabled={saving}>
            <Plus className="size-4" />
            행 추가
          </Button>
        </div>
      </div>
      {items.length ? (
        <div className="overflow-hidden rounded-md border bg-white">
          <div className="overflow-x-auto">
            <table className="min-w-full border-collapse text-left text-xs">
              <thead className="bg-slate-50 text-slate-600">
                <tr>
                  {columns.map((column) => (
                    <th key={column} className="whitespace-nowrap border-b px-2 py-2 font-medium">{column}</th>
                  ))}
                  <th className="w-16 whitespace-nowrap border-b px-2 py-2 text-right font-medium">삭제</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item, rowIndex) => (
                  <tr key={rowIndex} className="border-b last:border-0">
                    {columns.map((column) => {
                      const field = rawTableFieldForColumn(column);
                      const cellSuggestions = suggestionsByCell[`${rowIndex}|${field}`] || [];
                      return (
                        <td key={column} className="min-w-[120px] px-2 py-2 align-top">
                          <Input
                            value={String((item as Record<string, unknown>)[field] ?? "")}
                            onChange={(event) => onChange(rowIndex, field, event.target.value)}
                            className="h-8 min-w-[110px] text-xs"
                            inputMode={numericLineItemFields.has(field) ? "decimal" : undefined}
                            disabled={saving}
                          />
                          {cellSuggestions.length ? (
                            <div className="mt-1 grid gap-1">
                              {cellSuggestions.map((suggestion, index) => (
                                <div key={`${field}-${suggestion.suggested_value}-${index}`} className="rounded border border-amber-200 bg-amber-50 p-1 text-[10px] text-amber-950">
                                  <button
                                    type="button"
                                    disabled={saving}
                                    onClick={() => {
                                      onChange(rowIndex, field, String(suggestion.suggested_value ?? ""));
                                      void recordDictionaryFeedback(document.id, suggestion, "accepted");
                                    }}
                                    className="block text-left transition hover:text-amber-800 disabled:opacity-60"
                                  >
                                    추천: {displayValue(suggestion.suggested_value)}
                                  </button>
                                  <button
                                    type="button"
                                    disabled={saving}
                                    onClick={() => {
                                      void recordDictionaryFeedback(document.id, suggestion, "rejected");
                                      toast.success("이 추천을 거절로 기록했습니다");
                                    }}
                                    className="mt-1 rounded border border-amber-200 bg-white px-1 text-[10px] text-amber-800 transition hover:bg-amber-100 disabled:opacity-60"
                                  >
                                    거절
                                  </button>
                                </div>
                              ))}
                            </div>
                          ) : null}
                        </td>
                      );
                    })}
                    <td className="px-2 py-2 text-right">
                      <Button type="button" variant="outline" size="sm" onClick={() => onDelete(rowIndex)} disabled={saving}>
                        <Trash2 className="size-4" />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="rounded-lg border border-dashed bg-white p-4 text-sm text-muted-foreground">
          추출된 표 행이 없습니다. 행 추가로 직접 입력할 수 있습니다.
        </div>
      )}
    </div>
  );
}

function OfficialTableSourceCard({ document }: { document: DocumentRecord }) {
  const tables = officialTableEntries(document);
  if (!tables.length) return null;
  const rowCount = tables.reduce((sum, table) => sum + officialTableRowCount(table), 0);
  const tableTypes = Array.from(new Set(tables.map((table) => officialTableLabel(table.table_type))));
  return (
    <Card className="border-blue-200 bg-blue-50/40">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FileText className="size-5 text-primary" />
          공식 표 추출 근거
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="rounded-lg border border-blue-200 bg-white p-4 text-sm text-blue-950">
          <p className="font-semibold">PaddleOCR-VL 공식 표 추출</p>
          <p className="mt-1 text-blue-900">원본 표 구조를 기반으로 행과 열을 분리했습니다.</p>
          <p className="mt-1 text-xs text-blue-800">확정값 아님 · 원본 검토 후 확정</p>
        </div>
        <InfoGrid
          items={[
            ["표 개수", `${tables.length}개`],
            ["행 개수", rowCount ? `${rowCount}개` : null],
            ["표 유형", tableTypes.join(", ")],
          ]}
        />
        <div className="flex flex-wrap gap-2">
          {tables.map((table, index) => (
            <Badge key={`${table.table_type ?? "table"}-${index}`} variant="outline" className="bg-white text-blue-900">
              {officialTableLabel(table.table_type)} · {officialTableRowCount(table)}행
            </Badge>
          ))}
        </div>
        <details className="rounded-lg border bg-white p-3 text-sm">
          <summary className="cursor-pointer text-xs font-medium text-muted-foreground">공식 표 추출 상세</summary>
          <div className="mt-3 grid gap-2">
            {tables.map((table, index) => {
              const provenance = readRecord(table.provenance);
              const blockBbox = provenance.block_bbox ?? table.block_bbox;
              const polygon = provenance.polygon ?? table.polygon;
              return (
                <div key={`${table.source}-${index}`} className="grid gap-2 rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-700">
                  <span>source key: {String(table.source)}</span>
                  <span>table type: {String(table.table_type ?? "-")}</span>
                  <span>rows: {officialTableRowCount(table)}</span>
                  {Array.isArray(table.columns) ? <span>columns: {table.columns.map(String).join(", ")}</span> : null}
                  {Array.isArray(blockBbox) ? <span>block bbox: {blockBbox.map(String).join(", ")}</span> : null}
                  {Array.isArray(polygon) ? <span>polygon points: {polygon.length}</span> : null}
                </div>
              );
            })}
          </div>
        </details>
      </CardContent>
    </Card>
  );
}

function AiParsedDocumentCard({ document }: { document: DocumentRecord }) {
  const aiDocument = aiParsedDocumentMetadata(document);
  if (!aiDocument) {
    return (
      <Card className="border-slate-200 bg-slate-50/60">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="size-5 text-primary" />
            AI가 읽은 구조
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">AI가 읽은 구조 정보가 없습니다.</p>
        </CardContent>
      </Card>
    );
  }
  const keyValueSections = aiParsedSections(aiDocument, "key_value");
  const tableSections = aiParsedSections(aiDocument, "table");
  const noteSections = aiParsedSections(aiDocument, "notes");
  const blockedCandidates = aiParsedFields(aiDocument.blocked_candidates);
  const unmappedFields = aiParsedFields(aiDocument.unmapped_fields);
  const warnings = Array.isArray(aiDocument.warnings) ? aiDocument.warnings : [];
  const receiptCandidates = workflowCandidates(document, "receipt_item_candidates");
  const dateCandidates = workflowCandidates(document, "date_candidates");
  const identifierCandidates = workflowCandidates(document, "document_number_candidates");
  const partyCandidates = workflowCandidates(document, "party_review_candidates");
  const posSummary = posSettlementSummary(document);
  return (
    <Card className="border-indigo-200 bg-indigo-50/30">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sparkles className="size-5 text-primary" />
          AI가 읽은 구조
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="rounded-lg border border-indigo-200 bg-white p-4 text-sm text-indigo-950">
          <p className="font-semibold">이 영역은 AI가 문서에서 읽은 원형 구조입니다.</p>
          <p className="mt-1 text-indigo-900">확정값이 아니며, export에는 guardrail을 통과한 값만 포함됩니다.</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {aiDocument.document_type_hint ? <Badge variant="outline" className="bg-indigo-50">유형 후보: {titleCaseLabel(aiDocument.document_type_hint)}</Badge> : null}
            {aiDocument.document_type_confidence ? <Badge variant="outline" className="bg-indigo-50">신뢰도 {confidenceLabel(aiDocument.document_type_confidence)}</Badge> : null}
            {aiDocument.source ? <Badge variant="outline" className="bg-white">source: {aiDocument.source}</Badge> : null}
          </div>
        </div>

        <CandidateList
          title="거래처 후보"
          description="거래처/공급자/매장처럼 보이는 값입니다. 문서 유형과 라벨이 확실하지 않으면 확정값으로 자동 반영하지 않습니다."
          candidates={partyCandidates}
        />

        <CandidateList
          title="문서번호 / 참조번호 후보"
          description="문서번호, 원문서, 승인번호를 역할별로 분리해서 보여줍니다. 원문서나 승인번호는 현재 문서번호로 자동 사용하지 않습니다."
          candidates={identifierCandidates}
        />

        <CandidateList
          title="날짜 후보"
          description="작성일, 납품일, 검사일, 정산일 등 역할별 날짜 후보입니다. 확정값과 다를 수 있어 검토용으로 표시합니다."
          candidates={dateCandidates}
        />

        {posSummary ? (
          <details open className="rounded-lg border bg-white p-4">
            <summary className="cursor-pointer text-sm font-semibold">
              POS 정산 요약 후보
              <span className="ml-2 text-xs font-normal text-muted-foreground">제조 품목표 아님 · 검토 후보</span>
            </summary>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              {Object.entries(readRecord(posSummary.fields)).map(([field, value]) => {
                const record = readRecord(value);
                return (
                  <div key={field} className="rounded-md border bg-slate-50 p-3 text-sm">
                    <p className="text-xs text-muted-foreground">{record.source_label ? String(record.source_label) : field}</p>
                    <p className="mt-1 font-medium">{displayValue(record.value ?? value)}</p>
                    <Badge variant="outline" className="mt-2 border-blue-200 bg-blue-50 text-blue-800">{aiStatusLabel(record.status)}</Badge>
                  </div>
                );
              })}
            </div>
          </details>
        ) : null}

        <CandidateList
          title="영수증 품목 후보"
          description="영수증에서 읽은 품목 후보입니다. 행 경계나 금액 위치가 불안정할 수 있어 export 확정 행과 분리합니다."
          candidates={receiptCandidates}
        />

        {keyValueSections.length ? (
          <div className="space-y-3">
            {keyValueSections.map((section, sectionIndex) => (
              <div key={`kv-${sectionIndex}`} className="rounded-lg border bg-white p-4">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="text-sm font-semibold">{section.title || "일반 정보"}</p>
                    <p className="text-xs text-muted-foreground">key / value 형태로 보존된 검토 후보입니다.</p>
                  </div>
                  {section.source ? <Badge variant="outline">{section.source}</Badge> : null}
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  {aiParsedFields(section.fields).map((field, fieldIndex) => (
                    <div key={`${field.key}-${fieldIndex}`} className={`rounded-md border p-3 ${field.status === "unmapped" ? "bg-slate-50" : "bg-white"}`}>
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div>
                          <p className="text-xs text-muted-foreground">{field.key || "알 수 없는 키"}</p>
                          <p className="mt-1 break-words text-sm font-medium">{displayValue(field.value)}</p>
                        </div>
                        <Badge variant="outline" className={aiStatusClass(field.status)}>{aiStatusLabel(field.status)}</Badge>
                      </div>
                      <div className="mt-2 flex flex-wrap gap-1 text-[11px] text-muted-foreground">
                        {field.normalized_key ? <span className="rounded bg-slate-100 px-1.5 py-0.5">mapped: {field.normalized_key}</span> : <span className="rounded bg-slate-100 px-1.5 py-0.5">미매핑 후보</span>}
                        {field.source ? <span className="rounded bg-slate-100 px-1.5 py-0.5">{field.source}</span> : null}
                        {confidenceLabel(field.confidence) ? <span className="rounded bg-slate-100 px-1.5 py-0.5">{confidenceLabel(field.confidence)}</span> : null}
                      </div>
                      {field.evidence ? <p className="mt-2 break-words text-xs text-muted-foreground">근거: {field.evidence}</p> : null}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : null}

        {tableSections.length ? (
          <div className="space-y-3">
            {tableSections.map((section, sectionIndex) => {
              const rows = aiParsedRows(section.rows);
              const columns = aiParsedColumns(section, rows);
              return (
                <details key={`table-${sectionIndex}`} open className="rounded-lg border bg-white p-4">
                  <summary className="cursor-pointer text-sm font-semibold">
                    {section.title || "검토 후보 표"}
                    <span className="ml-2 text-xs font-normal text-muted-foreground">AI가 읽은 표 구조 · 확정값 아님</span>
                  </summary>
                  <div className="mt-3 overflow-auto">
                    <table className="min-w-full border-collapse text-sm">
                      <thead>
                        <tr className="bg-slate-50 text-left text-xs text-muted-foreground">
                          {columns.map((column) => <th key={column} className="border px-2 py-2 font-medium">{column}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((row, rowIndex) => {
                          const cells = readRecord(row.cells);
                          return (
                            <tr key={`${row.row_index ?? rowIndex}`} className="align-top">
                              {columns.map((column) => <td key={column} className="border px-2 py-2">{displayValue(cells[column])}</td>)}
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
                    {section.source ? <Badge variant="outline">source: {section.source}</Badge> : null}
                    {section.table_type_guess ? <Badge variant="outline">table: {section.table_type_guess}</Badge> : null}
                    {confidenceLabel(section.confidence) ? <Badge variant="outline">confidence {confidenceLabel(section.confidence)}</Badge> : null}
                  </div>
                </details>
              );
            })}
          </div>
        ) : null}

        {noteSections.length ? (
          <div className="rounded-lg border bg-white p-4">
            <p className="text-sm font-semibold">문서 안내/메모</p>
            <ul className="mt-3 grid gap-2 text-sm text-slate-700">
              {noteSections.flatMap((section) => Array.isArray(section.items) ? section.items : []).map((item, index) => (
                <li key={`${item}-${index}`} className="rounded-md bg-slate-50 px-3 py-2">{item}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {blockedCandidates.length ? (
          <div className="rounded-lg border border-red-200 bg-red-50 p-4">
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-red-700" />
              <div>
                <p className="text-sm font-semibold text-red-900">차단된 후보</p>
                <p className="mt-1 text-xs text-red-800">아래 후보는 위험 가능성이 있어 확정/export에서 제외되었습니다.</p>
              </div>
            </div>
            <div className="mt-3 grid gap-2">
              {blockedCandidates.map((field, index) => (
                <div key={`${field.key}-${index}`} className="rounded-md border border-red-200 bg-white p-3 text-sm">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <p className="text-xs text-muted-foreground">{field.key || field.normalized_key || "차단 후보"}</p>
                      <p className="mt-1 font-medium">{displayValue(field.value)}</p>
                    </div>
                    <Badge variant="outline" className={aiStatusClass("blocked")}>차단됨</Badge>
                  </div>
                  <div className="mt-2 grid gap-1 text-xs text-red-800 sm:grid-cols-2">
                    {field.risk ? <span>risk: {field.risk}</span> : null}
                    {field.reason ? <span>reason: {field.reason}</span> : null}
                    {field.normalized_key ? <span>field: {field.normalized_key}</span> : null}
                    {field.source ? <span>source: {field.source}</span> : null}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {(unmappedFields.length || warnings.length) ? (
          <details className="rounded-lg border bg-white p-4">
            <summary className="cursor-pointer text-sm font-semibold">미매핑 후보 / AI 구조화 경고</summary>
            {unmappedFields.length ? (
              <div className="mt-3">
                <p className="text-xs font-medium uppercase text-muted-foreground">미매핑 후보</p>
                <div className="mt-2 grid gap-2 sm:grid-cols-2">
                  {unmappedFields.map((field, index) => (
                    <div key={`${field.key}-${index}`} className="rounded-md bg-slate-50 px-3 py-2 text-sm">
                      <span className="font-medium">{field.key || "알 수 없는 키"}</span>
                      <span className="mx-1 text-muted-foreground">=</span>
                      <span>{displayValue(field.value)}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
            {warnings.length ? (
              <div className="mt-4">
                <p className="text-xs font-medium uppercase text-muted-foreground">AI 구조화 경고</p>
                <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-slate-50 p-3 text-xs text-slate-700">
                  {JSON.stringify(warnings, null, 2)}
                </pre>
              </div>
            ) : null}
          </details>
        ) : null}
      </CardContent>
    </Card>
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

function QualityDiagnosisCard({ document }: { document: DocumentRecord }) {
  const metadata = readRecord(document.workflow_metadata);
  const quality = readRecord(metadata.document_quality);
  const pages = Array.isArray(quality.pages) ? quality.pages.filter((page): page is Record<string, unknown> => Boolean(page && typeof page === "object" && !Array.isArray(page))) : [];
  if (!Object.keys(quality).length) return null;
  const score = typeof quality.overall_quality_score === "number" ? Math.round(quality.overall_quality_score * 100) : null;
  const reasons = readList(quality.review_reasons);
  const visibleColumns = readList(quality.visible_columns);
  const hiddenColumns = readList(quality.hidden_or_cropped_columns);
  const hasCropRisk = Boolean(quality.possible_right_column_crop);
  const hasBlurryPages = Boolean(quality.has_blurry_pages);
  const hasSkewedPages = Boolean(quality.has_skewed_pages);
  const scanType = typeof quality.likely_scan_type === "string" ? quality.likely_scan_type : "unknown";
  const reasonLabels: Record<string, string> = {
    document_low_resolution: "해상도가 낮아 수량과 금액을 원본으로 확인해야 합니다.",
    document_image_blurry: "글자가 흐려 일부 값은 원본 확인이 필요합니다.",
    document_low_contrast: "명암이 낮아 인식 정확도가 떨어질 수 있습니다.",
    document_page_skewed: "문서가 기울어져 표 행/열 확인이 필요합니다.",
    document_right_column_crop_risk: "오른쪽 금액/세액 컬럼이 잘렸을 가능성이 있습니다.",
    document_photo_source: "사진 촬영본으로 감지되어 자동 확정보다 검토가 필요합니다.",
    document_fax_like_source: "팩스/저품질 스캔 문서로 감지되어 원본 확인이 필요합니다.",
    document_quality_unreadable_image: "문서 이미지를 품질 진단하기 어려워 원본 확인이 필요합니다.",
    document_quality_no_rendered_pages: "렌더링된 페이지가 없어 원본 확인이 필요합니다.",
  };
  return (
    <Card className={hasCropRisk || hasBlurryPages || hasSkewedPages ? "border-amber-300 bg-amber-50/30" : ""}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="size-5 text-primary" />
          문서 품질 진단
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <InfoGrid
          items={[
            ["품질 점수", score !== null ? `${score}%` : null],
            ["문서 유형", scanType === "digital_pdf" ? "디지털 PDF" : scanType === "photo" ? "사진 문서" : scanType === "fax_like" ? "팩스형 문서" : scanType === "scan" ? "스캔 문서" : "확인 필요"],
            ["페이지 수", typeof quality.page_count === "number" ? String(quality.page_count) : null],
            ["오른쪽 컬럼", hasCropRisk ? "금액/세액 컬럼 확인 필요" : "잘림 위험 낮음"],
          ]}
        />
        {reasons.length ? (
          <div className="flex flex-wrap gap-2">
            {reasons.map((reason) => (
              <Badge key={reason} variant="outline" className="bg-white text-amber-800">
                {reasonLabels[reason] ?? titleCaseLabel(reason)}
              </Badge>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">자동 품질 진단에서 특별한 위험 신호가 없습니다.</p>
        )}
        {visibleColumns.length || hiddenColumns.length ? (
          <div className="grid gap-3 text-sm sm:grid-cols-2">
            {visibleColumns.length ? (
              <div className="rounded-lg border bg-white p-3">
                <p className="mb-2 text-xs font-medium text-muted-foreground">보이는 컬럼 후보</p>
                <div className="flex flex-wrap gap-1">
                  {visibleColumns.map((column) => <Badge key={column} variant="outline">{businessColumnLabel(column)}</Badge>)}
                </div>
              </div>
            ) : null}
            {hiddenColumns.length ? (
              <div className="rounded-lg border border-amber-200 bg-white p-3">
                <p className="mb-2 text-xs font-medium text-amber-700">가려졌거나 잘렸을 수 있는 컬럼</p>
                <div className="flex flex-wrap gap-1">
                  {hiddenColumns.map((column) => <Badge key={column} className="bg-amber-100 text-amber-900">{businessColumnLabel(column)}</Badge>)}
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
        {pages.length ? (
          <details className="rounded-lg border bg-white p-3 text-sm">
            <summary className="cursor-pointer text-xs font-medium text-muted-foreground">페이지별 품질 정보</summary>
            <div className="mt-3 grid gap-2">
              {pages.slice(0, 6).map((page, index) => (
                <div key={`${page.page_index ?? index}`} className="grid gap-1 rounded-md bg-slate-50 px-3 py-2 text-xs sm:grid-cols-4">
                  <span>{String(page.page_index ?? index + 1)}페이지</span>
                  <span>{String(page.width ?? "-")}×{String(page.height ?? "-")}</span>
                  <span>흐림 {String(page.blur_score ?? "-")}</span>
                  <span>{page.possible_right_column_crop ? "금액/세액 컬럼 확인" : "컬럼 위험 낮음"}</span>
                </div>
              ))}
            </div>
          </details>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ErpReadinessBanner({
  document,
  openIssueCount,
  exportTemplates,
  selectedExportTemplateId,
  onExportTemplateChange,
}: {
  document: DocumentRecord;
  openIssueCount: number;
  exportTemplates: ExportTemplateRecord[];
  selectedExportTemplateId: string;
  onExportTemplateChange: (value: string) => void;
}) {
  const readiness = getErpReadinessStatus(document);
  const summary = getErpReadinessSummary(document);
  const schedule = getDocumentScheduleDate(document);
  const layoutDebug = layoutDebugMetadata(document);
  const exportParams = new URLSearchParams({ document_ids: document.id });
  if (selectedExportTemplateId) exportParams.set("template_id", selectedExportTemplateId);
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
          <p className="text-sm font-medium">업무데이터/엑셀 입력 준비 상태</p>
          <h2 className="mt-1 text-2xl font-semibold tracking-normal">{readiness.title}</h2>
          <p className="mt-2 text-sm">{summary}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Badge variant="outline">{document.processing_status}</Badge>
            {document.review_required ? <Badge className="border-amber-400 bg-white text-amber-800">검토 필요 {openIssueCount}건</Badge> : <Badge className="border-emerald-400 bg-white text-emerald-800">검토 항목 없음</Badge>}
            {schedule ? <Badge variant="outline">{schedule.label}: {schedule.date}</Badge> : <Badge variant="outline">일정 날짜 없음</Badge>}
            {layoutDebug?.bbox_candidate_summary?.uncertain_count ? <Badge className="border-amber-400 bg-white text-amber-800">OCR 위치 기반 후보 {layoutDebug.bbox_candidate_summary.uncertain_count}건</Badge> : null}
          </div>
        </div>
        <div className="flex flex-col gap-2 lg:min-w-80">
          <label className="grid gap-1 text-xs font-medium">
            출력 템플릿
            <select
              className="h-9 rounded-md border bg-white px-3 text-sm text-slate-900"
              value={selectedExportTemplateId}
              onChange={(event) => onExportTemplateChange(event.target.value)}
            >
              <option value="">기본 출력</option>
              {exportTemplates.map((template) => <option key={template.id} value={template.id}>{template.name}</option>)}
            </select>
          </label>
          <div className="flex flex-wrap gap-2 lg:justify-end">
            <Button asChild variant="outline">
              <a href={api.exportExcelUrl(exportParams)}>
                <Download className="size-4" />
                Excel 다운로드
              </a>
            </Button>
            <Button asChild variant="outline">
              <a href={api.exportCsvUrl(exportParams)}>
                <Download className="size-4" />
                CSV 다운로드
              </a>
            </Button>
            <Button asChild variant="outline">
              <Link href="/settings/export-templates">템플릿 관리</Link>
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

type PreviewSize = { width: number; height: number };
type NaturalImageSize = { width: number; height: number };
type DocumentNeighbor = { id: string; label: string | null; filename: string };
type DocumentNeighbors = { previous: DocumentNeighbor | null; next: DocumentNeighbor | null };

function OriginalPreviewCard({ document, isImage }: { document: DocumentRecord; isImage: boolean }) {
  const previewRef = useRef<HTMLDivElement | null>(null);
  const zoomAnchorRef = useRef<{ ratioX: number; ratioY: number; offsetX: number; offsetY: number } | null>(null);
  const dragRef = useRef<{ pointerId: number; startX: number; startY: number; scrollLeft: number; scrollTop: number } | null>(null);
  const [zoom, setZoom] = useState(100);
  const [naturalSize, setNaturalSize] = useState<NaturalImageSize | null>(null);
  const [previewSize, setPreviewSize] = useState<PreviewSize>({ width: 0, height: 0 });
  const [isDraggingPreview, setIsDraggingPreview] = useState(false);
  const isPdf = document.mime_type === "application/pdf";
  const fileUrl = documentFileUrl(document.file_url);
  const previewUrl = isPdf ? `${fileUrl}#toolbar=0&navpanes=0&scrollbar=0&view=Fit` : fileUrl;
  const imageLayout = isImage && naturalSize && previewSize.width && previewSize.height
    ? imagePreviewLayout({ naturalSize, previewSize, zoom })
    : null;
  const pdfLayout = isPdf && previewSize.width && previewSize.height
    ? pdfPreviewLayout({ previewSize, zoom })
    : null;

  useEffect(() => {
    if (!previewRef.current) return;
    const element = previewRef.current;
    let frame = 0;
    const updatePreviewSize = () => {
      const next = {
        width: Math.max(1, Math.floor(element.clientWidth)),
        height: Math.max(1, Math.floor(element.clientHeight)),
      };
      setPreviewSize((current) => (current.width === next.width && current.height === next.height ? current : next));
    };
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(updatePreviewSize);
    });
    observer.observe(element);
    updatePreviewSize();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, []);

  useEffect(() => {
    setZoom(100);
    setNaturalSize(null);
    zoomAnchorRef.current = null;
    dragRef.current = null;
    setIsDraggingPreview(false);
  }, [document.id]);

  useLayoutEffect(() => {
    const element = previewRef.current;
    const anchor = zoomAnchorRef.current;
    if (!element || !anchor) return;
    zoomAnchorRef.current = null;
    element.scrollLeft = clampNumber(
      element.scrollWidth * anchor.ratioX - anchor.offsetX,
      0,
      Math.max(0, element.scrollWidth - element.clientWidth),
    );
    element.scrollTop = clampNumber(
      element.scrollHeight * anchor.ratioY - anchor.offsetY,
      0,
      Math.max(0, element.scrollHeight - element.clientHeight),
    );
  }, [zoom, imageLayout, pdfLayout]);

  const handlePreviewWheel = useCallback((event: globalThis.WheelEvent) => {
    event.preventDefault();
    event.stopPropagation();
    const element = previewRef.current;
    if (!element) return;
    const rect = element.getBoundingClientRect();
    const offsetX = event.clientX - rect.left;
    const offsetY = event.clientY - rect.top;
    zoomAnchorRef.current = {
      ratioX: element.scrollWidth ? (element.scrollLeft + offsetX) / element.scrollWidth : 0.5,
      ratioY: element.scrollHeight ? (element.scrollTop + offsetY) / element.scrollHeight : 0.5,
      offsetX,
      offsetY,
    };
    setZoom((value) => clampNumber(value + (event.deltaY < 0 ? 10 : -10), 60, 320));
  }, []);

  useEffect(() => {
    const element = previewRef.current;
    if (!element) return;
    element.addEventListener("wheel", handlePreviewWheel, { passive: false });
    return () => element.removeEventListener("wheel", handlePreviewWheel);
  }, [handlePreviewWheel]);

  function handlePreviewPointerDown(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    const element = previewRef.current;
    if (!element) return;
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      scrollLeft: element.scrollLeft,
      scrollTop: element.scrollTop,
    };
    element.setPointerCapture(event.pointerId);
    setIsDraggingPreview(true);
    event.preventDefault();
  }

  function handlePreviewPointerMove(event: PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    const element = previewRef.current;
    if (!drag || !element || drag.pointerId !== event.pointerId) return;
    element.scrollLeft = drag.scrollLeft - (event.clientX - drag.startX);
    element.scrollTop = drag.scrollTop - (event.clientY - drag.startY);
  }

  function endPreviewDrag(event: PointerEvent<HTMLDivElement>) {
    const element = previewRef.current;
    const drag = dragRef.current;
    if (element && drag?.pointerId === event.pointerId && element.hasPointerCapture(event.pointerId)) {
      element.releasePointerCapture(event.pointerId);
    }
    dragRef.current = null;
    setIsDraggingPreview(false);
  }

  function handleImageLoad(event: SyntheticEvent<HTMLImageElement>) {
    const image = event.currentTarget;
    setNaturalSize({ width: image.naturalWidth || 1, height: image.naturalHeight || 1 });
  }

  return (
    <Card className="overflow-hidden xl:flex xl:h-[calc(100vh-6rem)] xl:flex-col">
      <CardHeader className="gap-3 space-y-0 xl:shrink-0">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <CardTitle>원본 문서</CardTitle>
          <Button asChild variant="outline" size="sm">
            <a href={fileUrl} target="_blank" rel="noreferrer">원본 열기</a>
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          원본 전체 페이지를 유지합니다. 마우스 위치에서 휠로 확대/축소하고, 확대 후에는 문서를 드래그해서 이동하세요. 현재 {zoom}%
        </p>
      </CardHeader>
      <CardContent className="space-y-3 xl:flex xl:min-h-0 xl:flex-1 xl:flex-col">
        {isImage ? (
          <div
            ref={previewRef}
            className={`relative h-[calc(100vh-12rem)] min-h-[34rem] max-h-[58rem] w-full overflow-auto overscroll-contain rounded-lg border bg-neutral-100 [scrollbar-gutter:stable_both-edges] xl:min-h-0 xl:max-h-none xl:flex-1 ${isDraggingPreview ? "cursor-grabbing" : "cursor-grab"}`}
            onPointerDown={handlePreviewPointerDown}
            onPointerMove={handlePreviewPointerMove}
            onPointerUp={endPreviewDrag}
            onPointerCancel={endPreviewDrag}
          >
            <div
              className="relative bg-white"
              style={{
                width: imageLayout?.innerWidth ?? "100%",
                height: imageLayout?.innerHeight ?? "100%",
                minWidth: "100%",
                minHeight: "100%",
              }}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={fileUrl}
                alt={document.original_filename}
                onLoad={handleImageLoad}
                className="absolute max-w-none select-none"
                draggable={false}
                style={imageLayout ? {
                  width: imageLayout.imageWidth,
                  height: imageLayout.imageHeight,
                  left: imageLayout.left,
                  top: imageLayout.top,
                } : {
                  width: "100%",
                  height: "100%",
                  objectFit: "contain",
                  left: 0,
                  top: 0,
                }}
              />
            </div>
          </div>
        ) : isPdf ? (
          <div
            ref={previewRef}
            className={`relative h-[calc(100vh-12rem)] min-h-[34rem] max-h-[58rem] w-full overflow-auto overscroll-contain rounded-lg border bg-neutral-100 [scrollbar-gutter:stable_both-edges] xl:min-h-0 xl:max-h-none xl:flex-1 ${isDraggingPreview ? "cursor-grabbing" : "cursor-grab"}`}
            onPointerDown={handlePreviewPointerDown}
            onPointerMove={handlePreviewPointerMove}
            onPointerUp={endPreviewDrag}
            onPointerCancel={endPreviewDrag}
          >
            <div
              className="relative mx-auto bg-white shadow-sm"
              style={{
                width: pdfLayout?.innerWidth ?? "100%",
                height: pdfLayout?.innerHeight ?? "100%",
                minWidth: "100%",
                minHeight: "100%",
              }}
            >
              <div
                className="absolute bg-white shadow-sm"
                style={{
                  width: pdfLayout?.pageWidth ?? "100%",
                  height: pdfLayout?.pageHeight ?? "100%",
                  left: pdfLayout?.left ?? 0,
                  top: pdfLayout?.top ?? 0,
                }}
              >
              <iframe
                key={previewUrl}
                src={previewUrl}
                title={document.original_filename}
                className="h-full w-full bg-white pointer-events-none"
              />
              </div>
            </div>
          </div>
        ) : (
          <div className="flex min-h-72 flex-col items-center justify-center rounded-lg border bg-white p-8 text-center xl:flex-1">
            <FileText className="mb-3 size-10 text-primary" />
            <p className="font-semibold">{document.original_filename}</p>
            <p className="mt-1 text-sm text-muted-foreground">{document.mime_type}</p>
          </div>
        )}
        <details className="xl:shrink-0">
          <summary className="cursor-pointer text-sm font-medium text-muted-foreground">파일 정보</summary>
          <div className="mt-3">
          <InfoGrid
            items={[
              ["파일 형식", titleCaseLabel(document.source_file_type || "unknown")],
              ["추출 방식", extractionMethodLabel(document)],
              ["업로드 날짜", formatDateTime(document.created_at)],
              ["최근 수정", formatDateTime(document.updated_at)],
            ]}
          />
          </div>
        </details>
      </CardContent>
    </Card>
  );
}

function clampNumber(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function layoutPixel(value: number) {
  return Math.max(1, Math.round(value));
}

function imagePreviewLayout({
  naturalSize,
  previewSize,
  zoom,
}: {
  naturalSize: NaturalImageSize;
  previewSize: PreviewSize;
  zoom: number;
}) {
  const imageWidth = Math.max(1, naturalSize.width);
  const imageHeight = Math.max(1, naturalSize.height);
  const viewportWidth = Math.max(1, previewSize.width);
  const viewportHeight = Math.max(1, previewSize.height);

  const baseScale = Math.min(viewportWidth / imageWidth, viewportHeight / imageHeight);
  const scale = Math.max(0.05, baseScale * (zoom / 100));
  const scaledImageWidth = layoutPixel(imageWidth * scale);
  const scaledImageHeight = layoutPixel(imageHeight * scale);
  const innerWidth = layoutPixel(Math.max(viewportWidth, scaledImageWidth));
  const innerHeight = layoutPixel(Math.max(viewportHeight, scaledImageHeight));
  const left = Math.max(0, Math.round((innerWidth - scaledImageWidth) / 2));
  const top = Math.max(0, Math.round((innerHeight - scaledImageHeight) / 2));
  const scrollLeft = clampNumber((innerWidth - viewportWidth) / 2, 0, Math.max(0, innerWidth - viewportWidth));
  const scrollTop = clampNumber((innerHeight - viewportHeight) / 2, 0, Math.max(0, innerHeight - viewportHeight));

  return {
    innerWidth,
    innerHeight,
    imageWidth: scaledImageWidth,
    imageHeight: scaledImageHeight,
    left,
    top,
    scrollLeft,
    scrollTop,
  };
}

function pdfPreviewLayout({ previewSize, zoom }: { previewSize: PreviewSize; zoom: number }) {
  const viewportWidth = Math.max(1, previewSize.width);
  const viewportHeight = Math.max(1, previewSize.height);
  const scale = Math.max(0.6, zoom / 100);
  const pageWidth = layoutPixel(viewportWidth * scale);
  const pageHeight = layoutPixel(viewportHeight * scale);
  const innerWidth = layoutPixel(Math.max(viewportWidth, pageWidth));
  const innerHeight = layoutPixel(Math.max(viewportHeight, pageHeight));
  return {
    innerWidth,
    innerHeight,
    pageWidth,
    pageHeight,
    left: Math.max(0, Math.round((innerWidth - pageWidth) / 2)),
    top: Math.max(0, Math.round((innerHeight - pageHeight) / 2)),
  };
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
  const quality = (metadata.document_quality ?? metadata.quality ?? {}) as Record<string, unknown>;
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
  const [exportTemplates, setExportTemplates] = useState<ExportTemplateRecord[]>([]);
  const [selectedExportTemplateId, setSelectedExportTemplateId] = useState("");
  const [approvalNote, setApprovalNote] = useState("");
  const [documentNeighbors, setDocumentNeighbors] = useState<DocumentNeighbors>({ previous: null, next: null });
  const rawLineItemsInitializedFor = useRef<string | null>(null);
  const dictionarySuggestionsRefreshedFor = useRef<string | null>(null);
  const form = useForm<DocumentUpdate & { tags_text: string }>();

  const syncDocument = useCallback((item: DocumentRecord) => {
    setDocument(item);
    form.reset(toForm(item));
    setApprovalNote(documentReviewMetadata(item).approval_note || "");
  }, [form]);

  useEffect(() => {
    setLoading(true);
    api
      .get(params.id)
      .then(syncDocument)
      .catch((error) => toast.error(error instanceof Error ? error.message : "문서를 불러오지 못했습니다"))
      .finally(() => setLoading(false));
    api.categories().then(setCategories).catch(() => setCategories([]));
    api.exportTemplates.list()
      .then((items) => {
        setExportTemplates(items);
        setSelectedExportTemplateId((current) => current || items.find((item) => item.is_default)?.id || "");
      })
      .catch(() => setExportTemplates([]));
  }, [params.id, syncDocument]);

  const refreshDocument = useCallback(async () => {
    try {
      const item = await api.get(params.id);
      syncDocument(item);
    } catch {
      // The user may be editing; leave the current form intact until the next successful refresh.
    } finally {
      setLoading(false);
    }
  }, [params.id, syncDocument]);

  const refreshDictionarySuggestions = useCallback(async ({ silent = false }: { silent?: boolean } = {}) => {
    if (!silent) setSaving(true);
    try {
      const item = await api.refreshDictionarySuggestions(params.id);
      if (!form.formState.isDirty) syncDocument(item);
      else setDocument(item);
      if (!silent) toast.success("추천을 새로 계산했습니다");
    } catch (error) {
      if (!silent) toast.error(error instanceof Error ? error.message : "추천 새로고침에 실패했습니다");
    } finally {
      if (!silent) setSaving(false);
    }
  }, [form.formState.isDirty, params.id, syncDocument]);

  useEffect(() => {
    if (!document || dictionarySuggestionsRefreshedFor.current === document.id) return;
    dictionarySuggestionsRefreshedFor.current = document.id;
    void refreshDictionarySuggestions({ silent: true });
  }, [document?.id, document, refreshDictionarySuggestions]);

  useDocumentsChanged(useCallback((detail) => {
    if (!document) return;
    if (form.formState.isDirty && !isLiveProcessingStatus(document.processing_status)) return;
    if (isLiveProcessingStatus(document.processing_status) || detail.stats?.processing || detail.stats?.queued) {
      void refreshDocument();
    }
  }, [document, form.formState.isDirty, refreshDocument]), Boolean(document));

  useEffect(() => {
    loadDocumentNeighbors(params.id)
      .then(setDocumentNeighbors)
      .catch(() => setDocumentNeighbors({ previous: null, next: null }));
  }, [params.id]);

  useEffect(() => {
    if (!document || rawLineItemsInitializedFor.current === document.id) return;
    rawLineItemsInitializedFor.current = document.id;
    const currentItems = form.getValues("line_items") || [];
    if (currentItems.length) return;
    const rawItems = rawLineItemsFromOfficialTables(document);
    if (rawItems.length) {
      form.setValue("line_items", rawItems, { shouldDirty: false });
    }
  }, [document, form]);

  async function onSubmit(values: DocumentReviewForm) {
    setSaving(true);
    try {
      const payload = buildDocumentUpdatePayload(values, document?.document_type);
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
      const currentValues = form.getValues() as DocumentReviewForm;
      const saved = await api.update(params.id, buildDocumentUpdatePayload(currentValues, document?.document_type));
      syncDocument(saved);
      const updated = await api.confirm(params.id, { approval_note: approvalNote || null });
      syncDocument(updated);
      toast.success("현재 리뷰값을 저장하고 확정 완료로 변경했습니다");
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

  function navigateToDocument(id: string) {
    const from = searchParams.get("from");
    router.push(`/documents/${id}${from ? `?from=${encodeURIComponent(from)}` : ""}`);
  }

  function updateLineItem(index: number, field: string, value: string) {
    const items = [...(form.getValues("line_items") || [])];
    const current = { ...(items[index] || {}) };
    const cleaned = cleanLineItemValue(field, value);
    current[field] = cleaned;
    items[index] = current;
    form.setValue("line_items", items, { shouldDirty: true });
  }

  function updateReviewedKeyValue(index: number, field: "key" | "value", value: string) {
    const entries = [...((form.getValues("reviewed_key_values") as Array<Record<string, unknown>> | undefined) || [])];
    const current = { ...(entries[index] || {}) };
    current[field] = value;
    current.reviewed = true;
    entries[index] = current;
    form.setValue("reviewed_key_values", entries, { shouldDirty: true });
  }

  function addLineItem() {
    const columns = document ? rawEditorColumns(document, form.getValues("line_items") || []) : [];
    const items = [...(form.getValues("line_items") || [])];
    const next: ManufacturingLineItem = {};
    for (const column of columns) {
      (next as Record<string, unknown>)[rawTableFieldForColumn(column)] = "";
    }
    items.push(next);
    form.setValue("line_items", items, { shouldDirty: true });
  }

  function removeLineItem(index: number) {
    const items = [...(form.getValues("line_items") || [])];
    const itemLabel = String(items[index]?.item_name || items[index]?.document_item_code || items[index]?.item_code || `${index + 1}번째 품목`);
    if (!window.confirm(`"${itemLabel}" 행을 삭제할까요? 저장 버튼을 눌러야 최종 반영됩니다.`)) return;
    items.splice(index, 1);
    form.setValue("line_items", items, { shouldDirty: true });
    toast.success("행을 삭제했습니다. 저장하면 문서에 반영됩니다.");
  }

  const watchedLineItems = form.watch("line_items") ?? [];
  const reviewedKeyValues = (form.watch("reviewed_key_values") as Array<Record<string, unknown>> | undefined) ?? [];
  const keyValueSuggestions = document ? dictionarySuggestions(document) : [];
  const rawTableSuggestions = document ? tableDictionarySuggestions(document) : [];

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
  const lineItems = watchedLineItems;
  const blockingIssues = blockingReviewIssues(document);
  const blockingIssueSummaryItems = reviewIssueSummaryItems(blockingIssues);
  const groupedBlockingIssueItems = groupedReviewIssues(blockingIssues);
  const infoIssues = informationalReviewIssues(document);
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
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!documentNeighbors.previous}
              title={documentNeighbors.previous ? `${documentNeighbors.previous.label || documentNeighbors.previous.filename} 문서로 이동` : "이전 문서가 없습니다"}
              onClick={() => documentNeighbors.previous && navigateToDocument(documentNeighbors.previous.id)}
            >
              <ChevronLeft className="size-4" />
              이전 문서
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!documentNeighbors.next}
              title={documentNeighbors.next ? `${documentNeighbors.next.label || documentNeighbors.next.filename} 문서로 이동` : "다음 문서가 없습니다"}
              onClick={() => documentNeighbors.next && navigateToDocument(documentNeighbors.next.id)}
            >
              다음 문서
              <ChevronRight className="size-4" />
            </Button>
          </div>
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
            <Button variant="outline" onClick={() => void refreshDictionarySuggestions()} disabled={saving}>
              <RefreshCw className="size-4" />
              추천 새로고침
            </Button>
            <Button asChild variant="outline" disabled={!isConfirmed}>
              <a href={isConfirmed ? api.exportJsonUrl(document.id) : undefined} aria-disabled={!isConfirmed}>
                <Download className="size-4" />
                JSON으로 내보내기
              </a>
            </Button>
            {document.document_type === "invoice" ? (
              <Button asChild variant="outline" disabled={!isConfirmed}>
                <a href={isConfirmed ? api.exportTaxInvoiceXmlUrl(document.id) : undefined} aria-disabled={!isConfirmed}>
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

      <ErpReadinessBanner
        document={document}
        openIssueCount={openIssueCount}
        exportTemplates={exportTemplates}
        selectedExportTemplateId={selectedExportTemplateId}
        onExportTemplateChange={setSelectedExportTemplateId}
      />

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

      <form onSubmit={form.handleSubmit(onSubmit)} className="grid gap-6 xl:grid-cols-[minmax(420px,1fr)_minmax(520px,0.95fr)] xl:items-start">
        <section className="xl:sticky xl:top-24 xl:max-h-[calc(100vh-7rem)] xl:min-h-0">
          <OriginalPreviewCard document={document} isImage={isImage} />
        </section>

        <section className="flex min-w-0 flex-col gap-6 xl:min-h-0 xl:pb-6">
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
                {readRecord(readRecord(document.workflow_metadata).field_provenance).summary ? (
                  <details className="rounded-lg border bg-white p-4">
                    <summary className="cursor-pointer text-xs font-medium uppercase text-muted-foreground">
                      필드 근거 / 보이는 값 정책 상세
                    </summary>
                    <p className="mt-3 text-sm text-muted-foreground">
                      업무 화면에는 확정값만 사용하고, 이 영역에는 출처와 검토 필요 여부를 점검하기 위한 상세 근거를 보관합니다.
                    </p>
                    <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-slate-50 p-3 text-xs text-slate-700">
                      {JSON.stringify(readRecord(document.workflow_metadata).field_provenance, null, 2)}
                    </pre>
                  </details>
                ) : null}
                <ProcessingMetadataDetails document={document} />
              </CardContent>
            </Card>
          ) : null}

          <TaxonomyPolicyCard document={document} />
          <QualityDiagnosisCard document={document} />
          <OfficialTableSourceCard document={document} />
          <AiParsedDocumentCard document={document} />
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
                  [
                    "검토 필요 항목",
                    blockingIssueSummaryItems.length
                      ? blockingIssueSummaryItems.join(", ")
                      : document.review_required
                        ? "처리 경고 또는 참고 정보를 확인하세요"
                        : "없음",
                  ],
                ]}
              />
              <div className="grid gap-2 rounded-lg border bg-white p-3">
                <label className="grid gap-2 text-sm font-medium">
                  승인 메모
                  <Textarea
                    className="min-h-20"
                    placeholder="예: 원본 PDF와 대조 후 업무데이터 확정 전 확인 완료"
                    value={approvalNote}
                    onChange={(event) => setApprovalNote(event.target.value)}
                  />
                </label>
                <p className="text-xs text-muted-foreground">검토 완료 버튼을 누르면 이 메모가 workflow metadata에 저장됩니다.</p>
              </div>
              {blockingIssues.length ? (
                <div className="grid gap-2">
                  <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-xs text-blue-900">
                    <p className="font-medium">검토 항목 버튼 안내</p>
                    <p className="mt-1">
                      <span className="font-semibold">해결</span>은 값을 직접 수정하거나 후보를 선택해 문제가 처리되었다는 뜻입니다.
                      <span className="ml-1 font-semibold">무시</span>는 원본 확인 결과 업무상 문제 없다고 판단해 확정 차단에서 제외한다는 뜻입니다.
                      두 선택 모두 기록에 남고, 이후 다시 검토 필요 상태로 되돌릴 수 있습니다.
                    </p>
                  </div>
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
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              disabled={saving}
                              title="값을 수정하거나 후보를 선택해 이 검토 항목이 해결되었을 때 누릅니다."
                              onClick={() => issueKeys.length === 1 ? setReviewIssueStatus(issueKeys[0], "resolved") : setReviewIssueGroupStatus(issueKeys, "resolved")}
                            >
                              해결
                            </Button>
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              disabled={saving}
                              title="원본 확인 결과 업무상 문제 없다고 판단해 확정 차단에서 제외할 때 누릅니다."
                              onClick={() => issueKeys.length === 1 ? setReviewIssueStatus(issueKeys[0], "ignored") : setReviewIssueGroupStatus(issueKeys, "ignored")}
                            >
                              무시
                            </Button>
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
          <Card className="order-first overflow-hidden">
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
                <ClassificationCandidatePanel document={document} />
                <p className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
                  <Tag className="size-3.5" />
                  문서 유형은 검색, 필터, 내보내기 데이터에 함께 반영됩니다.
                </p>
              </section>

              <section className="grid gap-4 rounded-lg border bg-white p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold">품목 정보</p>
                    <p className="mt-1 text-xs text-muted-foreground">추출된 표를 그대로 확인하고 필요한 셀만 수정하세요.</p>
                  </div>
                </div>
                <RawKeyValueEditor documentId={document.id} entries={reviewedKeyValues} suggestions={keyValueSuggestions} saving={saving} onChange={updateReviewedKeyValue} />
                <EditableRawExtractedTable
                  document={document}
                  items={lineItems}
                  suggestions={rawTableSuggestions}
                  saving={saving}
                  onChange={updateLineItem}
                  onDelete={removeLineItem}
                  onAdd={addLineItem}
                />
              </section>

              <section className="grid gap-4 rounded-lg border bg-slate-50/60 p-4">
                <div>
                  <p className="text-sm font-semibold">문서 기본 정보</p>
                  <p className="mt-1 text-xs text-muted-foreground">공급업체, 고객사, 문서번호, 날짜, 금액을 업무데이터 기준으로 수정하세요.</p>
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
