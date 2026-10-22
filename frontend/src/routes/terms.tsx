import { createFileRoute } from "@tanstack/react-router";

import { TextPage } from "@/components/site-shell";

export const Route = createFileRoute("/terms")({
  head: () => ({
    meta: [
      { title: "Terms & Conditions | BloodSignal" },
      {
        name: "description",
        content:
          "Terms for using BloodSignal, a blood availability search tool that is not an emergency service.",
      },
      { property: "og:title", content: "Terms & Conditions | BloodSignal" },
      { property: "og:description", content: "Terms for using BloodSignal." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: TermsPage,
});

function TermsPage() {
  return (
    <TextPage eyebrow="Legal" title="Terms & Conditions">
      <p>Last updated: 21 October 2026.</p>
      <h2>Not an emergency service</h2>
      <p>
        BloodSignal is an information tool. In an emergency, call 112 or contact a hospital
        directly. It does not provide medical advice.
      </p>
      <h2>No guarantee of stock</h2>
      <p>
        Stock information comes from third-party sources and can be out of date or wrong. Always
        confirm directly with the blood bank before travelling.
      </p>
      <h2>AI suggestions</h2>
      <p>
        Search results can be incomplete, stale or unavailable. You are responsible for checking the
        request details and confirming information with the blood bank and treating clinician.
      </p>
      <h2>Your actions</h2>
      <p>
        BloodSignal never places calls or reserves blood for you. Approving a call list only
        confirms you reviewed it.
      </p>
    </TextPage>
  );
}
