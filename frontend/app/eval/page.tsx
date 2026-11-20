import type { Metadata } from "next";
import { SiteHeader } from "@/components/site-header";
import EvalView from "./eval-view";

export const metadata: Metadata = {
  title: "Evals · GETaJOB",
  description: "Measured numbers for the skill extractor, the Why-Not-Me simulation, LLM grounding and search latency.",
};

export default function EvalPage() {
  return (
    <>
      <SiteHeader />
      <main className="flex-1">
        <EvalView />
      </main>
    </>
  );
}
