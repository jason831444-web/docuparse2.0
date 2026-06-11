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
  Files,
  FolderKanban,
  LayoutDashboard,
  Search,
  Settings,
  Upload,
} from "lucide-react";

import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const navItems = [
  { href: "/", label: "대시보드", icon: LayoutDashboard },
  { href: "/upload", label: "문서 업로드", icon: Upload },
  { href: "/documents", label: "문서 목록", icon: Files },
  { href: "/calendar", label: "문서 일정", icon: CalendarDays },
  { href: "/categories", label: "문서 유형", icon: FolderKanban },
  { href: "/review", label: "검토 필요", icon: BellRing },
  { href: "/favorites", label: "즐겨찾기", icon: FileHeart },
  { href: "/masters/items", label: "내부 장부", icon: Database },
  { href: "/settings", label: "설정", icon: Settings }
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [query, setQuery] = useState("");
  const isAuthPage = pathname.startsWith("/login") || pathname.startsWith("/signup");
  const [notificationCount, setNotificationCount] = useState(0);

  useEffect(() => {
    if (isAuthPage) return;
    api
      .notifications()
      .then((items) => setNotificationCount(items.filter((item) => item.action_required).length || items.length))
      .catch(() => setNotificationCount(0));
  }, [isAuthPage, pathname]);

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = query.trim();
    router.push(trimmed ? `/documents?search=${encodeURIComponent(trimmed)}` : "/documents");
  }

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
      <div className="grid min-h-screen lg:grid-cols-[260px_minmax(0,1fr)]">
        <aside className="border-r bg-white/80 px-5 py-6 backdrop-blur">
          <Link href="/" className="mb-8 flex items-center gap-3 font-semibold tracking-normal">
            <span className="grid size-10 place-items-center rounded-md bg-primary text-primary-foreground">
              <FileSearch className="size-5" />
            </span>
            <div>
              <p>DocuParse</p>
              <p className="text-xs font-normal text-muted-foreground">제조업 문서 자동화</p>
            </div>
          </Link>
          <nav className="space-y-1">
            {navItems.map((item) => {
              const active = pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href));
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn(
                    "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition",
                    active ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground"
                  )}
                >
                  <item.icon className="size-4" />
                  {item.label}
                </Link>
              );
            })}
          </nav>
          <div className="mt-8 rounded-lg border bg-muted/40 p-4 text-sm text-muted-foreground">
            발주서, 견적서, 거래명세서, 납품서를 ERP/엑셀 입력용 데이터로 변환합니다.
          </div>
        </aside>
        <div className="min-w-0">
          <header className="sticky top-0 z-20 border-b bg-[hsl(var(--background))/0.86] backdrop-blur">
            <div className="shell flex h-16 items-center gap-4">
              <form onSubmit={submitSearch} className="relative max-w-xl flex-1">
                <Search className="pointer-events-none absolute left-3 top-3.5 size-4 text-muted-foreground" />
                <Input className="pl-9" placeholder="파일명, 거래처명, 품목명, 문서번호로 검색" value={query} onChange={(event) => setQuery(event.target.value)} />
              </form>
              <Link
                href="/notifications"
                aria-label="알림"
                className={cn(
                  "relative grid size-10 place-items-center rounded-md border bg-white text-muted-foreground transition hover:border-primary/40 hover:text-foreground",
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
          </header>
          {children}
        </div>
      </div>
    </div>
  );
}
