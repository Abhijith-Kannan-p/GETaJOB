import type { Metadata } from "next";
import { SiteHeader } from "@/components/site-header";
import ClientOnly from "./client-only";

export const metadata: Metadata = { title: "Match · GETaJOB" };

export default function AppPage() {
  return (
    <>
      <SiteHeader />
      <main className="flex-1">
        <ClientOnly />
      </main>
    </>
  );
}
