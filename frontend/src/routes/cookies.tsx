import { createFileRoute } from "@tanstack/react-router";

import { TextPage } from "@/components/site-shell";

export const Route = createFileRoute("/cookies")({
  head: () => ({
    meta: [
      { title: "Cookie Policy | BloodSignal" },
      { name: "description", content: "BloodSignal uses no advertising or tracking cookies." },
      { property: "og:title", content: "Cookie Policy | BloodSignal" },
      {
        property: "og:description",
        content: "BloodSignal uses no advertising or tracking cookies.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: CookiesPage,
});

function CookiesPage() {
  return (
    <TextPage eyebrow="Legal" title="Cookie Policy">
      <p>Last updated: 21 October 2026.</p>
      <p>
        BloodSignal does not enable advertising or cross-site tracking cookies. A first-visit notice
        lets you acknowledge the policy and stores your choice in this browser.
      </p>
      <h2>Essential storage</h2>
      <p>
        The consent choice is stored in browser local storage. Search results, device coordinates
        and call lists are not intentionally persisted by the cookie notice.
      </p>
      <h2>Your choices</h2>
      <p>
        You can clear site data at any time in your browser settings. If browser storage is
        disabled, the notice may appear again.
      </p>
    </TextPage>
  );
}
