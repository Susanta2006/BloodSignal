import { createFileRoute } from "@tanstack/react-router";

import { TextPage } from "@/components/site-shell";

export const Route = createFileRoute("/privacy")({
  head: () => ({
    meta: [
      { title: "Privacy Policy | BloodSignal" },
      {
        name: "description",
        content:
          "How BloodSignal handles location, search details, request assistance and browser consent.",
      },
      { property: "og:title", content: "Privacy Policy | BloodSignal" },
      { property: "og:description", content: "How BloodSignal handles your information." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: PrivacyPage,
});

function PrivacyPage() {
  return (
    <TextPage eyebrow="Legal" title="Privacy Policy">
      <p>Last updated: 21 October 2026.</p>
      <h2>Location</h2>
      <p>
        Your device location is requested only when you tap “Use my device location”. It is sent
        with that single search and is not saved in your browser.
      </p>
      <h2>Request assistance</h2>
      <p>
        If you request field suggestions, your description and any attached prescription image are
        sent to the configured AI service for extraction. Voice recordings are sent for
        transcription. Review extracted details against the clinician’s instructions before
        searching. Do not upload personal information that is not needed for the request.
      </p>
      <h2>Browser consent</h2>
      <p>
        The first-visit notice stores your choice in browser local storage. The app does not enable
        advertising or cross-site tracking cookies.
      </p>
      <h2>Search details</h2>
      <p>
        Your blood group, component, quantity and location are sent to the search service to find
        blood banks. Search results are not stored for offline use on your device.
      </p>
      <h2>Contact</h2>
      <p>For privacy questions, contact team Pragmatrix.</p>
    </TextPage>
  );
}
