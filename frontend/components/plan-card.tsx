"use client";

// Learning plan (server memory) + "Forget me".
import { useState } from "react";
import { BookOpen, Trash2, X } from "lucide-react";
import type { PlanItem } from "@/lib/session";
import { Button, Card, SectionLabel, cx } from "./ui";

const STATUSES: [PlanItem["status"], string][] = [
  ["todo", "To do"],
  ["learning", "Learning"],
  ["done", "Done"],
];

export function PlanCard({
  plan,
  hasSession,
  note,
  onStatus,
  onRemove,
  onForget,
}: {
  plan: PlanItem[];
  hasSession: boolean;
  note: string | null;
  onStatus: (id: number, s: PlanItem["status"]) => void;
  onRemove: (id: number) => void;
  onForget: () => Promise<void>;
}) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <Card className="p-5">
      <SectionLabel icon={<BookOpen size={13} aria-hidden />}>My learning plan</SectionLabel>
      {plan.length === 0 ? (
        <p className="mt-2 text-sm text-muted">Add skills from the assistant&apos;s next steps or the Why-Not-Me panel.</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {plan.map((p) => (
            <li key={p.id} className="rounded-xl border border-line bg-white/45 p-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className={cx("text-sm font-medium", p.status === "done" && "text-muted line-through")}>{p.skill}</span>
                <button
                  type="button"
                  onClick={() => onRemove(p.id)}
                  aria-label={`Remove ${p.skill} from plan`}
                  className="rounded-md p-1 text-muted hover:text-bad focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink"
                >
                  <X size={14} aria-hidden />
                </button>
              </div>
              <div className="mt-2 grid grid-cols-3 gap-1 rounded-lg bg-surface-2 p-0.5" role="group" aria-label={`${p.skill} status`}>
                {STATUSES.map(([s, label]) => (
                  <button
                    key={s}
                    type="button"
                    aria-pressed={p.status === s}
                    onClick={() => p.status !== s && onStatus(p.id, s)}
                    className={cx(
                      "rounded-md py-1 text-xs transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink",
                      p.status === s ? (s === "done" ? "bg-good text-white" : "bg-white/85 font-medium text-ink shadow-sm") : "text-muted hover:text-ink",
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </li>
          ))}
        </ul>
      )}
      {note && <p className="mt-2 text-xs text-good" role="status">{note}</p>}

      <div className="mt-4 border-t border-line pt-3">
        {!confirm ? (
          <button
            type="button"
            onClick={() => setConfirm(true)}
            disabled={!hasSession}
            className="inline-flex items-center gap-1.5 text-xs text-muted hover:text-bad focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink disabled:opacity-50"
          >
            <Trash2 size={13} aria-hidden /> Forget me
          </button>
        ) : (
          <div className="space-y-2">
            <p className="text-xs">Delete your saved profile, plan and assistant history from the server and this browser?</p>
            <div className="flex gap-2">
              <button
                type="button"
                className="rounded-xl bg-bad px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink disabled:opacity-50"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await onForget();
                  } finally {
                    setBusy(false);
                    setConfirm(false);
                  }
                }}
              >
                {busy ? "Deleting…" : "Delete everything"}
              </button>
              <Button size="sm" variant="ghost" onClick={() => setConfirm(false)}>
                Cancel
              </Button>
            </div>
          </div>
        )}
        {!hasSession && <p className="mt-1 text-[11px] text-muted">Nothing is stored on the server yet.</p>}
      </div>
    </Card>
  );
}
