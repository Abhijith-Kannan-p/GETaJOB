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
