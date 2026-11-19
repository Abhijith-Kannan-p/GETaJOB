"use client";

// Slice-3 matching UI: profile -> ranked jobs with score breakdown -> "Why not me?" gap panel.
// The profile lives in this browser only (localStorage) until slice 4 adds server-side memory.
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  rankApi,
  type Level,
  type ProfileIn,
  type RankResponse,
  type RankedJob,
  type WhyNotResponse,
} from "@/lib/api";
import { GapPanel } from "@/components/gap-panel";
import { ScoreBar, ScoreLegend } from "@/components/score-bar";

const STORE_KEY = "getajob.profile.v1";
const LEVELS: [Level, string][] = [
  ["intern", "Intern"],
  ["new_grad", "New grad"],
  ["entry", "Entry"],
  ["mid", "Mid"],
];
const EMPTY: ProfileIn = {
  skills: [],
  target_roles: [],
  seniority: ["intern", "new_grad", "entry"],
  remote_ok: true,
  locations: [],
  free_text: "",
};

function loadProfile(): ProfileIn {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    return raw ? { ...EMPTY, ...JSON.parse(raw) } : EMPTY;
  } catch {
    return EMPTY;
  }
}

function saveProfile(p: ProfileIn) {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(p));
  } catch {
    /* storage unavailable: the app still works, it just won't remember */
  }
}

function errMsg(e: unknown) {
  return e instanceof ApiError ? e.message : "Something went wrong. Please try again.";
}

export default function AppClient() {
  // Client-only component (see page.tsx), so reading localStorage in the initialiser is safe.
  const [profile, setProfile] = useState<ProfileIn>(loadProfile);
  const [data, setData] = useState<RankResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [gap, setGap] = useState<{ open: boolean; data: WhyNotResponse | null; loading: boolean; error: string | null }>(
    { open: false, data: null, loading: false, error: null },
  );
  const ctrl = useRef<AbortController | null>(null);

  useEffect(() => saveProfile(profile), [profile]);

  const runRank = useCallback(async () => {
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    setLoading(true);
    setError(null);
    try {
      setData(await rankApi.rank(profile, c.signal));
    } catch (e) {
      if ((e as Error).name !== "AbortError") setError(errMsg(e));
    } finally {
      if (ctrl.current === c) setLoading(false);
    }
  }, [profile]);

  const openGap = useCallback(
    async (jobId: number | null) => {
      setGap({ open: true, data: null, loading: true, error: null });
      try {
        setGap({ open: true, data: await rankApi.whyNot(profile, jobId), loading: false, error: null });
      } catch (e) {
        setGap({ open: true, data: null, loading: false, error: errMsg(e) });
      }
    },
    [profile],
  );
  const closeGap = useCallback(() => setGap((g) => ({ ...g, open: false })), []);

  return (
    <div className="mx-auto grid w-full max-w-6xl gap-6 px-4 py-8 lg:grid-cols-[320px_1fr]">
      <aside className="space-y-5">
        <div>
          <Link href="/" className="text-sm text-zinc-500 hover:underline">
            ← Search
          </Link>
          <h1 className="mt-1 text-xl font-semibold">Your profile</h1>
          <p className="text-xs text-zinc-500">Stored only in this browser for now.</p>
        </div>
        <ProfileForm profile={profile} onChange={setProfile} />
        <button
          onClick={runRank}
          disabled={loading}
          className="w-full rounded-xl bg-indigo-600 px-4 py-2.5 font-medium text-white hover:bg-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:outline-none disabled:opacity-50"
        >
          {loading ? "Ranking…" : "Rank jobs for me"}
        </button>
        {data && (
          <button
            onClick={() => openGap(null)}
            className="w-full rounded-xl border border-zinc-300 px-4 py-2 text-sm hover:border-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none dark:border-zinc-700"
          >
            What would move me up overall?
          </button>
        )}
      </aside>

      <main className="min-w-0">
        {data?.degraded === "fulltext_only" && (
          <div role="status" className="mb-4 rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm">
            Semantic matching is unavailable right now, so candidates come from full-text search only.
          </div>
        )}
        {data && data.profile.unknown_skills.length > 0 && (
          <div role="status" className="mb-4 rounded-xl border border-zinc-300 px-4 py-2 text-sm dark:border-zinc-700">
            Not in the skill list, so not used for matching: {data.profile.unknown_skills.join(", ")}
          </div>
        )}
        {error && (
          <div role="alert" className="mb-4 rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm">
            {error}
          </div>
        )}
        {!data && !loading && !error && (
          <div className="rounded-2xl border border-dashed border-zinc-300 p-8 text-center text-sm text-zinc-500 dark:border-zinc-700">
            Add a few skills and a target role, then press <b>Rank jobs for me</b>.
          </div>
        )}
        {loading && !data && (
          <ul className="space-y-3" aria-hidden>
            {[0, 1, 2].map((i) => (
              <li key={i} className="h-28 animate-pulse rounded-2xl bg-zinc-100 dark:bg-zinc-900" />
            ))}
          </ul>
        )}
        {data && (
          <>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm text-zinc-500">
                Top {data.results.length} of {data.pool_size} candidates, ranked by a transparent score.
              </p>
              <ScoreLegend />
            </div>
            <ul className="space-y-3">
              {data.results.map((j) => (
                <RankedCard key={j.id} job={j} onWhyNot={() => openGap(j.id)} />
              ))}
            </ul>
          </>
        )}
      </main>

      {gap.open && <GapPanel data={gap.data} loading={gap.loading} error={gap.error} onClose={closeGap} />}
    </div>
  );
}

function RankedCard({ job, onWhyNot }: { job: RankedJob; onWhyNot: () => void }) {
  return (
    <li className="rounded-2xl border border-zinc-200 p-4 dark:border-zinc-800">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-xs text-zinc-500">
            #{job.rank} · score {job.score.toFixed(2)}
          </p>
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
      <div className="mt-3">
        <ScoreBar breakdown={job.breakdown} score={job.score} />
      </div>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-sm">
        <p className="min-w-0 text-zinc-600 dark:text-zinc-300">
          {job.matched_skills.length > 0 && <>You have: {job.matched_skills.slice(0, 6).join(", ")}. </>}
          {job.missing_required.length > 0 && (
            <span className="text-zinc-500">Missing: {job.missing_required.slice(0, 4).join(", ")}</span>
          )}
        </p>
        <button
          onClick={onWhyNot}
          className="rounded-lg px-3 py-1 text-sm font-medium text-indigo-600 hover:bg-indigo-500/10 focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none dark:text-indigo-400"
        >
          Why not me?
        </button>
      </div>
      <p className="mt-1 text-xs text-zinc-500">{job.attribution}</p>
    </li>
  );
}

function ChipInput({
  label,
  values,
  onChange,
  suggest,
  placeholder,
  max,
}: {
  label: string;
  values: string[];
  onChange: (v: string[]) => void;
  suggest?: (q: string) => Promise<string[]>;
  placeholder: string;
  max: number;
}) {
  const [text, setText] = useState("");
  const [opts, setOpts] = useState<string[]>([]);
  const id = `chip-${label.replace(/\s/g, "-").toLowerCase()}`;

  useEffect(() => {
    if (!suggest || text.trim().length < 1) return;
    let live = true;
    const t = setTimeout(() => {
      suggest(text.trim())
        .then((s) => live && setOpts(s))
        .catch(() => live && setOpts([]));
    }, 200);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [text, suggest]);
  // derived, not stored: no suggestions for empty input, never suggest a chip already added
  const shown = suggest && text.trim() ? opts.filter((x) => !values.includes(x)) : [];

  const add = (v: string) => {
    const x = v.trim().slice(0, 60);
    if (x && !values.includes(x) && values.length < max) onChange([...values, x]);
    setText("");
  };

  return (
    <div>
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <div className="mt-1 flex flex-wrap gap-1.5">
        {values.map((v) => (
          <button
            key={v}
            type="button"
            onClick={() => onChange(values.filter((x) => x !== v))}
            className="rounded-full border border-zinc-300 px-2.5 py-0.5 text-sm hover:border-red-400 focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none dark:border-zinc-700"
            aria-label={`Remove ${v}`}
          >
            {v} ×
          </button>
        ))}
      </div>
      <input
        id={id}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            add(shown[0] && shown[0].toLowerCase().startsWith(text.trim().toLowerCase()) ? shown[0] : text);
          }
        }}
        placeholder={placeholder}
        maxLength={60}
        className="mt-1.5 w-full rounded-xl border border-zinc-300 bg-transparent px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 dark:border-zinc-700"
      />
      {shown.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1">
          {shown.map((o) => (
            <button
              key={o}
              type="button"
              onClick={() => add(o)}
              className="rounded-full bg-zinc-100 px-2 py-0.5 text-xs hover:bg-indigo-500/10 focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none dark:bg-zinc-900"
            >
              + {o}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

const suggestSkills = (q: string) => rankApi.skills(q).then((r) => r.skills);

function ProfileForm({ profile, onChange }: { profile: ProfileIn; onChange: (p: ProfileIn) => void }) {
  const set = <K extends keyof ProfileIn>(k: K, v: ProfileIn[K]) => onChange({ ...profile, [k]: v });
  const toggleLevel = (l: Level) =>
    set("seniority", profile.seniority.includes(l) ? profile.seniority.filter((x) => x !== l) : [...profile.seniority, l]);
  return (
    <div className="space-y-4">
      <ChipInput
        label="Skills"
        values={profile.skills}
        onChange={(v) => set("skills", v)}
        suggest={suggestSkills}
        placeholder="Type a skill, press Enter"
        max={50}
      />
      <ChipInput
        label="Target roles"
        values={profile.target_roles}
        onChange={(v) => set("target_roles", v)}
        placeholder="e.g. backend engineer intern"
        max={5}
      />
      <fieldset>
        <legend className="text-sm font-medium">Seniority</legend>
        <div className="mt-1 flex flex-wrap gap-1.5">
          {LEVELS.map(([l, name]) => (
            <button
              key={l}
              type="button"
              aria-pressed={profile.seniority.includes(l)}
              onClick={() => toggleLevel(l)}
              className="rounded-full border border-zinc-300 px-3 py-1 text-sm aria-pressed:border-indigo-500 aria-pressed:bg-indigo-500/10 focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none dark:border-zinc-700"
            >
              {name}
            </button>
          ))}
        </div>
      </fieldset>
      <ChipInput
        label="Preferred locations"
        values={profile.locations}
        onChange={(v) => set("locations", v)}
        placeholder="e.g. India, Bengaluru"
        max={5}
      />
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={profile.remote_ok} onChange={(e) => set("remote_ok", e.target.checked)} />
        Remote is fine
      </label>
      <div>
        <label htmlFor="free" className="text-sm font-medium">
          Anything else? <span className="font-normal text-zinc-500">(optional)</span>
        </label>
        <textarea
          id="free"
          value={profile.free_text}
          onChange={(e) => set("free_text", e.target.value.slice(0, 300))}
          rows={3}
          maxLength={300}
          placeholder="e.g. interested in data pipelines and developer tools"
          className="mt-1 w-full rounded-xl border border-zinc-300 bg-transparent px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 dark:border-zinc-700"
        />
        <p className="text-xs text-zinc-500">
          {profile.free_text.length}/300 · Don&apos;t paste personal info: this text is sent to a free-tier AI
          provider for matching.
        </p>
      </div>
    </div>
  );
}
