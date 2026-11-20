"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Briefcase, FlaskConical, Search, Sparkles } from "lucide-react";
import { ThemeToggle } from "./theme";
import { cx } from "./ui";

const NAV = [
  { href: "/search", label: "Search", icon: Search },
  { href: "/app", label: "Match my profile", icon: Sparkles },
  { href: "/eval", label: "Evals", icon: FlaskConical },
];

export function SiteHeader() {
  const path = usePathname();
  return (
    <header className="sticky top-0 z-30 glass border-x-0 border-t-0">
      <div className="mx-auto flex h-14 w-full max-w-6xl items-center justify-between gap-4 px-4">
        <Link
          href="/"
          className="flex items-center gap-2 rounded-lg font-semibold tracking-tight focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink"
        >
          <span className="grid h-7 w-7 place-items-center rounded-lg bg-navy text-coral dark:bg-coral dark:text-navy">
            <Briefcase size={15} aria-hidden />
          </span>
          <span>
            GET<span className="text-accent-ink">a</span>JOB
          </span>
        </Link>
        <nav className="flex items-center gap-1" aria-label="Main">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = path === href;
            return (
              <Link
                key={href}
                href={href}
                aria-label={label}
                aria-current={active ? "page" : undefined}
                className={cx(
                  "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink",
                  active ? "bg-accent-soft font-medium text-accent-ink" : "text-muted hover:text-ink",
                )}
              >
                <Icon size={15} aria-hidden />
                <span className="hidden sm:inline">{label}</span>
              </Link>
            );
          })}
          <ThemeToggle className="ml-1" />
        </nav>
      </div>
    </header>
  );
}
