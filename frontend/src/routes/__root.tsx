import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  Outlet,
  Link,
  createRootRouteWithContext,
  useRouter,
  HeadContent,
  Scripts,
  type ErrorComponentProps,
} from "@tanstack/react-router";
import { useEffect, useState, type ReactNode } from "react";

import appCss from "../styles.css?url";
import { reportLovableError } from "../lib/lovable-error-reporting";
import { SiteFooter, SiteHeader } from "@/components/site-shell";
import { Button } from "@/components/ui/button";
import { Cookie, Settings2 } from "lucide-react";

const applicationStructuredData = {
  "@context": "https://schema.org",
  "@type": ["SoftwareApplication", "WebApplication"],
  name: "LabBench",
  url: "https://bloodsignal.vercel.app/",
  description: "BloodSignal is an evidence-aware blood-bank discovery application built by Team Pragmatrix. It helps families and care teams organize a search by blood group, component, quantity, and location.",
  applicationCategory: "DeveloperApplication",
  operatingSystem: "All",
  founder: {
    "@type": "Person",
    name: "Susanta Banik",
    url: "https://susanta-banik.vercel.app",
  },
  featureList: [
    "Evidence-aware blood bank discovery application",
    "Integrated map coordinates with proper ranking for efficiency",
    "AI Voice typing and provides true evidence",
    "Clearly shows and requests users to call to confirm",
  ],
};

function NotFoundComponent() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1 className="text-7xl font-bold text-foreground">404</h1>
        <h2 className="mt-4 text-xl font-semibold text-foreground">Page not found</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          The page you're looking for doesn't exist or has been moved.
        </p>
        <div className="mt-6">
          <Link
            to="/"
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Go home
          </Link>
        </div>
      </div>
    </div>
  );
}

function ErrorComponent({ error, reset }: ErrorComponentProps) {
  console.error(
    `Page render failed error_class=${error instanceof Error ? error.name : "UnknownError"}`,
  );
  const router = useRouter();
  useEffect(() => {
    reportLovableError(error, { boundary: "tanstack_root_error_component" });
  }, [error]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1 className="text-xl font-semibold tracking-tight text-foreground">
          This page didn't load
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Something went wrong on our end. You can try refreshing or head back home.
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-2">
          <button
            onClick={() => {
              router.invalidate();
              reset();
            }}
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Try again
          </button>
          <a
            href="/"
            className="inline-flex items-center justify-center rounded-md border border-input bg-background px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent"
          >
            Go home
          </a>
        </div>
      </div>
    </div>
  );
}

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { name: "google-site-verification", content: "8o9Dm94uzc3XtGBMijfLCeGXH8k0MQgKiY1eZskSbjE" },
      { title: "BloodSignal by Pragmatrix" },
      { name: "description", content: "BloodSignal is an evidence-aware blood-bank discovery application built by Team Pragmatrix. It helps families and care teams organize a search by blood group, component, quantity, and location." },
      { name: "team", content: "Pragmatrix" },
      { property: "og:site_name", content: "BloodSignal" },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
      { name: "theme-color", content: "#ffffff" },
    ],
    links: [
      { rel: "stylesheet", href: appCss },
      { rel: "icon", href: "/favicon.ico", type: "image/x-icon", sizes: "any" },
      { rel: "icon", href: "/favicon-32.png", type: "image/png", sizes: "32x32" },
      { rel: "icon", href: "/favicon-16.png", type: "image/png", sizes: "16x16" },
      { rel: "apple-touch-icon", href: "/apple-touch-icon.png", sizes: "180x180" },
      { rel: "manifest", href: "/manifest.webmanifest" },
    ],
  }),
  shellComponent: RootShell,
  component: RootComponent,
  notFoundComponent: NotFoundComponent,
  errorComponent: ErrorComponent,
});

function RootShell({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(applicationStructuredData) }}
        />
        <HeadContent />
      </head>
      <body>
        {children}
        <Scripts />
      </body>
    </html>
  );
}

function RootComponent() {
  const { queryClient } = Route.useRouteContext();

  useEffect(() => {
    if (import.meta.env.DEV || !("serviceWorker" in navigator)) return;
    void navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
      // App navigation and search remain available if offline installation is unsupported.
    });
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      <div className="flex min-h-screen flex-col bg-background text-foreground">
        <a
          href="#content"
          className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-sm focus:bg-primary focus:px-3 focus:py-2 focus:text-primary-foreground"
        >
          Skip to content
        </a>
        <SiteHeader />
        <div id="content" className="flex-1">
          <Outlet />
        </div>
        <SiteFooter />
        <CookieConsent />
      </div>
    </QueryClientProvider>
  );
}

function CookieConsent() {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    try {
      setVisible(window.localStorage.getItem("bloodsignal-cookie-choice-v1") === null);
    } catch {
      setVisible(false);
    }
  }, []);
  const choose = (choice: "essential-only" | "accepted") => {
    try {
      window.localStorage.setItem("bloodsignal-cookie-choice-v1", choice);
    } catch {
      /* Storage may be disabled; continue without it. */
    }
    setVisible(false);
  };
  if (!visible) return null;
  return (
    <div
      className="fixed inset-0 z-[100] flex items-end justify-center bg-[#07162a]/45 p-3 sm:items-center sm:p-6"
      role="dialog"
      aria-modal="true"
      aria-labelledby="cookie-title"
    >
      <section className="w-full max-w-lg rounded-xl border border-slate-200 bg-white p-5 shadow-2xl sm:p-7">
        <div className="flex items-start gap-3">
          <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-red-50 text-primary">
            <Cookie className="size-5" />
          </span>
          <div>
            <h2 id="cookie-title" className="text-lg font-bold text-[#102544]">
              Your privacy choices
            </h2>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              BloodSignal uses essential browser storage for app functionality and to remember this
              choice. We do not enable advertising or cross-site tracking cookies. You can continue
              with essential storage only.
            </p>
          </div>
        </div>
        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button
            type="button"
            variant="outline"
            onClick={() => choose("essential-only")}
            className="rounded-md border-slate-300"
          >
            Essential only
          </Button>
          <Button
            type="button"
            onClick={() => choose("accepted")}
            className="rounded-md bg-primary text-white hover:bg-primary/90"
          >
            <Settings2 className="size-4" /> Accept and continue
          </Button>
        </div>
        <p className="mt-4 text-xs leading-5 text-slate-500">
          Read our{" "}
          <a href="/cookies" className="underline underline-offset-2">
            Cookie Policy
          </a>{" "}
          and{" "}
          <a href="/privacy" className="underline underline-offset-2">
            Privacy Policy
          </a>
          . Your choice is saved in this browser.
        </p>
      </section>
    </div>
  );
}
