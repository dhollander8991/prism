import { Star } from "lucide-react";
import type { Evidence } from "@/lib/types";

function Stars({ n }: { n: number }) {
  return (
    <span className="inline-flex items-center gap-0.5" aria-label={`${n} of 5 stars`}>
      {Array.from({ length: 5 }).map((_, i) => (
        <Star
          key={i}
          size={12}
          strokeWidth={1.5}
          className={i < n ? "fill-star text-star" : "fill-transparent text-border-strong"}
        />
      ))}
    </span>
  );
}

export function EvidenceItem({ evidence }: { evidence: Evidence }) {
  const weighty = evidence.stars === 1;
  return (
    <blockquote
      className={`rounded-r-md border-l-2 bg-muted/50 py-2.5 pl-3.5 pr-3 ${
        weighty ? "border-l-priority-p0/70 bg-priority-p0/[0.04]" : "border-l-border-strong"
      }`}
    >
      <div className="mb-1.5 flex items-center gap-2">
        <Stars n={evidence.stars} />
        <span className="inline-flex h-4 items-center rounded-sm border border-border bg-card px-1 text-[9.5px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">
          {evidence.country}
        </span>
      </div>
      <p className="text-[13px] leading-[1.55] text-foreground/90">{evidence.text}</p>
    </blockquote>
  );
}
