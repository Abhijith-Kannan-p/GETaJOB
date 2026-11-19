import type { Breakdown, Term } from "@/lib/api";

export const TERM_META: Record<Term, { label: string; className: string }> = {
  semantic: { label: "Semantic", className: "bg-indigo-500" },
  keyword: { label: "Keyword", className: "bg-sky-500" },
  skills: { label: "Skills", className: "bg-emerald-500" },
  seniority: { label: "Seniority", className: "bg-amber-500" },
  location: { label: "Location", className: "bg-fuchsia-500" },
  recency: { label: "Recency", className: "bg-zinc-400" },
};
const ORDER: Term[] = ["semantic", "keyword", "skills", "seniority", "location", "recency"];

// Stacked bar: each segment's width is that term's contribution to the score (max total = 1.0).
export function ScoreBar({ breakdown, score }: { breakdown: Breakdown; score: number }) {
  const label = ORDER.map((t) => `${TERM_META[t].label} ${breakdown[t].contribution.toFixed(2)}`).join(", ");
  return (
    <div>
      <div
        className="flex h-2 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800"
        role="img"
        aria-label={`Score ${score.toFixed(2)} of 1: ${label}`}
      >
        {ORDER.map((t) => (
          <div
            key={t}
            className={TERM_META[t].className}
            style={{ width: `${breakdown[t].contribution * 100}%` }}
            title={`${TERM_META[t].label}: value ${breakdown[t].value.toFixed(2)} × weight ${breakdown[t].weight} = ${breakdown[t].contribution.toFixed(3)}`}
          />
        ))}
      </div>
    </div>
  );
}

export function ScoreLegend() {
  return (
    <ul className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-zinc-500 dark:text-zinc-400">
      {ORDER.map((t) => (
        <li key={t} className="flex items-center gap-1">
          <span className={`inline-block h-2 w-2 rounded-full ${TERM_META[t].className}`} aria-hidden />
          {TERM_META[t].label}
        </li>
      ))}
    </ul>
  );
}

export function BreakdownTable({ breakdown }: { breakdown: Breakdown }) {
  return (
    <table className="w-full text-xs">
      <thead className="text-zinc-500">
        <tr>
          <th className="text-left font-normal">Term</th>
          <th className="text-right font-normal">Value</th>
          <th className="text-right font-normal">Weight</th>
          <th className="text-right font-normal">Points</th>
        </tr>
      </thead>
      <tbody className="font-mono">
        {ORDER.map((t) => (
          <tr key={t}>
            <td className="font-sans">{TERM_META[t].label}</td>
            <td className="text-right">{breakdown[t].value.toFixed(2)}</td>
            <td className="text-right">{breakdown[t].weight}</td>
            <td className="text-right">{breakdown[t].contribution.toFixed(3)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
