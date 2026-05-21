"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { CheckCircle2, Clock3, FileText, RefreshCcw, ShieldCheck, TriangleAlert } from "lucide-react";

import { DocumentCard } from "@/components/document-card";
import { FolderCard } from "@/components/folder-card";
import { UploadDropzone } from "@/components/upload-dropzone";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { documentSummaryShort } from "@/lib/utils";
import type { ActivitySummary, DocumentStats } from "@/types/document";

export default function DashboardPage() {
  const [stats, setStats] = useState<DocumentStats | null>(null);
  const [activity, setActivity] = useState<ActivitySummary | null>(null);

  useEffect(() => {
    api.stats().then(setStats).catch(() => setStats(null));
    api.activity().then(setActivity).catch(() => setActivity(null));
  }, []);

  const metrics = [
    { label: "Total documents", value: stats?.total ?? 0, icon: FileText },
    { label: "Processing", value: stats?.processing ?? 0, icon: Clock3 },
    { label: "Needs review", value: stats?.needs_review ?? 0, icon: TriangleAlert },
    { label: "Confirmed", value: stats?.confirmed ?? 0, icon: ShieldCheck },
    { label: "Failed", value: stats?.failed ?? 0, icon: RefreshCcw }
  ];

  return (
    <main className="shell py-8">
      <section className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
        <div className="space-y-5">
          <div className="rounded-2xl border bg-white/95 p-8 shadow-sm shadow-slate-200/70">
            <p className="text-sm font-medium uppercase tracking-normal text-muted-foreground">Local AI document workspace</p>
            <h1 className="mt-3 max-w-3xl text-4xl font-semibold tracking-normal">
              Upload documents, inspect the extraction path, then review, organize, and find them later.
            </h1>
            <p className="mt-4 max-w-2xl text-muted-foreground">
              DocuParse classifies documents into meaningful folders, preserves extracted text and interpretation details separately,
              and keeps review-required work easy to spot before you rely on the result.
            </p>
            <div className="mt-6 flex flex-wrap gap-3">
              <Button asChild><Link href="/upload">Upload document</Link></Button>
              <Button asChild variant="outline"><Link href="/documents">Open library</Link></Button>
              <Button asChild variant="outline"><Link href="/review">Needs review</Link></Button>
            </div>
          </div>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
            {metrics.map((metric) => (
              <Card key={metric.label}>
                <CardContent className="flex items-center justify-between p-5">
                  <div>
                    <p className="text-sm text-muted-foreground">{metric.label}</p>
                    <p className="mt-2 text-3xl font-semibold">{metric.value}</p>
                  </div>
                  <metric.icon className="size-7 text-primary" />
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
        <UploadDropzone />
      </section>

      <section className="mt-8 grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle>Recent uploads</CardTitle>
            <Button asChild variant="ghost" size="sm"><Link href="/documents">View all</Link></Button>
          </CardHeader>
          <CardContent className="grid min-w-0 gap-4 lg:grid-cols-2">
            {(stats?.recent ?? []).slice(0, 4).map((document) => <DocumentCard key={document.id} document={document} />)}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle>Needs review</CardTitle>
            <Button asChild variant="ghost" size="sm"><Link href="/review">Open queue</Link></Button>
          </CardHeader>
          <CardContent className="space-y-3">
            {(stats?.recent_review ?? []).slice(0, 4).map((document) => (
              <Link key={document.id} href={`/documents/${document.id}`} className="block min-w-0 overflow-hidden rounded-lg border bg-white p-4 shadow-sm shadow-slate-200/50 transition hover:border-primary/30 hover:shadow-md">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="line-clamp-2 break-words font-medium leading-snug">{document.title || document.original_filename}</p>
                    <p className="mt-1 line-clamp-2 break-words text-sm leading-5 text-muted-foreground">{documentSummaryShort(document, 140)}</p>
                  </div>
                  <TriangleAlert className="size-5 shrink-0 text-amber-600" />
                </div>
              </Link>
            ))}
          </CardContent>
        </Card>
      </section>

      <section className="mt-8">
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle>Category folders</CardTitle>
            <Button asChild variant="ghost" size="sm"><Link href="/categories">Browse folders</Link></Button>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2">
            {(stats?.category_overview ?? []).slice(0, 6).map((folder) => (
              <FolderCard key={folder.value} folder={folder} href={`/categories/${encodeURIComponent(folder.value)}`} />
            ))}
          </CardContent>
        </Card>
      </section>

      <section className="mt-8 grid gap-6 xl:grid-cols-[1fr_1fr]">
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle>Recent edits</CardTitle>
            <Button asChild variant="ghost" size="sm"><Link href="/documents">Open library</Link></Button>
          </CardHeader>
          <CardContent className="space-y-3">
            {(activity?.recent_edits ?? []).slice(0, 5).map((document) => (
              <Link key={document.id} href={`/documents/${document.id}`} className="flex min-w-0 items-center justify-between gap-3 overflow-hidden rounded-lg border bg-white p-4 shadow-sm shadow-slate-200/50 transition hover:border-primary/30 hover:shadow-md">
                <div className="min-w-0">
                  <p className="line-clamp-2 break-words font-medium leading-snug">{document.title || document.original_filename}</p>
                  <p className="mt-1 line-clamp-2 break-words text-sm leading-5 text-muted-foreground">{documentSummaryShort(document, 140)}</p>
                </div>
                <CheckCircle2 className="size-5 shrink-0 text-primary" />
              </Link>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle>Favorites</CardTitle>
            <Button asChild variant="ghost" size="sm"><Link href="/favorites">Pinned documents</Link></Button>
          </CardHeader>
          <CardContent className="space-y-3">
            {(activity?.favorites ?? []).slice(0, 5).map((document) => (
              <Link key={document.id} href={`/documents/${document.id}`} className="block min-w-0 overflow-hidden rounded-lg border bg-white p-4 shadow-sm shadow-slate-200/50 transition hover:border-primary/30 hover:shadow-md">
                <p className="line-clamp-2 break-words font-medium leading-snug">{document.title || document.original_filename}</p>
                <p className="mt-1 line-clamp-2 break-words text-sm leading-5 text-muted-foreground">{documentSummaryShort(document, 140)}</p>
              </Link>
            ))}
          </CardContent>
        </Card>
      </section>
    </main>
  );
}
