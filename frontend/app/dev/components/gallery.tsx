"use client";

import { useState } from "react";
import { Plus, Sparkles } from "lucide-react";
import type { Breakdown } from "@/lib/api";
import { BreakdownTable, ScoreBar, ScoreLegend } from "@/components/score-bar";
import { Banner, Button, Card, Pill, SectionLabel, Skeleton, Switch } from "@/components/ui";

const TOKENS = [
  "bg", "surface", "surface-2", "line", "line-strong", "ink", "muted", "accent", "accent-strong", "accent-soft",
  "accent-ink", "on-accent", "raised", "raised-strong", "good", "good-soft", "warn", "warn-soft", "bad", "bad-soft",
];
const SAMPLE: Breakdown = {
  semantic: { value: 0.8, weight: 0.35, contribution: 0.28 },
  keyword: { value: 0.5, weight: 0.1, contribution: 0.05 },
  skills: { value: 0.6, weight: 0.3, contribution: 0.18 },
  seniority: { value: 1, weight: 0.15, contribution: 0.15 },
  location: { value: 1, weight: 0.05, contribution: 0.05 },
  recency: { value: 0.4, weight: 0.05, contribution: 0.02 },
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-10">
      <SectionLabel>{title}</SectionLabel>
      <div className="mt-3">{children}</div>
    </section>
  );
}

export function Gallery() {
  const [on, setOn] = useState(true);
  const [picked, setPicked] = useState<string[]>(["Python"]);
  return (
    <>
      <Section title="Colour tokens">
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-5">
          {TOKENS.map((t) => (
            <li key={t} className="text-xs">
              <span className="block h-12 rounded-xl border border-line" style={{ background: `var(--color-${t})` }} />
              <code className="mt-1 block font-mono">{t}</code>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Buttons">
        <div className="flex flex-wrap items-center gap-3">
          <Button>Primary</Button>
          <Button variant="outline">Outline</Button>
          <Button variant="ghost">Ghost</Button>
          <Button size="sm"><Plus size={14} aria-hidden /> Small</Button>
          <Button size="lg"><Sparkles size={18} aria-hidden /> Large</Button>
          <Button disabled>Disabled</Button>
        </div>
      </Section>

      <Section title="Pills and switch">
        <div className="flex flex-wrap items-center gap-2">
          {["Python", "SQL", "React", "Docker"].map((s) => (
            <Pill key={s} selected={picked.includes(s)} onClick={() => setPicked((p) => (p.includes(s) ? p.filter((x) => x !== s) : [...p, s]))}>
              {s}
            </Pill>
          ))}
          <span className="mx-2" />
          <Switch checked={on} onChange={setOn} label="Remote OK" />
        </div>
      </Section>

      <Section title="Banners">
        <div className="space-y-2">
          <Banner tone="info">Info: neutral context.</Banner>
          <Banner tone="warn">Warning: degraded mode, e.g. full-text search only.</Banner>
          <Banner tone="bad">Error: something failed; the message never shows raw errors.</Banner>
        </div>
      </Section>

      <Section title="Cards, skeletons, score bar">
        <div className="grid gap-4 md:grid-cols-2">
          <Card className="p-5">
            <p className="font-medium">Glass card</p>
            <div className="mt-3"><ScoreBar breakdown={SAMPLE} score={0.73} /></div>
            <div className="mt-2"><ScoreLegend /></div>
            <div className="mt-3"><BreakdownTable breakdown={SAMPLE} /></div>
          </Card>
          <Card className="space-y-3 p-5">
            <p className="font-medium">Loading state</p>
            <Skeleton className="h-6 w-2/3" />
            <Skeleton className="h-4" />
            <Skeleton className="h-4 w-5/6" />
            <Skeleton className="h-24" />
          </Card>
        </div>
      </Section>
    </>
  );
}
