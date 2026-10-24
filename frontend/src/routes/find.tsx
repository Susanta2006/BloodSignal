import { createFileRoute } from "@tanstack/react-router";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Clock3,
  LoaderCircle,
  LocateFixed,
  Mic,
  MapPin,
  Navigation,
  ImagePlus,
  Eye,
  X,
  Phone,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  UsersRound,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { z } from "zod";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  approveProposal,
  asValue,
  createProposal,
  createRun,
  getIdempotencyKey,
  getCandidates,
  getRun,
  type CallListProposal,
  type Candidate,
  type RunSummary,
  SearchApiError,
} from "@/features/blood-search/api";
import {
  buildSearchRequest,
  bloodGroups,
  fieldErrors,
  initialSearchForm,
  quantityUnits,
  searchRequestSchema,
  type LocationSelection,
  type SearchFormValues,
  type SearchRequest,
} from "@/features/blood-search/contracts";
import { evidenceState } from "@/features/blood-search/evidence";
import {
  hasPositiveReportedStock,
  hasReportedNonPositiveStock,
  isConfirmedCandidate,
  isReviewableCandidate,
} from "@/features/blood-search/eligibility";
import { facilityDistanceKm, validLatLon } from "@/features/blood-search/distance";
import { componentOptions, componentDisplayName, eraktkoshStates } from "@/features/blood-search/eraktkosh-catalog";
import { districtsByState, nearbyDistrictSuggestions } from "@/features/blood-search/district-catalog";
import { BloodBankMap, type FacilityMapItem } from "@/features/blood-search/BloodBankMap";
import { getNativeLocation, isNativeLocationAvailable } from "@/features/blood-search/native-location";
import {
  buildDirectionsUrl,
  hasReliableFacilityDestination,
  type TravelMode,
} from "@/features/blood-search/directions";
import { suggestFields } from "@/features/blood-search/interpret.functions";

export const Route = createFileRoute("/find")({
  head: () => ({
    meta: [
      { title: "Find blood | BloodSignal by Pragmatrix" },
      {
        name: "description",
        content:
          "Search blood banks by blood group, component, quantity and location, with source names and update times on every result.",
      },
      { property: "og:title", content: "Find blood | BloodSignal by Pragmatrix" },
      {
        property: "og:description",
        content:
          "Search blood availability with transparent source evidence. No unverified stock is shown.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: BloodSearchPage,
});

type SearchState = "idle" | "submitting" | "tracking" | "ready" | "error";
type AssistanceState = "idle" | "recording" | "transcribing" | "suggesting";

type NetworkInformationLike = EventTarget & {
  effectiveType?: string;
  saveData?: boolean;
};

function getNetworkQuality(): "fast" | "slow" | "offline" {
  if (typeof navigator === "undefined" || !navigator.onLine) return "offline";
  const connection = (navigator as Navigator & { connection?: NetworkInformationLike }).connection;
  const type = connection?.effectiveType?.toLowerCase();
  return connection?.saveData || type === "slow-2g" || type === "2g" ? "slow" : "fast";
}

const voiceLanguages = [
  ["en", "English"],
  ["hi", "Hindi"],
  ["bn", "Bengali"],
  ["gu", "Gujarati"],
  ["kn", "Kannada"],
  ["ml", "Malayalam"],
  ["mr", "Marathi"],
  ["pa", "Punjabi"],
  ["ta", "Tamil"],
  ["te", "Telugu"],
  ["ur", "Urdu"],
] as const;

const ongoingStatuses = new Set([
  "accepted",
  "needs_location",
  "planning",
  "running",
  "verifying",
  "queued",
  "pending",
  "processing",
  "in_progress",
  "started",
]);
const completeStatuses = new Set([
  "awaiting_approval",
  "needs_review",
  "complete",
  "completed",
  "succeeded",
  "success",
  "done",
]);
const failedStatuses = new Set(["failed", "error", "cancelled", "canceled"]);

function label(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function pretty(value: unknown): string {
  if (typeof value !== "string") return "Unknown";
  return value.replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function searchErrorMessage(error: unknown, fallback: string): string {
  if (!(error instanceof Error)) return fallback;
  return error instanceof SearchApiError && error.correlationId
    ? `${error.message} Reference ID: ${error.correlationId}`
    : error.message;
}

function numericCoordinate(value: unknown): number | null {
  if (typeof value !== "number" && typeof value !== "string") return null;
  if (typeof value === "string" && !value.trim()) return null;
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : null;
}

function getFacilityCoordinates(
  candidate: Candidate,
): { latitude: number; longitude: number } | null {
  const nestedLocation = candidate["location"];
  const locationRecord =
    nestedLocation && typeof nestedLocation === "object"
      ? (nestedLocation as Record<string, unknown>)
      : undefined;
  const nestedBank = candidate["bank"];
  const bankRecord = nestedBank && typeof nestedBank === "object"
    ? (nestedBank as Record<string, unknown>) : undefined;
  const nestedBankLocation = bankRecord?.["location"];
  const bankLocationRecord =
    nestedBankLocation && typeof nestedBankLocation === "object"
      ? (nestedBankLocation as Record<string, unknown>)
      : undefined;
  const nestedBankCoordinates = bankRecord?.["coordinates"];
  const bankCoordinateRecord =
    nestedBankCoordinates && typeof nestedBankCoordinates === "object"
      ? (nestedBankCoordinates as Record<string, unknown>)
      : undefined;
  const nestedBankLocationCoordinates = bankLocationRecord?.["coordinates"];
  const bankLocationCoordinateRecord =
    nestedBankLocationCoordinates && typeof nestedBankLocationCoordinates === "object"
      ? (nestedBankLocationCoordinates as Record<string, unknown>)
      : undefined;
  const coordinates = candidate["coordinates"];
  const coordinateRecord =
    coordinates && typeof coordinates === "object"
      ? (coordinates as Record<string, unknown>)
      : undefined;
  const nestedLocationCoordinates = locationRecord?.["coordinates"];
  const locationCoordinateRecord =
    nestedLocationCoordinates && typeof nestedLocationCoordinates === "object"
      ? (nestedLocationCoordinates as Record<string, unknown>)
      : undefined;

  const latitude = numericCoordinate(
    candidate["latitude"] ??
      bankRecord?.["latitude"] ??
      candidate["lat"] ??
      bankRecord?.["lat"] ??
      candidate["location_latitude"] ??
      locationRecord?.["latitude"] ??
      locationRecord?.["lat"] ??
      bankLocationRecord?.["latitude"] ??
      bankLocationRecord?.["lat"] ??
      coordinateRecord?.["latitude"] ??
      coordinateRecord?.["lat"] ??
      bankCoordinateRecord?.["latitude"] ??
      bankCoordinateRecord?.["lat"] ??
      locationCoordinateRecord?.["latitude"] ??
      locationCoordinateRecord?.["lat"] ??
      bankLocationCoordinateRecord?.["latitude"] ??
      bankLocationCoordinateRecord?.["lat"],
  );
  const longitude = numericCoordinate(
    candidate["longitude"] ??
      bankRecord?.["longitude"] ??
      candidate["lng"] ??
      candidate["lon"] ??
      bankRecord?.["lng"] ??
      bankRecord?.["lon"] ??
      candidate["location_longitude"] ??
      locationRecord?.["longitude"] ??
      locationRecord?.["lng"] ??
      locationRecord?.["lon"] ??
      bankLocationRecord?.["longitude"] ??
      bankLocationRecord?.["lng"] ??
      bankLocationRecord?.["lon"] ??
      coordinateRecord?.["longitude"] ??
      coordinateRecord?.["lng"] ??
      coordinateRecord?.["lon"] ??
      bankCoordinateRecord?.["longitude"] ??
      bankCoordinateRecord?.["lng"] ??
      bankCoordinateRecord?.["lon"] ??
      locationCoordinateRecord?.["longitude"] ??
      locationCoordinateRecord?.["lng"] ??
      locationCoordinateRecord?.["lon"] ??
      bankLocationCoordinateRecord?.["longitude"] ??
      bankLocationCoordinateRecord?.["lng"] ??
      bankLocationCoordinateRecord?.["lon"],
  );

  if (
    latitude === null ||
    longitude === null ||
    latitude < -90 ||
    latitude > 90 ||
    longitude < -180 ||
    longitude > 180
  )
    return null;

  return { latitude, longitude };
}

function candidateLocationPrecision(candidate: Candidate): "FACILITY" | "LOCALITY" | null {
  const nestedBank = candidate["bank"];
  const bankRecord =
    nestedBank && typeof nestedBank === "object"
      ? (nestedBank as Record<string, unknown>)
      : undefined;
  const precision = candidate["location_precision"] ?? bankRecord?.["location_precision"];
  return precision === "FACILITY" || precision === "LOCALITY" ? precision : null;
}

function candidateDistanceKm(
  candidate: Candidate,
  origin: { latitude: number; longitude: number } | null,
): number | null {
  // Locality-level (geocoded) coordinates are allowed: the distance is shown as approximate.
  // Prefer the backend's value, then fall back to the device origin + facility coordinates.
  const reported = candidate["distance_km"];
  if (typeof reported === "number" && Number.isFinite(reported) && reported >= 0) return reported;
  if (!origin) return null;
  return facilityDistanceKm(
    origin,
    getFacilityCoordinates(candidate),
    candidateLocationPrecision(candidate),
  );
}

/** Straight-line distance is always approximate; locality-level pins get a "~" prefix. */
function formatDistance(km: number, candidate: Candidate): string {
  const precision = candidateLocationPrecision(candidate);
  const isApprox = precision === "LOCALITY";
  return `${isApprox ? "~" : ""}${km.toFixed(2)} km${isApprox ? " (approx.)" : ""}`;
}


/** Contact values for display. Hides obvious placeholder fax numbers (e.g. 1234567890). */
function contactValues(candidate: Candidate, key: "emails" | "faxes", single: string): string[] {
  const list = Array.isArray(candidate[key]) ? (candidate[key] as unknown[]) : [];
  const values = [...list, candidate[single]]
    .filter((v): v is string => typeof v === "string" && v.trim() !== "")
    .map((v) => v.trim());
  return [...new Set(values)].filter((v) => key !== "faxes" || !isPlaceholderNumber(v));
}

function isPlaceholderNumber(value: string): boolean {
  const digits = value.replace(/\D/g, "");
  if (digits.length < 6) return false;
  return /^(\d)\1+$/.test(digits) || "01234567890123456789".includes(digits) || "9876543210987654321".includes(digits);
}

function componentLabel(value: string): string {
  return componentDisplayName(value) || pretty(value);
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Kolkata" });
}

function formatDuration(seconds: number): string {
  const minutes = Math.max(0, Math.round(seconds / 60));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes ? `${hours} hr ${remainingMinutes} min` : `${hours} hr`;
}

function telephoneHref(value: string | undefined): string | null {
  if (!value || !/^[+\d().\s-]+$/.test(value.trim())) return null;
  if ((value.match(/\+/g) ?? []).length > 1 || value.trim().indexOf("+") > 0) return null;
  const digits = value.replace(/\D/g, "");
  if (digits.length < 7 || digits.length > 15) return null;
  return `tel:${value.trim().startsWith("+") ? "+" : ""}${digits}`;
}

function downloadReviewReport(proposal: CallListProposal, run: RunSummary | null, payload: SearchRequest | null) {
  const ascii = (value: unknown) =>
    String(value ?? "Not provided")
      .normalize("NFKD")
      .replace(/[^\x20-\x7E]/g, "?")
      .replace(/\s+/g, " ")
      .trim();
  const wrap = (value: unknown, width = 92) => {
    const words = ascii(value).split(" ");
    const lines: string[] = [];
    let line = "";
    for (const word of words) {
      if (line && `${line} ${word}`.length > width) {
        lines.push(line);
        line = word;
      } else line = line ? `${line} ${word}` : word;
    }
    if (line) lines.push(line);
    return lines.length ? lines : [""];
  };
  const lines: string[] = [
    "BloodSignal - Call-list review report",
    `Generated: ${new Date().toLocaleString("en-IN")}`,
    `Run ID: ${run?.run_id ?? "Not provided"}`,
    `Proposal version: ${proposal.proposal_version}`,
    "",
    "IMPORTANT SAFETY NOTICE",
    "Inventory is source-reported and is not independent confirmation of current availability.",
    "Call the blood centre and care team to confirm group, component, quantity and stock before travel.",
    "This report does not reserve blood. No call was placed by BloodSignal.",
    "",
    "REQUEST",
    `Blood group: ${payload?.blood_group ?? "Not provided"}`,
    `Component: ${payload?.component ?? "Not provided"}`,
    `Quantity: ${payload?.quantity ?? "Not provided"} ${payload?.quantity_unit ?? ""}`,
    `Search location: ${payload?.location?.mode === "manual"
      ? `${payload.location.district}, ${payload.location.state}, India`
      : payload?.location?.mode === "device" ? "Device location (consented)" : "Not provided"}`,
    "",
  ];
  proposal.items.forEach((item, index) => {
    lines.push(
      `${index + 1}. ${ascii(item.bank_name)}`,
      `Facility identity: ${item.facility_source_type === "demo" ? "Synthetic demo facility" : "Real facility directory listing"}`,
      `Inventory: ${item.source_type === "demo" ? "SIMULATED - NOT LIVE OR VERIFIED STOCK" : "Source-reported; confirm before travel"}`,
      `Address: ${ascii(item.address)}`,
      `Phone: ${ascii(item.phone)}${item.phone ? " (call manually to confirm stock)" : ""}`,
      `Requested: ${ascii(item.blood_group || payload?.blood_group)} / ${ascii(item.component || payload?.component)} - ${ascii(payload?.quantity)} ${ascii(payload?.quantity_unit)}`,
      `Reported quantity: ${item.quantity == null ? "Not reported" : `${item.quantity} ${ascii(item.quantity_unit || "units")}`}`,
      `Distance: ${typeof item.distance_km === "number" ? `${item.distance_km.toFixed(1)} km straight-line estimate` : "Not available"}`,
      `Inventory source: ${ascii(item.source_name || item.directory_source)}`,
      `Source update time: ${ascii(item.source_updated_at)}`,
      `Retrieved by app: ${ascii(item.retrieved_at)}`,
      `Evidence state: ${ascii(item.evidence_state || item.section)}`,
      `Notes: ${ascii(item.display_note)}`,
      "",
    );
  });
  const wrapped = lines.flatMap((line) => wrap(line));
  const pageChunks: string[][] = [];
  for (let i = 0; i < wrapped.length; i += 52) pageChunks.push(wrapped.slice(i, i + 52));
  if (!pageChunks.length) pageChunks.push(["BloodSignal review report"]);

  const escapePdf = (value: string) => value.replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)");
  const objects: string[] = [];
  const pageObjectIds = pageChunks.map((_, index) => 4 + index * 2);
  objects[1] = "<< /Type /Catalog /Pages 2 0 R >>";
  objects[2] = `<< /Type /Pages /Kids [${pageObjectIds.map((id) => `${id} 0 R`).join(" ")}] /Count ${pageChunks.length} >>`;
  objects[3] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>";
  pageChunks.forEach((pageLines, index) => {
    const pageId = pageObjectIds[index];
    const contentId = pageId + 1;
    const textOps = [
      "BT",
      "/F1 10 Tf",
      "50 790 Td",
      "14 TL",
      ...pageLines.flatMap((line) => [`(${escapePdf(line)}) Tj`, "T*"]),
      "ET",
    ].join("\n");
    objects[pageId] = `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents ${contentId} 0 R >>`;
    objects[contentId] = `<< /Length ${textOps.length} >>\nstream\n${textOps}\nendstream`;
  });

  let pdf = "%PDF-1.4\n";
  const offsets = [0];
  for (let id = 1; id < objects.length; id++) {
    offsets[id] = pdf.length;
    pdf += `${id} 0 obj\n${objects[id]}\nendobj\n`;
  }
  const xrefOffset = pdf.length;
  pdf += `xref\n0 ${objects.length}\n0000000000 65535 f \n`;
  for (let id = 1; id < objects.length; id++) pdf += `${String(offsets[id]).padStart(10, "0")} 00000 n \n`;
  pdf += `trailer\n<< /Size ${objects.length} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF`;
  const blob = new Blob([pdf], { type: "application/pdf" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `bloodsignal-review-${new Date().toISOString().slice(0, 10)}.pdf`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1500);
}

function BloodSearchPage() {
  const [values, setValues] = useState<SearchFormValues>(initialSearchForm);
  const [demoScenario, setDemoScenario] = useState("tc1");
  const appMode = String(import.meta.env["VITE_APP_MODE"] ?? "citizen").trim().toLowerCase();
  const showDemoControls = appMode === "test";
  const [location, setLocation] = useState<LocationSelection | null>(null);
  const [useDevice, setUseDevice] = useState(false);
  const [deviceLocationStatus, setDeviceLocationStatus] = useState<"idle" | "requesting" | "ready" | "error">("idle");
  const deviceLocationRequestId = useRef(0);
  const [includeNearby, setIncludeNearby] = useState(false);
  const [formErrors, setFormErrors] = useState<Record<string, string>>({});
  const [state, setState] = useState<SearchState>("idle");
  const [message, setMessage] = useState("");
  const [run, setRun] = useState<RunSummary | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [previewCandidate, setPreviewCandidate] = useState<Candidate | null>(null);
  const [proposal, setProposal] = useState<CallListProposal | null>(null);
  const [approved, setApproved] = useState(false);
  const [approvalSubmitting, setApprovalSubmitting] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [showOtherResults, setShowOtherResults] = useState(false);
  const [resultPage, setResultPage] = useState(1);
  const [viewMode, setViewMode] = useState<"list" | "map">("list");
  const [travelMode, setTravelMode] = useState<TravelMode>("driving");
  const [lastPayload, setLastPayload] = useState<SearchRequest | null>(null);
  const [sortMode, setSortMode] = useState<"relevance" | "distance" | "name" | "quantity" | "updated">("relevance");
  const [networkQuality, setNetworkQuality] = useState<"fast" | "slow" | "offline">(
    getNetworkQuality,
  );
  const [requestDescription, setRequestDescription] = useState("");
  const [prescription, setPrescription] = useState<File | null>(null);
  const [voiceLanguage, setVoiceLanguage] = useState("auto");
  const [assistanceState, setAssistanceState] = useState<AssistanceState>("idle");
  const [assistanceMessage, setAssistanceMessage] = useState("");
  const prescriptionInput = useRef<HTMLInputElement>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const recordingStream = useRef<MediaStream | null>(null);
  const recordingChunks = useRef<Blob[]>([]);
  const searchInFlight = useRef(false);
  const pollInFlight = useRef(false);

  useEffect(
    () => () => {
      if (recorder.current?.state === "recording") recorder.current.stop();
      recordingStream.current?.getTracks().forEach((track) => track.stop());
    },
    [],
  );

  const suggestFromDescription = async () => {
    if (!requestDescription.trim() && !prescription) {
      setAssistanceMessage("Describe the request or attach a prescription first.");
      return;
    }
    setAssistanceState("suggesting");
    setAssistanceMessage("");
    try {
      let imageBase64: string | undefined;
      let imageMimeType: "image/jpeg" | "image/png" | "image/webp" | undefined;
      if (prescription) {
        if (!["image/jpeg", "image/png", "image/webp"].includes(prescription.type)) {
          throw new Error("Choose a JPG, PNG, or WebP prescription image.");
        }
        if (prescription.size > 5 * 1024 * 1024) {
          throw new Error("Prescription images must be 5 MB or smaller.");
        }
        const dataUrl = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () =>
            typeof reader.result === "string"
              ? resolve(reader.result)
              : reject(new Error("The prescription image could not be read."));
          reader.onerror = () => reject(new Error("The prescription image could not be read."));
          reader.readAsDataURL(prescription);
        });
        imageBase64 = dataUrl.slice(dataUrl.indexOf(",") + 1);
        imageMimeType = prescription.type as typeof imageMimeType;
      }

      const suggested = await suggestFields({
        data: {
          text: requestDescription.trim() || undefined,
          imageBase64,
          imageMimeType,
        },
      });
      const updates: Partial<SearchFormValues> = {};
      if (suggested.blood_group) updates.blood_group = suggested.blood_group;
      if (suggested.component) {
        updates.component = suggested.component === "platelets"
          ? "platelet_concentrate"
          : suggested.component;
      }
      if (suggested.quantity !== null) updates.quantity = String(suggested.quantity);
      if (suggested.quantity_unit === "units" || suggested.quantity_unit === "ml")
        updates.quantity_unit = suggested.quantity_unit;
      if (suggested.district) updates.district = suggested.district;
      if (suggested.state) updates.state = suggested.state;
      setValues((current) => ({ ...current, ...updates }));
      setFormErrors({});
      if (suggested.district || suggested.state) {
        setUseDevice(false);
        setLocation(null);
      }
      const hasSuggestions = Object.keys(updates).length > 0;
      setAssistanceMessage(
        hasSuggestions
          ? "Suggestions added to the form. Check every field against the clinician’s instructions before searching."
          : "No request fields could be read. You can enter the details manually.",
      );
    } catch (error) {
      setAssistanceMessage(
        error instanceof Error
          ? error.message
          : "Suggestions are unavailable. Enter the details manually.",
      );
    } finally {
      setAssistanceState("idle");
    }
  };

  const toggleVoiceRecording = async () => {
    if (recorder.current?.state === "recording") {
      setAssistanceState("transcribing");
      recorder.current.stop();
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setAssistanceMessage(
        "Voice recording is not supported in this browser. Type your request instead.",
      );
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recordingStream.current = stream;
      const nextRecorder = new MediaRecorder(stream);
      recorder.current = nextRecorder;
      recordingChunks.current = [];
      nextRecorder.ondataavailable = (event) => {
        if (event.data.size) recordingChunks.current.push(event.data);
      };
      nextRecorder.onerror = () => {
        stream.getTracks().forEach((track) => track.stop());
        recordingStream.current = null;
        setAssistanceState("idle");
        setAssistanceMessage("The voice note could not be recorded. Type your request instead.");
      };
      nextRecorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        recordingStream.current = null;
        const audio = new Blob(recordingChunks.current, {
          type: nextRecorder.mimeType || "audio/webm",
        });
        if (!audio.size) {
          setAssistanceState("idle");
          setAssistanceMessage("No audio was recorded. Try again or type your request.");
          return;
        }
        const form = new FormData();
        const extension = audio.type.includes("mp4")
          ? "m4a"
          : audio.type.includes("ogg")
            ? "ogg"
            : "webm";
        form.append("file", audio, `blood-request.${extension}`);
        if (voiceLanguage !== "auto") form.append("language", voiceLanguage);
        try {
          const response = await fetch("/api/transcribe", {
            method: "POST",
            body: form,
            cache: "no-store",
          });
          const body = (await response.json()) as { text?: unknown; message?: unknown };
          if (!response.ok) {
            throw new Error(
              typeof body.message === "string"
                ? body.message
                : "Voice transcription is unavailable.",
            );
          }
          if (typeof body.text !== "string" || !body.text.trim())
            throw new Error("No clear speech was detected. Try again or type your request.");
          setRequestDescription((current) =>
            current.trim() ? `${current.trim()}\n${body.text}` : (body.text as string),
          );
          setAssistanceMessage(
            "Voice note added to your description. Select Suggest fields to fill the form.",
          );
        } catch (error) {
          setAssistanceMessage(
            error instanceof Error ? error.message : "Voice transcription is unavailable.",
          );
        } finally {
          setAssistanceState("idle");
        }
      };
      nextRecorder.start();
      setAssistanceMessage("Recording. Select Stop recording when you are finished.");
      setAssistanceState("recording");
    } catch {
      setAssistanceState("idle");
      setAssistanceMessage("Microphone access was not available. Type your request instead.");
    }
  };

  const sharedDeviceOrigin = useMemo(
    () => {
      const selectedLocation = lastPayload?.location;
      if (selectedLocation?.mode !== "device") return null;
      return {
        latitude: selectedLocation.latitude,
        longitude: selectedLocation.longitude,
      };
    },
    [lastPayload],
  );

  const eligibilityRequest = useMemo(
    () =>
      lastPayload
        ? {
            blood_group: lastPayload.blood_group,
            component: lastPayload.component,
            quantity: lastPayload.quantity,
            quantity_unit: lastPayload.quantity_unit,
          }
        : null,
    [lastPayload],
  );

  const sortedCandidates = useMemo(() => {
    return [...candidates].sort((a, b) => {
      if (sortMode === "name") {
        return (label(a["name"] ?? a["bank_name"]) || "").localeCompare(
          label(b["name"] ?? b["bank_name"]) || "",
        );
      }
      if (sortMode === "quantity") {
        const quantityOf = (candidate: Candidate) => {
          const stock = asValue(candidate["stock"] ?? candidate["availability"]);
          const value = candidate["quantity_reported"] ?? stock["quantity"];
          return typeof value === "number" && Number.isFinite(value) ? value : -1;
        };
        const difference = quantityOf(b) - quantityOf(a);
        if (difference !== 0) return difference;
      }
      if (sortMode === "updated") {
        const updatedOf = (candidate: Candidate) => {
          const stock = asValue(candidate["stock"] ?? candidate["availability"]);
          const raw = candidate["source_updated_at"] ?? stock["source_updated_at"] ?? candidate["source_updated_at_raw"] ?? stock["source_updated_at_raw"];
          if (typeof raw !== "string") return Number.NEGATIVE_INFINITY;
          const time = Date.parse(raw);
          return Number.isFinite(time) ? time : Number.NEGATIVE_INFINITY;
        };
        const difference = updatedOf(b) - updatedOf(a);
        if (difference !== 0) return difference;
      }
      if (sortMode === "distance") {
        const sourcePriority = (candidate: Candidate) =>
          candidate["source_type"] === "demo"
            ? 2
            : hasPositiveReportedStock(candidate, eligibilityRequest)
              ? 0
              : 1;
        const sourceDifference = sourcePriority(a) - sourcePriority(b);
        if (sourceDifference !== 0) return sourceDifference;

        const freshnessBucket = (candidate: Candidate) =>
          candidate["evidence_state"] === "FRESH"
            ? 0
            : candidate["evidence_state"] === "STALE"
              ? 1
              : 2;
        const freshnessDifference = freshnessBucket(a) - freshnessBucket(b);
        if (freshnessDifference !== 0) return freshnessDifference;

        const updatedOf = (candidate: Candidate) => {
          const stock = asValue(candidate["stock"] ?? candidate["availability"]);
          const raw = candidate["source_updated_at"] ?? stock["source_updated_at"];
          if (typeof raw !== "string") return Number.NEGATIVE_INFINITY;
          const time = Date.parse(raw);
          return Number.isFinite(time) ? time : Number.NEGATIVE_INFINITY;
        };
        const updateDifference = updatedOf(b) - updatedOf(a);
        if (updateDifference !== 0) return updateDifference;

        const quantityOf = (candidate: Candidate) => {
          const stock = asValue(candidate["stock"] ?? candidate["availability"]);
          const value = candidate["quantity_reported"] ?? stock["quantity"];
          return typeof value === "number" && Number.isFinite(value) ? value : -1;
        };
        const availabilityOf = (candidate: Candidate) => {
          if (!hasPositiveReportedStock(candidate, eligibilityRequest)) return 3;
          const quantity = quantityOf(candidate);
          const requested = eligibilityRequest?.quantity ?? 0;
          return quantity > requested ? 0 : quantity === requested ? 1 : 2;
        };
        const availabilityDifference = availabilityOf(a) - availabilityOf(b);
        if (availabilityDifference !== 0) return availabilityDifference;
        const quantityDifference = quantityOf(b) - quantityOf(a);
        if (quantityDifference !== 0) return quantityDifference;

        const distanceA = candidateDistanceKm(a, sharedDeviceOrigin);
        const distanceB = candidateDistanceKm(b, sharedDeviceOrigin);
        // Freshness/evidence and stock quantity stay ahead of proximity; known
        // straight-line distances sort nearest-first, with unknowns following.
        if (distanceA !== null && distanceB !== null && distanceA !== distanceB)
          return distanceA - distanceB;
        if (distanceA !== null && distanceB === null) return -1;
        if (distanceA === null && distanceB !== null) return 1;
      }
      const rankA = typeof a["rank"] === "number" ? a["rank"] : Number.MAX_SAFE_INTEGER;
      const rankB = typeof b["rank"] === "number" ? b["rank"] : Number.MAX_SAFE_INTEGER;
      return (
        rankA - rankB ||
        (label(a["name"] ?? a["bank_name"]) || "").localeCompare(
          label(b["name"] ?? b["bank_name"]) || "",
        )
      );
    });
  }, [candidates, sortMode, sharedDeviceOrigin, eligibilityRequest]);
  const confirmedCandidates = useMemo(
    () => sortedCandidates.filter((candidate) =>
      !hasReportedNonPositiveStock(candidate) &&
      isConfirmedCandidate(candidate, eligibilityRequest)),
    [sortedCandidates, eligibilityRequest],
  );
  const positiveStockCandidates = useMemo(
    () => sortedCandidates.filter((candidate) =>
      !hasReportedNonPositiveStock(candidate) &&
      hasPositiveReportedStock(candidate, eligibilityRequest)),
    [sortedCandidates, eligibilityRequest],
  );
  const otherCandidates = useMemo(
    () => sortedCandidates.filter((candidate) =>
      !hasReportedNonPositiveStock(candidate) &&
      !hasPositiveReportedStock(candidate, eligibilityRequest)),
    [sortedCandidates, eligibilityRequest],
  );
  const visibleCandidates = [...positiveStockCandidates, ...otherCandidates];
  const showingOtherResults = showOtherResults && positiveStockCandidates.length > 0;
  const shownCandidates = showingOtherResults
    ? otherCandidates
    : positiveStockCandidates.length > 0
      ? positiveStockCandidates
      : otherCandidates;
  const hiddenNonPositiveCount = sortedCandidates.filter(hasReportedNonPositiveStock).length;
  const resultsPageCount = Math.max(1, Math.ceil(shownCandidates.length / 5));
  const paginatedCandidates = useMemo(
    () => shownCandidates.slice((resultPage - 1) * 5, resultPage * 5),
    [shownCandidates, resultPage],
  );
  const startEntry = shownCandidates.length ? (resultPage - 1) * 5 + 1 : 0;
  const endEntry = Math.min(resultPage * 5, shownCandidates.length);

  useEffect(() => {
    setResultPage((current) => Math.min(current, resultsPageCount));
  }, [resultsPageCount]);

  const mapFacilities = useMemo<FacilityMapItem[]>(() => {
    return shownCandidates.flatMap((candidate, index) => {
      const coordinates = getFacilityCoordinates(candidate);
      const nestedBank = candidate["bank"];
      const bankRecord = nestedBank && typeof nestedBank === "object"
        ? (nestedBank as Record<string, unknown>)
        : undefined;
      const name =
        label(candidate["bank_name"]) ??
        label(candidate["name"]) ??
        label(bankRecord?.["name"]);
      if (!name) return [];

      const id = label(candidate["candidate_id"] ?? candidate["id"]);
      const address =
        label(candidate["address"]) ??
        label(candidate["formatted_address"]) ??
        label(bankRecord?.["address"]);
      const city = label(candidate["city"]) ?? label(bankRecord?.["city"]);
      const district = label(candidate["district"]) ?? label(bankRecord?.["district"]);
      const state = label(candidate["state"]) ?? label(bankRecord?.["state"]);
      const locationNote = label(candidate["location_note"]);
      return [
        {
          id: id || `facility-${index}`,
          name,
          ...(address ? { address } : {}),
          ...(city ? { city } : {}),
          ...(district ? { district } : {}),
          ...(state ? { state } : {}),
          ...(coordinates
            ? { latitude: coordinates.latitude, longitude: coordinates.longitude }
            : {}),
          ...(candidateLocationPrecision(candidate)
            ? { location_precision: candidateLocationPrecision(candidate) as "LOCALITY" | "FACILITY" }
            : {}),
          ...(locationNote ? { location_note: locationNote } : {}),
        },
      ];
    });
  }, [shownCandidates]);

  const loadCandidates = useCallback(async (runId: string) => {
    try {
      const nextCandidates = await getCandidates(runId);
      setCandidates(nextCandidates);
      setSelected([]);
      setShowOtherResults(false);
      setResultPage(1);
      setState("ready");
      setMessage("");
    } catch (error) {
      setState("error");
      setMessage(error instanceof Error ? error.message : "The search results are unavailable.");
    }
  }, []);

  const pollRun = useCallback(
    async (runId: string) => {
      if (pollInFlight.current) return;
      pollInFlight.current = true;
      try {
        const nextRun = await getRun(runId);
        setRun(nextRun);
        const status = nextRun.status.toLowerCase();
        if (completeStatuses.has(status)) await loadCandidates(runId);
        else if (failedStatuses.has(status)) {
          setState("error");
          setMessage("This search could not be completed. No availability was verified.");
        } else if (!ongoingStatuses.has(status)) {
          setState("tracking");
          setMessage(
            `Search status: ${pretty(nextRun.status)}. Results will appear when the service returns them.`,
          );
        }
      } catch (error) {
        const retryable =
          !(error instanceof SearchApiError) || error.status === 429 || error.status >= 500;
        if (retryable) {
          setState("tracking");
          setMessage(
            navigator.onLine
              ? "The connection was interrupted. We’ll keep checking for this search’s results."
              : "You’re offline. This search will resume when the connection returns.",
          );
        } else {
          setState("error");
          setMessage(searchErrorMessage(error, "The search status is unavailable."));
        }
      } finally {
        pollInFlight.current = false;
      }
    },
    [loadCandidates],
  );

  useEffect(() => {
    const update = () => setNetworkQuality(getNetworkQuality());
    const connection = (navigator as Navigator & { connection?: NetworkInformationLike })
      .connection;
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    connection?.addEventListener("change", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
      connection?.removeEventListener("change", update);
    };
  }, []);

  useEffect(() => {
    if (!run?.run_id || state !== "tracking") return;
    let timer = 0;
    let delay = networkQuality === "slow" ? 5000 : 1200;
    let stopped = false;
    const schedule = () => {
      if (stopped) return;
      timer = window.setTimeout(async () => {
        if (document.visibilityState === "visible" && navigator.onLine) {
          await pollRun(run.run_id);
          delay = Math.min(delay * 1.5, networkQuality === "slow" ? 15000 : 6000);
        } else {
          delay = Math.min(delay * 1.5, networkQuality === "slow" ? 15000 : 6000);
        }
        schedule();
      }, delay);
    };
    const resume = () => {
      if (timer) window.clearTimeout(timer);
      delay = networkQuality === "slow" ? 5000 : 1200;
      schedule();
    };
    document.addEventListener("visibilitychange", resume);
    window.addEventListener("online", resume);
    schedule();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", resume);
      window.removeEventListener("online", resume);
    };
  }, [run?.run_id, state, pollRun, networkQuality]);

  const changeValue = (name: keyof SearchFormValues, value: string) => {
    setValues((current) => ({ ...current, [name]: value }));
    setFormErrors((current) => ({ ...current, [name]: "" }));
  };

  const requestDeviceLocation = () => {
    const requestId = ++deviceLocationRequestId.current;
    setUseDevice(true);
    setLocation(null);
    setDeviceLocationStatus("requesting");
    setMessage("Requesting your device location. Keep this page open while the browser determines it.");
    setFormErrors((current) => ({ ...current, location: "" }));

    // The device's native location service (available inside a native shell such as
    // Capacitor) is tried alongside the browser's Geolocation; whichever returns a
    // valid position first is used. In a plain browser this is false and nothing
    // below changes.
    const nativeAvailable = isNativeLocationAvailable();
    const browserAvailable = window.isSecureContext && Boolean(navigator.geolocation);

    // Geolocation requires a secure context. localhost and 127.0.0.1 are
    // trusted for development; plain HTTP LAN IPs and hostnames are not.
    if (!window.isSecureContext && !nativeAvailable) {
      setUseDevice(false);
      setDeviceLocationStatus("error");
      setMessage(
        "Your browser blocks device location on this address because it is not a secure context. Open BloodSignal at http://localhost:5173 on this same computer, or use an HTTPS URL on another device. You can also enter a city or district manually.",
      );
      return;
    }
    if (!navigator.geolocation && !nativeAvailable) {
      setUseDevice(false);
      setDeviceLocationStatus("error");
      setMessage("This browser does not support device location. Choose the state and district below.");
      return;
    }

    let settled = false;
    let fallbackStarted = false;
    let watchdog = 0;
    let nativePending = nativeAvailable;
    let pendingBrowserReason: string | null = null;
    const finishWithError = (reason: string) => {
      if (settled || requestId !== deviceLocationRequestId.current) return;
      // Give the native location service its chance before reporting a browser failure.
      if (nativePending) {
        pendingBrowserReason = reason;
        return;
      }
      settled = true;
      window.clearTimeout(watchdog);
      setUseDevice(false);
      setLocation(null);
      setDeviceLocationStatus("error");
      setFormErrors((current) => ({ ...current, location: "" }));
      setMessage(`${reason} Alternatively, enter a city or district and state.`);
    };
    // Some browser/device combinations fail to fire the Geolocation callback
    // promptly even after permission is granted. Keep the UI from spinning forever.
    watchdog = window.setTimeout(() => {
      finishWithError("The browser did not return a location within 32 seconds. Check site permission and system Location Services, then retry.");
    }, 32000);

    const acceptCoordinates = (coordinates: { latitude: number; longitude: number }) => {
      if (settled || requestId !== deviceLocationRequestId.current) return;
      if (!validLatLon(coordinates)) {
        finishWithError("The device returned invalid coordinates. Check system Location Services, then retry.");
        return;
      }
      settled = true;
      window.clearTimeout(watchdog);
      setLocation({
        mode: "device",
        ...coordinates,
        country: "IN",
        location_consent: true,
      });
      setDeviceLocationStatus("ready");
      setFormErrors((current) => ({ ...current, location: "" }));
      setMessage("Device location is ready for this search only.");
    };

    const requestPosition = (highAccuracy: boolean, timeout: number, maximumAge: number) => {
      try {
        navigator.geolocation.getCurrentPosition(
          (position) => {
            if (settled || requestId !== deviceLocationRequestId.current) return;
            acceptCoordinates({
              latitude: position.coords.latitude,
              longitude: position.coords.longitude,
            });
          },
          (error) => {
            if (settled || requestId !== deviceLocationRequestId.current) return;
            if (!fallbackStarted && error.code !== error.PERMISSION_DENIED) {
              fallbackStarted = true;
              setMessage("Approximate browser location was unavailable; trying higher-accuracy device location.");
              requestPosition(true, 12000, 0);
              return;
            }
            const reason = error.code === error.PERMISSION_DENIED
              ? "Location permission was denied. Allow location access for this site in browser settings, and also check your operating system's location privacy settings."
              : error.code === error.POSITION_UNAVAILABLE
                ? "Permission was granted, but the device could not determine its location. Turn on system Location Services and Wi-Fi/location scanning if available, then retry."
                : error.code === error.TIMEOUT
                  ? "The location request timed out. Turn on system Location Services and retry."
                  : "Device location could not be accessed. Check browser and operating-system location settings.";
            finishWithError(reason);
          },
          { enableHighAccuracy: highAccuracy, timeout, maximumAge },
        );
      } catch {
        if (!fallbackStarted) {
          fallbackStarted = true;
          requestPosition(true, 12000, 0);
        } else {
          finishWithError("Device location could not be accessed. Check browser and operating-system location settings.");
        }
      }
    };

    // Prefer the browser's network-assisted location first. This works better on
    // laptops and desktops that do not have a GPS receiver; use high accuracy only
    // as a fallback when the browser cannot provide an approximate position.
    if (browserAvailable) requestPosition(true, 18000, 120000);

    if (nativeAvailable) {
      void getNativeLocation().then((coordinates) => {
        nativePending = false;
        if (settled || requestId !== deviceLocationRequestId.current) return;
        if (coordinates) {
          acceptCoordinates(coordinates);
          return;
        }
        // Native service gave nothing: report the browser's failure if it already
        // failed, or the native-only failure when the browser was never usable.
        if (pendingBrowserReason !== null) {
          finishWithError(pendingBrowserReason);
        } else if (!browserAvailable) {
          finishWithError(
            "The device's location service did not return a position. Check that Location Services are on and that BloodSignal has location permission.",
          );
        }
      });
    }
  };

  const validateForm = (): SearchRequest | null => {
    const district = values.district.trim();
    const stateName = values.state.trim();
    const nextErrors: Record<string, string> = {};
    if (!stateName) nextErrors.state = "Choose a state to search for blood banks.";
    if (!district) nextErrors.district = "Choose a district or city to search for blood banks.";
    if (Object.keys(nextErrors).length) {
      setFormErrors((current) => ({ ...current, ...nextErrors }));
      setMessage("Choose the state and district to search. Device location is used for distance calculation only.");
      return null;
    }

    // Device GPS is the distance origin, not the inventory search area. Keep the user's
    // selected state and district in the request even when mobile automatically obtains GPS.
    const nextLocation: LocationSelection =
      useDevice && location?.mode === "device"
        ? location
        : { mode: "manual", district, state: stateName, country: "IN" };
    try {
      const payload = buildSearchRequest(values, nextLocation, true);
      const parsed = searchRequestSchema.safeParse(payload);
      if (!parsed.success) {
        setFormErrors(fieldErrors(parsed.error));
        return null;
      }
      return parsed.data;
    } catch (error) {
      if (error instanceof z.ZodError) setFormErrors(fieldErrors(error));
      return null;
    }
  };

  const runSearch = async (payload: SearchRequest) => {
    if (searchInFlight.current) return;
    searchInFlight.current = true;
    setLastPayload(payload);
    setState("submitting");
    setMessage("Submitting your request securely…");
    setCandidates([]);
    setProposal(null);
    setApproved(false);
    setSelected([]);
    try {
      const created = await createRun(payload, await getIdempotencyKey());
      setRun(created);
      setState("tracking");
      setMessage("Request received. Waiting for verified source results…");
      if (completeStatuses.has(created.status.toLowerCase())) await loadCandidates(created.run_id);
      else if (failedStatuses.has(created.status.toLowerCase())) {
        setState("error");
        setMessage("This search could not be completed. No availability was verified.");
      }
    } catch (error) {
      if (error instanceof SearchApiError && Object.keys(error.fieldErrors).length) {
        setFormErrors(error.fieldErrors);
        setState("idle");
        setMessage("Review the highlighted request fields.");
      } else {
        setState("error");
        setMessage(
          searchErrorMessage(
            error,
            "The search service could not be reached. No availability was verified.",
          ),
        );
      }
    } finally {
      searchInFlight.current = false;
    }
  };

  const submitSearch = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFormErrors({});
    const payload = validateForm();
    if (payload) await runSearch(payload);
  };

  const expandNearby = async () => {
    if (!lastPayload) return;
    setIncludeNearby(true);
    await runSearch({
      ...lastPayload,
      search_options: { ...lastPayload.search_options, include_nearby_districts: true },
    });
  };

  const proposeCallList = async () => {
    if (!run || !selected.length) return;
    try {
      const created = await createProposal(run.run_id, selected, await getIdempotencyKey());
      setProposal(created);
      setApproved(false);
      setMessage("");
    } catch (error) {
      setMessage(searchErrorMessage(error, "A call-list proposal could not be created."));
    }
  };

  const approveCallList = async () => {
    if (!proposal || !proposal.items.length || approvalSubmitting || approved) return;
    setApprovalSubmitting(true);
    try {
      await approveProposal(proposal, await getIdempotencyKey());
      setApproved(true);
      downloadReviewReport(proposal, run, lastPayload);
      setMessage("Review recorded. A PDF review sheet was downloaded. No calls were placed and stock was not confirmed.");
    } catch (error) {
      setMessage(searchErrorMessage(error, "Approval could not be recorded. You can retry safely."));
    } finally {
      setApprovalSubmitting(false);
    }
  };

  const activeStatus = state === "submitting" || state === "tracking";

  return (
    <div className="bg-background text-foreground">
      <main id="top" className="mx-auto max-w-7xl px-4 pb-24 pt-8 sm:px-7 sm:pt-12">
        <section className="grid gap-7 border-b border-border pb-9 md:grid-cols-[minmax(0,1fr)_260px] md:items-end">
          <div>
            <p className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.12em] text-primary">
              <span className="size-2 rounded-full bg-destructive" /> Blood search · India
            </p>
            <h1 className="max-w-3xl text-3xl font-semibold leading-tight sm:text-4xl">
              A clearer path to the blood you need.
            </h1>
            <p className="mt-4 max-w-2xl text-base leading-7 text-muted-foreground">
              Search by blood group, component and location. Availability appears only when a source
              provides evidence.
            </p>
          </div>
          <div className="flex items-start gap-3 border-l-2 border-primary/30 pl-4 text-sm leading-6 text-muted-foreground">
            <ShieldCheck className="mt-0.5 size-5 shrink-0 text-primary" />
            <span>
              Places and routes help with location only — they do not confirm blood stock.
            </span>
          </div>
        </section>

        <div className="grid gap-10 pt-9">
          <section id="search" aria-labelledby="request-heading" className="min-w-0">
            <div className="mb-5 flex items-start justify-between gap-4">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                  01 / Your request
                </p>
                <h2 id="request-heading" className="mt-2 text-2xl font-semibold">
                  Request details
                </h2>
              </div>
              <Badge
                variant="outline"
                className="mt-1 shrink-0 rounded-sm border-border px-2.5 py-1 text-xs"
              >
                Manual review
              </Badge>
            </div>

            {showDemoControls ? <div className="mb-5 rounded-md border border-warning/40 bg-warning/5 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h3 className="font-semibold">Official test-case inputs</h3>
                  <p className="mt-1 max-w-2xl text-sm leading-5 text-muted-foreground">
                    Loads the input values for the official test case. Searches still query the configured live source; these presets do not create or simulate inventory.
                  </p>
                </div>
                <Badge variant="outline" className="border-border">Test inputs only</Badge>
              </div>
              <div className="mt-3 grid gap-3 sm:grid-cols-[1fr_auto]">
                <Select value={demoScenario} onValueChange={setDemoScenario}>
                  <SelectTrigger aria-label="Choose official demo scenario"><SelectValue placeholder="Choose a test case" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="tc1">TC1 · O-negative ranking and contacts</SelectItem>
                    <SelectItem value="tc2">TC2 · Stale stock evidence</SelectItem>
                    <SelectItem value="tc3">TC3 · Platelet component mismatch</SelectItem>
                    <SelectItem value="tc4">TC4 · No match and nearby districts</SelectItem>
                  </SelectContent>
                </Select>
                <Button type="button" variant="outline" onClick={() => {
                  const presets: Record<string, Partial<SearchFormValues>> = {
                    tc1: { blood_group: "O-", component: "whole_blood", quantity: "2", quantity_unit: "units", district: "Imphal West", state: "Manipur" },
                    tc2: { blood_group: "O-", component: "whole_blood", quantity: "2", quantity_unit: "units", district: "Imphal West", state: "Manipur" },
                    tc3: { blood_group: "O-", component: "platelet_concentrate", quantity: "1", quantity_unit: "units", district: "Imphal West", state: "Manipur" },
                    tc4: { blood_group: "O-", component: "whole_blood", quantity: "2", quantity_unit: "units", district: "Thoubal", state: "Manipur" },
                  };
                  setValues((current) => ({ ...current, ...presets[demoScenario] }));
                  deviceLocationRequestId.current += 1;
                  setUseDevice(false);
                  setLocation(null);
                  setDeviceLocationStatus("idle");
                  setIncludeNearby(demoScenario === "tc4");
                  setFormErrors({});
                  setMessage(`${demoScenario.toUpperCase()} inputs loaded. Select Search to run this scenario.`);
                }}>Load test case</Button>
              </div>
            </div> : null}
            <form onSubmit={(event) => void submitSearch(event)} noValidate className="space-y-6">
              <section aria-labelledby="describe-request-heading" className="space-y-3">
                <div>
                  <h3
                    id="describe-request-heading"
                    className="flex items-center gap-2 text-lg font-semibold"
                  >
                    <Sparkles className="size-5 text-primary" aria-hidden="true" />
                    Describe it in your own words
                  </h3>
                  <p className="mt-1 text-sm leading-6 text-muted-foreground">
                    Type, speak, or add a prescription photo in a supported language. Suggestions
                    are optional—check every field against the clinician’s instructions before
                    searching.
                  </p>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">
                    Prescription images are sent for field extraction. Avoid uploading images with
                    personal identifiers when possible.
                  </p>
                </div>
                <label className="sr-only" htmlFor="request-description">
                  Describe the blood request
                </label>
                <textarea
                  id="request-description"
                  value={requestDescription}
                  onChange={(event) => setRequestDescription(event.target.value.slice(0, 2000))}
                  maxLength={2000}
                  placeholder="For example: We need 2 units of O-negative red cells in Pune, Maharashtra."
                  className="min-h-36 w-full resize-y rounded-sm border border-input bg-card px-3 py-3 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
                <div className="grid gap-3 sm:grid-cols-2">
                  <Button
                    type="button"
                    variant="outline"
                    className="h-11 justify-start rounded-sm"
                    disabled={
                      assistanceState === "suggesting" || assistanceState === "transcribing"
                    }
                    onClick={() => void suggestFromDescription()}
                  >
                    {assistanceState === "suggesting" ? (
                      <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                    ) : (
                      <Sparkles className="size-4" aria-hidden="true" />
                    )}
                    Suggest fields
                  </Button>
                  <Button
                    type="button"
                    variant={assistanceState === "recording" ? "default" : "outline"}
                    className="h-11 justify-start rounded-sm"
                    disabled={
                      assistanceState === "suggesting" || assistanceState === "transcribing"
                    }
                    onClick={() => void toggleVoiceRecording()}
                  >
                    {assistanceState === "transcribing" ? (
                      <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                    ) : (
                      <Mic className="size-4" aria-hidden="true" />
                    )}
                    {assistanceState === "recording" ? "Stop recording" : "Record voice note"}
                  </Button>
                  <div>
                    <input
                      ref={prescriptionInput}
                      type="file"
                      accept="image/jpeg,image/png,image/webp"
                      className="sr-only"
                      aria-label="Choose prescription image"
                      onChange={(event) => {
                        const file = event.target.files?.[0] ?? null;
                        setPrescription(file);
                        setAssistanceMessage(
                          file ? `${file.name} attached. Select Suggest fields to read it.` : "",
                        );
                      }}
                    />
                    <Button
                      type="button"
                      variant="outline"
                      className="h-11 w-full justify-start rounded-sm"
                      disabled={
                        assistanceState === "suggesting" || assistanceState === "transcribing"
                      }
                      onClick={() => prescriptionInput.current?.click()}
                    >
                      <ImagePlus className="size-4" aria-hidden="true" />
                      {prescription ? "Change prescription" : "Add prescription"}
                    </Button>
                  </div>
                  <div>
                    <label htmlFor="voice-language" className="mb-1 block text-xs font-medium">
                      Language
                    </label>
                    <Select value={voiceLanguage} onValueChange={setVoiceLanguage}>
                      <SelectTrigger id="voice-language" className="h-11 rounded-sm bg-card">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="auto">Detect automatically</SelectItem>
                        {voiceLanguages.map(([code, name]) => (
                          <SelectItem key={code} value={code}>
                            {name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                {prescription ? (
                  <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
                    <span className="min-w-0 truncate">Attached: {prescription.name}</span>
                    <Button
                      type="button"
                      variant="ghost"
                      className="h-8 shrink-0 px-2"
                      onClick={() => {
                        setPrescription(null);
                        if (prescriptionInput.current) prescriptionInput.current.value = "";
                      }}
                    >
                      Remove
                    </Button>
                  </div>
                ) : null}
                {assistanceMessage ? (
                  <p role="status" className="text-xs leading-5 text-muted-foreground">
                    {assistanceMessage}
                  </p>
                ) : null}
              </section>

              <div className="grid grid-cols-2 gap-3 sm:gap-4">
                <div className="min-w-0">
                  <label htmlFor="blood-group" className="mb-2 block text-sm font-medium">
                    Blood group
                  </label>
                  <Select
                    value={values.blood_group}
                    onValueChange={(value) => changeValue("blood_group", value)}
                  >
                    <SelectTrigger id="blood-group" className="h-11 rounded-sm bg-card">
                      <SelectValue placeholder="Choose group" />
                    </SelectTrigger>
                    <SelectContent>
                      {bloodGroups.map((group) => (
                        <SelectItem key={group} value={group}>
                          {group}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {formErrors["blood_group"] ? (
                    <p className="mt-1 text-xs text-destructive">{formErrors["blood_group"]}</p>
                  ) : null}
                </div>
                <div className="min-w-0">
                  <label htmlFor="component" className="mb-2 block text-sm font-medium">
                    Component
                  </label>
                  <Select
                    value={values.component}
                    onValueChange={(value) =>
                      changeValue("component", value as SearchFormValues["component"])
                    }
                  >
                    <SelectTrigger id="component" className="h-11 rounded-sm bg-card">
                      <SelectValue placeholder="Choose component" />
                    </SelectTrigger>
                    <SelectContent>
                      {componentOptions.map((component) => (
                        <SelectItem key={component.value} value={component.value}>
                          {component.displayName}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {formErrors["component"] ? (
                    <p className="mt-1 text-xs text-destructive">{formErrors["component"]}</p>
                  ) : null}
                </div>
              </div>

              <div>
                <label htmlFor="quantity" className="mb-2 block text-sm font-medium">
                  Quantity needed
                </label>
                <div className="grid grid-cols-[minmax(0,1fr)_minmax(120px,0.8fr)] gap-3">
                  <Input
                    id="quantity"
                    inputMode="decimal"
                    type="number"
                    min="1"
                    max="20"
                    step="1"
                    value={values.quantity}
                    onChange={(event) => changeValue("quantity", event.target.value)}
                    className="h-11 rounded-sm bg-card"
                    aria-describedby={formErrors["quantity"] ? "quantity-error" : undefined}
                  />
                  <Select
                    value={values.quantity_unit}
                    onValueChange={(value) =>
                      changeValue("quantity_unit", value as SearchFormValues["quantity_unit"])
                    }
                  >
                    <SelectTrigger
                      id="quantity-unit"
                      aria-label="Quantity unit"
                      className="h-11 rounded-sm bg-card"
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {quantityUnits.map((unit) => (
                        <SelectItem key={unit} value={unit}>
                          {unit}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {formErrors["quantity"] ? (
                  <p id="quantity-error" className="mt-1 text-xs text-destructive">
                    {formErrors["quantity"]}
                  </p>
                ) : null}
              </div>

              <fieldset className="space-y-3">
                <legend className="text-sm font-medium">Search location</legend>
                <p className="text-xs leading-5 text-muted-foreground">
                  Choose the state and district to search for blood banks. Device location is used only to calculate straight-line distance; it does not replace your selected search area. Browser permission remains under your control, and coordinates are used for this search only.
                </p>

                <p role="status" aria-live="polite" className="text-xs text-muted-foreground">
                  {deviceLocationStatus === "ready" && location?.mode === "device"
                    ? "Device location is available for straight-line distance calculations."
                    : deviceLocationStatus === "requesting"
                      ? "Requesting device location… Your browser may ask for permission."
                      : deviceLocationStatus === "error"
                        ? "Device location unavailable. Enter the state and district below; distance will be shown only when valid coordinates are available."
                       : "Select Try device location to request a browser location. HTTPS or localhost is required."}
                </p>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-10 rounded-sm"
                  disabled={deviceLocationStatus === "requesting"}
                  onClick={requestDeviceLocation}
                >
                  <LocateFixed className="size-4" />
                  {deviceLocationStatus === "ready" ? "Refresh device location" : "Try device location"}
                </Button>
                <div className="grid gap-3 sm:grid-cols-2">
                    <div>
                      <label htmlFor="state" className="mb-1.5 block text-xs font-medium">
                        State
                      </label>
                      <Select
                        value={values.state}
                        onValueChange={(value) => {
                          setValues((current) => ({ ...current, state: value, district: "" }));
                          setFormErrors((current) => ({ ...current, state: "", district: "" }));
                        }}
                      >
                        <SelectTrigger id="state" className="h-11 rounded-sm bg-card">
                          <SelectValue placeholder="Choose state or union territory" />
                        </SelectTrigger>
                        <SelectContent>
                          {eraktkoshStates.map((item) => (
                            <SelectItem key={item.id} value={item.displayName}>
                              {item.displayName}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>

                    <div>
                      <label htmlFor="district" className="mb-1.5 block text-xs font-medium">
                        District or city
                      </label>
                      <Select
                        value={values.district}
                        onValueChange={(value) => changeValue("district", value)}
                        disabled={!values.state}
                      >
                        <SelectTrigger id="district" className="h-11 rounded-sm bg-card">
                          <SelectValue placeholder={values.state ? "Choose district" : "Choose a state first"} />
                        </SelectTrigger>
                        <SelectContent>
                          {(districtsByState[values.state] ?? []).map((district) => (
                            <SelectItem key={district} value={district}>{district}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      {!values.state ? <p className="mt-1 text-xs text-muted-foreground">Choose a state to load its district list.</p> : null}
                      {values.state && !(districtsByState[values.state]?.length) ? <p className="mt-1 text-xs text-muted-foreground">A verified local district list is not bundled for this state yet.</p> : null}
                    </div>
                </div>
                {formErrors["location"] ? (
                  <p className="text-xs text-destructive">{formErrors["location"]}</p>
                ) : null}
              </fieldset>

              <p className="border-t border-border pt-4 text-xs leading-5 text-muted-foreground">
                If no confirmed match is found, BloodSignal automatically checks nearby districts. Results are ranked by confirmed availability first, then proximity.
              </p>
              <div className="flex flex-col gap-3 border-t border-border pt-5 sm:flex-row sm:items-center sm:justify-between">
                <p className="max-w-sm text-xs leading-5 text-muted-foreground">
                  Search results depend on participating, identified sources. This is not an
                  emergency service.
                </p>
                <Button
                  type="submit"
                  disabled={activeStatus}
                  className="h-12 w-full rounded-sm px-6 text-sm sm:w-auto"
                >
                  {state === "submitting" ? (
                    <LoaderCircle className="size-4 animate-spin" />
                  ) : (
                    <ArrowRight className="size-4" />
                  )}
                  {state === "submitting" ? "Sending request" : "Search availability"}
                </Button>
              </div>
            </form>
          </section>

          <section
            aria-labelledby="results-heading"
            className="min-w-0 border-t border-border pt-7"
          >
            <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                  02 / Evidence
                </p>
                <h2 id="results-heading" className="mt-2 text-2xl font-semibold">
                  Search results
                </h2>
              </div>
              <div className="flex items-center gap-1 rounded-sm border border-border p-1">
                <Button
                  size="sm"
                  variant={viewMode === "list" ? "secondary" : "ghost"}
                  className="h-8 rounded-sm px-2.5"
                  onClick={() => setViewMode("list")}
                  aria-pressed={viewMode === "list"}
                >
                  List
                </Button>
                <Button
                  size="sm"
                  variant={viewMode === "map" ? "secondary" : "ghost"}
                  className="h-8 rounded-sm px-2.5"
                  onClick={() => setViewMode("map")}
                  aria-pressed={viewMode === "map"}
                >
                  Map
                </Button>
              </div>
            </div>

            <div className="mb-5 flex items-center gap-3 border-y border-border py-3 text-xs text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <Clock3 className="size-3.5" />
                Source freshness matters
              </span>
              {run ? (
                <span className="ml-auto font-mono">Request {run.run_id}</span>
              ) : (
                <span className="ml-auto">No active search</span>
              )}
            </div>

            {run && typeof run["demo_label"] === "string" ? (
              <p
                role="note"
                className="mb-5 rounded-sm border border-warning/40 bg-warning/10 px-4 py-3 text-sm font-semibold text-warning"
              >
                {run["demo_label"]}
              </p>
            ) : null}

            {activeStatus ? (
              <div
                className="space-y-5 border-b border-border py-6"
                role="status"
                aria-live="polite"
              >
                <div className="flex items-start gap-4">
                  <LoaderCircle className="mt-0.5 size-5 shrink-0 animate-spin text-primary motion-reduce:animate-none" />
                  <div>
                    <h3 className="font-semibold">Checking available sources</h3>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {message || "Waiting for the search service…"}
                    </p>
                  </div>
                </div>
                {networkQuality !== "fast" ? (
                  <p className="rounded-sm bg-muted px-3 py-2 text-xs leading-5 text-muted-foreground">
                    {networkQuality === "offline"
                      ? "You’re offline. Your search is saved on this page; we’ll check for results when the connection returns."
                      : "Slow connection detected. Results may take longer; this page will keep checking with fewer requests."}
                  </p>
                ) : null}
                <div aria-hidden="true" className="space-y-3">
                  {[0, 1].map((item) => (
                    <div key={item} className="rounded-sm border border-border p-4">
                      <div className="flex items-center gap-3">
                        <Skeleton className="size-10 rounded-full motion-reduce:animate-none" />
                        <div className="flex-1 space-y-2">
                          <Skeleton className="h-4 w-2/5 motion-reduce:animate-none" />
                          <Skeleton className="h-3 w-3/5 motion-reduce:animate-none" />
                        </div>
                      </div>
                      <Skeleton className="mt-4 h-3 w-full motion-reduce:animate-none" />
                      <Skeleton className="mt-2 h-3 w-4/5 motion-reduce:animate-none" />
                    </div>
                  ))}
                </div>
                {run ? (
                  <p className="font-mono text-xs text-muted-foreground">
                    Request ID: {run.run_id} · {pretty(run.status)}
                  </p>
                ) : null}
              </div>
            ) : null}

            {state === "error" ? (
              <div className="border-b border-destructive/30 py-7" role="alert">
                <div className="flex items-start gap-3">
                  <AlertCircle className="mt-0.5 size-5 shrink-0 text-destructive" />
                  <div>
                    <h3 className="font-semibold">Search results unavailable</h3>
                    <p className="mt-1 text-sm leading-6 text-muted-foreground">
                      {message ||
                        "The search service could not be reached. No blood availability was verified."}
                    </p>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="mt-4 h-10 rounded-sm"
                      onClick={() =>
                        run ? (setState("tracking"), void pollRun(run.run_id)) : setState("idle")
                      }
                    >
                      <RefreshCw className="size-4" />
                      Try again
                    </Button>
                  </div>
                </div>
              </div>
            ) : null}

            {state === "ready" && !visibleCandidates.length ? (
              <div className="border-b border-border py-8">
                <div className="flex gap-3">
                  <UsersRound className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
                  <div>
                    <h3 className="font-semibold">
                      {hiddenNonPositiveCount > 0
                        ? "No positive stock reports returned"
                        : "No matching results returned"}
                    </h3>
                    <p className="mt-1 max-w-lg text-sm leading-6 text-muted-foreground">
                      {hiddenNonPositiveCount > 0
                        ? `${hiddenNonPositiveCount} zero or negative stock report(s) were excluded. No positive report matched this request in the results.`
                        : "No blood banks were returned for this request. This does not confirm whether stock exists."}
                      {" "}This does not confirm whether stock exists elsewhere.
                    </p>
                    {lastPayload && !lastPayload.search_options.include_nearby_districts ? (
                      <>
                        <p className="mt-3 text-sm leading-6">
                          {lastPayload.location.mode === "manual"
                            ? nearbyDistrictSuggestions(lastPayload.location.district).length
                              ? `Suggested nearby districts: ${nearbyDistrictSuggestions(lastPayload.location.district).join(", ")}.`
                              : "A verified nearby-district list is not available for this location yet; choose another district from the selector."
                            : "Choose a district manually to receive nearby-district suggestions."}
                          {" "}These are search suggestions only, not evidence that stock exists there.
                        </p>
                        <p className="mt-2 text-sm leading-6">
                          Search again in nearby districts with the same request:{" "}
                          <strong>{lastPayload.blood_group}</strong>,{" "}
                          {componentLabel(lastPayload.component)}, {lastPayload.quantity}{" "}
                          {lastPayload.quantity_unit}.
                        </p>
                        <Button
                          type="button"
                          variant="outline"
                          className="mt-3 h-11 rounded-sm"
                          onClick={() => void expandNearby()}
                        >
                          <MapPin className="size-4" />
                          Include nearby districts
                        </Button>
                      </>
                    ) : lastPayload ? (
                      <p className="mt-3 text-sm text-muted-foreground">
                        Nearby districts were already included in this search.
                      </p>
                    ) : null}
                  </div>
                </div>
              </div>
            ) : null}

            {state === "ready" && visibleCandidates.length > 0 ? (
              <>
                {confirmedCandidates.length === 0 ? (
                  <div className="mb-5 border border-border p-4" role="status">
                    <h3 className="font-semibold">No confirmed full match for this request</h3>
                    <p className="mt-1 text-sm leading-6 text-muted-foreground">
                      No result passed the blood-group, component, positive-stock, requested-quantity, and freshness checks. Unverified or mismatched records are kept separate and are not counted as matches.
                    </p>
                    {lastPayload?.location.mode === "manual" && nearbyDistrictSuggestions(lastPayload.location.district).length ? (
                      <p className="mt-2 text-sm">
                        Suggested nearby districts: {nearbyDistrictSuggestions(lastPayload.location.district).join(", ")}.
                        These are search suggestions, not stock confirmations.
                      </p>
                    ) : null}
                    {lastPayload && !lastPayload.search_options.include_nearby_districts ? (
                      <Button type="button" variant="outline" className="mt-3 h-11 rounded-sm" onClick={() => void expandNearby()}>
                        <MapPin className="size-4" /> Search nearby districts
                      </Button>
                    ) : (
                      <p className="mt-2 text-xs text-muted-foreground">Nearby districts were already included where the configured area mapping supports them.</p>
                    )}
                  </div>
                ) : null}
                <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium">
                      {showingOtherResults
                        ? "Other facility records — not confirmed matches"
                        : positiveStockCandidates.length > 0
                          ? "Positive reported stock — full and partial matches"
                          : "Other facility records — not confirmed matches"}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground" aria-live="polite">
                      {shownCandidates.length
                        ? `Showing ${startEntry}–${endEntry} of ${shownCandidates.length} ${showingOtherResults ? "other records" : "positive stock reports"}`
                        : showingOtherResults
                          ? "No other facility records were returned."
                          : "No positive stock reports were returned."}
                      {hiddenNonPositiveCount > 0
                        ? ` · ${hiddenNonPositiveCount} zero-stock report(s) hidden`
                        : ""}
                    </p>
                  </div>
                  {positiveStockCandidates.length > 0 && otherCandidates.length > 0 ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="rounded-sm"
                      onClick={() => {
                        setShowOtherResults(!showingOtherResults);
                        setResultPage(1);
                      }}
                    >
                      {showingOtherResults
                        ? `Back to positive stock (${positiveStockCandidates.length})`
                        : `Show other records (${otherCandidates.length})`}
                    </Button>
                  ) : null}
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                    <label htmlFor="travel-mode" className="text-xs text-muted-foreground">
                      Directions
                    </label>
                    <Select
                      value={travelMode}
                      onValueChange={(value) => setTravelMode(value as TravelMode)}
                    >
                      <SelectTrigger id="travel-mode" className="h-9 w-36 rounded-sm">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="driving">Driving</SelectItem>
                        <SelectItem value="walking">Walking</SelectItem>
                        <SelectItem value="transit">Transit</SelectItem>
                        <SelectItem value="two-wheeler">Two-wheeler</SelectItem>
                      </SelectContent>
                    </Select>
                    <label htmlFor="sort" className="text-xs text-muted-foreground">
                      Sort
                    </label>
                    <Select
                      value={sortMode}
                      onValueChange={(value) => setSortMode(value as typeof sortMode)}
                    >
                      <SelectTrigger id="sort" className="h-9 w-48 rounded-sm">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="relevance">Relevance</SelectItem>
                        <SelectItem value="distance">Priority & proximity</SelectItem>
                        <SelectItem value="quantity">Most reported units</SelectItem>
                        <SelectItem value="updated">Most recently updated</SelectItem>
                        <SelectItem value="name">Name</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <div className="mb-4 flex flex-wrap items-center gap-3">
                  <p className="text-xs text-muted-foreground">
                    Priority is live exact positive stock, freshness, surplus/exact/partial
                    quantity, then validated straight-line distance—not road distance.
                  </p>
                </div>
                {viewMode === "map" ? (
                  <div className="mb-5">
                    <BloodBankMap
                      facilities={mapFacilities}
                      origin={sharedDeviceOrigin}
                      originLabel={
                        lastPayload?.location.mode === "manual"
                          ? `${lastPayload.location.district}, ${lastPayload.location.state}, India`
                          : null
                      }
                      travelMode={travelMode}
                    />
                    {mapFacilities.length < shownCandidates.length ? (
                      <p className="mt-3 text-xs leading-5 text-muted-foreground">
                        {shownCandidates.length - mapFacilities.length} result(s) omitted from the
                        map because a facility name was not provided. These results remain
                        available in the list below.
                      </p>
                    ) : null}
                  </div>
                ) : null}
                {viewMode === "list" ? (
                  <>
                  <div className="hidden overflow-x-auto rounded-sm border border-border md:block">
                    <table className="w-full min-w-[1120px] border-collapse text-left text-sm">
                      <caption className="sr-only">
                        Blood bank search results with request match, evidence, distance, contact,
                        and actions
                      </caption>
                      <thead className="bg-muted/70 text-xs uppercase tracking-wide text-muted-foreground">
                        <tr>
                          <th scope="col" className="px-3 py-3">
                            Review
                          </th>
                          <th scope="col" className="px-3 py-3">
                            Facility and location
                          </th>
                          <th scope="col" className="px-3 py-3">
                            Request and reported stock
                          </th>
                          <th scope="col" className="px-3 py-3">
                            Evidence and source
                          </th>
                          <th scope="col" className="px-3 py-3">
                            Source updated
                          </th>
                          <th scope="col" className="px-3 py-3">
                            Straight-line distance
                          </th>
                          <th scope="col" className="px-3 py-3">
                            Contact
                          </th>
                          <th scope="col" className="px-3 py-3">
                            Directions
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border">
                        {paginatedCandidates.map((candidate, index) => {
                          const id = label(candidate["candidate_id"] ?? candidate["id"]);
                          const name =
                            label(candidate["bank_name"] ?? candidate["name"]) ||
                            "Facility name unavailable";
                          const address = label(
                            candidate["address"] ?? candidate["formatted_address"],
                          );
                          const district = label(candidate["district"]);
                          const city = label(candidate["city"]);
                          const stateName = label(candidate["state"]);
                          const locationText =
                            [...new Set([city, district, stateName].filter(Boolean))].join(", ") ||
                            address ||
                            "Location not provided";
                          const phone = label(candidate["phone"] ?? candidate["phone_number"]);
                          const directorySource = label(candidate["directory_source"]);
                          const syntheticFacility =
                            candidate["facility_source_type"] === "demo" ||
                            directorySource === "Synthetic demo directory";
                          const inventorySimulated = candidate["source_type"] === "demo";
                          const status = evidenceState(candidate);
                          const sourceName = label(
                            candidate["source_name"] ??
                              candidate["source_display_name"] ??
                              candidate["source"],
                          );
                          const stock = asValue(candidate["stock"] ?? candidate["availability"]);
                          const sourceUpdate = label(
                            stock["source_updated_at"] ?? candidate["source_updated_at"] ?? stock["source_updated_at_raw"] ?? candidate["source_updated_at_raw"],
                          );
                          const retrieved = label(
                            candidate["retrieved_at"] ?? candidate["retrieval_time"],
                          );
                          const reportedGroup = label(
                            candidate["blood_group_reported"] ?? stock["blood_group"],
                          );
                          const reportedComponent = label(
                            candidate["component_reported"] ?? stock["component"],
                          );
                          const reportedQuantity =
                            candidate["quantity_reported"] ?? stock["quantity"];
                          const reportedUnit = label(
                            candidate["quantity_unit_reported"] ?? stock["quantity_unit"],
                          );
                          const coordinates = getFacilityCoordinates(candidate);
                          const routeMeters =
                            typeof candidate["route_meters"] === "number" &&
                            Number.isFinite(candidate["route_meters"]) &&
                            candidate["route_meters"] >= 0
                              ? candidate["route_meters"]
                              : null;
                          const routeSeconds =
                            typeof candidate["route_seconds"] === "number" &&
                            Number.isFinite(candidate["route_seconds"]) &&
                            candidate["route_seconds"] >= 0
                              ? candidate["route_seconds"]
                              : null;
                          const facilityDestination = {
                            name,
                            ...(coordinates ?? {}),
                            ...(address ? { address } : {}),
                          };
                          const directionsUrl = syntheticFacility
                            ? null
                            : buildDirectionsUrl(
                                facilityDestination,
                                travelMode,
                                lastPayload?.location.mode === "device"
                                  ? `${lastPayload.location.latitude},${lastPayload.location.longitude}`
                                  : lastPayload?.location.mode === "manual"
                                    ? `${lastPayload.location.district}, ${lastPayload.location.state}, India`
                                    : undefined,
                              );
                          const hasDirectionsDestination =
                            !syntheticFacility &&
                            hasReliableFacilityDestination(facilityDestination);
                          const phoneLink = syntheticFacility ? null : telephoneHref(phone);
                          const distanceKm = candidateDistanceKm(candidate, sharedDeviceOrigin);
                          const selectedId = id || "";
                          const reportedSummary = [
                            reportedGroup || "group not stated",
                            reportedComponent || "component not stated",
                            typeof reportedQuantity === "number"
                              ? String(reportedQuantity) + " " + (reportedUnit || "units")
                              : "quantity not reported",
                          ].join(" · ");
                          return (
                            <tr
                              key={selectedId || "candidate-" + index}
                              className="align-top hover:bg-muted/30"
                            >
                              <td className="px-3 py-4">
                                <div className="flex items-center gap-2">
                                  <Checkbox
                                    checked={Boolean(selectedId) && selected.includes(selectedId)}
                                    disabled={!selectedId || !isReviewableCandidate(candidate, eligibilityRequest)}
                                    onCheckedChange={(checked) =>
                                      setSelected((current) =>
                                        checked === true
                                          ? [...new Set([...current, selectedId])]
                                          : current.filter((item) => item !== selectedId),
                                      )
                                    }
                                    aria-label={"Select " + name + " for call-list review"}
                                  />
                                  <span className="font-mono text-xs text-muted-foreground">
                                    {(resultPage - 1) * 5 + index + 1}
                                  </span>
                                </div>
                              </td>
                              <td className="max-w-64 px-3 py-4">
                                <div className="font-semibold">{name}</div>
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="sm"
                                  className="mt-1 h-8 px-2 text-xs"
                                  onClick={() => setPreviewCandidate(candidate)}
                                  aria-label={`Preview details for ${name}`}
                                >
                                  <Eye className="mr-1 size-3.5" aria-hidden="true" /> Preview details
                                </Button>
                                <div className="mt-1 text-xs leading-5 text-muted-foreground">
                                  {locationText}
                                </div>
                                {address && address !== locationText ? (
                                  <div className="mt-1 text-xs leading-5 text-muted-foreground">
                                    {address}
                                  </div>
                                ) : null}
                                {syntheticFacility ? (
                                  <Badge
                                    variant="outline"
                                    className="mt-2 border-warning/40 bg-warning/10 text-warning"
                                  >
                                    Demo location · not a real facility
                                  </Badge>
                                ) : (
                                  <Badge variant="outline" className="mt-2">Real facility</Badge>
                                )}
                              </td>
                              <td className="px-3 py-4">
                                <div className="font-medium">
                                  {lastPayload?.blood_group || "Request"} ·{" "}
                                  {lastPayload
                                    ? componentLabel(lastPayload.component)
                                    : "component"}{" "}
                                  · {lastPayload?.quantity ?? "—"}{" "}
                                  {lastPayload?.quantity_unit || "units"}
                                </div>
                                <div className="mt-2 text-xs leading-5 text-muted-foreground">
                                  {reportedGroup ||
                                  reportedComponent ||
                                  typeof reportedQuantity === "number"
                                    ? "Reported: " + reportedSummary
                                    : "No matching stock quantity reported"}
                                </div>
                              </td>
                              <td className="min-w-52 px-3 py-4">
                                <Badge
                                  variant="outline"
                                  className={"rounded-sm font-normal " + status.tone}
                                >
                                  {status.title}
                                </Badge>
  
                                {inventorySimulated || syntheticFacility ? (
                                  <div className="mt-1 text-xs font-medium text-warning">
                                    Simulated demo data — not verified hospital inventory.
                                  </div>
                                ) : null}
                                {status.warning && !/simulated demo data|not verified hospital inventory/i.test(status.warning) ? (
                                  <div className="mt-1 max-w-64 text-xs leading-5 text-warning">
                                    {status.warning}
                                  </div>
                                ) : null}
                                {sortMode === "distance" ? (
                                  <div className="mt-2 max-w-64 text-xs leading-5 text-muted-foreground">
                                    Rank reason: source trust, then freshness, stock amount, and nearest validated straight-line distance. This is not road distance or travel time.
                                  </div>
                                ) : label(candidate["ranking_explanation"]) ? (
                                  <div className="mt-2 max-w-64 text-xs leading-5 text-muted-foreground">
                                    Rank reason: {label(candidate["ranking_explanation"])}
                                  </div>
                                ) : null}
                              </td>
                              <td className="whitespace-nowrap px-3 py-4 text-xs text-muted-foreground">
                                {sourceUpdate ? formatTime(sourceUpdate) : "Not reported"}
                                {retrieved ? (
                                  <div className="mt-1">Fetched {formatTime(retrieved)}</div>
                                ) : null}
                              </td>
                              <td className="whitespace-nowrap px-3 py-4">
                                {typeof distanceKm === "number" && Number.isFinite(distanceKm) ? (
                                  <>
                                    <div className="font-medium">{formatDistance(distanceKm, candidate)}</div>
                                    <div className="mt-1 text-xs text-muted-foreground">
                                      {candidateLocationPrecision(candidate) === "LOCALITY"
                                        ? "Approx. straight-line from geocoded address, not road distance"
                                        : "Straight-line, not road distance"}
                                    </div>
                                  </>
                                ) : (
                                  <span
                                    className="text-xs text-muted-foreground"
                                    title={label(candidate["distance_unavailable_reason"])}
                                  >
                                    {sharedDeviceOrigin
                                      ? "Distance unavailable"
                                      : "Share device location to calculate distance"}
                                  </span>
                                )}
                              </td>
                              <td className="px-3 py-4">
                                {phoneLink ? (
                                  <a
                                    href={phoneLink}
                                    className="inline-flex min-h-9 items-center gap-1 rounded-sm bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground"
                                    aria-label={"Call " + name + " to confirm availability"}
                                  >
                                    <Phone className="size-3.5" aria-hidden="true" />
                                    {directorySource ? "Public listing · call to verify stock" : "Call to confirm"}
                                  </a>
                                ) : syntheticFacility ? (
                                  <span className="text-xs font-medium text-warning">
                                    DEMO ONLY
                                    <br />
                                    No callable number
                                  </span>
                                ) : (
                                  <span className="text-xs text-muted-foreground">
                                    {phone || "Phone number unavailable"}
                                  </span>
                                )}
                              </td>
                              <td className="px-3 py-4">
                                {directionsUrl ? (
                                  <a
                                    href={directionsUrl}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="inline-flex min-h-9 items-center gap-1 rounded-sm border border-input px-3 py-2 text-xs font-semibold hover:bg-accent"
                                    aria-label={coordinates ? "Get " + travelMode + " directions to " + name : "Find " + name + " on Google Maps"}
                                  >
                                    <Navigation className="size-3.5" aria-hidden="true" />
                                    {coordinates ? "Get directions" : "Find on Google Maps"}
                                  </a>
                                ) : (
                                  <span className="text-xs text-muted-foreground">
                                    {syntheticFacility
                                      ? "Demo location · directions disabled"
                                      : "Reliable destination unavailable"}
                                  </span>
                                )}
                                {routeMeters !== null && routeSeconds !== null ? (
                                  <p className="mt-2 max-w-56 text-xs leading-5 text-muted-foreground">
                                    Computed driving route: {(routeMeters / 1000).toFixed(1)} km ·{" "}
                                    {formatDuration(routeSeconds)}. Google Maps can show turn-by-turn
                                    directions and alternatives.
                                  </p>
                                ) : null}
                                {!hasDirectionsDestination && !syntheticFacility ? (
                                  <span className="sr-only">
                                    Reliable location information is unavailable.
                                  </span>
                                ) : null}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <div className="block space-y-3 md:hidden">
                    {paginatedCandidates.map((candidate, index) => {
                      const id = label(candidate["candidate_id"] ?? candidate["id"]);
                      const name = label(candidate["bank_name"] ?? candidate["name"]) || "Facility name unavailable";
                      const address = label(candidate["address"] ?? candidate["formatted_address"]);
                      const district = label(candidate["district"]);
                      const city = label(candidate["city"]);
                      const stateName = label(candidate["state"]);
                      const locationText = [...new Set([city, district, stateName].filter(Boolean))].join(", ") || address || "Location not provided";
                      const phone = label(candidate["phone"] ?? candidate["phone_number"]);
                      const directorySource = label(candidate["directory_source"]);
                      const syntheticFacility = candidate["facility_source_type"] === "demo" || directorySource === "Synthetic demo directory";
                      const status = evidenceState(candidate);
                      const stock = asValue(candidate["stock"] ?? candidate["availability"]);
                      const reportedGroup = label(candidate["blood_group_reported"] ?? stock["blood_group"]);
                      const reportedComponent = label(candidate["component_reported"] ?? stock["component"]);
                      const reportedQuantity = candidate["quantity_reported"] ?? stock["quantity"];
                      const reportedUnit = label(candidate["quantity_unit_reported"] ?? stock["quantity_unit"]);
                      const sourceUpdate = label(candidate["source_updated_at"] ?? stock["source_updated_at"] ?? candidate["source_updated_at_raw"] ?? stock["source_updated_at_raw"]);
                      const retrieved = label(candidate["retrieved_at"] ?? candidate["retrieval_time"]);
                      const coordinates = getFacilityCoordinates(candidate);
                      const directionsUrl = syntheticFacility ? null : buildDirectionsUrl(
                        { name, ...(coordinates ?? {}), ...(address ? { address } : {}) },
                        travelMode,
                        lastPayload?.location.mode === "device"
                          ? `${lastPayload.location.latitude},${lastPayload.location.longitude}`
                          : lastPayload?.location.mode === "manual"
                            ? `${lastPayload.location.district}, ${lastPayload.location.state}, India`
                            : undefined,
                      );
                      const phoneLink = syntheticFacility ? null : telephoneHref(phone);
                      const distanceKm = candidateDistanceKm(candidate, sharedDeviceOrigin);
                      const reviewable = isReviewableCandidate(candidate, eligibilityRequest);
                      return (
                        <article key={id || `mobile-result-${index}`} className="rounded-sm border border-border p-4">
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <h3 className="font-semibold">{name}</h3>
                              <p className="mt-1 text-sm text-muted-foreground">{locationText}</p>
                              {address && address !== locationText ? <p className="mt-1 text-sm text-muted-foreground">{address}</p> : null}
                            </div>
                            <Checkbox
                              checked={Boolean(id) && selected.includes(id)}
                              disabled={!id || !reviewable}
                              onCheckedChange={(checked) => setSelected((current) => checked === true
                                ? [...new Set([...current, id])]
                                : current.filter((item) => item !== id))}
                              aria-label={`Select ${name} for call-list review`}
                            />
                          </div>
                          <div className="mt-3 flex flex-wrap items-center gap-2">
                            <Badge variant="outline" className={"rounded-sm font-normal " + status.tone}>{status.title}</Badge>
                            {candidate["source_type"] === "demo" ? <Badge variant="outline" className="border-warning/40 bg-warning/10 text-warning">Demo inventory</Badge> : null}
                          </div>
                          {status.warning ? <p className="mt-2 text-sm leading-5 text-warning">{status.warning}</p> : null}
                          <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
                            <div><dt className="text-muted-foreground">Requested</dt><dd>{lastPayload ? `${lastPayload.blood_group} · ${componentLabel(lastPayload.component)} · ${lastPayload.quantity} ${lastPayload.quantity_unit}` : "Not available"}</dd></div>
                            <div><dt className="text-muted-foreground">Reported stock</dt><dd>{reportedGroup || "Group not stated"} · {reportedComponent ? componentLabel(reportedComponent) : "Component not stated"} · {typeof reportedQuantity === "number" ? `${reportedQuantity} ${reportedUnit || "units"}` : "Quantity not reported"}</dd></div>
                            <div><dt className="text-muted-foreground">Source updated</dt><dd>{sourceUpdate ? formatTime(sourceUpdate) : "Not reported"}</dd></div>
                            <div><dt className="text-muted-foreground">Distance</dt><dd>{typeof distanceKm === "number" && Number.isFinite(distanceKm) ? `${formatDistance(distanceKm, candidate)} straight-line` : "Unavailable"}</dd></div>
                          </dl>
                          {retrieved ? <p className="mt-2 text-xs text-muted-foreground">Fetched {formatTime(retrieved)}</p> : null}
                          <div className="mt-4 flex flex-wrap gap-2">
                            <Button type="button" size="sm" variant="outline" onClick={() => setPreviewCandidate(candidate)}>
                              <Eye className="mr-1 size-3.5" aria-hidden="true" /> Preview details
                            </Button>
                            {phoneLink ? <a href={phoneLink} className="inline-flex min-h-9 items-center gap-1 rounded-sm bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground"><Phone className="size-3.5" aria-hidden="true" />Call to confirm</a> : <span className="self-center text-xs text-muted-foreground">{syntheticFacility ? "Demo only · no call" : "Phone unavailable"}</span>}
                            {directionsUrl && hasReliableFacilityDestination({ name, ...(coordinates ?? {}), ...(address ? { address } : {}) }) ? <a href={directionsUrl} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-9 items-center gap-1 rounded-sm border border-input px-3 py-2 text-xs font-semibold"><Navigation className="size-3.5" aria-hidden="true" />Directions</a> : null}
                          </div>
                          {!reviewable ? <p className="mt-2 text-xs text-muted-foreground">This record cannot be added to the call-list review because it is not an exactly scoped live-stock match or uncertain match.</p> : null}
                        </article>
                      );
                    })}
                  </div>
                  </>
                ) : null}
                {!shownCandidates.length ? (
                  <p className="py-5 text-sm text-muted-foreground">
                    {showOtherResults ? "No other facility records were returned." : "No facilities meet every confirmed-match requirement. Show other records to inspect unverified or mismatched source rows."}
                  </p>
                ) : null}
                {shownCandidates.length > 0 ? (
                  <nav className="mt-5 flex flex-wrap items-center justify-between gap-3" aria-label="Search result pages">
                    <span className="text-xs text-muted-foreground">Page {resultPage} of {resultsPageCount}</span>
                    <div className="flex flex-wrap items-center gap-1">
                      <Button type="button" size="sm" variant="outline" disabled={resultPage <= 1} onClick={() => setResultPage((page) => Math.max(1, page - 1))}>Previous</Button>
                      {Array.from({ length: resultsPageCount }, (_, index) => index + 1).map((pageNumber) => (
                        <Button key={pageNumber} type="button" size="sm" variant={resultPage === pageNumber ? "default" : "outline"} aria-current={resultPage === pageNumber ? "page" : undefined} onClick={() => setResultPage(pageNumber)}>{pageNumber}</Button>
                      ))}
                      <Button type="button" size="sm" variant="outline" disabled={resultPage >= resultsPageCount} onClick={() => setResultPage((page) => Math.min(resultsPageCount, page + 1))}>Next</Button>
                    </div>
                  </nav>
                ) : null}
                <div className="mt-5 flex flex-wrap items-center gap-3">
                  <Button
                    type="button"
                    variant="outline"
                    className="h-10 rounded-sm"
                    disabled={!selected.some((candidateId) => {
                      const candidate = candidates.find((item) => label(item["candidate_id"] ?? item["id"]) === candidateId);
                      return Boolean(candidate && isReviewableCandidate(candidate, eligibilityRequest));
                    })}
                    onClick={() => void proposeCallList()}
                  >
                    Prepare call-list review
                  </Button>
                  <span className="text-xs text-muted-foreground">
                    No calls or reservations are made here.
                  </span>
                </div>
              </>
            ) : null}

            {state === "idle" && !run ? (
              <div className="flex min-h-60 flex-col items-center justify-center border-b border-border py-10 text-center">
                <span className="mb-4 grid size-12 place-items-center rounded-full bg-secondary text-primary">
                  <MapPin className="size-5" />
                </span>
                <h3 className="font-semibold">Your results will appear here</h3>
                <p className="mt-2 max-w-sm text-sm leading-6 text-muted-foreground">
                  Start a search to request current information from connected sources. No sample
                  blood-bank results are shown.
                </p>
              </div>
            ) : null}

            {proposal ? (
              <section
                className="mt-7 rounded-md border border-border bg-card px-4 py-5 sm:px-5"
                aria-labelledby="proposal-heading"
              >
                <div className="flex items-start gap-3">
                  <ShieldCheck className="mt-0.5 size-5 shrink-0 text-primary" />
                  <div className="min-w-0 flex-1">
                    <h3 id="proposal-heading" className="font-semibold">
                      Review facilities before calling
                    </h3>
                    <p className="mt-1 text-sm leading-6 text-muted-foreground">
                      Check the contact details and source information. Confirm current availability
                      directly with each facility before travelling.
                    </p>
                    <div className="mt-5">
                      <h4 className="text-sm font-semibold">Selected facilities</h4>
                      {proposal.items.length ? (
                        <ol className="mt-3 divide-y divide-border rounded-md border border-border bg-background">
                          {proposal.items.map((item, index) => {
                            const itemPhoneLink = telephoneHref(item.phone ?? undefined);
                            return (
                              <li
                                key={item.candidate_id || item.bank_id || `proposal-item-${index}`}
                                className="p-4"
                              >
                                <div className="flex flex-wrap items-center gap-2">
                                  <h5 className="font-semibold">{item.bank_name}</h5>
                                  {item.facility_source_type === "demo" ? (
                                    <Badge
                                      variant="outline"
                                      className="rounded-sm border-warning/40 bg-warning/10 text-warning"
                                    >
                                      Demo facility · not callable
                                    </Badge>
                                  ) : (
                                    <Badge variant="outline" className="rounded-sm">Real facility</Badge>
                                  )}
                                  {item.source_type === "demo" ? (
                                    <Badge variant="outline" className="rounded-sm border-warning/40 bg-warning/10 text-warning">Synthetic demo inventory</Badge>
                                  ) : null}
                                </div>
                                <dl className="mt-2 grid gap-x-4 gap-y-2 text-xs sm:grid-cols-2">
                                  <div>
                                    <dt className="font-medium">Inventory evidence</dt>
                                    <dd>{pretty(item.evidence_state ?? item.section)}</dd>
                                  </div>
                                  <div>
                                    <dt className="font-medium">Source update</dt>
                                    <dd>
                                      {item.source_updated_at
                                        ? formatTime(item.source_updated_at)
                                        : "Update time not reported by source"}
                                    </dd>
                                  </div>
                                  <div>
                                    <dt className="font-medium">Location</dt>
                                    <dd>{item.address || "Address unavailable"}</dd>
                                  </div>
                                  <div>
                                    <dt className="font-medium">Requested</dt>
                                    <dd>{item.blood_group || lastPayload?.blood_group || "Unknown"} · {pretty(item.component || lastPayload?.component || "component")} · {lastPayload?.quantity ?? "—"} {lastPayload?.quantity_unit || "units"}</dd>
                                  </div>
                                  <div>
                                    <dt className="font-medium">Reported quantity</dt>
                                    <dd>{item.quantity == null ? "Not reported" : `${item.quantity} ${item.quantity_unit || ""}`}</dd>
                                  </div>
                                  <div>
                                    <dt className="font-medium">Inventory source</dt>
                                    <dd>{item.source_name || item.directory_source || "Source unavailable"} · {item.source_type || "unknown"} mode</dd>
                                  </div>
                                  <div>
                                    <dt className="font-medium">Retrieved by app</dt>
                                    <dd>{item.retrieved_at ? formatTime(item.retrieved_at) : "Retrieval time unavailable"}</dd>
                                  </div>
                                  <div>
                                    <dt className="font-medium">Distance</dt>
                                    <dd>{typeof item.distance_km === "number" ? `${item.distance_km.toFixed(1)} km straight-line` : "Distance unavailable"}</dd>
                                  </div>
                                  <div>
                                    <dt className="font-medium">Telephone</dt>
                                    <dd>
                                      {itemPhoneLink ? (
                                        <a
                                          href={itemPhoneLink}
                                          className="underline underline-offset-2"
                                        >
                                          {item.phone} · Call to confirm
                                        </a>
                                      ) : (
                                        "Phone number unavailable"
                                      )}
                                    </dd>
                                  </div>
                                </dl>
                                {item.display_note ? (
                                  <p className="mt-2 text-xs leading-5 text-muted-foreground">
                                    {item.display_note}
                                  </p>
                                ) : null}
                              </li>
                            );
                          })}
                        </ol>
                      ) : (
                        <p className="mt-2 text-sm text-muted-foreground">
                          No facilities were included. Create a new proposal from selected results.
                        </p>
                      )}
                      <p className="mt-2 text-xs text-muted-foreground">
                        Confirm current stock with the facility before travelling. Source update
                        times describe what the source reported; they do not guarantee current
                        availability.
                      </p>
                    </div>
                    {approved ? (
                      <p
                        role="status"
                        className="mt-4 flex items-center gap-2 text-sm font-medium text-primary"
                      >
                        <CheckCircle2 className="size-4" />
                        Review recorded
                      </p>
                    ) : (
                      <Button
                        type="button"
                        className="mt-4 h-10 rounded-sm"
                        onClick={() => void approveCallList()}
                        disabled={!proposal.approval_id || !proposal.items.length || approvalSubmitting || approved}
                      >
                        Confirm review
                      </Button>
                    )}
                    {!proposal.approval_id && !approved ? (
                      <p className="mt-2 text-xs text-muted-foreground">
                        Review approval is unavailable because the service did not provide an
                        approval reference.
                      </p>
                    ) : null}
                  </div>
                </div>
              </section>
            ) : null}

            {message && !activeStatus && state !== "error" && !(proposal && approved) ? (
              <p role="status" aria-live="polite" className="mt-4 text-sm text-muted-foreground">
                {message}
              </p>
            ) : null}
          </section>
        </div>

        <p className="mt-10 border-t border-border pt-5 text-xs leading-5 text-muted-foreground">
          Blood availability can change quickly. Confirm every detail directly with the blood bank
          and your care team. This service does not provide medical advice or emergency response.
        </p>
      </main>
      <div className="fixed inset-x-0 bottom-0 z-20 border-t border-border bg-background p-3 sm:hidden">
        <Button
          type="button"
          className="h-12 w-full rounded-sm"
          onClick={() => document.getElementById("search")?.scrollIntoView({ behavior: "smooth" })}
        >
          Go to request details <ArrowRight className="size-4" />
        </Button>
      </div>
      {previewCandidate ? (() => {
        const preview = previewCandidate;
        const previewStock = asValue(preview["stock"] ?? preview["availability"]);
        const previewName = label(preview["bank_name"] ?? preview["name"]) || "Facility name unavailable";
        const previewAddress = label(preview["address"] ?? preview["formatted_address"]);
        const previewPhone = label(preview["phone"] ?? preview["phone_number"]);
        const previewEmails = contactValues(preview, "emails", "email");
        const previewFaxes = contactValues(preview, "faxes", "fax");
        const previewEmail = previewEmails[0];
        const previewSourceTime = label(preview["source_updated_at"] ?? previewStock["source_updated_at"] ?? preview["source_updated_at_raw"] ?? previewStock["source_updated_at_raw"]);
        const previewFetchedTime = label(preview["retrieved_at"] ?? preview["retrieval_time"]);
        const previewCity = label(preview["city"]);
        const previewDistrict = label(preview["district"]);
        const previewState = label(preview["state"]);
        const previewDestination = [previewName, previewCity, previewDistrict, previewState].filter(Boolean).join(", ");
        const previewLat = typeof preview["latitude"] === "number" ? preview["latitude"] : null;
        const previewLon = typeof preview["longitude"] === "number" ? preview["longitude"] : null;
        const previewMapsSearch = previewLat !== null && previewLon !== null
          ? `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${previewLat},${previewLon}`)}`
          : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(previewDestination)}`;
        const previewDirections = previewLat !== null && previewLon !== null
          ? `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(`${previewLat},${previewLon}`)}`
          : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(previewDestination)}`;
        const previewFacilityType = label(preview["facility_type"] ?? preview["classification"] ?? preview["category"]);
        const previewClassification = preview["source_type"] === "live" || preview["source"] === "eraktkosh" || preview["match_state"] === "MATCHED_REPORTED"
          ? (previewFacilityType ? `Reported (${previewFacilityType})` : "Reported")
          : (previewFacilityType ?? "Not reported");
        return (
          <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-0 sm:items-center sm:p-4" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setPreviewCandidate(null); }}>
            <section role="dialog" aria-modal="true" aria-labelledby="facility-preview-title" className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-t-xl border border-border bg-background p-5 shadow-xl sm:rounded-xl">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 id="facility-preview-title" className="text-lg font-semibold">{previewName}</h2>
                  <p className="mt-1 text-sm text-muted-foreground">Facility details from the returned source record</p>
                </div>
                <Button type="button" variant="ghost" size="icon" onClick={() => setPreviewCandidate(null)} aria-label="Close facility preview"><X className="size-4" /></Button>
              </div>
              <dl className="mt-5 grid gap-4 text-sm sm:grid-cols-2">
                <div><dt className="font-medium">Facility code / ID</dt><dd className="mt-1 break-words text-muted-foreground">{label(preview["bank_id"] ?? preview["facility_code"] ?? preview["candidate_id"] ?? preview["id"]) || "Not reported"}</dd></div>
                <div><dt className="font-medium">Classification</dt><dd className="mt-1 text-muted-foreground">{previewClassification}</dd></div>
                <div className="sm:col-span-2"><dt className="font-medium">Address</dt><dd className="mt-1 text-muted-foreground">{previewAddress || "Address not reported"}</dd></div>
                <div><dt className="font-medium">Blood group reported</dt><dd className="mt-1 text-muted-foreground">{label(preview["blood_group_reported"] ?? previewStock["blood_group"]) || "Not reported"}</dd></div>
                <div><dt className="font-medium">Component reported</dt><dd className="mt-1 text-muted-foreground">{componentLabel(label(preview["component_reported"] ?? previewStock["component"]) || "Unknown")}</dd></div>
                <div><dt className="font-medium">Reported quantity</dt><dd className="mt-1 text-muted-foreground">{typeof (preview["quantity_reported"] ?? previewStock["quantity"]) === "number" ? `${preview["quantity_reported"] ?? previewStock["quantity"]} ${label(preview["quantity_unit_reported"] ?? previewStock["quantity_unit"]) || "units"}` : "Quantity not reported"}</dd></div>
                <div><dt className="font-medium">Evidence status</dt><dd className="mt-1 text-muted-foreground">{evidenceState(preview).title}</dd></div>
                <div><dt className="font-medium">Source updated</dt><dd className="mt-1 text-muted-foreground">{previewSourceTime ? formatTime(previewSourceTime) : "Not reported by source"}</dd></div>
                <div><dt className="font-medium">Last checked by BloodSignal</dt><dd className="mt-1 text-muted-foreground">{previewFetchedTime ? formatTime(previewFetchedTime) : "Not available"}</dd></div>
                <div><dt className="font-medium">Phone</dt><dd className="mt-1 text-muted-foreground">{previewPhone || "No phone reported"}</dd></div>
                <div><dt className="font-medium">Email</dt><dd className="mt-1 break-words text-muted-foreground">{previewEmails.length ? previewEmails.map((email, i) => (<span key={email}>{i ? ", " : ""}<a href={`mailto:${email}`} className="underline underline-offset-2">{email}</a></span>)) : "No email reported"}</dd></div>
                <div><dt className="font-medium">Fax (not callable)</dt><dd className="mt-1 text-muted-foreground">{previewFaxes.length ? previewFaxes.join(", ") : "No fax reported"}</dd></div>
              </dl>
              {evidenceState(preview).warning ? <p className="mt-4 rounded-md border border-warning/40 bg-warning/10 p-3 text-sm">{evidenceState(preview).warning}</p> : null}
              <p className="mt-4 text-sm text-muted-foreground">Stock may change. Call the blood centre to confirm before travelling.</p>
              <div className="mt-5 flex flex-wrap gap-2">
                {telephoneHref(previewPhone || undefined) ? <a href={telephoneHref(previewPhone || undefined) || undefined} className="inline-flex min-h-10 items-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"><Phone className="mr-2 size-4" />Call to confirm</a> : <Button type="button" disabled variant="outline">Phone unavailable</Button>}
                {previewEmail ? <a href={`mailto:${previewEmail}`} className="inline-flex min-h-10 items-center rounded-md border border-input px-4 py-2 text-sm font-medium">Email</a> : null}
                {previewDestination.trim() ? <><a href={previewMapsSearch} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-10 items-center rounded-md border border-input px-4 py-2 text-sm font-medium"><MapPin className="mr-2 size-4" />Google Maps search</a><a href={previewDirections} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-10 items-center rounded-md border border-input px-4 py-2 text-sm font-medium"><Navigation className="mr-2 size-4" />Open in Google Maps</a></> : null}
              </div>
            </section>
          </div>
        );
      })() : null}
    </div>
  );
}
