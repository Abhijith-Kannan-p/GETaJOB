import type { Metadata } from "next";
import { SiteHeader } from "@/components/site-header";
import { Gallery } from "./gallery";

export const metadata: Metadata = { title: "Components · GETaJOB", robots: { index: false, follow: false } };

// Living style guide: every shared primitive and token in one place, in both themes (toggle in the header).
export default function ComponentsPage() {
  return (
    <>
      <SiteHeader />
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-10">
        <h1 className="text-2xl font-semibold tracking-tight">Components</h1>
        <p className="mt-1 text-sm text-muted">
          Design tokens and shared primitives (components/ui.tsx). Sample values below are illustrative, not data.
        </p>
        <Gallery />
      </main>
    </>
  );
}
