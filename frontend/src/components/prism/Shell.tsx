import { useEffect, useState, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { Moon, Sun } from "lucide-react";
import { headerStats } from "@/lib/mockData";

function ThemeToggle() {
  const [isDark, setIsDark] = useState(false);
  useEffect(() => {
    const stored = localStorage.getItem("prism-theme");
    const dark = stored === "dark";
    setIsDark(dark);
    document.documentElement.classList.toggle("dark", dark);
  }, []);
  const toggle = () => {
    const next = !isDark;
    setIsDark(next);
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("prism-theme", next ? "dark" : "light");
  };
  return (
    <button
      type="button"
      onClick={toggle}
      className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-border bg-card text-muted-foreground transition-colors hover:text-foreground"
      aria-label="Toggle theme"
    >
      {isDark ? <Sun size={13} /> : <Moon size={13} />}
    </button>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b border-border bg-card/70 backdrop-blur">
        <div className="mx-auto flex max-w-[1200px] items-center justify-between px-6 py-3.5">
          <div className="flex items-baseline gap-4">
            <Link
              to="/"
              className="text-[13px] font-semibold uppercase tracking-[0.12em] text-foreground"
            >
              PRISM
            </Link>
            <span className="text-[11.5px] text-muted-foreground">
              <span className="tabular-nums">
                {headerStats.totalReviews.toLocaleString()}
              </span>{" "}
              reviews ·{" "}
              <span className="tabular-nums">{headerStats.totalThemes}</span> themes ·{" "}
              {headerStats.product} · last synced {headerStats.lastSynced}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <nav className="flex items-center gap-1 text-xs">
              <Link
                to="/"
                className="rounded-md px-2.5 py-1 text-muted-foreground transition-colors hover:text-foreground"
                activeOptions={{ exact: true }}
                activeProps={{ className: "text-foreground bg-muted" }}
              >
                Insights
              </Link>
              <Link
                to="/themes"
                className="rounded-md px-2.5 py-1 text-muted-foreground transition-colors hover:text-foreground"
                activeProps={{ className: "text-foreground bg-muted" }}
              >
                Themes
              </Link>
            </nav>
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1200px] px-6 py-8">{children}</main>
    </div>
  );
}
