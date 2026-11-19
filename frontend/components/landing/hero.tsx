"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { ArrowRight, ChevronDown } from "lucide-react";

// Full-screen intro: the name fades in once (2 s), then Enter appears. Pressing the Enter key anywhere on the
// page (outside a form control or link) goes straight in.
export function Hero() {
  const router = useRouter();

  useEffect(() => {
    router.prefetch("/app");
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Enter" || e.defaultPrevented || e.repeat) return;
      const t = e.target as HTMLElement | null;
      if (t && t.closest("a, button, input, textarea, select, [contenteditable]")) return; // let it do its own thing
      router.push("/app");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);

  return (
    <section className="gj-glow relative flex min-h-[100svh] flex-col items-center justify-center px-4 text-center text-cream">
      <h1 className="gj-fade-in text-6xl font-semibold tracking-tight text-cream sm:text-8xl">
        GET<span className="text-coral">a</span>JOB
      </h1>
      <p className="gj-fade-in mt-4 max-w-xl text-base text-cream/80 sm:text-lg">
        Real open internships and entry-level roles, ranked transparently, plus the skills that would move you up.
      </p>
      <div className="gj-fade-in-late mt-8 flex flex-col items-center gap-2">
        <Link
          href="/app"
          className="inline-flex items-center gap-2 rounded-xl bg-accent px-6 py-3 font-semibold text-navy shadow-[0_10px_30px_-8px_rgb(255_107_91/0.8)] transition-colors duration-150 hover:bg-accent-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cream focus-visible:ring-offset-2 focus-visible:ring-offset-navy"
        >
          Enter <ArrowRight size={18} aria-hidden />
        </Link>
        <p className="text-xs text-cream/70">
          or press <kbd className="glass-dark rounded px-1.5 py-0.5 font-mono">Enter</kbd>
        </p>
      </div>
      <a
        href="#showcase"
        aria-label="Scroll to see it in action"
        className="gj-fade-in-late absolute bottom-8 grid h-11 w-11 place-items-center glass-dark rounded-full text-cream/80 transition-colors duration-150 hover:border-coral hover:text-coral focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cream"
      >
        <ChevronDown size={20} aria-hidden />
      </a>
    </section>
  );
}
