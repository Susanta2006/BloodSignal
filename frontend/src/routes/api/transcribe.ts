import { createFileRoute } from "@tanstack/react-router";

const MAX_AUDIO_BYTES = 14 * 1024 * 1024;

function jsonError(message: string, status: number) {
  return Response.json({ message }, { status, headers: { "Cache-Control": "no-store" } });
}

export const Route = createFileRoute("/api/transcribe")({
  server: {
    handlers: {
      POST: async ({ request }) => {
        const declaredLength = Number(request.headers.get("content-length") || 0);
        if (declaredLength > MAX_AUDIO_BYTES + 64_000)
          return jsonError("That recording is too large. Try a shorter voice note.", 413);

        let form: FormData;
        try {
          form = await request.formData();
        } catch {
          return jsonError("The voice note could not be read.", 400);
        }
        const upload = form.get("file");
        if (!(upload instanceof File) || !upload.size)
          return jsonError("Choose a voice note before transcribing.", 400);
        if (upload.size > MAX_AUDIO_BYTES)
          return jsonError("That recording is too large. Try a shorter voice note.", 413);
        if (!upload.type.startsWith("audio/"))
          return jsonError("Choose an audio recording, such as a voice note.", 415);

        const configuredBackendUrl = process.env["BACKEND_INTERNAL_URL"]?.trim();
        const backendUrl = configuredBackendUrl || (
          process.env["NODE_ENV"] === "production" ? "" : "http://127.0.0.1:8000"
        );
        if (!backendUrl) {
          return jsonError(
            "Voice assistance is not connected to the backend. Set BACKEND_INTERNAL_URL in the frontend deployment.",
            503,
          );
        }
        const clientIp =
          request.headers.get("cf-connecting-ip") || request.headers.get("x-real-ip");
        const gatewayForm = new FormData();
        gatewayForm.append("file", upload, upload.name || "voice-note.webm");
        const language = form.get("language");
        if (typeof language === "string") gatewayForm.append("language", language);

        let upstream: Response;
        try {
          upstream = await fetch(`${backendUrl.replace(/\/+$/, "")}/api/transcribe`, {
            method: "POST",
            headers: clientIp
              ? {
                  "X-Forwarded-For": clientIp,
                  "X-Forwarded-Proto": new URL(request.url).protocol.slice(0, -1),
                }
              : {},
            body: gatewayForm,
            signal: AbortSignal.timeout(45_000),
          });
        } catch (error) {
          const message = error instanceof Error && error.name === "TimeoutError"
            ? "Voice transcription took too long. Try a shorter recording or type your request."
            : "Voice assistance could not be reached. You can type your request instead.";
          return jsonError(message, 503);
        }

        const body = await upstream.text();
        return new Response(body, {
          status: upstream.status,
          headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
        });
      },
    },
  },
});
