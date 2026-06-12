import { Badge } from "@/components/ui/badge";
import { taxonomyBadgeLabels } from "@/lib/utils";
import type { DocumentRecord } from "@/types/document";

const toneClass = {
  subtype: "border-sky-200 bg-sky-50 text-sky-800",
  profile: "border-slate-200 bg-slate-50 text-slate-700",
  layout: "border-violet-200 bg-violet-50 text-violet-800",
};

export function TaxonomyBadges({
  document,
  maxProfiles = 2,
}: {
  document: Pick<DocumentRecord, "document_type" | "workflow_metadata" | "ingestion_metadata">;
  maxProfiles?: number;
}) {
  const labels = taxonomyBadgeLabels(document, maxProfiles);
  if (!labels.length) return null;
  return (
    <>
      {labels.map((item) => (
        <Badge key={item.key} variant="outline" className={toneClass[item.tone]}>
          {item.label}
        </Badge>
      ))}
    </>
  );
}
