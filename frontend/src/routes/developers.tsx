import { createFileRoute } from "@tanstack/react-router";
import { Github, Linkedin, Globe, Users } from "lucide-react";
import { TextPage } from "@/components/site-shell";

export const Route = createFileRoute("/developers")({
  head: () => ({
    meta: [
      { title: "Developers | BloodSignal by Pragmatrix" },
      {
        name: "description",
        content:
          "Meet Susanta Banik and Shreya Bhattacharjee, the Pragmatrix team behind BloodSignal.",
      },
      { property: "og:title", content: "Developers | BloodSignal by Pragmatrix" },
      { property: "og:description", content: "Meet the people building BloodSignal." },
    ],
  }),
  component: DevelopersPage,
});
const team = [
  {
    name: "Susanta Banik",
    role: "Frontend Developer · Backend Developer · System Designer",
    initials: "SB",
    links: [
      { label: "LinkedIn", href: "https://linkedin.com/in/susanta-banik", icon: Linkedin },
      { label: "GitHub", href: "https://github.com/Susanta2006", icon: Github },
      { label: "Portfolio", href: "https://susanta-banik.vercel.app", icon: Globe },
    ],
  },
  {
    name: "Shreya Bhattacharjee",
    role: "UI Developer & Feature Designer",
    initials: "SB",
    links: [
      {
        label: "LinkedIn",
        href: "https://linkedin.com/in/shreya-bhattacharjee-90abb3415",
        icon: Linkedin,
      },
      { label: "GitHub", href: "https://github.com/shreyaBhattacharjee20", icon: Github },
    ],
  },
];
function DevelopersPage() {
  return (
    <TextPage eyebrow="Team Pragmatrix" title="The people behind BloodSignal">
      <p className="max-w-3xl text-lg">
        We are building a more transparent way to discover blood banks: one that distinguishes
        directory information from inventory evidence and keeps people in control of confirmation.
      </p>
      <div className="grid gap-5 md:grid-cols-2">
        {team.map((person) => (
          <article
            key={person.name}
            className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm"
          >
            <div className="flex items-center gap-4">
              <span
                className="grid size-14 shrink-0 place-items-center rounded-full bg-[#102544] text-lg font-bold text-white"
                aria-hidden="true"
              >
                {person.initials}
              </span>
              <div>
                <h2 className="text-xl">{person.name}</h2>
                <p className="mt-1 text-sm">{person.role}</p>
              </div>
            </div>
            <div className="mt-6 flex flex-wrap gap-2">
              {person.links.map(({ label, href, icon: Icon }) => (
                <a
                  key={label}
                  href={href}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="inline-flex items-center gap-2 rounded-md border border-slate-200 px-3 py-2 text-sm font-semibold text-[#102544] no-underline hover:border-primary hover:bg-red-50"
                >
                  <Icon className="size-4" aria-hidden="true" />
                  {label}
                  <span className="sr-only"> (opens in a new tab)</span>
                </a>
              ))}
            </div>
          </article>
        ))}
      </div>
      <section className="rounded-xl bg-[#f0f7fd] p-6">
        <div className="flex items-start gap-3">
          <Users className="mt-1 size-6 shrink-0 text-[#123d76]" />
          <div>
            <h2 className="text-lg">Team Pragmatrix</h2>
            <p className="mt-2">
              Our focus is a practical, evidence-aware blood-bank discovery workflow: use real
              directory data where available, label synthetic demonstration inventory honestly, and
              make the limitations visible rather than promising unverified live stock.
            </p>
          </div>
        </div>
      </section>
      <p>
        Profiles link to each developer’s public pages. BloodSignal is a prototype and its
        data-source status should always be checked on the application itself.
      </p>
    </TextPage>
  );
}
