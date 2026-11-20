"use client";

import { useEffect, useState, type ReactNode } from "react";
import { Bot, Gauge, ListChecks, Scale, Timer } from "lucide-react";
import { api, ApiError, type EvalResponse, type ExtractorRun, type GroundingOutcome } from "@/lib/api";
import { Banner, Card, Skeleton } from "@/components/ui";

const pct = (x: number) => `${(100 * x).toFixed(1)}%`;
const day = (iso: string) => iso.slice(0, 10);
const OUTCOMES: [GroundingOutcome, string][] = [
  ["first_try", "Passed on the first try"],
  ["after_retry", "Passed after one retry"],
  ["fallback", "Failed twice: rule-based answer"],
];

function Section({
  icon,
  title,
  question,
  measured,
  method,
  children,
}: {
  icon: ReactNode;
  title: string;
  question: string;
  measured?: string;
  method?: string;
  children: ReactNode;
}) {
  return (
    <Card className="p-5 sm:p-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-base font-semibold">
            <span className="grid h-7 w-7 place-items-center rounded-lg bg-accent-soft text-accent-ink">{icon}</span>
            {title}
          </h2>
          <p className="mt-1 text-sm text-muted">{question}</p>
        </div>
        {measured && <span className="shrink-0 font-mono text-xs text-muted">{day(measured)}</span>}
      </div>
      <div className="mt-4">{children}</div>
      {method && (
        <details className="mt-4 text-sm">
          <summary className="cursor-pointer rounded text-muted hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink">
            How it was measured
          </summary>
          <p className="mt-2 leading-relaxed text-muted">{method}</p>
        </details>
      )}
    </Card>
  );
}

function NotYet({ what }: { what: string }) {
  return <p className="rounded-xl bg-surface-2 px-4 py-3 text-sm text-muted">Not measured yet: {what}</p>;
}

function Table({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[28rem] text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-muted">
            {head.map((h, i) => (
              <th key={h} scope="col" className={i ? "px-2 py-2 text-right font-medium" : "py-2 pr-2 font-medium"}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-line last:border-0">
              {r.map((c, j) => (
                <td key={j} className={j ? "px-2 py-2 text-right font-mono tabular-nums" : "py-2 pr-2"}>
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Extractor({ before, after }: { before?: ExtractorRun; after?: ExtractorRun }) {
  const cur = after ?? before;
  const runs = [
    ["Before fixes", before],
    ["After fixes", after],
  ] as const;
  return (
    <Section
      icon={<ListChecks size={15} aria-hidden />}
      title="Skill extractor"
      question="Does the rule-based extractor find the skills a posting actually asks for?"
      measured={cur?.measured_at}
      method={cur?.method}
    >
      {cur ? (
        <>
          <Table
            head={["Run", "Postings", "Precision", "Recall", "F1"]}
            rows={runs
              .filter(([, r]) => r)
              .map(([label, r]) => [label, r!.n_postings, pct(r!.micro.precision), pct(r!.micro.recall), pct(r!.micro.f1)])}
          />
          <p className="mt-3 text-xs text-muted">
            {cur.n_postings} postings from {cur.n_companies} companies, {cur.n_gold} labelled (posting, skill) pairs;
            micro-averaged. Labelled by an AI assistant (Claude) at the author&apos;s request, one annotator.
            {cur.not_in_taxonomy > 0 && ` ${cur.not_in_taxonomy} skills the taxonomy does not cover were noted separately.`}
          </p>
          {after && (
            <p className="mt-2 text-xs text-muted">
              The fixes behind &ldquo;after&rdquo; were chosen from errors on this same sample, so it is an optimistic,
              in-sample estimate.
            </p>
          )}
        </>
      ) : (
        <NotYet what="70 postings are sampled and waiting for hand-labelling." />
      )}
    </Section>
  );
}

export default function EvalView() {
  const [data, setData] = useState<EvalResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const c = new AbortController();
    api
      .evals(c.signal)
      .then(setData)
      .catch((e) => {
        if ((e as Error).name !== "AbortError") setError(e instanceof ApiError ? e.message : "Could not load results.");
      });
    return () => c.abort();
  }, []);

  const r = data?.offline ?? {};
  const live = data?.live_grounding;
  const liveTotal = live ? Object.values(live.counts).reduce((a, b) => a + (b ?? 0), 0) : 0;

  return (
    <div className="mx-auto w-full max-w-3xl space-y-5 px-4 py-8 sm:py-10">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Evals</h1>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          Every number on this page was measured by the scripts in <code className="font-mono text-xs">backend/evals</code>,
          and each comes with its sample size, date and method. Anything not yet measured says so instead of showing a
          number.
        </p>
      </div>

      {error && <Banner tone="bad">{error}</Banner>}
      {!data && !error && (
        <div className="space-y-5" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-40" />
          ))}
        </div>
      )}

      {data && (
        <>
          <Extractor before={r.extractor?.before} after={r.extractor?.after} />

          <Section
            icon={<Scale size={15} aria-hidden />}
            title="Why-Not-Me simulation vs. full re-rank"
            question="When the app says “add this skill and you move to #4”, is that what the real ranker would do?"
            measured={r.simulation?.measured_at}
            method={r.simulation?.method}
          >
            {r.simulation ? (
              <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
                <div>
                  <div className="font-mono text-3xl font-semibold tabular-nums">{pct(r.simulation.agreement)}</div>
                  <div className="text-xs text-muted">agreement</div>
                </div>
                <p className="max-w-md text-sm text-muted">
                  {r.simulation.n_simulations.toLocaleString()} simulations over {r.simulation.n_profiles} test profiles (
                  {r.simulation.n_rank_comparisons.toLocaleString()} job ranks compared); largest score difference{" "}
                  {r.simulation.max_abs_score_diff.toExponential(1)}.
                </p>
              </div>
            ) : (
              <NotYet what="run python -m evals.simulation." />
            )}
          </Section>

          <Section
            icon={<Bot size={15} aria-hidden />}
            title="Assistant grounding"
            question="How often does the AI's answer cite only facts the tools actually returned?"
            measured={r.grounding?.measured_at}
            method={r.grounding?.method}
          >
            {r.grounding ? (
              <>
                <Table
                  head={["Outcome", "Runs", "Share"]}
                  rows={OUTCOMES.map(([k, label]) => [label, r.grounding!.counts[k], pct(r.grounding!.rates[k])])}
                />
                <p className="mt-3 text-xs text-muted">
                  n = {r.grounding.n_runs} runs with {r.grounding.model}; on average {r.grounding.mean_llm_calls.toFixed(2)}{" "}
                  LLM calls per run (limit 4).
                </p>
              </>
            ) : (
              <NotYet what="needs real Gemini runs (python -m evals.grounding)." />
            )}
            {live && liveTotal > 0 && (
              <div className="mt-4 rounded-xl bg-surface-2 px-4 py-3 text-sm">
                <div className="font-medium">From real visitors (not a controlled test)</div>
                <p className="mt-1 text-muted">
                  {liveTotal} assistant runs since {live.since}:{" "}
                  {OUTCOMES.map(([k]) => `${live.counts[k] ?? 0} ${k.replace("_", " ")}`).join(", ")}
                  {live.counts.rules_only ? `, ${live.counts.rules_only} without the AI (quota or offline)` : ""}.
                </p>
              </div>
            )}
          </Section>

          <Section
            icon={<Timer size={15} aria-hidden />}
            title="Search latency"
            question="How long does a search take, end to end?"
            measured={r.latency?.deployed?.measured_at ?? r.latency?.local?.measured_at}
            method={(r.latency?.deployed ?? r.latency?.local)?.method}
          >
            {r.latency && Object.keys(r.latency).length ? (
              <Table
                head={["Where", "Jobs", "n", "p50", "p95", "Server p50"]}
                rows={(["local", "deployed"] as const)
                  .filter((k) => r.latency![k])
                  .map((k) => {
                    const v = r.latency![k]!;
                    return [
                      k === "local" ? "Local (same machine)" : "Deployed (Render free)",
                      v.jobs_active?.toLocaleString() ?? "–",
                      v.n,
                      `${Math.round(v.round_trip_ms.p50)} ms`,
                      `${Math.round(v.round_trip_ms.p95)} ms`,
                      `${Math.round(v.server_ms.p50)} ms`,
                    ];
                  })}
              />
            ) : (
              <NotYet what="run python -m evals.latency." />
            )}
          </Section>

          <p className="flex items-center gap-2 text-xs text-muted">
            <Gauge size={13} aria-hidden /> Results file updated {r.updated_at ? day(r.updated_at) : "never"}.
          </p>
        </>
      )}
    </div>
  );
}
