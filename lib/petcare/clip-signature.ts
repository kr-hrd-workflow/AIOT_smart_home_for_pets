import { PetCareError } from "./errors";

const BASE64URL = /^[A-Za-z0-9_-]+$/;
const EVENT_ID = /^[A-Za-z0-9_-]+$/;
const RFC3339_UTC =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?Z$/;

export const CLIP_MAX_BYTES = 50 * 1024 * 1024;
export const CLIP_SIGNATURE_WINDOW_SECONDS = 300;

export type ClipEventType = "bed_sensor_mismatch" | "eating" | "resting";

export type ClipEvent = {
  eventType: ClipEventType;
  eventId: string;
};

export type SignedClipHeaders = {
  agentId: string;
  cameraId: string;
  timestamp: number;
  nonce: string;
  digest: string;
  startedAt: string;
  endedAt: string;
  events: ClipEvent[];
  signature: Uint8Array;
};

function fail(code = "invalid_clip_request"): never {
  throw new PetCareError(400, code);
}

function singleton(headers: Headers, name: string, allowCommas = false): string {
  const value = headers.get(name);
  if (!value || /[\u0000-\u001f\u007f]/.test(value)) fail();
  if (!allowCommas && value.includes(",")) fail();
  return value;
}

export function decodeBase64Url(value: string, bytes: number): Uint8Array {
  if (!BASE64URL.test(value)) fail();
  let binary: string;
  try {
    binary = atob(value.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((value.length + 3) % 4));
  } catch {
    fail();
  }
  const decoded = Uint8Array.from(binary!, (character) => character.charCodeAt(0));
  if (decoded.byteLength !== bytes || encodeBase64Url(decoded) !== value) fail();
  return decoded;
}

export function encodeBase64Url(value: Uint8Array): string {
  let binary = "";
  for (const byte of value) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function parseUtcNanoseconds(value: string): { seconds: number; nanoseconds: number } {
  const match = RFC3339_UTC.exec(value);
  if (!match) fail();
  const [, yearText, monthText, dayText, hourText, minuteText, secondText, fraction = ""] = match;
  const [year, month, day, hour, minute, second] = [
    yearText,
    monthText,
    dayText,
    hourText,
    minuteText,
    secondText,
  ].map(Number);
  if (month < 1 || month > 12 || day < 1 || hour > 23 || minute > 59 || second > 59) fail();
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day);
  date.setUTCHours(hour, minute, second, 0);
  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day ||
    date.getUTCHours() !== hour ||
    date.getUTCMinutes() !== minute ||
    date.getUTCSeconds() !== second
  ) fail();
  return {
    seconds: Math.trunc(date.getTime() / 1000),
    nanoseconds: Number(fraction.padEnd(9, "0")),
  };
}

function parseEvents(value: string): ClipEvent[] {
  if (!value || /\s/.test(value)) fail();
  const tuples = value.split(",");
  const seen = new Set<string>();
  let previous = "";
  return tuples.map((tuple) => {
    const separator = tuple.indexOf(":");
    if (separator <= 0 || separator !== tuple.lastIndexOf(":")) fail();
    const eventType = tuple.slice(0, separator) as ClipEventType;
    const eventId = tuple.slice(separator + 1);
    if (
      !(["bed_sensor_mismatch", "eating", "resting"] as string[]).includes(eventType) ||
      !EVENT_ID.test(eventId) ||
      seen.has(tuple) ||
      (previous && tuple <= previous)
    ) fail("invalid_clip_events");
    seen.add(tuple);
    previous = tuple;
    return { eventType, eventId };
  });
}

export function parseSignedClipHeaders(request: Request): SignedClipHeaders {
  const url = new URL(request.url);
  if (request.method !== "POST" || url.pathname !== "/api/petcare/agent/clips" || url.search) fail();
  if (singleton(request.headers, "Content-Type") !== "video/mp4") fail("invalid_content_type");

  const contentLengthText = singleton(request.headers, "Content-Length");
  if (!/^(?:[1-9]\d*)$/.test(contentLengthText)) fail("invalid_content_length");
  const contentLength = Number(contentLengthText);
  if (!Number.isSafeInteger(contentLength) || contentLength > CLIP_MAX_BYTES) {
    fail("invalid_content_length");
  }

  const agentId = singleton(request.headers, "X-PetCare-Agent-Id");
  const cameraId = singleton(request.headers, "X-PetCare-Camera-Id");
  const timestampText = singleton(request.headers, "X-PetCare-Timestamp");
  if (!/^(?:0|[1-9]\d*)$/.test(timestampText)) fail();
  const timestamp = Number(timestampText);
  if (!Number.isSafeInteger(timestamp)) fail();

  const nonce = singleton(request.headers, "X-PetCare-Nonce");
  const digest = singleton(request.headers, "X-PetCare-Content-SHA256");
  const startedAt = singleton(request.headers, "X-PetCare-Started-At");
  const endedAt = singleton(request.headers, "X-PetCare-Ended-At");
  const eventHeader = singleton(request.headers, "X-PetCare-Events", true);
  const signatureText = singleton(request.headers, "X-PetCare-Signature");

  decodeBase64Url(nonce, 16);
  decodeBase64Url(digest, 32);
  const signature = decodeBase64Url(signatureText, 64);
  const start = parseUtcNanoseconds(startedAt);
  const end = parseUtcNanoseconds(endedAt);
  const seconds = end.seconds - start.seconds;
  const nanoseconds = end.nanoseconds - start.nanoseconds;
  if (
    seconds < 0 ||
    (seconds === 0 && nanoseconds <= 0) ||
    seconds > 120 ||
    (seconds === 120 && nanoseconds > 0)
  ) fail("invalid_clip_duration");
  const events = parseEvents(eventHeader);

  return {
    agentId,
    cameraId,
    timestamp,
    nonce,
    digest,
    startedAt,
    endedAt,
    events,
    signature,
  };
}

export function canonicalClipRequest(headers: SignedClipHeaders): Uint8Array {
  return new TextEncoder().encode(
    [
      "PETCARE-CLIP-V1",
      "POST",
      "/api/petcare/agent/clips",
      headers.agentId,
      headers.cameraId,
      String(headers.timestamp),
      headers.nonce,
      headers.digest,
      headers.startedAt,
      headers.endedAt,
      headers.events.map(({ eventType, eventId }) => `${eventType}:${eventId}`).join(","),
      "",
    ].join("\n"),
  );
}

export async function verifyClipSignature(
  headers: SignedClipHeaders,
  publicKey: string,
): Promise<void> {
  try {
    const rawKey = decodeBase64Url(publicKey, 32);
    const key = await crypto.subtle.importKey(
      "raw",
      Uint8Array.from(rawKey).buffer,
      { name: "Ed25519" },
      false,
      ["verify"],
    );
    const valid = await crypto.subtle.verify(
      { name: "Ed25519" },
      key,
      Uint8Array.from(headers.signature).buffer,
      Uint8Array.from(canonicalClipRequest(headers)).buffer,
    );
    if (!valid) throw new PetCareError(401, "invalid_agent_signature");
  } catch (error) {
    if (error instanceof PetCareError && error.code === "invalid_agent_signature") throw error;
    throw new PetCareError(401, "invalid_agent_signature");
  }
}
