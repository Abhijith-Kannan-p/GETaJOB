// Typed client for the GETaJOB backend. Base URL from NEXT_PUBLIC_API_URL (defaults to local uvicorn).
// 127.0.0.1, not "localhost": on Windows the browser tries IPv6 ::1 first, where another process
// (e.g. a Docker container publishing the same port) can answer instead of uvicorn.
export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export type Retrieval = {
  rrf: number;
  semantic_sim: number | null;
  keyword: number | null;
  vec_rank: number | null;
  fts_rank: number | null;
};

export type Job = {
  id: number;
  title: string;
  company: string;
  location: string | null;
  is_remote: boolean;
  seniority: string;
  required_skills: string[];
  preferred_skills: string[];
  url: string;
  posted_at: string | null;
  source: "greenhouse" | "lever";
  attribution: string;
  snippet?: string;
  retrieval?: Retrieval;
};

export type SearchResponse = {
  query: string;
  degraded: "fulltext_only" | null;
  took_ms: number;
  results: Job[];
};

export type Stats = {
  jobs_indexed: number;
  jobs_active: number;
  new_24h: number;
  companies: number;
  embedding_coverage: number;
  last_sync: string | null;
  last_sync_mode: string | null;
  semantic_search: boolean;
  by_seniority: Record<string, number>;
  top_companies: { company: string; jobs: number }[];
  ranking_weights: Record<string, number>;
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { signal, headers: { Accept: "application/json" } });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    // Render free tier sleeps when idle; the first request can take ~30-60 s to wake it.
    throw new ApiError(0, "Can't reach the server. If it was asleep, try again in a few seconds.");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, body.message || (res.status === 429 ? "Too many requests." : "Request failed."));
  }
  return res.json() as Promise<T>;
}

export const api = {
  search: (q: string, opts: { remote?: boolean; seniority?: string[] } = {}, signal?: AbortSignal) => {
    const p = new URLSearchParams({ q, limit: "20" });
    if (opts.remote) p.set("remote", "true");
    if (opts.seniority?.length) p.set("seniority", opts.seniority.join(","));
    return get<SearchResponse>(`/api/search?${p}`, signal);
  },
  stats: (signal?: AbortSignal) => get<Stats>("/api/stats", signal),
};

// ---------- ranking + Why-Not-Me ----------
export type Level = "intern" | "new_grad" | "entry" | "mid" | "senior" | "unknown";
export type ProfileIn = {
  skills: string[];
  target_roles: string[];
  seniority: Level[];
  remote_ok: boolean;
  locations: string[];
  free_text: string;
};
export type Term = "semantic" | "keyword" | "skills" | "seniority" | "location" | "recency";
export type Breakdown = Record<Term, { value: number; weight: number; contribution: number }>;
export type RankedJob = Job & {
  rank: number;
  score: number;
  breakdown: Breakdown;
  matched_skills: string[];
  missing_required: string[];
};
type Meta = {
  degraded: "fulltext_only" | null;
  pool_size: number;
  profile: { skills: string[]; unknown_skills: string[] };
};
export type RankResponse = Meta & { weights: Record<Term, number>; results: RankedJob[] };
export type Effect = {
  skill: string;
  jobs_with_skill: number;
  entering_top10: number;
  entering_top20: number;
  mean_rank_change: number;
  rr_gain: number;
  effort_tier: string;
  effort_hours: number;
  gain_per_hour: number;
  job_rank: { before: number; after: number } | null;
};
export type Plan = {
  steps: { step: number; skill: string; effort_tier: string; effort_hours: number; effect: Effect }[];
  cumulative: {
    affected_jobs: number;
    median_rank_before: number | null;
    median_rank_after: number | null;
    entering_top10: number;
    entering_top20: number;
    total_hours: number;
    job_rank: { before: number; after: number } | null;
  };
};
export type WhyNotResponse = Meta & {
  disclaimer: string;
  effort_note: string;
  job?: Job;
  rank?: number;
  score?: number;
  breakdown?: Breakdown;
  matched?: string[];
  missing?: { skill: string; kind: "required" | "preferred"; weight: number; share: number }[];
  fixes: Effect[];
  plan: Plan;
};

async function post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      method: "POST",
      signal,
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "Can't reach the server. If it was asleep, try again in a few seconds.");
  }
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    const msg = typeof b.detail === "string" ? b.detail : b.message;
    throw new ApiError(res.status, msg || (res.status === 429 ? "Too many requests." : "Request failed."));
  }
  return res.json() as Promise<T>;
}

export const rankApi = {
  rank: (profile: ProfileIn, signal?: AbortSignal) => post<RankResponse>("/api/rank", { profile, limit: 20 }, signal),
  whyNot: (profile: ProfileIn, job_id: number | null, signal?: AbortSignal) =>
    post<WhyNotResponse>("/api/why-not-me", { profile, job_id }, signal),
  skills: (q: string, signal?: AbortSignal) =>
    get<{ skills: string[] }>(`/api/skills?q=${encodeURIComponent(q)}&limit=8`, signal),
};
