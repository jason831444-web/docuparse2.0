"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  BellRing,
  CalendarDays,
  Database,
  FileHeart,
  FileSearch,
  FileSpreadsheet,
  Files,
  FolderKanban,
  LayoutDashboard,
  Search,
  Settings,
  Upload,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { providerHealthLabel } from "@/lib/provider-health";
import { cn } from "@/lib/utils";
import type { DocumentStats, ProviderHealth } from "@/types/document";

const navGroups = [
  {
    label: "운영",
    items: [
      { href: "/", label: "대시보드", icon: LayoutDashboard },
      { href: "/upload", label: "문서 업로드", icon: Upload },
      { href: "/documents", label: "문서 목록", icon: Files },
      { href: "/calendar", label: "문서 일정", icon: CalendarDays },
      { href: "/reports/monthly", label: "월말 보고서", icon: FileSpreadsheet },
      { href: "/categories", label: "문서 유형", icon: FolderKanban },
    ],
  },
  {
    label: "작업",
    items: [
      { href: "/review", label: "검토 필요", icon: BellRing },
      { href: "/favorites", label: "즐겨찾기", icon: FileHeart },
    ],
  },
  {
    label: "시스템",
    items: [
      { href: "/masters/items", label: "내부 장부", icon: Database },
      { href: "/settings", label: "설정", icon: Settings },
    ],
  },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [query, setQuery] = useState("");
  const isAuthPage = pathname.startsWith("/login") || pathname.startsWith("/signup");
  const [notificationCount, setNotificationCount] = useState(0);
  const [stats, setStats] = useState<DocumentStats | null>(null);
  const [providerHealth, setProviderHealth] = useState<ProviderHealth | null>(null);

  useEffect(() => {
    if (isAuthPage) return;
    api
      .notifications()
      .then((items) => setNotificationCount(items.filter((item) => item.action_required).length || items.length))
      .catch(() => setNotificationCount(0));
    api.stats().then(setStats).catch(() => setStats(null));
    api.health().then(setProviderHealth).catch(() => setProviderHealth(null));
  }, [isAuthPage, pathname]);

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = query.trim();
    router.push(trimmed ? `/documents?search=${encodeURIComponent(trimmed)}` : "/documents");
  }

  const providerStatus = providerHealthLabel(providerHealth);

  if (isAuthPage) {
    return (
      <>
        <div className="border-b bg-white/80 backdrop-blur">
          <div className="shell flex h-16 items-center justify-between">
            <Link href="/" className="flex items-center gap-3 font-semibold tracking-normal">
              <span className="grid size-9 place-items-center rounded-md bg-primary text-primary-foreground">
                <FileSearch className="size-5" />
              </span>
              DocuParse
            </Link>
          </div>
        </div>
        {children}
      </>
    );
  }

  return (
    <div className="min-h-screen bg-[hsl(var(--background))]">
      <div className="grid min-h-screen lg:grid-cols-[250px_minmax(0,1fr)]">
        <aside className="sticky top-0 hidden h-screen flex-col border-r bg-white px-5 py-5 lg:flex">
          <Link href="/" className="mb-8 flex items-center gap-3 font-semibold tracking-normal">
            <span className="grid size-9 place-items-center rounded-lg bg-primary text-primary-foreground shadow-sm">
              <FileSearch className="size-5" />
            </span>
            <div>
              <p className="leading-tight">DocuParse</p>
              <p className="text-xs font-normal text-muted-foreground">제조업 문서 자동화</p>
            </div>
          </Link>
          <nav className="space-y-5">
            {navGroups.map((group) => (
              <div key={group.label}>
                <p className="mb-2 px-3 text-[11px] font-semibold text-muted-foreground">{group.label}</p>
                <div className="space-y-1">
                  {group.items.map((item) => {
                    const active = pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href));
                    return (
                      <Link
                        key={item.href}
                        href={item.href}
                        className={cn(
                          "flex items-center justify-between gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition",
                          active ? "bg-blue-50 text-primary" : "text-slate-600 hover:bg-slate-100 hover:text-slate-950"
                        )}
                      >
                        <span className="flex items-center gap-3">
                          <item.icon className="size-4" />
                          {item.label}
                        </span>
                        {item.href === "/review" && notificationCount ? (
                          <span className="grid min-w-5 place-items-center rounded-full bg-amber-600 px-1.5 text-[11px] leading-5 text-white">
                            {notificationCount > 9 ? "9+" : notificationCount}
                          </span>
                        ) : null}
                      </Link>
                    );
                  })}
                </div>
              </div>
            ))}
          </nav>
          <div className="mt-auto rounded-lg border bg-slate-50 p-3 text-xs text-muted-foreground">
            <div className="flex items-center justify-between">
              <span>입력 준비 완료</span>
              <span className="font-semibold text-emerald-700">{stats?.completed ?? 0}건</span>
            </div>
            <div className="mt-1 flex items-center justify-between">
              <span>검토 필요</span>
              <span className="font-semibold text-amber-700">{stats?.needs_review ?? 0}건</span>
            </div>
          </div>
        </aside>
        <div className="min-w-0">
          <header className="sticky top-0 z-20 border-b bg-white/95 backdrop-blur">
            <div className="shell flex h-14 items-center gap-4">
              <div className="flex min-w-0 flex-1 items-center gap-3">
                <form onSubmit={submitSearch} className="relative w-full max-w-xl">
                  <Search className="pointer-events-none absolute left-3 top-2.5 size-4 text-muted-foreground" />
                  <Input className="h-9 border-slate-200 bg-slate-50 pl-9" placeholder="파일명, 거래처명, 품목명, 문서번호로 검색" value={query} onChange={(event) => setQuery(event.target.value)} />
                </form>
                <Link
                  href="/notifications"
                  aria-label="알림"
                  className={cn(
                    "relative grid size-9 shrink-0 place-items-center rounded-lg border bg-white text-muted-foreground transition hover:border-primary/40 hover:text-foreground",
                    pathname.startsWith("/notifications") && "border-primary/50 text-primary"
                  )}
                >
                  <BellRing className="size-4" />
                  {notificationCount ? (
                    <span className="absolute -right-1 -top-1 grid min-w-5 place-items-center rounded-full bg-primary px-1 text-[11px] font-semibold leading-5 text-primary-foreground">
                      {notificationCount > 9 ? "9+" : notificationCount}
                    </span>
                  ) : null}
                </Link>
              </div>
              <div className="ml-auto flex shrink-0 items-center gap-4">
                <Badge
                  title={providerStatus.detail}
                  className={cn(
                    "hidden shadow-none md:inline-flex",
                    providerStatus.tone === "primary"
                      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                      : "border-amber-200 bg-amber-50 text-amber-700"
                  )}
                >
                  {providerStatus.label}
                </Badge>
                <div className="hidden items-center gap-2 md:flex">
                  <span className="grid size-9 place-items-center rounded-full bg-blue-100 text-sm font-semibold text-primary">대성</span>
                  <div className="text-right text-xs">
                    <p className="font-semibold text-slate-900">(주)대성정공</p>
                    <p className="text-muted-foreground">회계팀 · 김선영</p>
                  </div>
                </div>
              </div>
            </div>
          </header>
          {children}
        </div>
      </div>
    </div>
  );
}
