import type { Metadata } from "next";
import ClientOnly from "./client-only";

export const metadata: Metadata = { title: "Match · GETaJOB" };

export default function AppPage() {
  return (
    <main className="flex-1">
      <ClientOnly />
    </main>
  );
}
