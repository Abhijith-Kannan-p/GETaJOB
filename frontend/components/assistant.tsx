"use client";

// The assistant: preset questions as buttons (or a short typed question), a live trace of what the agent does,
// and a narrative where every sentence cites the facts it came from.
import { useState } from "react";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  Compass,
  Cpu,
  GraduationCap,
  ListOrdered,
  Loader2,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  Wand2,
} from "lucide-react";
import type { AgentEvent, Narrative } from "@/lib/session";
import { Button, Card, SectionLabel, cx } from "./ui";

export const PRESETS = [
  { label: "What should I learn next?", icon: GraduationCap },
  { label: "Which jobs fit me best right now?", icon: ListOrdered },
  { label: "Why am I not ranking higher?", icon: Compass },
  { label: "Find remote roles for my skills", icon: Search },
];

const TOOL_LABEL: Record<string, (a: Record<string, unknown>) => string> = {
  rank_jobs: (a) => `Ranking open jobs for your profile${a.limit ? ` (top ${a.limit})` : ""}`,
  best_skills: () => "Simulating skills you could add",
  explain_job: (a) => `Why-Not-Me for job ${a.job_id}`,
  simulate_skill: (a) => `Simulating + ${a.skill}${a.job_id ? ` for job ${a.job_id}` : ""}`,
  search_jobs: (a) => `Searching “${a.query}”${a.remote_only ? " (remote)" : ""}`,
};

type Final = Extract<AgentEvent, { type: "narrative" }>;

export function Trace({ events, running }: { events: AgentEvent[]; running: boolean }) {
  const rows = events.filter((e) => e.type !== "narrative" && e.type !== "done");
  const done = events.find((e) => e.type === "done") as Extract<AgentEvent, { type: "done" }> | undefined;
  return (
    <ol className="space-y-1.5 text-sm" aria-label="What the assistant did">
      {rows.map((e, i) => {
        if (e.type === "start")
          return (
            <li key={i} className="flex items-center gap-2 text-muted">
              <Cpu size={14} aria-hidden />
              {e.mode === "llm" ? `Gemini plans the steps (at most ${e.max_llm_calls} AI calls)` : "Offline mode: fixed rule-based steps, no AI calls"}
            </li>
          );
        if (e.type === "thinking")
          return (
            <li key={i} className="flex items-center gap-2 text-muted">
              <Sparkles size={14} aria-hidden />
              {e.round === "narrative" ? "Writing the answer from the facts" : `Deciding what to check (round ${e.round})`}
            </li>
          );
        if (e.type === "tool_call")
          return (
            <li key={i} className="flex items-center gap-2">
              <Wand2 size={14} className="text-accent-ink" aria-hidden />
              <span className="font-medium">{(TOOL_LABEL[e.name] ?? (() => e.name))(e.args)}</span>
            </li>
          );
        if (e.type === "tool_result")
          return (
            <li key={i} className="ml-6">
              {e.error ? (
                <span className="text-xs text-warn">{e.error}</span>
              ) : (
                <details className="text-xs text-muted">
                  <summary className="cursor-pointer select-none hover:text-ink">
                    {e.facts?.length ?? 0} fact{e.facts?.length === 1 ? "" : "s"} recorded
                  </summary>
                  <ul className="mt-1 space-y-1">
                    {e.facts?.map((f) => (
                      <li key={f.id}>
                        <b className="font-mono">{f.id}</b> {f.text}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </li>
          );
        if (e.type === "notice")
          return (
            <li key={i} className="flex items-center gap-2 text-warn">
              <AlertTriangle size={14} aria-hidden /> {e.message}
            </li>
          );
        if (e.type === "error")
          return (
            <li key={i} role="alert" className="flex items-center gap-2 text-bad">
              <AlertTriangle size={14} aria-hidden /> {e.message}
            </li>
          );
        return null;
      })}
      {running && (
        <li className="flex items-center gap-2 text-muted">
          <Loader2 size={14} className="motion-safe:animate-spin" aria-hidden /> Working…
        </li>
      )}
      {done && (
        <li className="flex items-center gap-2 text-xs text-muted">
          <CheckCircle2 size={14} className="text-good" aria-hidden /> Done in {(done.ms / 1000).toFixed(1)} s ·{" "}
          {done.llm_calls} AI call{done.llm_calls === 1 ? "" : "s"}
        </li>
      )}
    </ol>
  );
}

function Cites({ ids, facts }: { ids: string[]; facts: Record<string, string> }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <>
      {ids.map((id) => (
        <button
          key={id}
          type="button"
          onClick={() => setOpen(open === id ? null : id)}
          aria-expanded={open === id}
          title={facts[id]}
          className="ml-1 rounded-md bg-accent-soft px-1.5 py-0.5 align-middle font-mono text-[10px] font-semibold text-accent-ink hover:bg-accent hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink"
        >
          {id}
        </button>
      ))}
      {open && facts[open] && (
        <span className="mt-1 block rounded-lg border border-line bg-white/60 px-2 py-1 text-xs text-muted">
          <b className="font-mono">{open}</b> {facts[open]}
        </span>
      )}
    </>
  );
}

export function Answer({
  final,
  plannedSkills,
  onAddPlan,
}: {
  final: Pick<Final, "narrative" | "facts" | "source"> & Partial<Pick<Final, "grounding" | "disclaimer" | "degraded">>;
  plannedSkills: string[];
  onAddPlan: (skill: string) => void;
}) {
  const n: Narrative = final.narrative;
  const dropped = final.grounding?.dropped.length ?? 0;
  return (
    <div className="space-y-4">
      {n.headline && (
        <p className="text-lg font-semibold leading-snug">
          {n.headline.text}
          <Cites ids={n.headline.facts} facts={final.facts} />
        </p>
      )}
      {n.points.length > 0 && (
        <ul className="space-y-2 text-sm">
          {n.points.map((p, i) => (
            <li key={i} className="flex gap-2">
              <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" aria-hidden />
              <span>
                {p.text}
                <Cites ids={p.facts} facts={final.facts} />
              </span>
            </li>
          ))}
        </ul>
      )}
      {n.next_steps.length > 0 && (
        <div>
          <SectionLabel icon={<GraduationCap size={13} aria-hidden />}>Next steps</SectionLabel>
          <ul className="mt-2 space-y-2">
            {n.next_steps.map((s) => {
              const planned = plannedSkills.includes(s.skill);
              return (
                <li key={s.skill} className="glass flex items-start justify-between gap-3 rounded-xl p-3">
                  <div className="text-sm">
                    <p className="font-semibold">{s.skill}</p>
                    <p className="text-muted">
                      {s.why}
                      <Cites ids={s.facts} facts={final.facts} />
                    </p>
                  </div>
                  <Button
                    size="sm"
                    variant={planned ? "ghost" : "primary"}
                    disabled={planned}
                    onClick={() => onAddPlan(s.skill)}
                    aria-label={planned ? `${s.skill} is in your plan` : `Add ${s.skill} to your plan`}
                  >
                    {planned ? <CheckCircle2 size={14} aria-hidden /> : <Plus size={14} aria-hidden />}
                    {planned ? "In plan" : "Plan"}
                  </Button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
      <p className="flex items-start gap-1.5 text-xs text-muted">
        <ShieldCheck size={14} className="mt-px shrink-0 text-good" aria-hidden />
        <span>
          {final.source === "llm"
            ? `Written by Gemini, then checked: every sentence must cite a fact, and every number and skill must appear in what it cites.${dropped ? ` ${dropped} unsupported part${dropped === 1 ? " was" : "s were"} removed.` : ""}`
            : "Rule-based summary built directly from the facts (no AI writing)."}{" "}
          {final.disclaimer}
        </span>
      </p>
    </div>
  );
}

export function AssistantCard({
  events,
  running,
  error,
  lastAnswer,
  plannedSkills,
  onAsk,
  onAddPlan,
}: {
  events: AgentEvent[];
  running: boolean;
  error: string | null;
  lastAnswer: Pick<Final, "narrative" | "facts" | "source"> | null;
  plannedSkills: string[];
  onAsk: (message: string) => void;
  onAddPlan: (skill: string) => void;
}) {
  const [text, setText] = useState("");
  const final = (events.find((e) => e.type === "narrative") as Final | undefined) ?? lastAnswer;
  return (
    <Card className="p-5">
      <div className="flex items-center gap-3">
        <span className="grid h-10 w-10 place-items-center rounded-xl bg-navy text-coral">
          <Bot size={20} aria-hidden />
        </span>
        <div>
          <h2 className="font-semibold">Ask the assistant</h2>
          <p className="text-xs text-muted">It runs the real ranker and Why-Not-Me, then explains, citing every fact.</p>
        </div>
      </div>

      <div className="mt-4 grid gap-2 sm:grid-cols-2">
        {PRESETS.map(({ label, icon: Icon }) => (
          <button
            key={label}
            type="button"
            disabled={running}
            onClick={() => onAsk(label)}
            className="glass flex items-center gap-2 rounded-xl px-3 py-2.5 text-left text-sm font-medium transition-colors duration-150 hover:border-accent hover:text-accent-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink disabled:opacity-50"
          >
            <Icon size={16} className="shrink-0 text-accent-ink" aria-hidden /> {label}
          </button>
        ))}
      </div>

      <form
        className="mt-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) onAsk(text.trim());
        }}
      >
        <label htmlFor="ask" className="sr-only">
          Your question
        </label>
        <div className="flex gap-2">
          <input
            id="ask"
            value={text}
            maxLength={300}
            onChange={(e) => setText(e.target.value)}
            placeholder="Or ask your own question…"
            className="min-w-0 flex-1 rounded-xl border border-line bg-surface px-3 py-2 text-sm outline-none placeholder:text-muted focus:border-accent"
          />
          <Button type="submit" disabled={running || !text.trim()}>
            Ask
          </Button>
        </div>
        <p className="mt-1.5 text-[11px] text-muted">
          Questions go to Google&apos;s Gemini free tier, which may use them to improve its models. Don&apos;t include
          personal details. {text.length}/300
        </p>
      </form>

      {error && (
        <p role="alert" className="mt-4 rounded-xl border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900">
          {error}
        </p>
      )}
      {events.length > 0 && (
        <div className={cx("mt-5 rounded-xl border border-line bg-white/40 p-3")}>
          <Trace events={events} running={running} />
        </div>
      )}
      {final && (
        <div className="mt-5 border-t border-line pt-5">
          {!events.length && <p className="mb-2 text-xs text-muted">Your last answer:</p>}
          <Answer final={final} plannedSkills={plannedSkills} onAddPlan={onAddPlan} />
        </div>
      )}
    </Card>
  );
}
