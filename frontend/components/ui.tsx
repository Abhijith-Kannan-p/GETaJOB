// Small shared UI primitives on the design tokens (globals.css). Kept dependency-free on purpose.
import type { ButtonHTMLAttributes, ReactNode } from "react";

const ring = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink focus-visible:ring-offset-2 focus-visible:ring-offset-bg";

export function cx(...xs: (string | false | null | undefined)[]) {
  return xs.filter(Boolean).join(" ");
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cx("glass rounded-[var(--radius-card)]", className)}>
      {children}
    </div>
  );
}

export function Button({
  variant = "primary",
  size = "md",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "outline" | "ghost"; size?: "sm" | "md" | "lg" }) {
  return (
    <button
      {...props}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-xl font-medium transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50",
        ring,
        size === "sm" && "px-3 py-1.5 text-sm",
        size === "md" && "px-4 py-2.5 text-sm",
        size === "lg" && "px-6 py-3 text-base",
        variant === "primary" && "bg-accent text-ink shadow-[0_8px_20px_-8px_rgb(255_107_91/0.7)] hover:bg-accent-strong",
        variant === "outline" && "glass text-ink hover:border-accent hover:text-accent-ink",
        variant === "ghost" && "text-accent-ink hover:bg-accent-soft",
        className,
      )}
    />
  );
}

/** Toggle "pill" button: aria-pressed drives the selected look. */
export function Pill({
  selected,
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { selected: boolean }) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      {...props}
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm transition-colors duration-150",
        ring,
        selected
          ? "border-accent bg-accent text-ink shadow-sm"
          : "border-white/70 bg-white/45 text-ink backdrop-blur hover:border-accent hover:text-accent-ink",
        className,
      )}
    >
      {children}
    </button>
  );
}

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={cx("flex items-center gap-3 rounded-full text-sm", ring)}
    >
      <span
        className={cx(
          "relative inline-flex h-6 w-11 shrink-0 rounded-full transition-colors duration-150",
          checked ? "bg-accent" : "bg-line-strong",
        )}
      >
        <span
          className={cx(
            "absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform duration-150",
            checked ? "translate-x-5" : "translate-x-0.5",
          )}
        />
      </span>
      {label}
    </button>
  );
}

export function SectionLabel({ icon, children }: { icon?: ReactNode; children: ReactNode }) {
  return (
    <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted">
      {icon}
      {children}
    </p>
  );
}

export function Banner({ tone, children }: { tone: "warn" | "bad" | "info"; children: ReactNode }) {
  return (
    <div
      role={tone === "bad" ? "alert" : "status"}
      className={cx(
        "rounded-xl border px-4 py-2.5 text-sm",
        tone === "warn" && "border-amber-300 bg-amber-50 text-amber-900",
        tone === "bad" && "border-red-300 bg-red-50 text-red-900",
        tone === "info" && "glass text-ink",
      )}
    >
      {children}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("animate-pulse rounded-lg bg-surface-2", className)} aria-hidden />;
}
