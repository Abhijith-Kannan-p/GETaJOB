// Anonymous session + server-side memory + agent streaming.
// The token is random and lives only in this browser; the server stores a hash of it. "Forget me" deletes
// the server copy (profile, plan, messages) and this token.
import { API_URL, ApiError, type ProfileIn } from "./api";

const TOKEN_KEY = "getajob.session.v1";

export function readToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}
function writeToken(t: string | null) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage blocked: the session lasts for this page only */
  }
}

let memoryToken: string | null = null;
const current = () => readToken() ?? memoryToken;

async function req<T>(method: string, path: string, body?: unknown, token?: string | null): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      method,
      headers: {
        Accept: "application/json",
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(token ? { "X-Session-Token": token } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Can't reach the server. If it was asleep, try again in a few seconds.");
  }
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    const msg = typeof b.detail === "string" ? b.detail : b.message;
    throw new ApiError(res.status, msg || "Request failed.");
  }
  return res.json() as Promise<T>;
}

export type PlanItem = { id: number; skill: string; status: "todo" | "learning" | "done"; source: string; added_at: string };
export type Claim = { text: string; facts: string[] };
export type Narrative = {
  headline: Claim | null;
  points: Claim[];
  next_steps: { skill: string; why: string; facts: string[] }[];
};
export type StoredAnswer = { narrative: Narrative; facts: Record<string, string>; source: "llm" | "template" };
export type Me = {
  profile: ProfileIn | null;
  plan: PlanItem[];
  messages: { role: "user" | "assistant"; content: Record<string, unknown>; at: string }[];
};

export type AgentEvent =
  | { type: "start"; mode: "llm" | "offline"; max_llm_calls: number }
  | { type: "thinking"; round: number | "narrative" }
  | { type: "tool_call"; name: string; args: Record<string, unknown>; by: "model" | "rules" }
  | { type: "tool_result"; name: string; facts?: { id: string; text: string }[]; error?: string }
  | { type: "notice"; message: string }
  | {
      type: "narrative";
      source: "llm" | "template";
      narrative: Narrative;
      facts: Record<string, string>;
      grounding: { kept: number; dropped: { text: string; reason: string }[] };
      degraded: string | null;
      disclaimer: string;
    }
  | { type: "done"; llm_calls: number; ms: number }
  | { type: "error"; message: string };

async function ensureToken(): Promise<string> {
  const t = current();
  if (t) return t;
  const { token } = await req<{ token: string }>("POST", "/api/session");
  memoryToken = token;
  writeToken(token);
  return token;
}

/** Run fn with a valid session; if the server forgot it (expired / deleted), start a new one once. */
async function withSession<T>(fn: (t: string) => Promise<T>): Promise<T> {
  const t = await ensureToken();
  try {
    return await fn(t);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      writeToken(null);
      memoryToken = null;
      return fn(await ensureToken());
    }
    throw e;
  }
}

export const meApi = {
  hasSession: () => current() !== null,
  /** Load memory if a session exists; never creates one (nothing is stored until you use the assistant or plan). */
  load: async (): Promise<Me | null> => {
    const t = current();
    if (!t) return null;
    try {
      return await req<Me>("GET", "/api/me", undefined, t);
    } catch (e) {
      if (e instanceof ApiError && (e.status === 404 || e.status === 401)) {
        writeToken(null);
        memoryToken = null;
        return null;
      }
      throw e;
    }
  },
  saveProfile: (p: ProfileIn) => withSession((t) => req<{ ok: true }>("PUT", "/api/me/profile", p, t)),
  addPlan: (p: ProfileIn, skill: string, source: "user" | "agent" | "why_not_me") =>
    withSession(async (t) => {
      await req("PUT", "/api/me/profile", p, t);
      return req<{ plan: PlanItem[] }>("POST", "/api/me/plan", { skill, source }, t);
    }),
  setStatus: (id: number, status: PlanItem["status"]) =>
    withSession((t) =>
      req<{ plan: PlanItem[]; added_to_profile: boolean; profile: ProfileIn | null }>(
        "PATCH",
        `/api/me/plan/${id}`,
        { status },
        t,
      ),
    ),
  removePlan: (id: number) => withSession((t) => req<{ plan: PlanItem[] }>("DELETE", `/api/me/plan/${id}`, undefined, t)),
  forget: async () => {
    const t = current();
    if (t) await req("DELETE", "/api/me", undefined, t);
    writeToken(null);
    memoryToken = null;
  },
};

/** POST + read the SSE stream (EventSource can't POST). Calls onEvent for each event in order. */
export async function runAgent(
  profile: ProfileIn,
  body: { message: string; job_id?: number | null },
  onEvent: (e: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  await meApi.saveProfile(profile);
  const t = await ensureToken();
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/agent/run`, {
      method: "POST",
      signal,
      headers: { "Content-Type": "application/json", Accept: "text/event-stream", "X-Session-Token": t },
      body: JSON.stringify(body),
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "Can't reach the server. If it was asleep, try again in a few seconds.");
  }
  if (!res.ok || !res.body) {
    const b = await res.json().catch(() => ({}));
    const msg = typeof b.detail === "string" ? b.detail : b.message;
    throw new ApiError(res.status, msg || "The assistant could not start.");
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const data = block
        .split("\n")
        .filter((l) => l.startsWith("data: "))
        .map((l) => l.slice(6))
        .join("\n");
      if (data) onEvent(JSON.parse(data) as AgentEvent);
    }
  }
}
