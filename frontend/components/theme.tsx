"use client";

// Light (cream) / dark (navy) theme. next-themes sets the .dark class on <html> before paint (no flash),
// follows the OS until the user picks, and remembers the pick in localStorage.
import { ThemeProvider, useTheme } from "next-themes";
import { Moon, Sun } from "lucide-react";
import type { ReactNode } from "react";
import { cx } from "./ui";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      {children}
    </ThemeProvider>
  );
}

/** Icons switch by CSS (dark: variant), so server and client render the same markup: no hydration mismatch. */
export function ThemeToggle({ className, onDark = false }: { className?: string; onDark?: boolean }) {
  const { resolvedTheme, setTheme } = useTheme();
  return (
    <button
      type="button"
      onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
      aria-label="Switch between light and dark theme"
      title="Light / dark"
      className={cx(
        "grid h-9 w-9 place-items-center rounded-lg transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2",
        onDark
          ? "glass-dark text-cream/80 hover:text-coral focus-visible:ring-cream"
          : "text-muted hover:bg-surface-2 hover:text-ink focus-visible:ring-ink",
        className,
      )}
    >
      <Moon size={17} className="dark:hidden" aria-hidden />
      <Sun size={17} className="hidden dark:block" aria-hidden />
    </button>
  );
}
