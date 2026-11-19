import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Hero } from "@/components/landing/hero";
import { Showcase } from "@/components/landing/showcase";

// Landing: intro (static shell, prerendered) -> live showcase tiles -> enter.
export default function Landing() {
  return (
    <main className="flex-1">
      <Hero />
      <Showcase />
      <section className="flex flex-col items-center px-4 pb-24 pt-4 text-center">
        <p className="text-muted">Ready to see where you stand?</p>
        <Link
          href="/app"
          className="mt-4 inline-flex items-center gap-2 rounded-xl bg-accent px-5 py-2.5 text-sm font-semibold text-navy shadow-[0_8px_20px_-8px_rgb(255_107_91/0.7)] transition-colors duration-150 hover:bg-accent-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
        >
          Enter <ArrowRight size={16} aria-hidden />
        </Link>
      </section>
    </main>
  );
}
