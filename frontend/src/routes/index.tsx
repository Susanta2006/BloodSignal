import { Link, createFileRoute } from "@tanstack/react-router";
import {
  ArrowRight,
  Clock3,
  Database,
  MapPin,
  Phone,
  ShieldCheck,
  Workflow,
  CheckCircle2,
  Search,
  FileCheck2,
} from "lucide-react";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      {
        name: "title",
        content: "BloodSignal — Find Blood, Save Lives",
      },
      {
        name: "description",
        content:
          "Find nearby blood banks and check blood availability with BloodSignal. Discover blood stock information and locate facilities when every second matters.",
      },
      {
        name: "keywords",
        content:
          "BloodSignal, Susanta Banik, blood bank locator, blood availability, blood stock, nearby blood banks, blood donation, emergency blood search, e-RaktKosh, India blood banks",
      },
      { property: "og:type", content: "website" },
      {
        property: "og:title",
        content: "BloodSignal — Find Blood, Save Lives",
      },
      {
        property: "og:description",
        content:
          "Search blood availability, discover nearby blood banks, and find the information you need when every second matters.",
      },
      {
        property: "og:url",
        content: "https://bloodsignal.vercel.app/",
      },
      {
        property: "og:image",
        content: "https://bloodsignal.vercel.app/og-image.png",
      },
      {
        property: "og",
        content: "BloodSignal — Developed by Susanta Banik",
      },
      { name: "twitter:card", content: "summary_large_image" },
      {
        name: "twitter:title",
        content: "BloodSignal — Find Blood, Save Lives",
      },
      {
        name: "twitter:description",
        content:
          "Search blood availability and locate nearby blood banks with BloodSignal.",
      },
      {
        name: "twitter:image",
        content: "https://bloodsignal.vercel.app/og-image.png",
      },
    ],
    links: [
      {
        rel: "canonical",
        href: "https://bloodsignal.vercel.app/",
      },
    ],
  }),
  component: HomePage,
});

const features = [
  {
    icon: Database,
    title: "Structured Blood-Bank Directory",
    body: "Discover facilities using directory information from data.gov.in.",
  },
  {
    icon: FileCheck2,
    title: "Evidence-Aware Results",
    body: "Distinguish sample records from information verified with a live stock source.",
  },
  {
    icon: Clock3,
    title: "Freshness Indicators",
    body: "See source-reported update times and flags for stale or unknown data.",
  },
  {
    icon: MapPin,
    title: "Location-Aware Discovery",
    body: "Review facility locations and calculate distance only when reliable coordinates exist.",
  },
  {
    icon: Workflow,
    title: "AI-Assisted Search Workflow",
    body: "The agent helps interpret, retrieve, validate and rank evidence.",
  },
  {
    icon: CheckCircle2,
    title: "Transparent Ranking",
    body: "Understand why a result appears higher, including evidence quality and relevance.",
  },
  {
    icon: Phone,
    title: "Direct Contact Information",
    body: "Review available contact details and call the blood bank before travelling.",
  },
  {
    icon: ShieldCheck,
    title: "Responsible Fallbacks",
    body: "No match, stale data and source failures remain distinct — never disguised as availability.",
  },
];
const steps = [
  [Search, "Search", "Enter blood group, component, quantity and location."],
  [Database, "Retrieve", "Read configured directory and inventory sources."],
  [ShieldCheck, "Validate", "Check component compatibility, provenance and freshness."],
  [Workflow, "Rank", "Order only by supported relevance and evidence."],
  [FileCheck2, "Review", "Inspect result details, caveats and contact information."],
  [Phone, "Contact", "Call the blood bank to confirm current stock."],
] as const;

function HomePage() {
  return (
    <main>
      <section className="hero-panel">
        <div className="mx-auto grid max-w-7xl items-center gap-8 px-4 py-12 sm:px-7 sm:py-16 lg:grid-cols-[1.02fr_.98fr] lg:py-14">
          <div className="relative z-10">
            <p className="mb-4 text-xs font-bold uppercase tracking-[.16em] text-primary">
              Blood availability · India
            </p>
            <h1 className="max-w-2xl text-4xl font-extrabold leading-[1.08] tracking-tight text-[#102544] sm:text-5xl">
              Find blood banks.
              <br />
              Understand availability.
              <br />
              <span className="text-primary">Act with better information.</span>
            </h1>
            <p className="mt-5 max-w-xl text-base leading-7 text-slate-600">
              Search blood-bank directories by blood group, component, quantity and district. Review
              available evidence, source freshness, contact details, and location information before
              travelling.
            </p>
            <div className="mt-7 flex flex-wrap gap-3">
              <Button
                asChild
                className="h-12 rounded-md bg-primary px-6 text-white hover:bg-primary/90"
              >
                <Link to="/find">
                  <Search className="size-4" /> Find Blood Banks
                </Link>
              </Button>
              <Button
                asChild
                variant="outline"
                className="h-12 rounded-md border-[#27466c] bg-white px-5 text-[#102544]"
              >
                <Link to="/about">
                  <ArrowRight className="size-4" /> How BloodSignal Works
                </Link>
              </Button>
            </div>
            <p className="mt-5 max-w-lg text-xs leading-5 text-slate-500">
              A directory listing does not prove current stock. Always call the blood bank before
              travelling.
            </p>
          </div>
          <div className="relative min-h-64 overflow-hidden rounded-xl border border-slate-200 bg-[#e8f1f8] shadow-sm sm:min-h-80 lg:min-h-[360px]">
            <img
              src="https://images.unsplash.com/photo-1615461066841-6116e61058f4?auto=format&fit=crop&w=1200&q=85"
              alt="Blood donation bag and medical equipment in a clinical setting"
              className="absolute inset-0 size-full object-cover"
              loading="eager"
            />
            <div className="absolute inset-0 bg-gradient-to-r from-white/10 via-transparent to-[#0b3151]/15" />
            <div className="absolute bottom-4 left-4 max-w-xs rounded-lg border border-white/70 bg-white/95 p-4 shadow-md">
              <p className="text-xs font-bold uppercase tracking-wide text-primary">
                Evidence first
              </p>
              <p className="mt-1 text-sm font-semibold text-[#102544]">
                Availability must be confirmed with the blood bank.
              </p>
            </div>
          </div>
        </div>
      </section>
      <section className="mx-auto max-w-7xl px-4 py-8 sm:px-7 sm:py-12">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-[#102544]">Key Features</h2>
          <p className="mt-2 text-sm text-slate-600">
            Everything you need to find the right blood bank, with confidence.
          </p>
        </div>
        <ul className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {features.map(({ icon: Icon, title, body }) => (
            <li
              key={title}
              className="rounded-lg border border-slate-200 bg-white p-5 transition-shadow hover:shadow-sm"
            >
              <Icon className="size-6 text-[#102544]" aria-hidden="true" />
              <h3 className="mt-4 text-sm font-bold text-[#102544]">{title}</h3>
              <p className="mt-2 text-sm leading-6 text-slate-600">{body}</p>
            </li>
          ))}
        </ul>
      </section>
      <section className="mx-auto max-w-7xl px-4 py-6 sm:px-7">
        <div className="rounded-xl border border-blue-100 bg-[#f0f7fd] p-5 sm:p-7">
          <h2 className="text-xl font-bold text-[#102544]">How It Works</h2>
          <p className="mt-1 max-w-2xl text-sm text-slate-600">
            AI assists with processing and ranking, while source evidence and deterministic rules
            control availability claims.
          </p>
          <ol className="mt-7 grid gap-5 sm:grid-cols-3 lg:grid-cols-6">
            {steps.map(([Icon, title, body], i) => (
              <li key={title} className="relative text-center">
                <span className="mx-auto grid size-11 place-items-center rounded-full bg-[#123d76] text-white">
                  <Icon className="size-5" />
                </span>
                <p className="mt-2 text-sm font-bold text-[#102544]">{title}</p>
                <p className="mt-1 text-xs leading-5 text-slate-600">{body}</p>
                {i < steps.length - 1 && (
                  <span className="hidden" aria-hidden="true">
                    →
                  </span>
                )}
              </li>
            ))}
          </ol>
        </div>
      </section>
      <section className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-8 sm:flex-row sm:items-center sm:justify-between sm:px-7">
        <div className="flex items-start gap-3">
          <ShieldCheck className="mt-1 size-6 shrink-0 text-[#123d76]" />
          <div>
            <h2 className="font-bold text-[#102544]">Our Data & Transparency</h2>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-600">
              We use facility-directory data from data.gov.in where available. Any sample inventory
              is identified separately and is not evidence of current stock.
            </p>
          </div>
        </div>
        <Button
          asChild
          variant="outline"
          className="shrink-0 rounded-md border-[#27466c] text-[#102544]"
        >
          <Link to="/about">Learn About Our Data</Link>
        </Button>
      </section>
      <section className="border-t border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-8 sm:flex-row sm:items-center sm:justify-between sm:px-7">
          <p className="max-w-2xl text-sm leading-6 text-slate-600">
            <strong className="text-[#102544]">Medical emergency?</strong> Contact your hospital or
            local emergency services. BloodSignal is an information tool, not an emergency service.
          </p>
          <Button asChild variant="outline" className="rounded-md">
            <Link to="/find">
              Start a search <ArrowRight className="size-4" />
            </Link>
          </Button>
        </div>
      </section>
    </main>
  );
}
