import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ChevronRight } from "lucide-react";
import type { Finding } from "@/lib/types";
import { EvidenceItem } from "./EvidenceItem";
import { usePrefersReducedMotion } from "@/hooks/use-reduced-motion";

export function FindingCard({ finding }: { finding: Finding }) {
  const [open, setOpen] = useState(false);
  const reduced = usePrefersReducedMotion();
  const count = finding.evidence.length;
  return (
    <div className="rounded-md border border-border border-l-2 border-l-priority-p0 bg-card shadow-[var(--shadow-card)]">
      <div className="p-5">
        <p className="text-[15px] font-medium leading-snug tracking-tight text-foreground">
          {finding.claim}
        </p>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="mt-3.5 inline-flex items-center gap-1 text-[12px] font-medium text-muted-foreground transition-colors hover:text-foreground"
          aria-expanded={open}
        >
          <motion.span
            animate={{ rotate: open ? 90 : 0 }}
            transition={{ duration: reduced ? 0 : 0.18, ease: "easeOut" }}
            className="inline-flex"
          >
            <ChevronRight size={14} />
          </motion.span>
          Evidence ({count} {count === 1 ? "review" : "reviews"})
        </button>
      </div>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: reduced ? 0 : 0.28, ease: [0.22, 0.61, 0.36, 1] }}
            className="overflow-hidden"
          >
            <motion.div
              className="space-y-3 border-t border-border bg-surface/60 px-5 py-4"
              initial="hidden"
              animate="show"
              variants={{
                hidden: {},
                show: { transition: { staggerChildren: reduced ? 0 : 0.05 } },
              }}
            >
              {finding.evidence.map((e) => (
                <motion.div
                  key={e.id}
                  variants={{
                    hidden: { opacity: 0, y: 6 },
                    show: { opacity: 1, y: 0 },
                  }}
                  transition={{ duration: 0.2, ease: "easeOut" }}
                >
                  <EvidenceItem evidence={e} />
                </motion.div>
              ))}
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
