"use client";

// Slice-1 minimal search UI. Slice 5 restyles this with shadcn/ui and the design tokens.
import { useEffect, useRef, useState } from "react";
import { api, ApiError, type Job, type SearchResponse, type Stats } from "@/lib/api";

const LEVELS = [
  ["intern", "Intern"],
  ["new_grad", "New grad"],
  ["entry", "Entry"],
] as const;

export default function SearchClient() {
  const [q, setQ] = useState("");
  const [remote, setRemote] = useState(false);
  const [levels, setLevels] = useState<string[]>([]);
  const [data, setData] = useState<SearchResponse | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const ctrl = useRef<AbortController | null>(null);

  useEffect(() => {
    const c = new AbortController();
    api.stats(c.signal).then(setStats).catch(() => {});
    return () => c.abort();
  }, []);

  async function run(e?: React.FormEvent) {
    e?.preventDefault();
    if (q.trim().length < 2) return;
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    setLoading(true);
    setError(null);
    try {
      setData(await api.search(q.trim(), { remote, seniority: levels }, c.signal));
    } catch (err) {
      if ((err as Error).name !== "AbortError") setError(err instanceof ApiError ? err.message : "Search failed.");
    } finally {
      if (ctrl.current === c) setLoading(false);
    }
  }

  const toggle = (l: string) => setLevels((xs) => (xs.includes(l) ? xs.filter((x) => x !== l) : [...xs, l]));

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-10">
      <div className="flex items-baseline justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">GETaJOB</h1>
        <a href="/app" className="text-sm font-medium text-indigo-600 hover:underline dark:text-indigo-400">
          Match my profile →
        </a>
      </div>
      <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
        Hybrid semantic + keyword search over real open postings from public Greenhouse and Lever job boards.
      </p>

      {stats && (
        <p className="mt-3 font-mono text-xs text-zinc-500 dark:text-zinc-400">
          {stats.jobs_active} active jobs · {stats.companies} companies · embeddings{" "}
          {Math.round(stats.embedding_coverage * 100)}% · last sync{" "}
          {stats.last_sync ? new Date(stats.last_sync).toLocaleString() : "never"}
          {stats.last_sync_mode === "fixtures" && " (fixture snapshot)"}
        </p>
      )}

      <form onSubmit={run} className="mt-6 flex gap-2" role="search">
        <label htmlFor="q" className="sr-only">
          Search jobs
        </label>
        <input
          id="q"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          maxLength={200}
          placeholder="e.g. machine learning intern python"
          className="flex-1 rounded-xl border border-zinc-300 bg-transparent px-4 py-2.5 outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 dark:border-zinc-700"
        />
        <button
          disabled={loading || q.trim().length < 2}
          className="rounded-xl bg-indigo-600 px-4 py-2.5 font-medium text-white hover:bg-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:outline-none disabled:opacity-50"
        >
          {loading ? "Searching…" : "Search"}
        </button>
      </form>

      <div className="mt-3 flex flex-wrap gap-2 text-sm">
        {LEVELS.map(([v, label]) => (
          <button
            key={v}
            type="button"
            aria-pressed={levels.includes(v)}
            onClick={() => toggle(v)}
            className="rounded-full border border-zinc-300 px-3 py-1 aria-pressed:border-indigo-500 aria-pressed:bg-indigo-500/10 dark:border-zinc-700"
          >
            {label}
          </button>
        ))}
        <label className="flex items-center gap-1.5 px-2">
          <input type="checkbox" checked={remote} onChange={(e) => setRemote(e.target.checked)} /> Remote only
        </label>
      </div>

      {data?.degraded === "fulltext_only" && (
        <div role="status" className="mt-6 rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm">
          Semantic search is unavailable right now, so results use full-text search only.
        </div>
      )}
      {error && (
        <div role="alert" className="mt-6 rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm">
          {error}
        </div>
      )}

      {data && !error && (
        <p className="mt-6 text-xs text-zinc-500">
          {data.results.length} results · {data.took_ms} ms
        </p>
      )}
      {data && data.results.length === 0 && !error && (
        <p className="mt-4 text-sm text-zinc-500">No matching open jobs. Try broader words.</p>
      )}
      <ul className="mt-3 space-y-3">
        {data?.results.map((j) => <JobCard key={j.id} job={j} />)}
      </ul>
    </div>
  );
}

function JobCard({ job }: { job: Job }) {
  const r = job.retrieval;
  return (
    <li className="rounded-2xl border border-zinc-200 p-4 dark:border-zinc-800">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="font-medium">{job.title}</h2>
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            {job.company} · {job.location || "Location not listed"}
            {job.is_remote && " · Remote"} · {job.seniority.replace("_", " ")}
          </p>
        </div>
        <a
          href={job.url}
          target="_blank"
          rel="noopener noreferrer"
          className="shrink-0 rounded-lg border border-zinc-300 px-3 py-1 text-sm hover:border-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none dark:border-zinc-700"
        >
          Apply ↗
        </a>
      </div>
      {job.required_skills.length > 0 && (
        <p className="mt-2 text-sm">
          <span className="text-zinc-500">Required: </span>
          {job.required_skills.slice(0, 10).join(", ")}
        </p>
      )}
      {r && (
        <p className="mt-2 font-mono text-xs text-zinc-500">
          rrf {r.rrf.toFixed(4)} · semantic {r.vec_rank ? `#${r.vec_rank} (${r.semantic_sim?.toFixed(3)})` : "–"} ·
          keyword {r.fts_rank ? `#${r.fts_rank}` : "–"}
        </p>
      )}
      <p className="mt-1 text-xs text-zinc-500">{job.attribution}</p>
    </li>
  );
}
