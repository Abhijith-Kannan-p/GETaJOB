"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ExternalLink, MapPin, Search, Wifi } from "lucide-react";
import { api, ApiError, type Job, type SearchResponse } from "@/lib/api";
import { Banner, Button, Card, Pill, Skeleton } from "@/components/ui";

const LEVELS = [
  ["intern", "Intern"],
  ["new_grad", "New grad"],
  ["entry", "Entry"],
] as const;
const EXAMPLES = ["machine learning intern", "backend python", "frontend react", "data analyst"];

export default function SearchView() {
  const params = useSearchParams();
  const router = useRouter();
  const initial = params.get("q") ?? "";
  const [q, setQ] = useState(initial);
  const [remote, setRemote] = useState(false);
  const [levels, setLevels] = useState<string[]>([]);
  const [data, setData] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const ctrl = useRef<AbortController | null>(null);

  async function run(query: string, opts = { remote, levels }) {
    const text = query.trim();
    if (text.length < 2) return;
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    setLoading(true);
    setError(null);
    try {
      setData(await api.search(text, { remote: opts.remote, seniority: opts.levels }, c.signal));
    } catch (err) {
      if ((err as Error).name !== "AbortError") setError(err instanceof ApiError ? err.message : "Search failed.");
    } finally {
      if (ctrl.current === c) setLoading(false);
    }
  }

  // Run once for a ?q= link (from the landing page tiles).
  const ran = useRef(false);
  useEffect(() => {
    if (ran.current || initial.trim().length < 2) return;
    ran.current = true;
    const c = new AbortController();
    api
      .search(initial.trim(), {}, c.signal)
      .then(setData)
      .catch((err) => err.name !== "AbortError" && setError(err instanceof ApiError ? err.message : "Search failed."));
    return () => c.abort();
  }, [initial]);

  function submit(e?: React.FormEvent) {
    e?.preventDefault();
    router.replace(`/search?q=${encodeURIComponent(q.trim())}`, { scroll: false });
    run(q);
  }

  const toggle = (l: string) => {
    const next = levels.includes(l) ? levels.filter((x) => x !== l) : [...levels, l];
    setLevels(next);
    if (data) run(q, { remote, levels: next });
  };
  const toggleRemote = () => {
    setRemote(!remote);
    if (data) run(q, { remote: !remote, levels });
  };

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-10">
      <h1 className="text-3xl font-semibold tracking-tight">Search open roles</h1>
      <p className="mt-1 text-muted">Semantic + keyword search over real postings from public job boards.</p>

      <form onSubmit={submit} className="mt-6" role="search">
        <label htmlFor="q" className="sr-only">Search jobs</label>
        <div className="flex gap-2 glass rounded-2xl p-2 focus-within:border-accent">
          <Search className="ml-2 mt-2.5 shrink-0 text-muted" size={18} aria-hidden />
          <input
            id="q"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            maxLength={200}
            placeholder="Try “machine learning intern”"
            className="min-w-0 flex-1 bg-transparent px-1 text-base outline-none placeholder:text-muted"
          />
          <Button type="submit" disabled={loading || q.trim().length < 2}>
            {loading ? "Searching…" : "Search"}
          </Button>
        </div>
      </form>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {LEVELS.map(([v, label]) => (
          <Pill key={v} selected={levels.includes(v)} onClick={() => toggle(v)}>
            {label}
          </Pill>
        ))}
        <Pill selected={remote} onClick={toggleRemote}>
          <Wifi size={14} aria-hidden /> Remote
        </Pill>
      </div>

      {!data && !loading && !error && (
        <div className="mt-6 flex flex-wrap gap-2">
          <span className="text-sm text-muted">Examples:</span>
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              onClick={() => {
                setQ(ex);
                run(ex);
              }}
              className="rounded-full bg-accent-soft px-3 py-1 text-sm text-accent-ink hover:bg-accent hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink"
            >
              {ex}
            </button>
          ))}
        </div>
      )}

      <div className="mt-6 space-y-3">
        {data?.degraded === "fulltext_only" && (
          <Banner tone="warn">Semantic search is unavailable right now, so results use full-text search only.</Banner>
        )}
        {error && <Banner tone="bad">{error}</Banner>}
      </div>

      {loading && !data && (
        <div className="mt-4 space-y-3">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-28 rounded-2xl" />)}
        </div>
      )}
      {data && !error && (
        <p className="mt-4 text-xs text-muted">
          {data.results.length} results · {data.took_ms} ms
        </p>
      )}
      {data && data.results.length === 0 && !error && (
        <p className="mt-4 text-sm text-muted">No matching open jobs. Try broader words.</p>
      )}
      <ul className="mt-3 space-y-3">{data?.results.map((j) => <JobCard key={j.id} job={j} />)}</ul>
    </div>
  );
}

function JobCard({ job }: { job: Job }) {
  const r = job.retrieval;
  return (
    <li>
      <Card className="p-4 transition-colors duration-150 hover:border-line-strong">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="font-medium">{job.title}</h2>
            <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-sm text-muted">
              <span>{job.company}</span>
              <span className="inline-flex items-center gap-1">
                <MapPin size={13} aria-hidden /> {job.location || "Location not listed"}
              </span>
              {job.is_remote && <span className="rounded-full bg-accent-soft px-2 text-xs text-accent-ink">Remote</span>}
              <span className="rounded-full bg-surface-2 px-2 text-xs">{job.seniority.replace("_", " ")}</span>
            </p>
          </div>
          <a
            href={job.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-line-strong px-3 py-1.5 text-sm hover:border-accent hover:text-accent-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink"
          >
            Apply <ExternalLink size={13} aria-hidden />
          </a>
        </div>
        {job.required_skills.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {job.required_skills.slice(0, 8).map((s) => (
              <span key={s} className="rounded-md bg-surface-2 px-2 py-0.5 text-xs">{s}</span>
            ))}
          </div>
        )}
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
          <span>{job.attribution}</span>
          {r && (
            <span className="font-mono">
              semantic {r.vec_rank ? `#${r.vec_rank}` : "–"} · keyword {r.fts_rank ? `#${r.fts_rank}` : "–"}
            </span>
          )}
        </div>
      </Card>
    </li>
  );
}
