import { Link } from "@tanstack/react-router";
import { Droplet, Menu, Search, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";

const nav = [
  { to: "/", label: "Home" },
  { to: "/about", label: "About" },
  { to: "/find", label: "Find Blood Banks" },
  { to: "/developers", label: "Developers" },
] as const;
const legal = [
  { to: "/privacy", label: "Privacy" },
  { to: "/cookies", label: "Cookies" },
  { to: "/terms", label: "Terms" },
] as const;

export function SiteHeader() {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <header className="sticky top-0 z-40 border-b border-border bg-white/95 backdrop-blur-sm">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-3 px-4 py-3 sm:px-7">
        <Link
          to="/"
          className="flex shrink-0 items-center gap-2.5 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label="BloodSignal home"
          onClick={() => setMenuOpen(false)}
        >
          <span className="grid size-10 place-items-center rounded-full bg-primary text-primary-foreground">
            <Droplet className="size-6" aria-hidden="true" />
          </span>
          <span>
            <span className="block text-base font-extrabold tracking-tight text-[#102544]">
              Blood<span className="text-primary">Signal</span>
            </span>
            <span className="block text-[10px] leading-tight text-muted-foreground">
              AI Blood Availability Finder
            </span>
          </span>
        </Link>
        <nav
          aria-label="Main navigation"
          className="hidden items-center gap-6 text-xs font-semibold text-[#102544] md:flex"
        >
          {nav.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              className="rounded-sm py-2 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              activeProps={{ className: "text-primary underline decoration-2 underline-offset-8" }}
              activeOptions={{ exact: true }}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="flex items-center gap-2">
          <Button
            asChild
            className="hidden h-10 rounded-md bg-primary px-4 text-white hover:bg-primary/90 sm:inline-flex"
          >
            <Link to="/find">
              <Search className="size-4" /> Search
            </Link>
          </Button>
          <button
            type="button"
            aria-label={menuOpen ? "Close navigation menu" : "Open navigation menu"}
            aria-expanded={menuOpen}
            aria-controls="mobile-navigation"
            onClick={() => setMenuOpen((open) => !open)}
            className="inline-flex size-10 items-center justify-center rounded-md border border-border bg-white text-[#102544] hover:bg-secondary md:hidden"
          >
            {menuOpen ? <X className="size-5" /> : <Menu className="size-5" />}
          </button>
        </div>
      </div>
      {menuOpen && (
        <nav
          id="mobile-navigation"
          aria-label="Mobile navigation"
          className="border-t border-border bg-white px-4 py-3 shadow-sm md:hidden"
        >
          <div className="mx-auto flex max-w-7xl flex-col gap-1">
            {nav.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                onClick={() => setMenuOpen(false)}
                className="rounded-md px-3 py-3 text-sm font-semibold text-[#102544] hover:bg-secondary"
                activeProps={{ className: "bg-secondary text-primary" }}
                activeOptions={{ exact: true }}
              >
                {item.label}
              </Link>
            ))}
            <Link
              to="/find"
              onClick={() => setMenuOpen(false)}
              className="mt-1 flex items-center justify-center gap-2 rounded-md bg-primary px-3 py-3 text-sm font-semibold text-white"
            >
              <Search className="size-4" /> Find Blood Banks
            </Link>
          </div>
        </nav>
      )}
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="border-t border-[#203957] bg-[#0b1e38] text-white">
      <div className="mx-auto flex max-w-7xl flex-col gap-5 px-4 py-7 sm:px-7">
        <div className="flex flex-col justify-between gap-5 md:flex-row md:items-center">
          <Link to="/" className="flex items-center gap-2.5">
            <span className="grid size-9 place-items-center rounded-full bg-white text-primary">
              <Droplet className="size-5" />
            </span>
            <span>
              <span className="block font-bold">BloodSignal</span>
              <span className="block text-[10px] text-slate-300">AI Blood Availability Finder</span>
            </span>
          </Link>
          <nav
            aria-label="Footer navigation"
            className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-slate-200"
          >
            {legal.map((item) => (
              <Link key={item.to} to={item.to} className="hover:text-white">
                {item.label}
              </Link>
            ))}
          </nav>
          <span className="text-xs text-slate-300">
            © {new Date().getFullYear()} Pragmatrix. All rights reserved.
          </span>
        </div>
        <div className="border-t border-white/15 pt-4 text-center text-[11px] leading-5 text-slate-300">
          BloodSignal provides information for convenience and does not guarantee blood availability
          or provide medical advice. Always contact the blood bank or a healthcare professional
          before travelling.
        </div>
      </div>
    </footer>
  );
}

export function TextPage({
  eyebrow,
  title,
  children,
}: {
  eyebrow: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <main className="mx-auto max-w-5xl px-4 py-12 sm:px-7 sm:py-16">
      <p className="text-xs font-bold uppercase tracking-[0.14em] text-primary">{eyebrow}</p>
      <h1 className="mt-2 max-w-4xl text-3xl font-bold tracking-tight text-[#102544] sm:text-4xl">
        {title}
      </h1>
      <div className="mt-8 space-y-6 text-base leading-7 text-muted-foreground [&_h2]:mt-9 [&_h2]:text-xl [&_h2]:font-bold [&_h2]:text-[#102544] [&_h3]:font-semibold [&_h3]:text-[#102544] [&_a]:text-primary [&_a]:underline [&_a]:underline-offset-4">
        {children}
      </div>
    </main>
  );
}
