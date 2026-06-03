"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { FileUp, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const acceptedTypes = [
  "image/jpeg",
  "image/png",
  "image/webp",
  "image/bmp",
  "image/tiff",
  "application/pdf",
  "text/plain",
  "text/markdown",
  "text/csv",
  "application/json",
  "application/xml",
  "text/xml",
  "text/html",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
].join(",");

const acceptedExtensions = [
  ".jpg",
  ".jpeg",
  ".png",
  ".webp",
  ".bmp",
  ".tif",
  ".tiff",
  ".pdf",
  ".txt",
  ".md",
  ".csv",
  ".json",
  ".xml",
  ".html",
  ".htm",
  ".docx",
  ".xlsx",
  ".pptx",
  ".doc",
  ".xls",
  ".ppt",
  ".rtf",
  ".odt",
  ".ods",
  ".odp",
  ".epub",
  ".eml",
  ".msg",
].join(",");

export function UploadDropzone() {
  const inputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);

  async function handleFiles(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const document = await api.upload(file);
      toast.success("문서가 업로드되었습니다", {
        description: "AI 추출 결과를 확인하고 업무 데이터를 검토하세요.",
        action: {
          label: "열기",
          onClick: () => router.push(`/documents/${document.id}`),
        },
      });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "업로드에 실패했습니다");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div
      className={cn(
        "flex min-h-72 flex-col items-center justify-center rounded-lg border border-dashed bg-white p-8 text-center transition",
        dragging && "border-primary bg-emerald-50"
      )}
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        void handleFiles(event.dataTransfer.files);
      }}
    >
      <div className="mb-4 grid size-14 place-items-center rounded-md bg-secondary">
        {uploading ? <Loader2 className="size-7 animate-spin" /> : <FileUp className="size-7 text-primary" />}
      </div>
      <h2 className="text-xl font-semibold">제조업 문서 업로드</h2>
      <p className="mt-2 max-w-lg text-sm text-muted-foreground">
        발주서, 견적서, 거래명세서, 납품서를 업로드하세요. 업로드 후 AI가 문서 유형과 핵심 업무 데이터를 자동으로 추출합니다.
      </p>
      <input
        ref={inputRef}
        type="file"
        accept={`${acceptedTypes},${acceptedExtensions}`}
        className="hidden"
        onChange={(event) => void handleFiles(event.target.files)}
      />
      <Button className="mt-5" onClick={() => inputRef.current?.click()} disabled={uploading}>
        {uploading ? <Loader2 className="size-4 animate-spin" /> : <FileUp className="size-4" />}
        파일을 끌어다 놓거나 클릭해서 업로드하세요
      </Button>
      <p className="mt-3 text-xs text-muted-foreground">
        PDF, 이미지, 엑셀, 워드 문서를 지원합니다.
      </p>
    </div>
  );
}
