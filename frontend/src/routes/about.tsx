import { Link, createFileRoute } from "@tanstack/react-router";
import {
  Database,
  ShieldCheck,
  Clock3,
  MapPin,
  PhoneCall,
  Workflow,
  AlertTriangle,
  CheckCircle2,
} from "lucide-react";
import { TextPage } from "@/components/site-shell";

export const Route = createFileRoute("/about")({
  head: () => ({
    meta: [
      { title: "About BloodSignal | How it works, benefits and data limitations" },
      {
        name: "description",
        content:
          "Understand BloodSignal's benefits, evidence model, hybrid data sources, limitations and responsible workflow.",
      },
      { property: "og:title", content: "About BloodSignal | Pragmatrix" },
      {
        property: "og:description",
        content:
          "A transparent blood-bank discovery workflow built around evidence, freshness and human confirmation.",
      },
    ],
  }),
  component: AboutPage,
});
const benefits = [
  [
    Clock3,
    "Less time spent searching",
    "One structured search brings blood group, component, quantity and district into a single workflow. It helps organize discovery; it cannot guarantee a faster clinical outcome.",
  ],
  [
    ShieldCheck,
    "Evidence is visible",
    "Results should identify whether the information is directory-only, synthetic demonstration inventory, stale, or returned by an actually verified inventory source.",
  ],
  [
    Database,
    "Sources are separated",
    "Facility-directory data and inventory evidence serve different purposes. A bank appearing in a directory does not mean it has the requested component in stock.",
  ],
  [
    MapPin,
    "Location without false certainty",
    "Location details can help users compare facilities. Distance is shown only when reliable coordinates are available; a map or route never proves stock availability.",
  ],
  [
    Workflow,
    "Consistent, reviewable ranking",
    "The workflow validates blood group and component compatibility, freshness and evidence before ranking. AI may assist with interpreting a request, but it must not invent stock facts.",
  ],
  [
    PhoneCall,
    "People stay in control",
    "Users review contact details and call to confirm availability. BloodSignal does not automatically call a facility, reserve blood, or make clinical decisions.",
  ],
];
function AboutPage() {
  return (
    <TextPage
      eyebrow="About BloodSignal"
      title="Find blood banks. Understand availability. Act with better information."
    >
      <p className="text-lg">
        BloodSignal is a blood-bank discovery tool built by Team Pragmatrix to help families and
        care teams organize a search and understand the evidence behind the results. The goal is not
        to sound certain; it is to make the limits of the available information visible.
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        {benefits.map(([Icon, title, body]) => {
          const FeatureIcon = Icon as typeof Database;
          return (
            <article
              key={title as string}
              className="rounded-lg border border-slate-200 bg-white p-5"
            >
              <FeatureIcon className="size-6 text-primary" />
              <h2 className="mt-3 text-base">{title as string}</h2>
              <p className="mt-2 text-sm leading-6">{body as string}</p>
            </article>
          );
        })}
      </div>
      <h2>How a search works</h2>
      <ol className="list-decimal space-y-2 pl-5">
        <li>
          <strong className="text-[#102544]">Describe the request:</strong> select the blood group,
          component, quantity and district, with an optional nearby-district search.
        </li>
        <li>
          <strong className="text-[#102544]">Retrieve source information:</strong> the application
          reads from its configured directory and inventory providers. A provider failure is shown
          as a failure, not as proof of no stock.
        </li>
        <li>
          <strong className="text-[#102544]">Validate evidence:</strong> results are checked for
          component compatibility and source/freshness information. Whole blood is not treated as
          interchangeable with platelets or another component.
        </li>
        <li>
          <strong className="text-[#102544]">Rank and review:</strong> supported evidence and
          location details can help organize the list. Missing timestamps or coordinates are not
          invented.
        </li>
        <li>
          <strong className="text-[#102544]">Confirm directly:</strong> call the blood bank to
          verify current stock, required quantity, compatibility requirements and collection
          instructions before travelling.
        </li>
      </ol>
      <h2>Our hybrid data model</h2>
      <div className="space-y-4">
        <div className="rounded-lg border border-slate-200 bg-white p-5">
          <h3>1. Real facility directory — data.gov.in</h3>
          <p className="mt-1">
            The directory layer can provide facility names, addresses, district, contact information
            and other published metadata when present. Directory fields such as “component
            available” or facility status do not establish the number of units currently available.
          </p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white p-5">
          <h3>2. Synthetic sample inventory — not live stock</h3>
          <p className="mt-1">
            Synthetic records support internal testing, including stale-stock and no-match
            scenarios. They must be labelled as sample data and never presented as current stock.
          </p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white p-5">
          <h3>3. Future verified inventory provider</h3>
          <p className="mt-1">
            A live stock provider may be added only after the data owner authorizes access and
            provides a supported interface. Until then, no live integration should be implied.
          </p>
        </div>
      </div>
      <h2>What the labels mean</h2>
      <ul className="list-disc space-y-2 pl-5">
        <li>
          <strong className="text-[#102544]">Synthetic test inventory:</strong> sample data, not
          current real-world availability.
        </li>
        <li>
          <strong className="text-[#102544]">Stale:</strong> the source-reported update is older
          than the configured freshness policy. Stale information is not confirmation of current
          stock.
        </li>
        <li>
          <strong className="text-[#102544]">Unknown freshness:</strong> no trustworthy update time
          was supplied. The app should not guess one.
        </li>
        <li>
          <strong className="text-[#102544]">Directory only:</strong> facility information is
          available, but stock has not been verified.
        </li>
        <li>
          <strong className="text-[#102544]">No match found:</strong> no matching record was
          returned from the searched sources. It does not prove that no blood exists anywhere.
        </li>
        <li>
          <strong className="text-[#102544]">Source unavailable:</strong> a provider could not be
          reached or did not return usable data. This is not the same as zero stock.
        </li>
      </ul>
      <h2>Why this approach is better</h2>
      <p>
        Many search experiences risk blending old directory information, unverified stock and live
        inventory into one list. BloodSignal is designed to keep those categories distinct, make
        freshness and provenance understandable, and leave consequential decisions with people. This
        is a design goal, not a claim that every source is always complete or current.
      </p>
      <h2>Safety and limitations</h2>
      <p>
        BloodSignal does not diagnose, prescribe, determine clinical compatibility, reserve blood,
        or replace a hospital or blood bank. Search results can be incomplete or out of date. Follow
        the treating clinician’s instructions and confirm the details directly with the facility. In
        an emergency, contact your hospital or local emergency services.
      </p>
      <h2>Who is building it?</h2>
      <p>
        BloodSignal is developed by Team Pragmatrix. Visit the{" "}
        <Link to="/developers">developer page</Link> to view the team’s public profiles and project
        links.
      </p>
      <p className="flex items-start gap-2 rounded-lg bg-blue-50 p-4 text-sm text-blue-950">
        <AlertTriangle className="mt-0.5 size-5 shrink-0" />{" "}
        <span>
          <strong>Always confirm before travelling.</strong> A result, timestamp, directory listing
          or map pin is not a guarantee that the requested blood component is currently available.
        </span>
      </p>
      <p className="flex items-center gap-2 text-sm">
        <CheckCircle2 className="size-5 text-primary" /> Clear provenance. Honest limitations. Human
        confirmation.
      </p>
    </TextPage>
  );
}
