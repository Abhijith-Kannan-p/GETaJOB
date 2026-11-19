"use client";

// "See it working": square tiles filled with LIVE data from the API (nothing here is a mock-up).
// Fetching starts only when the section scrolls into view, so the hero stays instant.
import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowRight, Building2, Database, Gauge, Layers, Search, Sparkles, TrendingUp } from "lucide-react";
import { api, rankApi, type Job, type Stats, type WhyNotResponse } from "@/lib/api";
import { TERM_META } from "@/components/score-bar";
import { Card, Skeleton, cx } from "@/components/ui";

const SAMPLE_QUERIES = ["machine learning intern", "frontend react intern", "data analyst intern"];
const SAMPLE_PROFILE = {
  skills: ["Python", "SQL", "Git"],
  target_roles: ["data analyst intern"],
  seniority: ["intern", "new_grad", "entry"] as ("intern" | "new_grad" | "entry")[],
  remote_ok: true,
  locations: [],
  free_text: "",
};
const LEVEL_LABEL: Record<string, string> = {
  intern: "Intern",
  new_grad: "New grad",
  entry: "Entry",
  mid: "Mid",
  senior: "Senior",
  unknown: "Not stated",
};

function useInView<T extends Element>() {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) {
          setSeen(true);
          io.disconnect();
        }
      },
      { rootMargin: "120px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return [ref, seen] as const;
}

function Tile({ icon, title, children, footer, delay = 0 }: {
  icon: ReactNode; title: string; children: ReactNode; footer?: ReactNode; delay?: number;
}) {
  const [ref, seen] = useInView<HTMLDivElement>();
  return (
    <div ref={ref} className={cx("gj-reveal", seen && "is-visible")} style={{ animationDelay: `${delay}ms` }}>
      <Card className="flex h-full flex-col p-5 sm:aspect-square">
        <p className="flex items-center gap-2 text-sm font-medium text-muted">
          <span className="grid h-7 w-7 place-items-center rounded-lg bg-accent-soft text-accent-ink">{icon}</span>
          {title}
        </p>
        <div className="mt-4 min-h-0 flex-1">{children}</div>
        {footer && <div className="mt-3 text-xs text-muted">{footer}</div>}
      </Card>
    </div>
  );
}

const Unavailable = () => <p className="text-sm text-muted">Live data is unavailable right now. Try again shortly.</p>;

function SearchTile({ q, delay }: { q: string; delay: number }) {
  const [ref, seen] = useInView<HTMLDivElement>();
  const [res, setRes] = useState<Job[] | null | "error">(null);
  useEffect(() => {
    if (!seen) return;
    const c = new AbortController();
    api.search(q, {}, c.signal).then((r) => setRes(r.results.slice(0, 3))).catch((e) => e.name !== "AbortError" && setRes("error"));
    return () => c.abort();
  }, [seen, q]);
  return (
    <div ref={ref} className="h-full">
      <Tile
        icon={<Search size={15} />}
        title="Live search"
        delay={delay}
        footer={
          <Link href={`/search?q=${encodeURIComponent(q)}`} className="inline-flex items-center gap-1 font-medium text-accent-ink hover:underline">
            Run this search <ArrowRight size={13} aria-hidden />
          </Link>
        }
      >
        <p className="rounded-lg border border-line bg-surface-2 px-3 py-2 font-mono text-sm">“{q}”</p>
        <ul className="mt-3 space-y-2">
          {res === null && [0, 1, 2].map((i) => <Skeleton key={i} className="h-10" />)}
          {res === "error" && <Unavailable />}
          {Array.isArray(res) && res.length === 0 && <p className="text-sm text-muted">No open matches right now.</p>}
          {Array.isArray(res) &&
            res.map((j) => (
              <li key={j.id} className="text-sm">
                <p className="truncate font-medium">{j.title}</p>
                <p className="truncate text-xs text-muted">{j.company} · {j.location || "Location not listed"}</p>
              </li>
            ))}
        </ul>
      </Tile>
    </div>
  );
}

function WhyNotTile({ delay }: { delay: number }) {
  const [ref, seen] = useInView<HTMLDivElement>();
  const [res, setRes] = useState<WhyNotResponse | null | "error">(null);
  useEffect(() => {
    if (!seen) return;
    const c = new AbortController();
    rankApi.whyNot(SAMPLE_PROFILE, null, c.signal).then(setRes).catch((e) => e.name !== "AbortError" && setRes("error"));
    return () => c.abort();
  }, [seen]);
  const fix = res && res !== "error" ? res.fixes[0] : null;
  return (
    <div ref={ref} className="h-full">
      <Tile
        icon={<Sparkles size={15} />}
        title="Why-Not-Me, live"
        delay={delay}
        footer="Sample profile: Python, SQL, Git → data analyst intern. Ranker simulation, not a hiring prediction."
      >
        {res === null && <Skeleton className="h-28" />}
        {res === "error" && <Unavailable />}
        {res && res !== "error" && !fix && <p className="text-sm text-muted">No single skill changes this ranking.</p>}
        {fix && (
          <div>
            <p className="text-sm text-muted">The cheapest skill to add next:</p>
            <p className="mt-1 text-2xl font-semibold text-accent-ink">+ {fix.skill}</p>
            <p className="mt-2 text-sm">
              {fix.jobs_with_skill} matching jobs list it; they move up{" "}
              <b>{fix.mean_rank_change.toFixed(1)}</b> places on average
              {fix.entering_top20 > 0 && <>, and {fix.entering_top20} enter the top 20</>}.
            </p>
            <p className="mt-1 text-xs text-muted">≈ {fix.effort_hours} h (my estimate)</p>
          </div>
        )}
      </Tile>
    </div>
  );
}

export function Showcase() {
  const [ref, seen] = useInView<HTMLElement>();
  const [stats, setStats] = useState<Stats | null | "error">(null);
  useEffect(() => {
    if (!seen) return;
    const c = new AbortController();
    api.stats(c.signal).then(setStats).catch((e) => e.name !== "AbortError" && setStats("error"));
    return () => c.abort();
  }, [seen]);
  const s = stats && stats !== "error" ? stats : null;
  // defaults keep tiles safe against an older backend that lacks these fields
  const levels = s?.by_seniority ?? {};
  const companies = s?.top_companies ?? [];
  const weights = s?.ranking_weights ?? {};
  const levelMax = Math.max(1, ...Object.values(levels));

  return (
    <section ref={ref} id="showcase" className="mx-auto w-full max-w-6xl scroll-mt-6 px-4 py-20">
      <h2 className="text-center text-3xl font-semibold tracking-tight">See it working</h2>
      <p className="mx-auto mt-2 max-w-xl text-center text-muted">
        Every tile below is live: real postings, real searches, the real ranker.
      </p>
      {stats === null && seen && (
        <p className="mt-3 text-center text-xs text-muted">Waking the free-tier server; the first load can take up to a minute.</p>
      )}

      <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Tile icon={<Database size={15} />} title="Open roles right now" footer={s?.last_sync && `Last sync ${new Date(s.last_sync).toLocaleString()}`}>
          {stats === null && <Skeleton className="h-24" />}
          {stats === "error" && <Unavailable />}
          {s && (
            <div>
              <p className="text-6xl font-semibold tracking-tight text-accent-ink">{s.jobs_active.toLocaleString()}</p>
              <p className="mt-1 text-muted">searchable open roles</p>
              <p className="mt-4 text-sm">
                from <b>{s.companies}</b> companies, embedded for semantic search:{" "}
                <b>{Math.round(s.embedding_coverage * 100)}%</b>
              </p>
            </div>
          )}
        </Tile>

        {SAMPLE_QUERIES.map((q, i) => (
          <SearchTile key={q} q={q} delay={60 * (i + 1)} />
        ))}

        <WhyNotTile delay={60} />

        <Tile icon={<Layers size={15} />} title="Roles by level" delay={120}>
          {stats === null && <Skeleton className="h-32" />}
          {stats === "error" && <Unavailable />}
          {s && (
            <ul className="space-y-2">
              {Object.entries(levels).map(([lvl, n]) => (
                <li key={lvl} className="text-sm">
                  <div className="flex justify-between">
                    <span>{LEVEL_LABEL[lvl] ?? lvl}</span>
                    <span className="font-mono text-muted">{n}</span>
                  </div>
                  <div className="mt-1 h-1.5 rounded-full bg-surface-2">
                    <div className="h-1.5 rounded-full bg-accent" style={{ width: `${(n / levelMax) * 100}%` }} />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Tile>

        <Tile icon={<Building2 size={15} />} title="Companies indexed" delay={180}>
          {stats === null && <Skeleton className="h-32" />}
          {stats === "error" && <Unavailable />}
          {s && (
            <div className="flex flex-wrap gap-1.5">
              {companies.map((c) => (
                <span key={c.company} className="rounded-full border border-line bg-surface-2 px-2.5 py-1 text-xs">
                  {c.company} <span className="font-mono text-muted">{c.jobs}</span>
                </span>
              ))}
            </div>
          )}
        </Tile>

        <Tile icon={<Gauge size={15} />} title="Every match explains itself" delay={240}>
          {stats === null && <Skeleton className="h-24" />}
          {stats === "error" && <Unavailable />}
          {s && (
            <div>
              <p className="text-sm text-muted">Each score is a weighted sum you can inspect:</p>
              <div className="mt-3 flex h-3 overflow-hidden rounded-full">
                {Object.entries(weights).map(([t, w]) => (
                  <div key={t} className={TERM_META[t as keyof typeof TERM_META]?.className} style={{ width: `${w * 100}%` }} />
                ))}
              </div>
              <ul className="mt-3 grid grid-cols-2 gap-1 text-xs">
                {Object.entries(weights).map(([t, w]) => (
                  <li key={t} className="flex items-center gap-1.5">
                    <span className={cx("h-2 w-2 rounded-full", TERM_META[t as keyof typeof TERM_META]?.className)} aria-hidden />
                    {TERM_META[t as keyof typeof TERM_META]?.label ?? t} <span className="font-mono text-muted">{Math.round(w * 100)}%</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Tile>

        <Tile icon={<TrendingUp size={15} />} title="Real postings only" delay={300}>
          <ul className="space-y-2 text-sm">
            <li>From the official public <b>Greenhouse</b> and <b>Lever</b> job-board APIs.</li>
            <li>Synced nightly; closed postings drop out automatically.</li>
            <li>Every result links to the original posting to apply.</li>
            <li>No accounts, no resumes.</li>
          </ul>
        </Tile>
      </div>
    </section>
  );
}
