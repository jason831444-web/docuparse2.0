export type DocumentType =
  | "purchase_order"
  | "quotation"
  | "transaction_statement"
  | "delivery_note"
  | "invoice"
  | "packing_list"
  | "inspection_report"
  | "contract"
  | "general_document"
  | "receipt"
  | "notice"
  | "document"
  | "memo"
  | "presentation"
  | "other";
export type ProcessingStatus = "uploaded" | "queued" | "processing" | "ready" | "needs_review" | "confirmed" | "completed" | "failed";

export interface DocumentTaxonomy {
  document_subtype?: string | null;
  document_profile?: string | null;
  document_profiles?: string[];
  layout_profile?: string | null;
  amount_required?: boolean | null;
  party_required?: boolean | null;
  evidence?: string[];
}

export type ReviewIssueStatus = "open" | "resolved" | "ignored" | "blocked";

export interface ReviewIssueState {
  key: string;
  code?: string | null;
  field?: string | null;
  item_index?: number | null;
  status?: ReviewIssueStatus | string | null;
  message_ko?: string | null;
  note?: string | null;
  updated_at?: string | null;
  resolved_at?: string | null;
}

export interface DocumentReviewMetadata {
  issues?: ReviewIssueState[];
  reviewed_at?: string | null;
  approved_at?: string | null;
  approved_by?: string | null;
  approval_note?: string | null;
  review_state?: string | null;
  approved?: boolean | null;
  approval_validation?: {
    ok?: boolean;
    blocking?: string[];
    warnings?: string[];
  } | null;
}

export interface BBoxTableCandidate {
  row_index?: number | null;
  item_name?: string | null;
  source_text?: string | null;
  document_item_code?: string | null;
  internal_item_code?: string | null;
  specification?: string | null;
  quantity?: number | string | null;
  unit?: string | null;
  unit_price?: number | string | null;
  supply_amount?: number | string | null;
  tax_amount?: number | string | null;
  line_total?: number | string | null;
  confidence?: number | string | null;
  review_flags?: string[];
  missing_fields?: string[];
  untrusted_fields?: string[];
  source?: string | null;
  bbox_span?: Record<string, number> | null;
}

export interface BBoxCandidateSummary {
  candidate_count?: number;
  uncertain_count?: number;
  review_flags?: string[];
  parser_integrated?: boolean;
}

export interface LayoutDebugMetadata {
  parser_integrated?: boolean;
  bbox_table_candidates?: BBoxTableCandidate[];
  bbox_candidate_summary?: BBoxCandidateSummary;
  bbox_line_candidate_count?: number;
  grouped_row_count?: number;
  column_count?: number;
  candidate_count?: number;
  confirmed_line_item_count?: number;
  uncertain_count?: number;
  bbox_review_flags?: string[];
  source?: string | null;
}

export interface VLCandidate {
  source?: string | null;
  provider?: string | null;
  candidate_only?: boolean | null;
  parser_integrated?: boolean | null;
  recommended_handling?: string | null;
  provider_available_candidate?: boolean | null;
  validation_severity?: string | null;
  issue_codes?: string[];
  issue_details?: Array<{
    code?: string | null;
    severity?: string | null;
    field?: string | null;
    expected_value?: string | number | null;
    row_contains?: string | null;
    label?: string | null;
    message?: string | null;
    line?: string | null;
  }>;
  review_flags?: string[];
  text_preview?: string | null;
  matched_terms?: string[];
  inference_time_ms?: number | string | null;
}

export interface VLCandidateSummary {
  candidate_count?: number;
  warning_count?: number;
  failure_count?: number;
  issue_codes?: string[];
  parser_integrated?: boolean;
  provider?: string | null;
  provider_available_candidate?: boolean | null;
  recommended_handling?: string | null;
}

export interface VLCandidateMetadata {
  vl_candidates?: VLCandidate[];
  vl_candidate_summary?: VLCandidateSummary;
}

export interface AiParsedField {
  key?: string | null;
  value?: unknown;
  normalized_key?: string | null;
  confidence?: number | string | null;
  source?: string | null;
  evidence?: string | null;
  status?: "candidate" | "unmapped" | "blocked" | string | null;
  bbox?: unknown;
  reason?: string | null;
  risk?: string | null;
}

export interface AiParsedTableRow {
  row_index?: number | string | null;
  cells?: Record<string, unknown>;
  canonical_cells?: Record<string, unknown>;
  source?: string | null;
  status?: string | null;
  confidence?: number | string | null;
}

export interface AiParsedSection {
  title?: string | null;
  type?: "key_value" | "table" | "notes" | string | null;
  fields?: AiParsedField[];
  columns?: string[];
  rows?: AiParsedTableRow[];
  items?: string[];
  source?: string | null;
  status?: string | null;
  confidence?: number | string | null;
  table_type_guess?: string | null;
}

export interface AiParsedDocument {
  version?: number;
  source?: string | null;
  title?: string | null;
  document_type_hint?: string | null;
  document_type_confidence?: number | string | null;
  policy?: Record<string, unknown>;
  sections?: AiParsedSection[];
  unmapped_fields?: AiParsedField[];
  blocked_candidates?: AiParsedField[];
  warnings?: Array<Record<string, unknown> | string>;
  canonical_snapshot?: Record<string, unknown>;
}

export interface ReviewCandidate {
  field?: string | null;
  role?: string | null;
  key?: string | null;
  value?: unknown;
  normalized_value?: unknown;
  source?: string | null;
  source_label?: string | null;
  evidence?: string | null;
  confidence?: number | string | null;
  status?: string | null;
  reason?: string | null;
  blocked_reason?: string | null;
}

export interface PosSettlementSummary {
  schema?: string | null;
  mode?: string | null;
  fields?: Record<string, unknown>;
}

export interface FolderSummary {
  label: string;
  value: string;
  count: number;
  needs_review: number;
  confirmed: number;
  processing: number;
  parent: string | null;
  depth: number;
  category: string | null;
  custom: boolean;
}

export interface AppNotification {
  id: string;
  document_id: string;
  kind: "processing" | "processed" | "review" | "failed" | string;
  title: string;
  message: string;
  document_title: string | null;
  category: string | null;
  category_label: string | null;
  processing_status: ProcessingStatus;
  created_at: string;
  action_url: string;
  action_required: boolean;
}

export interface ProviderHealth {
  status?: string;
  providers?: {
    ocr_engine?: string | null;
    ocr_model?: string | null;
    primary_provider?: string | null;
    primary_provider_enabled?: boolean | null;
    primary_provider_available?: boolean | null;
    primary_provider_candidate_available?: boolean | null;
    primary_reader_available?: boolean | null;
    primary_reader_mode?: string | null;
    primary_provider_status?: string | null;
    fallback_provider?: string | null;
    fallback_provider_available?: boolean | null;
    fallback_reason?: string | null;
    runtime_strategy?: string | null;
    device?: string | null;
    ocr_worker_health?: {
      status?: string | null;
      ocr_engine?: string | null;
      model?: string | null;
      ocr_version?: string | null;
      device?: string | null;
      runtime_strategy?: string | null;
    } | null;
    paddleocr_vl_importable?: boolean | null;
    paddleocr_vl_usable?: boolean | null;
    paddleocr_vl_init_error?: string | null;
    paddleocr_vl_runtime_mode?: string | null;
    paddleocr_vl_model?: string | null;
    paddleocr_vl_gguf?: {
      provider?: string | null;
      status?: string | null;
      enabled?: boolean | null;
      available?: boolean | null;
      candidate_available?: boolean | null;
      primary_reader_enabled?: boolean | null;
      primary_reader_available?: boolean | null;
      in_process_enabled?: boolean | null;
      error?: string | null;
      server_url?: string | null;
      worker_url_host?: string | null;
      worker_location?: string | null;
      worker_provider?: string | null;
      worker_transport?: string | null;
      model_file?: string | null;
      mmproj_file?: string | null;
      smoke_passed?: boolean | null;
    } | null;
  };
}

export interface ExportTemplateColumn {
  header: string;
  source_field: string;
  column_type?: "field" | "static" | "blank" | string;
  static_value?: string | null;
}

export interface ExportTemplateRecord {
  id: string;
  name: string;
  description?: string | null;
  scope: string;
  is_default: boolean;
  columns: ExportTemplateColumn[];
  created_at: string;
  updated_at: string;
}

export interface ExportTemplatePayload {
  name: string;
  description?: string | null;
  scope?: string;
  is_default?: boolean;
  columns: ExportTemplateColumn[];
}

export interface ExportTemplateSourceField {
  value: string;
  label: string;
  group: string;
}

export interface DocumentRecord {
  id: string;
  original_filename: string;
  stored_file_path: string;
  mime_type: string;
  source_file_type: string | null;
  extraction_method: string | null;
  ingestion_metadata: Record<string, unknown> | null;
  document_type: DocumentType;
  title: string | null;
  raw_text: string | null;
  extracted_date: string | null;
  extracted_amount: string | null;
  subtotal: string | null;
  tax: string | null;
  currency: string | null;
  merchant_name: string | null;
  vendor_name: string | null;
  customer_name: string | null;
  document_number: string | null;
  issue_date: string | null;
  due_date: string | null;
  line_items: ManufacturingLineItem[];
  low_confidence_fields: string[];
  category: string | null;
  tags: string[];
  summary: string | null;
  confidence_score: string | null;
  ai_document_type: DocumentType | null;
  ai_confidence_score: string | null;
  ai_extraction_notes: string | null;
  review_required: boolean;
  extraction_provider: string | null;
  refinement_provider: string | null;
  provider_chain: string | null;
  merge_strategy: string | null;
  field_sources: Record<string, string> | null;
  workflow_summary: string | null;
  action_items: string[];
  warnings: string[];
  key_dates: string[];
  urgency_level: "low" | "medium" | "high" | null;
  follow_up_required: boolean;
  workflow_metadata: Record<string, unknown> | null;
  is_favorite: boolean;
  processing_status: ProcessingStatus;
  preview_image_path: string | null;
  processing_error: string | null;
  created_at: string;
  updated_at: string;
  file_url: string;
}

export interface DocumentBulkStatusResponse {
  items: DocumentRecord[];
  missing_ids: string[];
}

export interface ManufacturingLineItem {
  [key: string]: unknown;
  item_name?: string | null;
  item_code?: string | null;
  document_item_code?: string | null;
  source_item_name?: string | null;
  source_item_code?: string | null;
  internal_item_code?: string | null;
  specification?: string | null;
  quantity?: string | number | null;
  unit?: string | null;
  unit_price?: string | number | null;
  supply_amount?: string | number | null;
  tax_amount?: string | number | null;
  line_total?: string | number | null;
  item_master_match_status?: "auto_matched" | "direct_code_match" | "alias_matched" | "user_selected" | "manual_confirmed" | "ambiguous" | "needs_review" | "unmatched" | "skipped_no_item_master" | string | null;
  item_master_match_confidence?: string | number | null;
  item_master_candidates?: ItemMasterCandidate[];
  item_master_match_reason?: string | null;
}

export interface ItemMasterCandidate {
  item_master_id?: string | null;
  internal_item_code: string;
  item_name: string | null;
  spec: string | null;
  unit: string | null;
  standard_price: string | null;
  score: string;
  score_breakdown?: Record<string, string>;
}

export interface ItemMasterRecord {
  id: string;
  internal_item_code: string;
  item_name: string;
  normalized_item_name: string | null;
  spec: string | null;
  normalized_spec: string | null;
  unit: string | null;
  category: string | null;
  standard_price: string | null;
  active: boolean;
  aliases: string[];
  alias_records: ItemAliasRecord[];
  last_uploaded_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ItemAliasRecord {
  id: string;
  item_master_id: string;
  alias_name: string;
  normalized_alias_name: string | null;
  alias_spec: string | null;
  vendor_name: string | null;
  customer_name: string | null;
  source: string | null;
  confidence: string | null;
  memo: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ItemMasterListResponse {
  items: ItemMasterRecord[];
  total: number;
  page: number;
  page_size: number;
}

export interface ItemMasterStats {
  total_items: number;
  active_items: number;
  inactive_items: number;
  alias_count: number;
  last_uploaded_at: string | null;
  last_updated_at: string | null;
}

export interface ItemMasterUploadResult {
  inserted: number;
  updated: number;
  skipped: number;
  errors: string[];
}

export interface DomainDictionaryAliasRecord {
  id: string;
  entry_id: string;
  alias_value: string;
  normalized_alias_value: string | null;
  source: string;
  confidence: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface DomainDictionaryEntryRecord {
  id: string;
  dictionary_type: string;
  canonical_value: string;
  normalized_value: string | null;
  field: string | null;
  source: string;
  memo: string | null;
  active: boolean;
  aliases: DomainDictionaryAliasRecord[];
  created_at: string;
  updated_at: string;
}

export interface DomainDictionaryListResponse {
  items: DomainDictionaryEntryRecord[];
  total: number;
  page: number;
  page_size: number;
}

export interface DomainDictionaryStats {
  total_entries: number;
  active_entries: number;
  inactive_entries: number;
  alias_count: number;
  by_type: Record<string, number>;
  feedback_count: number;
  rejected_count: number;
}

export interface CreateDomainDictionaryEntryPayload {
  dictionary_type: string;
  canonical_value: string;
  field?: string | null;
  source?: string;
  memo?: string | null;
  active?: boolean;
  aliases?: string[];
}

export type UpdateDomainDictionaryEntryPayload = Partial<Omit<CreateDomainDictionaryEntryPayload, "dictionary_type" | "aliases">>;

export interface CreateDomainDictionaryAliasPayload {
  alias_value: string;
  source?: string;
  confidence?: string | number | null;
  active?: boolean;
}

export type UpdateDomainDictionaryAliasPayload = Partial<CreateDomainDictionaryAliasPayload>;

export interface CreateDomainDictionaryFeedbackPayload {
  document_id?: string | null;
  target: string;
  field?: string | null;
  original_value: string;
  suggested_value: string;
  action: "accepted" | "rejected" | "ignored";
  metadata?: Record<string, unknown> | null;
}

export interface CreateItemMasterPayload {
  internal_item_code: string;
  item_name: string;
  spec?: string | null;
  unit?: string | null;
  category?: string | null;
  standard_price?: string | number | null;
  active?: boolean;
  aliases?: string[];
}

export type UpdateItemMasterPayload = Omit<Partial<CreateItemMasterPayload>, "internal_item_code">;

export interface CreateItemAliasPayload {
  alias_name: string;
  alias_spec?: string | null;
  vendor_name?: string | null;
  customer_name?: string | null;
  source?: string;
  confidence?: string | number | null;
  memo?: string | null;
  active?: boolean;
}

export type UpdateItemAliasPayload = Partial<CreateItemAliasPayload>;

export interface DocumentListResponse {
  items: DocumentRecord[];
  total: number;
  page: number;
  page_size: number;
}

export interface DocumentBatchUploadError {
  index: number;
  filename: string;
  error: string;
}

export interface DocumentBatchUploadItem {
  index: number;
  document: DocumentRecord;
}

export interface DocumentBatchUploadResponse {
  items: DocumentBatchUploadItem[];
  errors: DocumentBatchUploadError[];
}

export interface DocumentCalendarItem {
  id: string;
  document_id: string;
  document_title: string | null;
  document_number: string | null;
  original_filename: string;
  document_type: DocumentType;
  vendor_name: string | null;
  customer_name: string | null;
  date: string;
  date_role: string;
  date_label: string;
  status: string;
  days_from_today: number;
  processing_status: ProcessingStatus;
  review_required: boolean;
  action_url: string;
}

export interface CalendarItemUpdate {
  date_role: string;
  date: string;
  original_date?: string | null;
  label?: string | null;
}

export interface MonthlyReportSummary {
  total_documents: number;
  verified_documents: number;
  pending_documents: number;
  total_amount: number;
  documents_with_errors: number;
  no_price_documents?: number;
  review_required_documents?: number;
}

export interface MonthlyReportPartyRow {
  name: string;
  document_count: number;
  total_amount: number;
}

export interface MonthlyReportItemRow {
  item_name: string;
  spec: string;
  quantity: number;
  total_amount: number;
}

export interface MonthlyReportDocumentTypeRow {
  document_type: string;
  document_count: number;
  verified_documents: number;
  pending_documents: number;
  total_amount: number;
  no_price_documents: number;
}

export interface MonthlyReportDateRow {
  date: string;
  document_count: number;
  verified_documents: number;
  pending_documents: number;
  total_amount: number;
  review_required_documents: number;
  no_price_documents: number;
}

export interface MonthlyReportIssueRow {
  document_id?: string | null;
  document_type?: string | null;
  document_number?: string | null;
  party_name?: string | null;
  date?: string | null;
  issue_type: string;
  description: string;
}

export interface MonthlyReport {
  year: number;
  month: number;
  period: "day" | "week" | "month" | "year" | "custom" | string;
  start_date: string;
  end_date: string;
  range_label: string;
  party_name?: string | null;
  summary: MonthlyReportSummary;
  by_party: MonthlyReportPartyRow[];
  by_item: MonthlyReportItemRow[];
  by_document_type?: MonthlyReportDocumentTypeRow[];
  by_date?: MonthlyReportDateRow[];
  top_parties: MonthlyReportPartyRow[];
  top_items: MonthlyReportItemRow[];
  issues: {
    missing_required_fields: MonthlyReportIssueRow[];
    calculation_mismatches: MonthlyReportIssueRow[];
    pending_documents: MonthlyReportIssueRow[];
  };
}

export interface DocumentStats {
  total: number;
  receipts: number;
  notices: number;
  completed: number;
  processing: number;
  failed: number;
  needs_review: number;
  queued: number;
  confirmed: number;
  recent: DocumentRecord[];
  recent_updated: DocumentRecord[];
  recent_review: DocumentRecord[];
  pinned: DocumentRecord[];
  category_overview: FolderSummary[];
  file_type_overview: FolderSummary[];
  ocr_metrics: Record<string, number>;
}

export type DocumentUpdate = Pick<
  DocumentRecord,
  | "title"
  | "raw_text"
  | "extracted_date"
  | "extracted_amount"
  | "subtotal"
  | "tax"
  | "currency"
  | "merchant_name"
  | "vendor_name"
  | "customer_name"
  | "document_number"
  | "issue_date"
  | "due_date"
  | "line_items"
  | "low_confidence_fields"
  | "category"
  | "tags"
  | "summary"
> & {
  confidence_score?: string | null;
  processing_status?: ProcessingStatus;
  is_favorite?: boolean;
  reviewed_key_values?: Array<Record<string, unknown>> | null;
};

export interface ActivitySummary {
  recent_uploads: DocumentRecord[];
  recent_edits: DocumentRecord[];
  recent_needs_review: DocumentRecord[];
  favorites: DocumentRecord[];
}
