import { createHash, randomUUID } from "node:crypto";
import { isIP } from "node:net";
import fs from "node:fs";
import path from "node:path";
import { NextRequest, NextResponse } from "next/server";
import {
  categoryForType,
  isRecommendationType,
  normalizePublicUrl,
  sanitizeText,
  type RecommendationType,
} from "@/lib/recommendationResolver";
import { checkRecommendationRateLimit } from "@/lib/recommendationRateLimit";

export const runtime = "nodejs";

type JsonRecord = Record<string, unknown>;

interface RecommendationDatabase {
  queue: JsonRecord[];
  history: JsonRecord[];
}

interface StorageSnapshot {
  database: RecommendationDatabase;
  sha: string | null;
  source: "github" | "local" | "empty";
}

interface GitHubConfig {
  token: string;
  repo: string;
  branch: string;
}

interface AppendPayload {
  action: "append";
  item?: {
    type?: unknown;
    title?: unknown;
    link?: unknown;
  };
}

interface ReplacePayload {
  action?: "replace";
  queue?: unknown;
  history?: unknown;
}

interface CommunitySubmissionSnapshot {
  readonly id: string;
  readonly sourceKind: "community_suggestion";
  readonly type: RecommendationType;
  readonly title: string;
  readonly link: string;
  readonly createdAt: string;
}

interface DiscordDeliveryProof {
  channelId: string;
  messageId: string;
  sentAt: string;
  payloadHash: string;
}

const STORAGE_RELATIVE_PATH = "website/public/recommendations.json";
const COMMUNITY_SOURCE_KIND = "community_suggestion" as const;
const COMMUNITY_ORIGINS = new Set([
  "community",
  "discord",
  "discord-command",
  "website",
  "website-community",
]);
const COMMUNITY_RESOLUTION_STATUSES = new Set([
  "unresolved",
  "verified",
  "rejected",
]);
const MAX_REQUEST_BYTES = 1_000_000;
const MAX_LINK_LENGTH = 2_048;
const MAX_RECORDS_PER_LIST = 1_000;
const MAX_STORAGE_RETRIES = 3;
const DISCORD_RETRY_EVENT = "deliver-pending-suggestions";
const INVISIBLE_OR_CONTROL_CHARACTERS =
  /[\u0000-\u001F\u007F-\u009F\p{Cf}\p{Cs}\p{Co}\p{Cn}]/gu;

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

class StorageConflictError extends Error {
  constructor() {
    super("A fila foi alterada por outro pedido.");
    this.name = "StorageConflictError";
  }
}

function emptyDatabase(): RecommendationDatabase {
  return { queue: [], history: [] };
}

function isJsonRecord(value: unknown): value is JsonRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function stripInvisibleOrControlCharacters(value: string): string {
  return value.replace(INVISIBLE_OR_CONTROL_CHARACTERS, "");
}

function sanitizeSubmissionText(value: unknown, maxLength = 600): string {
  return stripInvisibleOrControlCharacters(sanitizeText(value, maxLength))
    .replace(/\s+/gu, " ")
    .trim()
    .slice(0, maxLength);
}

function sanitizeIdentifier(value: unknown, position: string): string {
  const identifier = sanitizeSubmissionText(value, 140);
  if (!identifier || !/^[A-Za-z0-9][A-Za-z0-9_.:-]*$/u.test(identifier)) {
    throw new ApiError(`${position}: identificador invalido.`, 400);
  }
  return identifier;
}

function isForbiddenLiteralHostname(hostname: string): boolean {
  const host = hostname.toLocaleLowerCase().replace(/^\[|\]$/gu, "");
  if (
    host === "localhost" ||
    host === "localhost.localdomain" ||
    host.endsWith(".localhost") ||
    host.endsWith(".local")
  ) {
    return true;
  }

  const mappedIpv4 = host.match(/^::ffff:(\d{1,3}(?:\.\d{1,3}){3})$/u)?.[1];
  if (mappedIpv4) return isForbiddenLiteralHostname(mappedIpv4);

  const family = isIP(host);
  if (family === 6) {
    return (
      host === "::" ||
      host === "::1" ||
      host.startsWith("fc") ||
      host.startsWith("fd") ||
      host.startsWith("fe8") ||
      host.startsWith("fe9") ||
      host.startsWith("fea") ||
      host.startsWith("feb") ||
      host.startsWith("ff")
    );
  }
  if (family !== 4) return false;

  const octets = host.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/u);
  if (!octets) return true;
  const [a, b, c] = octets.slice(1).map(Number);
  if ([a, b, c, Number(octets[4])].some((octet) => octet < 0 || octet > 255)) {
    return true;
  }

  return (
    a === 0 ||
    a === 10 ||
    a === 127 ||
    (a === 100 && b >= 64 && b <= 127) ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 0 && c === 0) ||
    (a === 192 && b === 0 && c === 2) ||
    (a === 192 && b === 168) ||
    (a === 198 && (b === 18 || b === 19)) ||
    (a === 198 && b === 51 && c === 100) ||
    (a === 203 && b === 0 && c === 113) ||
    a >= 224
  );
}

function normalizePublicSubmissionLink(
  value: unknown,
  position = "link",
): string {
  if (value === undefined || value === null) return "";
  if (typeof value !== "string") {
    throw new ApiError(`${position}: link invalido.`, 400);
  }

  const cleaned = stripInvisibleOrControlCharacters(value).trim();
  if (!cleaned) return "";
  if (cleaned.length > MAX_LINK_LENGTH || /[<>]/u.test(cleaned)) {
    throw new ApiError(`${position}: link invalido.`, 400);
  }

  const normalized = normalizePublicUrl(cleaned);
  if (!normalized) throw new ApiError(`${position}: link invalido.`, 400);

  try {
    const parsed = new URL(normalized);
    if (
      (parsed.protocol !== "http:" && parsed.protocol !== "https:") ||
      parsed.username ||
      parsed.password ||
      isForbiddenLiteralHostname(parsed.hostname)
    ) {
      throw new ApiError(`${position}: link invalido.`, 400);
    }
    return normalized;
  } catch (error: unknown) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(`${position}: link invalido.`, 400);
  }
}

function validTimestamp(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length <= 80 &&
    Number.isFinite(new Date(value).getTime())
  );
}

function requireTimestamp(value: unknown, position: string): string {
  if (!validTimestamp(value)) {
    throw new ApiError(`${position}: data invalida.`, 400);
  }
  return value;
}

function submissionHash(snapshot: CommunitySubmissionSnapshot): string {
  const canonicalPayload = JSON.stringify([
    "approval-v1",
    snapshot.id,
    snapshot.sourceKind,
    snapshot.type,
    snapshot.title,
    snapshot.link,
    snapshot.createdAt,
  ]);
  return createHash("sha256").update(canonicalPayload, "utf8").digest("hex");
}

function isSubmissionHash(value: unknown): value is string {
  return typeof value === "string" && /^[a-f0-9]{64}$/u.test(value);
}

function isCommunityRecord(value: JsonRecord): boolean {
  const origin =
    typeof value.origin === "string"
      ? value.origin.trim().toLocaleLowerCase()
      : "";
  const id =
    typeof value.id === "string" ? value.id.trim().toLocaleLowerCase() : "";
  return (
    value.sourceKind === COMMUNITY_SOURCE_KIND ||
    "communitySubmission" in value ||
    "submissionHash" in value ||
    COMMUNITY_ORIGINS.has(origin) ||
    id.startsWith("web_") ||
    id.startsWith("discord_")
  );
}

function parseRecommendations(content: string): RecommendationDatabase {
  let parsed: unknown;
  try {
    parsed = JSON.parse(content || "{}");
  } catch {
    throw new ApiError(
      "O ficheiro recommendations.json esta corrompido; a escrita foi bloqueada.",
      500,
    );
  }

  let database: RecommendationDatabase;
  if (Array.isArray(parsed)) {
    if (parsed.some((item) => !isJsonRecord(item))) {
      throw new ApiError(
        "A lista legacy contem registos invalidos; a escrita foi bloqueada.",
        500,
      );
    }
    database = { queue: parsed, history: [] };
  } else {
    if (!isJsonRecord(parsed)) {
      throw new ApiError("O ficheiro recommendations.json nao e um objeto.", 500);
    }
    if (
      (parsed.queue !== undefined && !Array.isArray(parsed.queue)) ||
      (parsed.history !== undefined && !Array.isArray(parsed.history))
    ) {
      throw new ApiError(
        "queue/history tem um formato invalido em recommendations.json.",
        500,
      );
    }
    if (
      (Array.isArray(parsed.queue) &&
        parsed.queue.some((item) => !isJsonRecord(item))) ||
      (Array.isArray(parsed.history) &&
        parsed.history.some((item) => !isJsonRecord(item)))
    ) {
      throw new ApiError(
        "recommendations.json contem registos invalidos; a escrita foi bloqueada.",
        500,
      );
    }
    database = {
      queue: Array.isArray(parsed.queue)
        ? parsed.queue.filter(isJsonRecord)
        : [],
      history: Array.isArray(parsed.history)
        ? parsed.history.filter(isJsonRecord)
        : [],
    };
  }

  assertStoredCommunityRecords(database);
  return database;
}

function githubConfig(): GitHubConfig | null {
  const token = process.env.GITHUB_TOKEN?.trim();
  const repo = process.env.GITHUB_REPO?.trim();
  if (!token || !repo || token.includes("your_actual")) return null;
  return {
    token,
    repo,
    branch: process.env.GITHUB_BRANCH?.trim() || "main",
  };
}

function githubFileUrl(
  config: GitHubConfig,
  relativePath: string,
  includeRef: boolean,
): string {
  const encodedPath = relativePath
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  const base = `https://api.github.com/repos/${config.repo}/contents/${encodedPath}`;
  return includeRef
    ? `${base}?ref=${encodeURIComponent(config.branch)}`
    : base;
}

async function dispatchDiscordRetryWorkflow(): Promise<boolean> {
  const config = githubConfig();
  if (!config) {
    console.warn(
      "[suggestions API] Recuperacao imediata indisponivel: GitHub nao configurado.",
    );
    return false;
  }
  try {
    const response = await fetch(
      `https://api.github.com/repos/${config.repo}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${config.token}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          event_type: DISCORD_RETRY_EVENT,
          client_payload: { branch: config.branch },
        }),
        cache: "no-store",
      },
    );
    if (response.ok) return true;
    console.error(
      `[suggestions API] Nao foi possivel iniciar a recuperacao imediata (HTTP ${response.status}).`,
      sanitizeText(await response.text(), 500),
    );
  } catch (error: unknown) {
    console.error(
      "[suggestions API] Falha ao iniciar a recuperacao imediata:",
      error,
    );
  }
  return false;
}

async function readGitHubSnapshot(
  config: GitHubConfig,
): Promise<StorageSnapshot> {
  const response = await fetch(githubFileUrl(config, STORAGE_RELATIVE_PATH, true), {
    headers: {
      Authorization: `Bearer ${config.token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "Cache-Control": "no-cache",
    },
    cache: "no-store",
  });

  if (response.status === 404) {
    return { database: emptyDatabase(), sha: null, source: "github" };
  }
  if (!response.ok) {
    throw new ApiError(
      `Nao foi possivel ler a fila no GitHub (HTTP ${response.status}).`,
      502,
    );
  }

  const payload = (await response.json()) as {
    content?: unknown;
    sha?: unknown;
  };
  if (typeof payload.content !== "string") {
    throw new ApiError("O ficheiro da fila no GitHub e invalido.", 502);
  }

  return {
    database: parseRecommendations(
      Buffer.from(payload.content.replace(/\s/gu, ""), "base64").toString(
        "utf-8",
      ),
    ),
    sha: typeof payload.sha === "string" ? payload.sha : null,
    source: "github",
  };
}

function localStoragePath(): string {
  return path.join(process.cwd(), "public", "recommendations.json");
}

function readLocalSnapshot(): StorageSnapshot {
  const filePath = localStoragePath();
  if (!fs.existsSync(filePath)) {
    return { database: emptyDatabase(), sha: null, source: "empty" };
  }
  return {
    database: parseRecommendations(fs.readFileSync(filePath, "utf-8")),
    sha: null,
    source: "local",
  };
}

async function readStorageSnapshot(): Promise<StorageSnapshot> {
  const config = githubConfig();
  return config ? readGitHubSnapshot(config) : readLocalSnapshot();
}

async function writeGitHubDatabase(
  config: GitHubConfig,
  database: RecommendationDatabase,
  sha: string | null,
): Promise<string | null> {
  const body: Record<string, unknown> = {
    message: "Update recommendations [website API]",
    content: Buffer.from(JSON.stringify(database, null, 2)).toString("base64"),
    branch: config.branch,
  };
  if (sha) body.sha = sha;

  const response = await fetch(githubFileUrl(config, STORAGE_RELATIVE_PATH, false), {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${config.token}`,
      Accept: "application/vnd.github+json",
      "Content-Type": "application/json",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify(body),
    cache: "no-store",
  });

  if (response.status === 409 || response.status === 422) {
    throw new StorageConflictError();
  }
  if (!response.ok) {
    throw new ApiError(
      `Nao foi possivel guardar a fila no GitHub (HTTP ${response.status}).`,
      502,
    );
  }

  const payload = (await response.json()) as {
    content?: { sha?: unknown };
  };
  return typeof payload.content?.sha === "string"
    ? payload.content.sha
    : null;
}

function writeLocalDatabase(database: RecommendationDatabase): void {
  const filePath = localStoragePath();
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(database, null, 2), "utf-8");
}

async function writeStorageDatabase(
  database: RecommendationDatabase,
  snapshot: StorageSnapshot,
): Promise<{ sha: string | null; source: "github" | "local" }> {
  const config = githubConfig();
  if (config) {
    return {
      sha: await writeGitHubDatabase(config, database, snapshot.sha),
      source: "github",
    };
  }
  writeLocalDatabase(database);
  return { sha: null, source: "local" };
}

async function mutateDatabase<T>(
  mutate: (database: RecommendationDatabase) => T,
): Promise<{
  result: T;
  database: RecommendationDatabase;
  sha: string | null;
  source: "github" | "local";
}> {
  for (let attempt = 0; attempt < MAX_STORAGE_RETRIES; attempt += 1) {
    const snapshot = await readStorageSnapshot();
    const database: RecommendationDatabase = {
      queue: [...snapshot.database.queue],
      history: [...snapshot.database.history],
    };
    const result = mutate(database);
    try {
      const stored = await writeStorageDatabase(database, snapshot);
      return { result, database, ...stored };
    } catch (error: unknown) {
      if (
        error instanceof StorageConflictError &&
        attempt < MAX_STORAGE_RETRIES - 1
      ) {
        continue;
      }
      throw error;
    }
  }
  throw new ApiError(
    "Nao foi possivel atualizar a fila apos varias tentativas.",
    409,
  );
}

function activeHistory(history: JsonRecord[]): JsonRecord[] {
  const now = Date.now();
  return history.filter((item) => {
    if (item.is_test === true && typeof item.expires_at === "string") {
      const expiry = new Date(item.expires_at).getTime();
      return Number.isFinite(expiry) && expiry > now;
    }
    return true;
  });
}

function isExpiredRecord(item: JsonRecord): boolean {
  if (typeof item.expiryDate !== "string" || !item.expiryDate) return false;
  const expiry = new Date(item.expiryDate).getTime();
  return !Number.isFinite(expiry) || expiry <= Date.now();
}

function queueWithExpiryState(queue: JsonRecord[]): JsonRecord[] {
  return queue.map((item) =>
    isExpiredRecord(item) ? { ...item, status: "expired" } : item,
  );
}

function assertContentLength(req: NextRequest): void {
  const rawContentLength = req.headers.get("content-length");
  if (!rawContentLength) return;
  const contentLength = Number(rawContentLength);
  if (Number.isFinite(contentLength) && contentLength > MAX_REQUEST_BYTES) {
    throw new ApiError("O pedido excede o tamanho permitido.", 413);
  }
}

async function readJsonBody(req: NextRequest): Promise<unknown> {
  assertContentLength(req);

  let body: ArrayBuffer;
  try {
    body = await req.arrayBuffer();
  } catch {
    throw new ApiError("Nao foi possivel ler o pedido.", 400);
  }
  if (body.byteLength > MAX_REQUEST_BYTES) {
    throw new ApiError("O pedido excede o tamanho permitido.", 413);
  }

  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(body);
  } catch {
    throw new ApiError("O pedido tem uma codificacao invalida.", 400);
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new ApiError("O pedido tem um formato invalido.", 400);
  }
}

function isFullReplaceAuthorized(req: NextRequest): boolean {
  const configuredToken = process.env.SUGGESTIONS_ADMIN_TOKEN?.trim();
  const suppliedToken = req.headers.get("x-admin-token")?.trim();
  if (configuredToken && suppliedToken === configuredToken) return true;

  const hostname = req.nextUrl.hostname.toLocaleLowerCase();
  return (
    process.env.NODE_ENV !== "production" &&
    (hostname === "localhost" ||
      hostname === "127.0.0.1" ||
      hostname === "[::1]" ||
      hostname === "::1")
  );
}

function reviewerText(value: unknown): string {
  if (typeof value === "string" || typeof value === "number") {
    return sanitizeSubmissionText(String(value), 180);
  }
  if (!isJsonRecord(value)) return "";
  for (const key of ["id", "displayName", "username", "name"] as const) {
    const result = reviewerText(value[key]);
    if (result) return result;
  }
  return "";
}

function optionalSafeText(
  value: unknown,
  maxLength: number,
  position: string,
): string | undefined {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "string" && typeof value !== "number") {
    throw new ApiError(`${position}: valor invalido.`, 400);
  }
  const result = sanitizeSubmissionText(String(value), maxLength);
  if (!result) throw new ApiError(`${position}: valor invalido.`, 400);
  return result;
}

function optionalDiscordId(
  value: unknown,
  position: string,
): string | undefined {
  if (value === undefined || value === null || value === "") return undefined;
  if (typeof value !== "string") {
    throw new ApiError(`${position}: identificador Discord invalido.`, 400);
  }
  const identifier = stripInvisibleOrControlCharacters(value).trim();
  if (!/^\d+$/u.test(identifier)) {
    throw new ApiError(`${position}: identificador Discord invalido.`, 400);
  }
  return identifier;
}

function sanitizeJsonValue(value: unknown, depth = 0): unknown {
  if (depth > 8) return null;
  if (typeof value === "string") return sanitizeSubmissionText(value, 4_096);
  if (
    value === null ||
    typeof value === "boolean" ||
    (typeof value === "number" && Number.isFinite(value))
  ) {
    return value;
  }
  if (Array.isArray(value)) {
    return value
      .map((entry) => sanitizeJsonValue(entry, depth + 1))
      .filter((entry) => entry !== undefined);
  }
  if (!isJsonRecord(value)) return null;

  const result: JsonRecord = {};
  for (const [key, entry] of Object.entries(value)) {
    if (key === "__proto__" || key === "constructor" || key === "prototype") {
      continue;
    }
    const safeEntry = sanitizeJsonValue(entry, depth + 1);
    if (safeEntry !== undefined) result[key] = safeEntry;
  }
  return result;
}

function normalizeOptionalTimestamp(
  value: unknown,
  position: string,
): string | null {
  if (value === undefined || value === null || value === "") return null;
  return new Date(requireTimestamp(value, position)).toISOString();
}

function normalizeImageUrl(value: unknown, position: string): string {
  if (value === undefined || value === null || value === "") return "";
  if (typeof value !== "string") {
    throw new ApiError(`${position}: imageUrl invalido.`, 400);
  }
  const cleaned = stripInvisibleOrControlCharacters(value).trim();
  if (!cleaned) return "";
  if (/^\/covers\/[A-Za-z0-9_-]+\.jpg$/u.test(cleaned)) return cleaned;
  return normalizePublicSubmissionLink(cleaned, position);
}

function validateDiscordDelivery(
  value: unknown,
  position: string,
  expectedHash: string,
): DiscordDeliveryProof {
  if (!isJsonRecord(value)) {
    throw new ApiError(`${position}: prova de entrega invalida.`, 400);
  }
  const channelId = optionalDiscordId(value.channelId, `${position}.channelId`);
  const messageId = optionalDiscordId(value.messageId, `${position}.messageId`);
  const sentAt = requireTimestamp(value.sentAt, `${position}.sentAt`);
  const payloadHash =
    typeof value.payloadHash === "string" ? value.payloadHash : "";
  if (!channelId || !messageId || !isSubmissionHash(payloadHash)) {
    throw new ApiError(`${position}: prova de entrega invalida.`, 400);
  }
  if (payloadHash !== expectedHash) {
    throw new ApiError(`${position}: hash de entrega invalido.`, 400);
  }
  return { channelId, messageId, sentAt, payloadHash };
}

function validateDiscordApproval(
  value: unknown,
  position: string,
): JsonRecord {
  if (!isJsonRecord(value)) {
    throw new ApiError(`${position}: aprovacao Discord invalida.`, 400);
  }
  if (value.required !== undefined && typeof value.required !== "boolean") {
    throw new ApiError(`${position}.required: valor invalido.`, 400);
  }

  const normalized: JsonRecord = {
    required: value.required === true,
    status: sanitizeSubmissionText(value.status, 40) || "pending",
    notificationStatus:
      sanitizeSubmissionText(value.notificationStatus, 40) || "pending",
  };

  for (const field of ["channelId", "messageId"] as const) {
    const safeValue = optionalDiscordId(value[field], `${position}.${field}`);
    if (safeValue !== undefined) normalized[field] = safeValue;
  }

  for (const field of ["sentAt", "approvedAt", "reviewedAt"] as const) {
    const snakeCaseField = field.replace(/[A-Z]/gu, (letter) => `_${letter.toLowerCase()}`);
    const rawValue = value[field] ?? value[snakeCaseField];
    if (rawValue !== undefined) {
      normalized[field] = requireTimestamp(rawValue, `${position}.${field}`);
    }
  }

  if (value.payloadHash !== undefined) {
    if (!isSubmissionHash(value.payloadHash)) {
      throw new ApiError(`${position}.payloadHash: hash invalido.`, 400);
    }
    normalized.payloadHash = value.payloadHash;
  }

  const reviewerValue =
    value.approvedBy ?? value.reviewer ?? value.reviewedBy ?? value.reviewerId;
  if (reviewerValue !== undefined) {
    const reviewer = reviewerText(reviewerValue);
    const reviewerId = optionalDiscordId(reviewer, `${position}.approvedBy`);
    if (!reviewerId) {
      throw new ApiError(`${position}.approvedBy: valor invalido.`, 400);
    }
    normalized.reviewer = reviewerId;
    normalized.approvedBy = reviewerId;
  }

  const approvedByRole = optionalDiscordId(
    value.approvedByRole,
    `${position}.approvedByRole`,
  );
  if (approvedByRole !== undefined) normalized.approvedByRole = approvedByRole;

  const reviewerAuthorization = optionalSafeText(
    value.reviewerAuthorization,
    80,
    `${position}.reviewerAuthorization`,
  );
  if (reviewerAuthorization !== undefined) {
    normalized.reviewerAuthorization = reviewerAuthorization;
  }

  const mode = optionalSafeText(value.mode, 20, `${position}.mode`);
  if (mode !== undefined) normalized.mode = mode;

  for (const field of ["rejectedAt", "rejectedBy"] as const) {
    const safeValue =
      field === "rejectedBy"
        ? optionalDiscordId(value[field], `${position}.${field}`)
        : value[field] === undefined
          ? undefined
          : requireTimestamp(value[field], `${position}.${field}`);
    if (safeValue !== undefined) normalized[field] = safeValue;
  }

  return normalized;
}

function sameDelivery(
  left: DiscordDeliveryProof,
  right: DiscordDeliveryProof,
): boolean {
  return (
    left.channelId === right.channelId &&
    left.messageId === right.messageId &&
    left.sentAt === right.sentAt &&
    left.payloadHash === right.payloadHash
  );
}

function assertApprovedCommunityRecord(
  approval: JsonRecord | undefined,
  delivery: DiscordDeliveryProof | undefined,
  expectedHash: string,
  notificationStatus: string,
  topLevelMessageId: string,
  position: string,
): void {
  if (!approval || !delivery) {
    throw new ApiError(
      `${position}: uma sugestao da comunidade so pode entrar na fila apos aprovacao Discord verificavel.`,
      400,
    );
  }
  if (
    approval.required !== true ||
    approval.status !== "approved" ||
    approval.channelId !== delivery.channelId ||
    approval.messageId !== delivery.messageId ||
    approval.notificationStatus !== "sent" ||
    notificationStatus !== "sent" ||
    topLevelMessageId !== delivery.messageId ||
    approval.payloadHash !== expectedHash ||
    delivery.payloadHash !== expectedHash ||
    !validTimestamp(approval.sentAt) ||
    !validTimestamp(approval.approvedAt) ||
    !reviewerText(approval.reviewer ?? approval.approvedBy)
  ) {
    throw new ApiError(
      `${position}: aprovacao Discord incompleta ou inconsistente.`,
      400,
    );
  }
  if (
    approval.sentAt !== undefined &&
    approval.sentAt !== delivery.sentAt
  ) {
    throw new ApiError(
      `${position}: timestamp de entrega Discord inconsistente.`,
      400,
    );
  }
  if (
    new Date(approval.approvedAt as string).getTime() <
    new Date(approval.sentAt as string).getTime()
  ) {
    throw new ApiError(
      `${position}: a aprovacao Discord precede a entrega.`,
      400,
    );
  }
}

function validateCommunityRecord(value: JsonRecord, position: string): JsonRecord {
  if (value.sourceKind !== COMMUNITY_SOURCE_KIND) {
    throw new ApiError(`${position}: sourceKind invalido.`, 400);
  }
  if (!isJsonRecord(value.communitySubmission)) {
    throw new ApiError(`${position}: snapshot da comunidade invalido.`, 400);
  }

  const snapshotValue = value.communitySubmission;
  const snapshotKeys = Object.keys(snapshotValue).sort();
  const requiredSnapshotKeys = [
    "createdAt",
    "id",
    "link",
    "sourceKind",
    "title",
    "type",
  ];
  if (
    snapshotKeys.length !== requiredSnapshotKeys.length ||
    snapshotKeys.some((key, index) => key !== requiredSnapshotKeys[index])
  ) {
    throw new ApiError(`${position}: snapshot da comunidade invalido.`, 400);
  }

  const snapshotId = sanitizeIdentifier(
    snapshotValue.id,
    `${position}.communitySubmission.id`,
  );
  if (snapshotValue.id !== snapshotId) {
    throw new ApiError(`${position}: id do snapshot foi alterado.`, 400);
  }
  if (
    snapshotValue.sourceKind !== COMMUNITY_SOURCE_KIND ||
    !isRecommendationType(snapshotValue.type)
  ) {
    throw new ApiError(`${position}: snapshot da comunidade invalido.`, 400);
  }
  const snapshotTitle = sanitizeSubmissionText(snapshotValue.title, 220);
  if (
    typeof snapshotValue.title !== "string" ||
    snapshotValue.title !== snapshotTitle ||
    snapshotTitle.length < 3
  ) {
    throw new ApiError(`${position}: titulo do snapshot invalido.`, 400);
  }
  const snapshotLink = normalizePublicSubmissionLink(
    snapshotValue.link,
    `${position}.communitySubmission.link`,
  );
  if (snapshotValue.link !== snapshotLink) {
    throw new ApiError(`${position}: link do snapshot foi alterado.`, 400);
  }
  const snapshotCreatedAt = requireTimestamp(
    snapshotValue.createdAt,
    `${position}.communitySubmission.createdAt`,
  );
  const snapshot: CommunitySubmissionSnapshot = {
    id: snapshotId,
    sourceKind: COMMUNITY_SOURCE_KIND,
    type: snapshotValue.type,
    title: snapshotTitle,
    link: snapshotLink,
    createdAt: snapshotCreatedAt,
  };
  const expectedHash = submissionHash(snapshot);
  if (value.submissionHash !== expectedHash) {
    throw new ApiError(`${position}: submissionHash invalido.`, 400);
  }

  const id = sanitizeIdentifier(value.id, `${position}.id`);
  const title = sanitizeSubmissionText(value.title, 220);
  const link = normalizePublicSubmissionLink(value.link, `${position}.link`);
  const createdAt = requireTimestamp(value.createdAt, `${position}.createdAt`);
  if (
    id !== snapshot.id ||
    value.id !== id ||
    value.type !== snapshot.type ||
    createdAt !== snapshot.createdAt
  ) {
    throw new ApiError(
      `${position}: identidade do snapshot nao corresponde ao registo.`,
      400,
    );
  }
  if (typeof value.title !== "string" || value.title !== title) {
    throw new ApiError(`${position}: titulo invalido.`, 400);
  }
  if (title.length < 3) {
    throw new ApiError(`${position}: titulo demasiado curto.`, 400);
  }
  if (value.link !== link) {
    throw new ApiError(`${position}: link invalido.`, 400);
  }

  const rawApproval = isJsonRecord(value.discordApproval)
    ? value.discordApproval
    : undefined;
  const approvalContainsDelivery = Boolean(
    rawApproval?.channelId &&
      rawApproval.messageId &&
      rawApproval.sentAt &&
      rawApproval.payloadHash,
  );
  const rawDelivery =
    value.discordDelivery ??
    value.discordDeliveryProof ??
    (approvalContainsDelivery ? rawApproval : undefined);
  const delivery = rawDelivery
    ? validateDiscordDelivery(
        rawDelivery,
        `${position}.discordDelivery`,
        expectedHash,
      )
    : undefined;
  if (value.discordDelivery && value.discordDeliveryProof) {
    const alias = validateDiscordDelivery(
      value.discordDeliveryProof,
      `${position}.discordDeliveryProof`,
      expectedHash,
    );
    if (!delivery || !sameDelivery(delivery, alias)) {
      throw new ApiError(`${position}: provas Discord inconsistentes.`, 400);
    }
  }

  const approval = rawApproval
    ? validateDiscordApproval(
        value.discordApproval,
        `${position}.discordApproval`,
      )
    : undefined;
  const topLevelNotificationStatus = sanitizeSubmissionText(
    value.notificationStatus,
    40,
  );
  if (
    approval &&
    rawApproval &&
    rawApproval.notificationStatus === undefined &&
    topLevelNotificationStatus === "sent"
  ) {
    approval.notificationStatus = "sent";
  }
  if (approval && delivery) {
    if (
      (approval.channelId !== undefined &&
        approval.channelId !== delivery.channelId) ||
      (approval.messageId !== undefined &&
        approval.messageId !== delivery.messageId) ||
      (approval.sentAt !== undefined && approval.sentAt !== delivery.sentAt) ||
      (approval.payloadHash !== undefined &&
        approval.payloadHash !== delivery.payloadHash)
    ) {
      throw new ApiError(
        `${position}: prova e aprovacao Discord inconsistentes.`,
        400,
      );
    }
  }
  if (
    approval?.payloadHash !== undefined &&
    approval.payloadHash !== expectedHash
  ) {
    throw new ApiError(`${position}: hash da aprovacao Discord invalido.`, 400);
  }
  if (approval?.notificationStatus === "sent" && !delivery) {
    throw new ApiError(`${position}: notificacao Discord sem prova de entrega.`, 400);
  }

  const notificationStatus =
    topLevelNotificationStatus || (delivery ? "sent" : "pending");
  const status = sanitizeSubmissionText(value.status, 60) || "pending_approval";
  const topLevelMessageId = optionalDiscordId(
    value.discordMessageId,
    `${position}.discordMessageId`,
  );
  if (delivery && topLevelMessageId && topLevelMessageId !== delivery.messageId) {
    throw new ApiError(`${position}: messageId Discord inconsistente.`, 400);
  }

  const approvedProof = (() => {
    try {
      assertApprovedCommunityRecord(
        approval,
        delivery,
        expectedHash,
        notificationStatus,
        topLevelMessageId || "",
        position,
      );
      return true;
    } catch {
      return false;
    }
  })();
  if (
    approval?.status === "approved" &&
    !approvedProof
  ) {
    throw new ApiError(`${position}: aprovacao Discord invalida.`, 400);
  }
  if (
    ["approved", "approved_pending_enrichment"].includes(status) &&
    !approvedProof
  ) {
    throw new ApiError(`${position}: estado aprovado sem prova Discord valida.`, 400);
  }
  if (!approvedProof && (title !== snapshot.title || link !== snapshot.link)) {
    throw new ApiError(
      `${position}: uma sugestao pendente nao pode alterar o snapshot apresentado.`,
      400,
    );
  }
  if (status === "queue") {
    assertApprovedCommunityRecord(
      approval,
      delivery,
      expectedHash,
      notificationStatus,
      topLevelMessageId || "",
      position,
    );
  }

  const attempts =
    typeof value.notificationAttempts === "number" &&
    Number.isInteger(value.notificationAttempts) &&
    value.notificationAttempts >= 0
      ? value.notificationAttempts
      : 0;
  const preserveEditorialFields = approvedProof;
  const safeRecord = preserveEditorialFields
    ? sanitizeJsonValue(value)
    : {};
  if (!isJsonRecord(safeRecord)) {
    throw new ApiError(`${position}: registo editorial invalido.`, 400);
  }

  let normalized: JsonRecord;
  if (preserveEditorialFields) {
    const resolutionStatus =
      sanitizeSubmissionText(value.resolutionStatus, 40) || "unresolved";
    if (!COMMUNITY_RESOLUTION_STATUSES.has(resolutionStatus)) {
      throw new ApiError(`${position}: resolutionStatus invalido.`, 400);
    }
    const safeVerification = sanitizeJsonValue(value.verification);
    if (
      resolutionStatus === "verified" &&
      !isJsonRecord(safeVerification)
    ) {
      throw new ApiError(`${position}: verification invalida.`, 400);
    }
    const sourcePublishedAt = normalizeOptionalTimestamp(
      value.sourcePublishedAt,
      `${position}.sourcePublishedAt`,
    );
    const expiryDate = normalizeOptionalTimestamp(
      value.expiryDate,
      `${position}.expiryDate`,
    );
    normalized = {
      ...safeRecord,
      id,
      type: snapshot.type,
      category:
        sanitizeSubmissionText(value.category, 80) ||
        categoryForType(snapshot.type),
      title,
      link,
      authorOrMeta: sanitizeSubmissionText(value.authorOrMeta, 260),
      description: sanitizeSubmissionText(value.description, 1_000),
      imageUrl: normalizeImageUrl(value.imageUrl, `${position}.imageUrl`),
      sourcePublishedAt,
      expiryDate,
      priority:
        typeof value.priority === "number" && Number.isFinite(value.priority)
          ? value.priority
          : 3,
      createdAt,
      status,
      sourceKind: COMMUNITY_SOURCE_KIND,
      communitySubmission: snapshot,
      submissionHash: expectedHash,
      resolutionStatus,
      verification: isJsonRecord(safeVerification)
        ? safeVerification
        : { status: resolutionStatus },
      origin:
        sanitizeSubmissionText(value.origin, 80) || "website-community",
      notificationStatus,
      notificationAttempts: attempts,
    };
    if ("sourceImageUrl" in value) {
      normalized.sourceImageUrl = normalizeImageUrl(
        value.sourceImageUrl,
        `${position}.sourceImageUrl`,
      );
    }
  } else {
    normalized = {
      id,
      type: snapshot.type,
      category: categoryForType(snapshot.type),
      title,
      link,
      authorOrMeta: "",
      description: "Sugestao recebida da comunidade para revisao.",
      imageUrl: "",
      sourcePublishedAt: null,
      expiryDate: null,
      priority: 3,
      createdAt,
      status,
      sourceKind: COMMUNITY_SOURCE_KIND,
      communitySubmission: snapshot,
      submissionHash: expectedHash,
      resolutionStatus: "unresolved",
      verification: {
        status: "unresolved",
        provider: "community",
        confidence: 0,
        checkedAt: createdAt,
        matchedFields: [],
        warnings: ["A sugestao aguarda revisao da comunidade."],
      },
      origin: "website-community",
      notificationStatus,
      notificationAttempts: attempts,
    };
  }

  if (delivery) {
    normalized.discordDelivery = delivery;
    normalized.discordChannelId = delivery.channelId;
    normalized.discordMessageId = delivery.messageId;
    normalized.discordNotifiedAt = delivery.sentAt;
    normalized.discordPayloadHash = delivery.payloadHash;
  }
  if (approval) {
    normalized.discordApproval = approval;
  } else if (delivery) {
    normalized.discordApproval = {
      required: true,
      status: "pending",
      channelId: delivery.channelId,
      messageId: delivery.messageId,
      sentAt: delivery.sentAt,
      notificationStatus: "sent",
      payloadHash: delivery.payloadHash,
    };
  }
  return normalized;
}

function assertStoredCommunityRecords(database: RecommendationDatabase): void {
  for (const [listName, records] of [
    ["queue", database.queue],
    ["history", database.history],
  ] as const) {
    records.forEach((record, index) => {
      if (isCommunityRecord(record)) {
        validateCommunityRecord(record, `${listName}[${index}]`);
      }
    });
  }
}

function validateReplaceRecord(value: unknown, position: string): JsonRecord {
  if (!isJsonRecord(value)) {
    throw new ApiError(`${position}: registo invalido.`, 400);
  }
  if (isCommunityRecord(value)) return validateCommunityRecord(value, position);

  const id = sanitizeSubmissionText(value.id, 140);
  const title = sanitizeSubmissionText(value.title, 220);
  if (!id || title.length < 3 || !isRecommendationType(value.type)) {
    throw new ApiError(`${position}: id, tipo ou titulo invalido.`, 400);
  }

  const link = normalizePublicSubmissionLink(value.link, `${position}.link`);
  const rawImage =
    typeof value.imageUrl === "string"
      ? stripInvisibleOrControlCharacters(value.imageUrl).trim()
      : "";
  const validLocalCover = /^\/covers\/[A-Za-z0-9_-]+\.jpg$/u.test(rawImage);
  const imageUrl =
    !rawImage || validLocalCover
      ? rawImage
      : normalizePublicUrl(rawImage) ||
        (() => {
          throw new ApiError(`${position}: imageUrl invalido.`, 400);
        })();

  const dates: Record<"sourcePublishedAt" | "expiryDate", string | null> = {
    sourcePublishedAt: null,
    expiryDate: null,
  };
  for (const field of ["sourcePublishedAt", "expiryDate"] as const) {
    const raw = value[field];
    if (raw === undefined || raw === null || raw === "") continue;
    dates[field] = new Date(requireTimestamp(raw, `${position}.${field}`)).toISOString();
  }

  const normalized: JsonRecord = {
    ...value,
    id,
    type: value.type,
    category: sanitizeSubmissionText(value.category, 80) || categoryForType(value.type),
    title,
    link,
    authorOrMeta: sanitizeSubmissionText(value.authorOrMeta, 260),
    description: sanitizeSubmissionText(value.description, 1_000),
    imageUrl,
    sourcePublishedAt: dates.sourcePublishedAt,
    expiryDate: dates.expiryDate,
  };
  if (typeof value.status === "string") {
    normalized.status = sanitizeSubmissionText(value.status, 60);
  }
  if (typeof value.sourceKind === "string") {
    normalized.sourceKind = sanitizeSubmissionText(value.sourceKind, 80);
  }
  return position.startsWith("queue[") && isExpiredRecord(normalized)
    ? { ...normalized, status: "expired" }
    : normalized;
}

function validateReplacement(
  queueValue: unknown,
  historyValue: unknown,
): RecommendationDatabase {
  if (!Array.isArray(queueValue) || !Array.isArray(historyValue)) {
    throw new ApiError("queue e history tem de ser listas.", 400);
  }
  if (
    queueValue.length > MAX_RECORDS_PER_LIST ||
    historyValue.length > MAX_RECORDS_PER_LIST
  ) {
    throw new ApiError("A fila excede o numero maximo de registos.", 413);
  }
  return {
    queue: queueValue.map((item, index) =>
      validateReplaceRecord(item, `queue[${index}]`),
    ),
    history: historyValue.map((item, index) =>
      validateReplaceRecord(item, `history[${index}]`),
    ),
  };
}

function createCommunitySuggestion(
  type: RecommendationType,
  title: string,
  link: string,
): JsonRecord {
  const id = `web_${type}_${randomUUID()}`;
  const createdAt = new Date().toISOString();
  const snapshot: CommunitySubmissionSnapshot = {
    id,
    sourceKind: COMMUNITY_SOURCE_KIND,
    type,
    title,
    link,
    createdAt,
  };
  const hash = submissionHash(snapshot);
  return {
    id,
    type,
    category: categoryForType(type),
    title,
    link,
    authorOrMeta: "",
    description: "Sugestao recebida da comunidade para revisao.",
    imageUrl: "",
    sourcePublishedAt: null,
    expiryDate: null,
    priority: 3,
    createdAt,
    status: "pending_approval",
    sourceKind: COMMUNITY_SOURCE_KIND,
    communitySubmission: snapshot,
    submissionHash: hash,
    resolutionStatus: "unresolved",
    verification: {
      status: "unresolved",
      provider: "community",
      confidence: 0,
      checkedAt: createdAt,
      matchedFields: [],
      warnings: ["A sugestao aguarda revisao da comunidade."],
    },
    origin: "website-community",
    notificationStatus: "pending",
    notificationAttempts: 0,
    discordApproval: {
      required: true,
      status: "pending",
      notificationStatus: "pending",
    },
  };
}

function truncateDiscord(value: unknown, maxLength: number): string {
  return sanitizeSubmissionText(value, maxLength) || "N/D";
}

async function notifyDiscord(item: JsonRecord): Promise<DiscordDeliveryProof | null> {
  const token = process.env.DISCORD_BOT_TOKEN?.trim();
  const channelId = process.env.DISCORD_REVIEW_CHANNEL_ID?.trim();
  const itemId = sanitizeSubmissionText(item.id, 140);
  const payloadHash = typeof item.submissionHash === "string" ? item.submissionHash : "";
  if (
    !token ||
    !channelId ||
    !/^\d+$/u.test(channelId) ||
    !itemId ||
    !isSubmissionHash(payloadHash)
  ) {
    console.warn(
      "[suggestions API] Discord nao configurado ou sugestao sem identidade; entrega adiada.",
    );
    return null;
  }

  const type = isRecommendationType(item.type) ? item.type : "project";
  const typeEmojis: Record<RecommendationType, string> = {
    book: "📚",
    podcast: "🎙️",
    movie: "🎬",
    nostalgia: "📼",
    investigation: "🔎",
    highlight: "📰",
    project: "💡",
  };
  const typeColors: Record<RecommendationType, number> = {
    book: 0x2e86ab,
    podcast: 0x8338ec,
    movie: 0xe63946,
    nostalgia: 0x7e22ce,
    investigation: 0x0369a1,
    highlight: 0xf77f00,
    project: 0x0099ff,
  };

  const fields: Array<{ name: string; value: string; inline: boolean }> = [
    {
      name: "Tipo",
      value: truncateDiscord(categoryForType(type), 1_024),
      inline: true,
    },
    {
      name: "Estado",
      value: "Sugestao nao resolvida; requer revisao.",
      inline: true,
    },
  ];
  const link = typeof item.link === "string" ? item.link : "";
  if (link) {
    fields.push({
      name: "Link fornecido",
      value: truncateDiscord(link, 1_024),
      inline: false,
    });
  }

  const embed: JsonRecord = {
    title: truncateDiscord(
      `${typeEmojis[type]} Sugestao: ${truncateDiscord(item.title, 220)}`,
      256,
    ),
    description: "Submissao da comunidade para revisao antes de qualquer publicacao.",
    color: typeColors[type],
    fields,
    footer: {
      text: `ID: ${itemId} | Hash: ${payloadHash}`,
    },
    timestamp: typeof item.createdAt === "string" ? item.createdAt : undefined,
  };

  const messageBody = JSON.stringify({
    embeds: [embed],
    allowed_mentions: { parse: [] },
    components: [
      {
        type: 1,
        components: [
          {
            type: 2,
            style: 3,
            label: "Aprovar",
            emoji: { name: "✅" },
            custom_id: "rec_approve",
          },
          {
            type: 2,
            style: 4,
            label: "Rejeitar",
            emoji: { name: "❌" },
            custom_id: "rec_reject",
          },
          {
            type: 2,
            style: 2,
            label: "Alterar link",
            emoji: { name: "🔗" },
            custom_id: "rec_edit_link",
          },
        ],
      },
    ],
  });

  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      const response = await fetch(
        `https://discord.com/api/v10/channels/${encodeURIComponent(channelId)}/messages`,
        {
          method: "POST",
          headers: {
            Authorization: `Bot ${token}`,
            "Content-Type": "application/json",
          },
          body: messageBody,
          cache: "no-store",
        },
      );
      if (response.ok) {
        const delivered = (await response.json()) as { id?: unknown };
        if (
          typeof delivered.id !== "string" ||
          !/^\d+$/u.test(delivered.id)
        ) {
          return null;
        }
        return {
          channelId,
          messageId: delivered.id,
          sentAt: new Date().toISOString(),
          payloadHash,
        };
      }

      console.error(
        `[suggestions API] Discord respondeu com HTTP ${response.status}.`,
        sanitizeText(await response.text(), 500),
      );
      const retryable = response.status === 429;
      if (!retryable || attempt === 3) return null;
      const retryAfterHeader = Number(response.headers.get("retry-after"));
      const retryAfterMs = Number.isFinite(retryAfterHeader)
        ? Math.min(2_000, Math.max(250, retryAfterHeader * 1_000))
        : attempt * 500;
      await new Promise((resolve) => setTimeout(resolve, retryAfterMs));
    } catch (error: unknown) {
      console.error("[suggestions API] Falha ao notificar o Discord:", error);
      return null;
    }
  }
  return null;
}

async function appendSuggestion(payload: AppendPayload) {
  if (!payload.item || !isRecommendationType(payload.item.type)) {
    throw new ApiError("Tipo de recomendacao invalido.", 400);
  }

  const title = sanitizeSubmissionText(payload.item.title, 220);
  if (title.length < 3) {
    throw new ApiError("O titulo da sugestao e invalido.", 400);
  }
  const link = normalizePublicSubmissionLink(payload.item.link);
  const storedItem = createCommunitySuggestion(payload.item.type, title, link);

  // Persist the unresolved, sanitized submission before the external delivery.
  // The only network request below is the Discord review notification itself;
  // the submitted URL is never resolved, fetched, or used as an image source.
  const persisted = await mutateDatabase((database) => {
    database.queue.unshift(storedItem);
    return storedItem;
  });

  const delivery = await notifyDiscord(storedItem);
  const notificationSent = Boolean(delivery);
  let finalItem = storedItem;
  let finalSha = persisted.sha;
  let statusPersisted = false;

  if (delivery) {
    try {
      const marked = await mutateDatabase((database) => {
        const index = database.queue.findIndex((item) => item.id === storedItem.id);
        if (index < 0) {
          throw new ApiError(
            "A sugestao desapareceu da fila antes de ser marcada.",
            409,
          );
        }
        const current = database.queue[index];
        if (current.status !== "pending_approval") {
          return { item: current, changed: false };
        }
        const currentApproval = isJsonRecord(current.discordApproval)
          ? current.discordApproval
          : {};
        const updated = {
          ...current,
          status: "pending_sent",
          notificationStatus: "sent",
          notificationAttempts:
            (typeof current.notificationAttempts === "number"
              ? current.notificationAttempts
              : 0) + 1,
          discordDelivery: delivery,
          discordChannelId: delivery.channelId,
          discordMessageId: delivery.messageId,
          discordNotifiedAt: delivery.sentAt,
          discordPayloadHash: delivery.payloadHash,
          discordApproval: {
            required: true,
            status:
              currentApproval.status === "approved"
                ? "approved"
                : "pending",
            channelId: delivery.channelId,
            messageId: delivery.messageId,
            notificationStatus: "sent",
            sentAt: delivery.sentAt,
            payloadHash: delivery.payloadHash,
          },
        };
        database.queue[index] = updated;
        return { item: updated, changed: true };
      });
      finalItem = marked.result.item;
      finalSha = marked.sha;
      statusPersisted = marked.result.changed;
    } catch (error: unknown) {
      console.error(
        "[suggestions API] Discord foi notificado, mas a prova de entrega nao pode ser persistida:",
        error,
      );
    }
  } else {
    try {
      const retryState = await mutateDatabase((database) => {
        const index = database.queue.findIndex((item) => item.id === storedItem.id);
        if (index < 0) return { item: storedItem, changed: false };
        const current = database.queue[index];
        if (current.status !== "pending_approval") {
          return { item: current, changed: false };
        }
        const attempts =
          (typeof current.notificationAttempts === "number"
            ? current.notificationAttempts
            : 0) + 1;
        const updated = {
          ...current,
          notificationStatus: "pending_retry",
          notificationAttempts: attempts,
          nextNotificationAttemptAt: new Date().toISOString(),
        };
        database.queue[index] = updated;
        return { item: updated, changed: true };
      });
      finalItem = retryState.result.item;
      finalSha = retryState.sha;
      const retryQueued = await dispatchDiscordRetryWorkflow();
      if (!retryQueued) {
        console.warn(
          "[suggestions API] A sugestao ficou na fila duravel para a proxima recuperacao.",
        );
      }
    } catch (error: unknown) {
      console.error(
        "[suggestions API] Nao foi possivel gravar o estado de retry do Discord:",
        error,
      );
    }
  }

  return NextResponse.json(
    {
      success: true,
      item: finalItem,
      sha: finalSha,
      source: persisted.source,
      notificationSent,
      statusPersisted,
    },
    { status: notificationSent && statusPersisted ? 201 : 202 },
  );
}

async function replaceDatabase(req: NextRequest, payload: ReplacePayload) {
  if (!isFullReplaceAuthorized(req)) {
    throw new ApiError(
      "A substituicao integral da fila requer autorizacao de administrador.",
      403,
    );
  }
  const replacement = validateReplacement(payload.queue, payload.history);
  const stored = await mutateDatabase((database) => {
    database.queue = replacement.queue;
    database.history = replacement.history;
    return null;
  });
  return NextResponse.json({
    success: true,
    sha: stored.sha,
    source: stored.source,
  });
}

function errorStatus(error: unknown): number {
  if (error instanceof ApiError) return error.status;
  if (error instanceof StorageConflictError) return 409;
  return 500;
}

function genericErrorMessage(status: number): string {
  if (status === 413) return "O pedido excede o tamanho permitido.";
  if (status === 429) return "Demasiados pedidos. Tenta novamente mais tarde.";
  if (status === 403) return "Nao foi possivel autorizar este pedido.";
  if (status === 409) return "Nao foi possivel concluir o pedido neste momento.";
  if (status >= 500) return "Nao foi possivel concluir o pedido neste momento.";
  return "Nao foi possivel processar os dados enviados.";
}

function errorResponse(error: unknown): NextResponse {
  const status = errorStatus(error);
  return NextResponse.json({ error: genericErrorMessage(status) }, { status });
}

export async function GET() {
  try {
    const snapshot = await readStorageSnapshot();
    return NextResponse.json({
      queue: queueWithExpiryState(snapshot.database.queue),
      history: activeHistory(snapshot.database.history),
      sha: snapshot.sha,
      source: snapshot.source,
    });
  } catch (error: unknown) {
    console.error("[suggestions API] Falha ao carregar:", error);
    return errorResponse(error);
  }
}

export async function POST(req: NextRequest) {
  try {
    const fullReplaceAuthorized = isFullReplaceAuthorized(req);
    const rateLimit = checkRecommendationRateLimit(
      req,
      "suggestions-write",
      fullReplaceAuthorized ? 30 : 5,
      fullReplaceAuthorized ? 10 * 60_000 : 30 * 60_000,
      fullReplaceAuthorized ? 10 * 60_000 : 6 * 60 * 60_000,
    );
    if (!rateLimit.allowed) {
      return NextResponse.json(
        { error: genericErrorMessage(429) },
        {
          status: 429,
          headers: { "Retry-After": String(rateLimit.retryAfterSeconds) },
        },
      );
    }

    const payload = await readJsonBody(req);
    if (isJsonRecord(payload) && payload.action === "append") {
      const item = isJsonRecord(payload.item)
        ? {
            type: payload.item.type,
            title: payload.item.title,
            link: payload.item.link,
          }
        : undefined;
      return await appendSuggestion({ action: "append", item });
    }
    if (
      isJsonRecord(payload) &&
      (payload.action === "replace" ||
        (payload.action === undefined &&
          "queue" in payload &&
          "history" in payload))
    ) {
      return await replaceDatabase(req, {
        action: "replace",
        queue: payload.queue,
        history: payload.history,
      });
    }
    throw new ApiError("Acao invalida.", 400);
  } catch (error: unknown) {
    console.error("[suggestions API] Falha ao guardar:", error);
    return errorResponse(error);
  }
}
