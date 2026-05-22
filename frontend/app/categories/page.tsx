"use client";

import { useEffect, useState } from "react";
import { BellRing, CheckCircle2, FolderKanban, LoaderCircle, Plus } from "lucide-react";
import { toast } from "sonner";

import { FolderCard } from "@/components/folder-card";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { FolderSummary } from "@/types/document";

export default function CategoriesPage() {
  const [folders, setFolders] = useState<FolderSummary[]>([]);
  const [label, setLabel] = useState("");
  const [parent, setParent] = useState("");

  useEffect(() => {
    load();
  }, []);

  function load() {
    api.categories().then(setFolders).catch(() => setFolders([]));
  }

  async function createFolder() {
    if (!label.trim()) return;
    try {
      await api.createCategory({ label, parent: parent || null });
      setLabel("");
      setParent("");
      toast.success("Category folder added");
      load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not add category");
    }
  }

  async function deleteFolder(folder: FolderSummary) {
    if (folder.count > 0) {
      toast.error("Only empty category folders can be deleted");
      return;
    }
    if (!window.confirm(`Delete empty category folder "${folder.label}"?`)) return;
    try {
      await api.deleteCategory(folder.value);
      toast.success("Category folder deleted");
      load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not delete category");
    }
  }

  const totalDocuments = folders.reduce((sum, folder) => sum + folder.count, 0);
  const reviewCount = folders.reduce((sum, folder) => sum + folder.needs_review, 0);
  const confirmedCount = folders.reduce((sum, folder) => sum + folder.confirmed, 0);
  const processingCount = folders.reduce((sum, folder) => sum + folder.processing, 0);
  const activeFolders = folders
    .filter((folder) => folder.count > 0)
    .sort((a, b) => b.count - a.count || b.needs_review - a.needs_review || a.label.localeCompare(b.label));
  const emptyCustomFolders = folders.filter((folder) => folder.custom && folder.count === 0);

  return (
    <main className="shell py-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div className="max-w-3xl">
          <p className="text-sm font-medium uppercase tracking-normal text-muted-foreground">Category intelligence</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-normal">AI-organized document folders</h1>
          <p className="mt-2 text-muted-foreground">
            Browse documents by interpreted purpose, review status, and workflow readiness instead of raw filename or file type.
          </p>
        </div>
        <div className="rounded-lg border bg-white px-4 py-3 text-sm text-muted-foreground">
          <span className="font-semibold text-foreground">{activeFolders.length}</span> active folders
          <span className="mx-2 text-slate-300">/</span>
          <span className="font-semibold text-foreground">{totalDocuments}</span> documents
        </div>
      </div>

      <section className="mb-6 grid gap-3 md:grid-cols-4">
        <Card>
          <CardContent className="flex items-center justify-between p-4">
            <div>
              <p className="text-sm text-muted-foreground">Documents</p>
              <p className="mt-1 text-2xl font-semibold">{totalDocuments}</p>
            </div>
            <FolderKanban className="size-6 text-primary" />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center justify-between p-4">
            <div>
              <p className="text-sm text-muted-foreground">Needs review</p>
              <p className="mt-1 text-2xl font-semibold">{reviewCount}</p>
            </div>
            <BellRing className="size-6 text-amber-600" />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center justify-between p-4">
            <div>
              <p className="text-sm text-muted-foreground">Confirmed</p>
              <p className="mt-1 text-2xl font-semibold">{confirmedCount}</p>
            </div>
            <CheckCircle2 className="size-6 text-emerald-600" />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center justify-between p-4">
            <div>
              <p className="text-sm text-muted-foreground">Processing</p>
              <p className="mt-1 text-2xl font-semibold">{processingCount}</p>
            </div>
            <LoaderCircle className="size-6 text-primary" />
          </CardContent>
        </Card>
      </section>

      <Card className="mb-6 border-dashed">
        <CardContent className="grid gap-4 p-5 lg:grid-cols-[1.2fr_1fr_auto]">
          <div>
            <p className="text-sm font-medium">Create a folder</p>
            <p className="mt-1 text-sm text-muted-foreground">Add an empty category folder for planned organization, then delete it later if it stays unused.</p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:col-span-1">
            <Input placeholder="New category folder" value={label} onChange={(event) => setLabel(event.target.value)} />
            <select className="h-10 rounded-md border bg-white px-3 text-sm" value={parent} onChange={(event) => setParent(event.target.value)}>
              <option value="">Top level</option>
              {folders.filter((folder) => folder.depth === 0).map((folder) => <option key={folder.value} value={folder.value}>{folder.label}</option>)}
            </select>
          </div>
          <Button type="button" onClick={createFolder} className="lg:self-end">
            <Plus className="size-4" />
            Add folder
          </Button>
        </CardContent>
      </Card>

      {activeFolders.length ? (
        <section>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-lg font-semibold">Active folders</h2>
            <p className="text-sm text-muted-foreground">Sorted by document count and review activity</p>
          </div>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {activeFolders.map((folder) => (
              <FolderCard
                key={folder.value}
                folder={folder}
                href={`/categories/${encodeURIComponent(folder.value)}`}
                onDelete={folder.custom && folder.count === 0 ? () => deleteFolder(folder) : undefined}
              />
            ))}
          </div>
        </section>
      ) : null}

      {emptyCustomFolders.length ? (
        <section className="mt-8">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-lg font-semibold">Empty custom folders</h2>
            <p className="text-sm text-muted-foreground">Safe to delete while unused</p>
          </div>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {emptyCustomFolders.map((folder) => (
            <FolderCard
              key={folder.value}
              folder={folder}
              href={`/categories/${encodeURIComponent(folder.value)}`}
              onDelete={folder.custom && folder.count === 0 ? () => deleteFolder(folder) : undefined}
            />
            ))}
          </div>
        </section>
      ) : null}

      {!folders.length ? (
        <Card><CardContent className="p-10 text-center text-muted-foreground">Categories will appear automatically as documents are analyzed.</CardContent></Card>
      ) : null}
    </main>
  );
}
