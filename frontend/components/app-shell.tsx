"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  BellRing,
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
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/upload", label: "Upload", icon: Upload },
  { href: "/documents", label: "All Documents", icon: Files },
  { href: "/categories", label: "Categories", icon: FolderKanban },
  { href: "/review", label: "Needs Review", icon: BellRing },
  { href: "/favorites", label: "Favorites", icon: FileHeart },
  { href: "/settings", label: "Settings", icon: Settings }
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
              <p className="text-xs font-normal text-muted-foreground">AI document workflows</p>
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
            Local-first document understanding with visible provider chains and review-before-trust workflows.
          </div>
        </aside>
        <div className="min-w-0">
          <header className="sticky top-0 z-20 border-b bg-[hsl(var(--background))/0.86] backdrop-blur">
            <div className="shell flex h-16 items-center gap-4">
              <form onSubmit={submitSearch} className="relative max-w-xl flex-1">
                <Search className="pointer-events-none absolute left-3 top-3.5 size-4 text-muted-foreground" />
                <Input className="pl-9" placeholder="Search title, summary, merchant, OCR text" value={query} onChange={(event) => setQuery(event.target.value)} />
              </form>
              <Link
                href="/notifications"
                aria-label="Notifications"
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
