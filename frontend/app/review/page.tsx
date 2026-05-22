"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, ShieldCheck, TriangleAlert } from "lucide-react";

import { DocumentList } from "@/components/document-list";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { DocumentListResponse } from "@/types/document";

export default function ReviewPage() {
  const [data, setData] = useState<DocumentListResponse | null>(null);

  const load = useCallback(() => {
    api.review().then(setData).catch(() => setData(null));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <main className="shell py-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div className="max-w-3xl">
          <p className="text-sm font-medium uppercase tracking-normal text-muted-foreground">Human-in-the-loop review</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-normal">Needs Review</h1>
          <p className="mt-2 text-muted-foreground">The priority queue for documents that need correction, confirmation, or a second look before the extraction is trusted.</p>
        </div>
        <div className="rounded-lg border bg-white px-4 py-3 text-sm text-muted-foreground">
          <span className="font-semibold text-foreground">{data?.total ?? 0}</span> documents waiting
        </div>
      </div>
      {data?.items.length ? (
        <div className="space-y-4">
          <div className="grid gap-3 md:grid-cols-3">
            <Card>
              <CardContent className="flex items-center justify-between p-4">
                <div>
                  <p className="text-sm text-muted-foreground">Review queue</p>
                  <p className="mt-1 text-2xl font-semibold">{data.total}</p>
                </div>
                <TriangleAlert className="size-6 text-amber-600" />
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex items-center justify-between p-4">
                <div>
                  <p className="text-sm text-muted-foreground">Action</p>
                  <p className="mt-1 text-lg font-semibold">Correct or confirm</p>
                </div>
                <ShieldCheck className="size-6 text-primary" />
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex items-center justify-between p-4">
                <div>
                  <p className="text-sm text-muted-foreground">Outcome</p>
                  <p className="mt-1 text-lg font-semibold">Ready to trust</p>
                </div>
                <CheckCircle2 className="size-6 text-emerald-600" />
              </CardContent>
            </Card>
          </div>
          <DocumentList documents={data.items} onChanged={load} returnTo="/review" />
        </div>
      ) : (
        <Card>
          <CardContent className="grid gap-4 p-10 text-center text-muted-foreground">
            <TriangleAlert className="mx-auto size-10 text-amber-600" />
            <div>
              <p className="font-medium text-foreground">No documents currently need review.</p>
              <p className="mt-1">Upload a more complex PDF, spreadsheet, or scanned document to demonstrate the review-before-trust workflow.</p>
            </div>
          </CardContent>
        </Card>
      )}
    </main>
  );
}
