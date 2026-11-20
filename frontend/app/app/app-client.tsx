"use client";

// Matching UI: profile (buttons, not typing) -> ranked jobs with score breakdown -> "Why not me?" panel.
// The profile lives in this browser (localStorage). Using the assistant or the plan also stores a copy on the
// server under an anonymous session id; "Forget me" deletes both.
import { useCallback, useEffect, useRef, useState } from "react";
import {
  BarChart3,
  Bot,
  Briefcase,
  UserRound,
  Brain,
  Building2,
  Cloud,
  Database,
  ExternalLink,
  Layers,
  MapPin,
  Monitor,
  Plus,
  RotateCcw,
  Server,
  Shield,
  Smartphone,
  Sparkles,
  Target,
  Wand2,
  Wrench,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { ApiError, rankApi, type Level, type ProfileIn, type RankResponse, type RankedJob, type WhyNotResponse } from "@/lib/api";
import { meApi, runAgent, type AgentEvent, type PlanItem, type StoredAnswer } from "@/lib/session";
import { AssistantCard } from "@/components/assistant";
import { GapPanel } from "@/components/gap-panel";
import { PlanCard } from "@/components/plan-card";
import { ScoreBar, ScoreLegend } from "@/components/score-bar";
import { Banner, Button, Card, Pill, SectionLabel, Skeleton, Switch, cx } from "@/components/ui";

const STORE_KEY = "getajob.profile.v1";
const ROLES: { label: string; role: string; icon: LucideIcon }[] = [
  { label: "Backend", role: "backend engineer intern", icon: Server },
  { label: "Frontend", role: "frontend engineer intern", icon: Monitor },
  { label: "Full-stack", role: "full stack engineer intern", icon: Layers },
  { label: "Data analyst", role: "data analyst intern", icon: BarChart3 },
  { label: "Data engineer", role: "data engineer intern", icon: Database },
  { label: "ML / AI", role: "machine learning engineer intern", icon: Brain },
  { label: "Cloud / DevOps", role: "devops cloud engineer intern", icon: Cloud },
  { label: "Mobile", role: "mobile developer intern", icon: Smartphone },
  { label: "Security", role: "security engineer intern", icon: Shield },
];
const POPULAR_SKILLS = [
  "Python", "Java", "JavaScript", "TypeScript", "React", "Node.js", "SQL", "Git",
  "Docker", "AWS", "Linux", "C++", "pandas", "Machine Learning", "REST APIs", "Kubernetes",
];
const PLACES = ["India", "Bengaluru", "Hyderabad", "Pune", "Gurugram", "Chennai", "Mumbai"];
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
    /* storage unavailable: still works, just won't remember */
  }
}
const errMsg = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Please try again.");
type Tab = "profile" | "jobs" | "assistant";
const TABS: [Tab, string, LucideIcon][] = [
  ["profile", "Profile", UserRound],
  ["jobs", "Jobs", Briefcase],
  ["assistant", "Assistant", Bot],
];
const toggleIn = <T,>(xs: T[], x: T) => (xs.includes(x) ? xs.filter((y) => y !== x) : [...xs, x]);

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

  // ---- server memory (loaded only if a session already exists; visiting alone stores nothing)
  const [plan, setPlan] = useState<PlanItem[]>([]);
  const [hasSession, setHasSession] = useState(meApi.hasSession);
  const [lastAnswer, setLastAnswer] = useState<StoredAnswer | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [agentError, setAgentError] = useState<string | null>(null);
  const [planNote, setPlanNote] = useState<string | null>(null);
  const assistantRef = useRef<HTMLDivElement>(null);
  const [tab, setTab] = useState<Tab>("profile");
  useEffect(() => {
    meApi
      .load()
      .then((me) => {
        if (!me) return setHasSession(false);
        setPlan(me.plan);
        const last = [...me.messages].reverse().find((m) => m.role === "assistant");
        if (last) setLastAnswer(last.content as unknown as StoredAnswer);
      })
      .catch(() => {});
  }, []);

  const ask = useCallback(
    async (message: string, jobId: number | null = null) => {
      setGap((g) => ({ ...g, open: false }));
      setTab("assistant");
      assistantRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      setEvents([]);
      setAgentError(null);
      setRunning(true);
      try {
        await runAgent(profile, { message, job_id: jobId }, (ev) => {
          setEvents((prev) => [...prev, ev]);
          if (ev.type === "memory") {
            setPlan(ev.plan);
            setProfile((p) => ({ ...p, skills: ev.profile.skills }));
          }
        });
        setHasSession(true);
      } catch (e) {
        setAgentError(errMsg(e));
      } finally {
        setRunning(false);
      }
    },
    [profile],
  );
  const addPlan = useCallback(
    async (skill: string, source: "user" | "agent" | "why_not_me" = "agent") => {
      try {
        setPlan((await meApi.addPlan(profile, skill, source)).plan);
        setHasSession(true);
        setPlanNote(null);
      } catch (e) {
        setPlanNote(errMsg(e));
      }
    },
    [profile],
  );
  const setStatus = useCallback(async (id: number, status: PlanItem["status"]) => {
    try {
      const r = await meApi.setStatus(id, status);
      setPlan(r.plan);
      const item = r.plan.find((p) => p.id === id);
      if (r.added_to_profile && item) {
        setProfile((p) => (p.skills.includes(item.skill) ? p : { ...p, skills: [...p.skills, item.skill] }));
        setPlanNote(`${item.skill} added to your profile. Rank again to see the effect.`);
      }
    } catch (e) {
      setPlanNote(errMsg(e));
    }
  }, []);
  const removePlan = useCallback(async (id: number) => {
    try {
      setPlan((await meApi.removePlan(id)).plan);
    } catch (e) {
      setPlanNote(errMsg(e));
    }
  }, []);
  const forget = useCallback(async () => {
    try {
      await meApi.forget();
      setPlan([]);
      setEvents([]);
      setLastAnswer(null);
      setHasSession(false);
      setData(null);
      setProfile(EMPTY);
      setPlanNote("Deleted. Nothing about you is stored any more.");
    } catch (e) {
      setPlanNote(errMsg(e));
    }
  }, []);
  const plannedSkills = plan.map((p) => p.skill);

  const runRank = useCallback(async () => {
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    setLoading(true);
    setError(null);
    setTab("jobs");
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
  const ready = profile.skills.length > 0 || profile.target_roles.length > 0;
  const onMobile = (t: Tab) => tab !== t && "hidden lg:block"; // below lg only the active tab shows

  return (
    <div className="mx-auto grid w-full max-w-6xl gap-6 px-4 py-6 lg:grid-cols-[360px_1fr] lg:py-8">
      <nav aria-label="Sections" className="glass sticky top-16 z-20 grid grid-cols-3 gap-1 rounded-xl p-1 lg:hidden">
        {TABS.map(([id, label, Icon]) => (
          <button
            key={id}
            type="button"
            aria-pressed={tab === id}
            onClick={() => setTab(id)}
            className={cx(
              "flex items-center justify-center gap-1.5 rounded-lg py-2 text-sm transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink",
              tab === id ? "bg-accent font-medium text-on-accent shadow-sm" : "text-muted hover:text-ink",
            )}
          >
            <Icon size={15} aria-hidden /> {label}
            {id === "jobs" && data && <span className="font-mono text-xs">{data.results.length}</span>}
          </button>
        ))}
      </nav>

      <aside className={cx("lg:sticky lg:top-20 lg:max-h-[calc(100vh-6rem)] lg:overflow-y-auto", onMobile("profile"))}>
        <Card className="p-5">
          <div className="flex items-start justify-between gap-2">
            <div>
              <h1 className="text-lg font-semibold">Your profile</h1>
              <p className="text-xs text-muted">Saved in this browser. No name, email or resume needed.</p>
            </div>
            <button
              onClick={() => setProfile(EMPTY)}
              className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-muted hover:text-bad focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink"
            >
              <RotateCcw size={12} aria-hidden /> Reset
            </button>
          </div>
          <div className="mt-5">
            <ProfileForm profile={profile} onChange={setProfile} />
          </div>
          <Button size="lg" className="mt-6 w-full" onClick={runRank} disabled={loading || !ready}>
            <Target size={18} aria-hidden /> {loading ? "Ranking…" : "Rank jobs for me"}
          </Button>
          {!ready && <p className="mt-2 text-center text-xs text-muted">Pick a role or a few skills first.</p>}
          {data && (
            <Button variant="outline" className="mt-2 w-full" onClick={() => openGap(null)}>
              <Wand2 size={16} aria-hidden /> What would move me up overall?
            </Button>
          )}
        </Card>
        <div className="mt-4">
          <PlanCard
            plan={plan}
            hasSession={hasSession}
            note={planNote}
            onStatus={setStatus}
            onRemove={removePlan}
            onForget={forget}
          />
        </div>
      </aside>

      <section className="min-w-0" aria-live="polite">
        {!ready && tab === "assistant" && (
          <Card className="p-6 text-center text-sm text-muted lg:hidden">Pick a role or a few skills first, then ask.</Card>
        )}
        {ready && (
          <div ref={assistantRef} className={cx("mb-6 scroll-mt-32 lg:scroll-mt-20", onMobile("assistant"))}>
            <AssistantCard
              events={events}
              running={running}
              error={agentError}
              lastAnswer={lastAnswer}
              plannedSkills={plannedSkills}
              onAsk={(m) => ask(m)}
              onAddPlan={(s) => addPlan(s, "agent")}
            />
          </div>
        )}

        <div className={cx(onMobile("jobs"))}>
        <div className="space-y-3">
          {data?.degraded === "fulltext_only" && (
            <Banner tone="warn">Semantic matching is unavailable right now, so candidates come from full-text search only.</Banner>
          )}
          {data && data.profile.unknown_skills.length > 0 && (
            <Banner tone="info">Not in the skill list, so not used for matching: {data.profile.unknown_skills.join(", ")}</Banner>
          )}
          {error && <Banner tone="bad">{error}</Banner>}
        </div>

        {!data && !loading && !error && (
          <Card className="grid place-items-center gap-3 border-dashed p-12 text-center">
            <span className="grid h-12 w-12 place-items-center rounded-2xl bg-accent-soft text-accent-ink">
              <Sparkles size={22} aria-hidden />
            </span>
            <p className="font-medium">Pick a role and your skills, then press Rank.</p>
            <p className="max-w-sm text-sm text-muted">
              You get the best-matching open roles, why each one scores the way it does, and what to learn to move up.
            </p>
          </Card>
        )}
        {loading && !data && (
          <div className="space-y-3">
            {[0, 1, 2].map((i) => <Skeleton key={i} className="h-36 rounded-2xl" />)}
          </div>
        )}
        {data && (
          <>
            <div className="mb-3 mt-1 flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm text-muted">
                Top <b className="text-ink">{data.results.length}</b> of {data.pool_size} candidates
              </p>
              <ScoreLegend />
            </div>
            <ul className={cx("space-y-3 transition-opacity duration-150", loading && "opacity-60")}>
              {data.results.map((j) => (
                <RankedCard key={j.id} job={j} onWhyNot={() => openGap(j.id)} />
              ))}
            </ul>
          </>
        )}
        </div>
      </section>

      {gap.open && (
        <GapPanel
          data={gap.data}
          loading={gap.loading}
          error={gap.error}
          onClose={closeGap}
          onAddPlan={(s) => addPlan(s, "why_not_me")}
          onAsk={(jobId) => ask(jobId ? "Why am I not ranking higher for this job?" : "What should I learn next?", jobId)}
          plannedSkills={plannedSkills}
        />
      )}
    </div>
  );
}

function RankedCard({ job, onWhyNot }: { job: RankedJob; onWhyNot: () => void }) {
  return (
    <li>
      <Card className="p-4 transition-colors duration-150 hover:border-line-strong">
        <div className="flex items-start gap-3">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-accent-soft font-mono text-sm font-semibold text-accent-ink">
            #{job.rank}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 className="font-medium">{job.title}</h2>
                <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-sm text-muted">
                  <span className="inline-flex items-center gap-1">
                    <Building2 size={13} aria-hidden /> {job.company}
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <MapPin size={13} aria-hidden /> {job.location || "Location not listed"}
                  </span>
                  {job.is_remote && <span className="rounded-full bg-accent-soft px-2 text-xs text-accent-ink">Remote</span>}
                  <span className="rounded-full bg-surface-2 px-2 text-xs">{job.seniority.replace("_", " ")}</span>
                </p>
              </div>
              <span className="shrink-0 text-right">
                <span className="block font-mono text-lg font-semibold">{job.score.toFixed(2)}</span>
                <span className="block text-[10px] uppercase tracking-wide text-muted">score</span>
              </span>
            </div>
            <div className="mt-3">
              <ScoreBar breakdown={job.breakdown} score={job.score} />
            </div>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {job.matched_skills.slice(0, 6).map((s) => (
                <span key={s} className="rounded-md bg-good-soft px-2 py-0.5 text-xs text-good-ink">✓ {s}</span>
              ))}
              {job.missing_required.slice(0, 4).map((s) => (
                <span key={s} className="rounded-md bg-surface-2 px-2 py-0.5 text-xs text-muted">{s}</span>
              ))}
            </div>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs text-muted">{job.attribution}</span>
              <div className="flex gap-2">
                <Button variant="ghost" size="sm" onClick={onWhyNot}>
                  <Wand2 size={14} aria-hidden /> Why not me?
                </Button>
                <a
                  href={job.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 rounded-xl border border-line-strong px-3 py-1.5 text-sm hover:border-accent hover:text-accent-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink"
                >
                  Apply <ExternalLink size={13} aria-hidden />
                </a>
              </div>
            </div>
          </div>
        </div>
      </Card>
    </li>
  );
}

/** Free-entry chips with optional taxonomy autocomplete (for anything not covered by the preset buttons). */
function AddChip({ placeholder, onAdd, suggest, exclude }: {
  placeholder: string; onAdd: (v: string) => void; suggest?: (q: string) => Promise<string[]>; exclude: string[];
}) {
  const [text, setText] = useState("");
  const [opts, setOpts] = useState<string[]>([]);
  useEffect(() => {
    if (!suggest || text.trim().length < 1) return;
    let live = true;
    const t = setTimeout(() => {
      suggest(text.trim()).then((s) => live && setOpts(s)).catch(() => live && setOpts([]));
    }, 200);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [text, suggest]);
  const shown = suggest && text.trim() ? opts.filter((x) => !exclude.includes(x)).slice(0, 6) : [];
  const add = (v: string) => {
    const x = v.trim().slice(0, 60);
    if (x) onAdd(x);
    setText("");
  };
  return (
    <div>
      <div className="flex items-center gap-2 rounded-xl border border-line bg-surface px-3 focus-within:border-accent">
        <Plus size={14} className="text-muted" aria-hidden />
        <input
          value={text}
          aria-label={placeholder}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              add(shown[0] && shown[0].toLowerCase().startsWith(text.trim().toLowerCase()) ? shown[0] : text);
            }
          }}
          placeholder={placeholder}
          maxLength={60}
          className="min-w-0 flex-1 bg-transparent py-2 text-sm outline-none placeholder:text-muted"
        />
      </div>
      {shown.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {shown.map((o) => (
            <button
              key={o}
              type="button"
              onClick={() => add(o)}
              className="rounded-full bg-accent-soft px-2.5 py-1 text-xs text-accent-ink hover:bg-accent hover:text-on-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink"
            >
              + {o}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Removable({ items, onRemove }: { items: string[]; onRemove: (v: string) => void }) {
  if (!items.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {items.map((v) => (
        <button
          key={v}
          type="button"
          onClick={() => onRemove(v)}
          aria-label={`Remove ${v}`}
          className="inline-flex items-center gap-1 rounded-full border border-accent bg-accent px-3 py-1.5 text-sm text-ink hover:bg-accent-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink focus-visible:ring-offset-2"
        >
          {v} <X size={12} aria-hidden />
        </button>
      ))}
    </div>
  );
}

const suggestSkills = (q: string) => rankApi.skills(q).then((r) => r.skills);

function ProfileForm({ profile, onChange }: { profile: ProfileIn; onChange: (p: ProfileIn) => void }) {
  const set = <K extends keyof ProfileIn>(k: K, v: ProfileIn[K]) => onChange({ ...profile, [k]: v });
  const presetRoles = new Set(ROLES.map((r) => r.role));
  const customRoles = profile.target_roles.filter((r) => !presetRoles.has(r));
  const lowerSkills = profile.skills.map((s) => s.toLowerCase());
  const hasSkill = (s: string) => lowerSkills.includes(s.toLowerCase());
  const extraSkills = profile.skills.filter((s) => !POPULAR_SKILLS.some((p) => p.toLowerCase() === s.toLowerCase()));
  const customPlaces = profile.locations.filter((l) => !PLACES.includes(l));

  return (
    <div className="space-y-6">
      <div>
        <SectionLabel icon={<Target size={13} aria-hidden />}>I&apos;m looking for</SectionLabel>
        <div className="mt-2 grid grid-cols-3 gap-2">
          {ROLES.map(({ label, role, icon: Icon }) => {
            const on = profile.target_roles.includes(role);
            return (
              <button
                key={role}
                type="button"
                aria-pressed={on}
                disabled={!on && profile.target_roles.length >= 5}
                onClick={() => set("target_roles", toggleIn(profile.target_roles, role))}
                className={cx(
                  "flex flex-col items-center gap-1 rounded-xl border px-2 py-3 text-xs font-medium transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink disabled:opacity-40",
                  on ? "border-accent bg-accent text-on-accent shadow-sm" : "glass hover:border-accent hover:text-accent-ink",
                )}
              >
                <Icon size={18} aria-hidden />
                {label}
              </button>
            );
          })}
        </div>
        <Removable items={customRoles} onRemove={(v) => set("target_roles", profile.target_roles.filter((x) => x !== v))} />
        {profile.target_roles.length < 5 && (
          <div className="mt-2">
            <AddChip
              placeholder="Other role, e.g. SRE intern"
              exclude={profile.target_roles}
              onAdd={(v) => !profile.target_roles.includes(v) && set("target_roles", [...profile.target_roles, v])}
            />
          </div>
        )}
      </div>

      <div>
        <SectionLabel icon={<Wrench size={13} aria-hidden />}>My skills</SectionLabel>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {POPULAR_SKILLS.map((s) => (
            <Pill
              key={s}
              selected={hasSkill(s)}
              onClick={() =>
                set("skills", hasSkill(s) ? profile.skills.filter((x) => x.toLowerCase() !== s.toLowerCase()) : [...profile.skills, s])
              }
            >
              {s}
            </Pill>
          ))}
        </div>
        <Removable items={extraSkills} onRemove={(v) => set("skills", profile.skills.filter((x) => x !== v))} />
        <div className="mt-2">
          <AddChip
            placeholder="Search more skills…"
            suggest={suggestSkills}
            exclude={profile.skills}
            onAdd={(v) => !hasSkill(v) && profile.skills.length < 50 && set("skills", [...profile.skills, v])}
          />
        </div>
      </div>

      <div>
        <SectionLabel>Level</SectionLabel>
        <div className="mt-2 grid grid-cols-4 gap-1 rounded-xl bg-surface-2 p-1">
          {LEVELS.map(([l, name]) => {
            const on = profile.seniority.includes(l);
            return (
              <button
                key={l}
                type="button"
                aria-pressed={on}
                onClick={() => set("seniority", toggleIn(profile.seniority, l))}
                className={cx(
                  "rounded-lg py-1.5 text-sm transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink",
                  on ? "bg-accent font-medium text-on-accent shadow-sm" : "text-muted hover:text-ink",
                )}
              >
                {name}
              </button>
            );
          })}
        </div>
      </div>

      <div>
        <SectionLabel icon={<MapPin size={13} aria-hidden />}>Where</SectionLabel>
        <div className="mt-2">
          <Switch checked={profile.remote_ok} onChange={(v) => set("remote_ok", v)} label="Remote is fine" />
        </div>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {PLACES.map((p) => (
            <Pill
              key={p}
              selected={profile.locations.includes(p)}
              disabled={!profile.locations.includes(p) && profile.locations.length >= 5}
              onClick={() => set("locations", toggleIn(profile.locations, p))}
            >
              {p}
            </Pill>
          ))}
        </div>
        <Removable items={customPlaces} onRemove={(v) => set("locations", profile.locations.filter((x) => x !== v))} />
        {profile.locations.length < 5 && (
          <div className="mt-2">
            <AddChip
              placeholder="Other city or country"
              exclude={profile.locations}
              onAdd={(v) => !profile.locations.includes(v) && set("locations", [...profile.locations, v])}
            />
          </div>
        )}
      </div>

      <div>
        <label htmlFor="free" className="text-xs font-semibold uppercase tracking-wide text-muted">
          Anything else <span className="font-normal normal-case">(optional)</span>
        </label>
        <textarea
          id="free"
          value={profile.free_text}
          onChange={(e) => set("free_text", e.target.value.slice(0, 300))}
          rows={2}
          maxLength={300}
          placeholder="e.g. interested in data pipelines and developer tools"
          className="mt-2 w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm outline-none placeholder:text-muted focus:border-accent"
        />
        <p className="text-xs text-muted">
          {profile.free_text.length}/300 · Don&apos;t paste personal info: this text goes to a free-tier AI provider for matching.
        </p>
      </div>
    </div>
  );
}
