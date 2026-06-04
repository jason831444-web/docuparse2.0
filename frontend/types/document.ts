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

export interface ManufacturingLineItem {
  item_name?: string | null;
  item_code?: string | null;
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
  item_master_match_status?: "auto_matched" | "needs_review" | "unmatched" | "skipped_no_item_master" | string | null;
  item_master_match_confidence?: string | number | null;
  item_master_candidates?: ItemMasterCandidate[];
  item_master_match_reason?: string | null;
}

export interface ItemMasterCandidate {
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
  last_uploaded_at: string | null;
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
  last_uploaded_at: string | null;
}

export interface ItemMasterUploadResult {
  inserted: number;
  updated: number;
  skipped: number;
  errors: string[];
}

export interface DocumentListResponse {
  items: DocumentRecord[];
  total: number;
  page: number;
  page_size: number;
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
};

export interface ActivitySummary {
  recent_uploads: DocumentRecord[];
  recent_edits: DocumentRecord[];
  recent_needs_review: DocumentRecord[];
  favorites: DocumentRecord[];
}
