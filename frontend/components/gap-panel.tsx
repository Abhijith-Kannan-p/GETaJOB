"use client";

import { useEffect, useRef } from "react";
import type { Effect, WhyNotResponse } from "@/lib/api";
import { BreakdownTable } from "./score-bar";

function RankMove({ r }: { r: { before: number; after: number } | null }) {
  if (!r) return null;
  const up = r.before - r.after;
  return (
    <span className="font-mono">
      #{r.before} → #{r.after}
      {up > 0 && <span className="text-good"> (▲{up})</span>}
    </span>
  );
}

function FixRow({
  e,
  forJob,
  onAddPlan,
  planned,
}: {
  e: Effect;
  forJob: boolean;
  onAddPlan?: (s: string) => void;
  planned: boolean;
}) {
  return (
    <li className="rounded-xl border border-line p-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-medium">+ {e.skill}</span>
        <span className="text-xs text-muted">
          {e.effort_tier} · ~{e.effort_hours} h <span className="italic">(my estimate)</span>
        </span>
      </div>
      {onAddPlan && (
        <button
          type="button"
          disabled={planned}
          onClick={() => onAddPlan(e.skill)}
          className="mt-2 inline-flex items-center gap-1 rounded-lg bg-accent-soft px-2 py-1 text-xs font-medium text-accent-ink hover:bg-accent hover:text-on-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink disabled:opacity-60"
        >
          {planned ? "✓ In your plan" : "+ Add to my plan"}
        </button>
      )}
      <p className="mt-1 text-sm text-muted">
        {forJob && e.job_rank && (
          <>
            This job: <RankMove r={e.job_rank} />.{" "}
          </>
        )}
        {e.jobs_with_skill} pool jobs list it; they move up {e.mean_rank_change.toFixed(1)} places on average
        {e.entering_top20 > 0 && `, ${e.entering_top20} enter your top 20`}
        {e.entering_top10 > 0 && ` (${e.entering_top10} your top 10)`}.
      </p>
    </li>
  );
}

export function GapPanel({
  data,
  loading,
  error,
  onClose,
  onAddPlan,
  onAsk,
  plannedSkills = [],
}: {
  data: WhyNotResponse | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onAddPlan?: (skill: string) => void;
  onAsk?: (jobId: number | null) => void;
  plannedSkills?: string[];
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const forJob = !!data?.job;
  const c = data?.plan.cumulative;
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-navy/35 backdrop-blur-sm" onClick={onClose}>
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="gap-title"
        className="h-full w-full max-w-lg overflow-y-auto glass bg-raised-strong p-5 dark:bg-[#111c33]/90 motion-safe:transition-transform"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <h2 id="gap-title" className="text-lg font-semibold">
            {forJob ? `Why not me? ${data?.job?.title}` : "What would move you up?"}
          </h2>
          <button
            ref={closeRef}
            onClick={onClose}
            className="rounded-lg px-2 py-1 text-sm hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ink focus-visible:outline-none"
          >
            Close
          </button>
        </div>

        {loading && <p className="mt-6 text-sm text-muted">Simulating the ranker…</p>}
        {error && (
          <p role="alert" className="mt-6 rounded-xl border border-bad/40 bg-bad-soft text-bad-ink px-3 py-2 text-sm">
            {error}
          </p>
        )}

        {data && !loading && (
          <div className="mt-4 space-y-6">
            {forJob && (
              <section>
                <p className="text-sm">
                  {data.job?.company} · you are <b>#{data.rank}</b> of {data.pool_size} candidate jobs for your profile.
                </p>
                {data.breakdown && (
                  <div className="mt-3">
                    <BreakdownTable breakdown={data.breakdown} />
                  </div>
                )}
              </section>
            )}

            {forJob && (
              <section>
                <h3 className="text-sm font-semibold">Missing skills</h3>
                {data.missing && data.missing.length > 0 ? (
                  <ul className="mt-2 space-y-1 text-sm">
                    {data.missing.slice(0, 10).map((m) => (
                      <li key={m.skill} className="flex justify-between gap-2">
                        <span>
                          {m.skill}{" "}
                          <span className="text-xs text-muted">({m.kind})</span>
                        </span>
                        <span className="font-mono text-xs text-muted">
                          {(m.share * 100).toFixed(0)}% of this job&apos;s skill weight
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-2 text-sm text-muted">You have every skill this posting lists.</p>
                )}
              </section>
            )}

            <section>
              <h3 className="text-sm font-semibold">{forJob ? "Cheapest fixes for this job" : "Top fixes"}</h3>
              {data.fixes.length > 0 ? (
                <ul className="mt-2 space-y-2">
                  {data.fixes.map((e) => (
                    <FixRow
                      key={e.skill}
                      e={e}
                      forJob={forJob}
                      onAddPlan={onAddPlan}
                      planned={plannedSkills.includes(e.skill)}
                    />
                  ))}
                </ul>
              ) : (
                <p className="mt-2 text-sm text-muted">No single skill changes the ranking here.</p>
              )}
            </section>

            {data.plan.steps.length > 0 && c && (
              <section>
                <h3 className="text-sm font-semibold">3-step plan (best gain per hour, greedy)</h3>
                <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm">
                  {data.plan.steps.map((s) => (
                    <li key={s.skill}>
                      {s.skill} <span className="text-xs text-muted">~{s.effort_hours} h (my estimate)</span>
                    </li>
                  ))}
                </ol>
                <p className="mt-2 text-sm">
                  Together: {c.affected_jobs} jobs list at least one of these; their median rank goes from{" "}
                  <b className="font-mono">#{c.median_rank_before}</b> to <b className="font-mono">#{c.median_rank_after}</b>
                  {c.entering_top20 > 0 && `, ${c.entering_top20} enter your top 20`}.
                  {c.job_rank && (
                    <>
                      {" "}This job: <RankMove r={c.job_rank} />.
                    </>
                  )}{" "}
                  About {c.total_hours} h in total.
                </p>
              </section>
            )}

            {onAsk && (
              <button
                type="button"
                onClick={() => onAsk(data.job?.id ?? null)}
                className="w-full rounded-xl bg-navy px-4 py-2.5 text-sm font-medium text-cream dark:bg-cream dark:text-navy hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink focus-visible:ring-offset-2"
              >
                Ask the assistant to explain {forJob ? "this job" : "this"} →
              </button>
            )}

            <p className="rounded-xl border border-line bg-surface-2 p-3 text-xs text-muted">
              {data.disclaimer} {data.effort_note}
            </p>
          </div>
        )}
      </aside>
    </div>
  );
}
