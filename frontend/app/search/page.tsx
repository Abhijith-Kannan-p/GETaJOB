import type { Metadata } from "next";
import { Suspense } from "react";
import { SiteHeader } from "@/components/site-header";
import SearchView from "./search-view";

export const metadata: Metadata = { title: "Search · GETaJOB" };

export default function SearchPage() {
  return (
    <>
      <SiteHeader />
      <main className="flex-1">
        {/* useSearchParams needs a Suspense boundary so the rest of the page can prerender */}
        <Suspense fallback={<div className="mx-auto max-w-3xl px-4 py-10 text-sm text-muted">Loading…</div>}>
          <SearchView />
        </Suspense>
      </main>
    </>
  );
}
