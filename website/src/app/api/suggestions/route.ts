import { createHash, randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { NextRequest, NextResponse } from "next/server";
import {
  categoryForType,
  isRecommendationType,
  normalizePublicUrl,
  prepareRecommendationCover,
  resolutionErrorMessage,
  resolveRecommendation,
  sanitizeText,
  titleSimilarity,
  validateResolvedRecommendation,
  type RecommendationType,
  type ResolvedRecommendation,
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

const STORAGE_RELATIVE_PATH = "website/public/recommendations.json";
const MAX_REQUEST_BYTES = 1_000_000;
const MAX_RECORDS_PER_LIST = 1_000;
const MAX_STORAGE_RETRIES = 3;

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

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function emptyDatabase(): RecommendationDatabase {
  return { queue: [], history: [] };
}

function parseRecommendations(content: string): RecommendationDatabase {
  let parsed: unknown;
  try {
    parsed = JSON.parse(content || "{}") as unknown;
  } catch {
    throw new ApiError(
      "O ficheiro recommendations.json está corrompido; a escrita foi bloqueada.",
      500,
    );
  }
  if (Array.isArray(parsed)) {
    if (parsed.some((item) => !isJsonRecord(item))) {
      throw new ApiError(
        "A lista legacy contém registos inválidos; a escrita foi bloqueada.",
        500,
      );
    }
    return {
      queue: parsed,
      history: [],
    };
  }
  if (!isJsonRecord(parsed)) {
    throw new ApiError("O ficheiro recommendations.json não é um objeto.", 500);
  }
  if (
    (parsed.queue !== undefined && !Array.isArray(parsed.queue)) ||
    (parsed.history !== undefined && !Array.isArray(parsed.history))
  ) {
    throw new ApiError(
      "queue/history têm um formato inválido em recommendations.json.",
      500,
    );
  }
  if (
    (Array.isArray(parsed.queue) && parsed.queue.some((item) => !isJsonRecord(item))) ||
    (Array.isArray(parsed.history) &&
      parsed.history.some((item) => !isJsonRecord(item)))
  ) {
    throw new ApiError(
      "recommendations.json contém registos inválidos; a escrita foi bloqueada.",
      500,
    );
  }
  return {
    queue: Array.isArray(parsed.queue) ? parsed.queue.filter(isJsonRecord) : [],
    history: Array.isArray(parsed.history)
      ? parsed.history.filter(isJsonRecord)
      : [],
  };
}

function isJsonRecord(value: unknown): value is JsonRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
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

function githubContentsUrl(config: GitHubConfig, includeRef: boolean): string {
  return githubFileUrl(config, STORAGE_RELATIVE_PATH, includeRef);
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

async function readGitHubSnapshot(
  config: GitHubConfig,
): Promise<StorageSnapshot> {
  const response = await fetch(githubContentsUrl(config, true), {
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
      `Não foi possível ler a fila no GitHub (HTTP ${response.status}).`,
      502,
    );
  }

  const payload = (await response.json()) as {
    content?: unknown;
    sha?: unknown;
  };
  if (typeof payload.content !== "string") {
    throw new ApiError("O ficheiro da fila no GitHub é inválido.", 502);
  }

  return {
    database: parseRecommendations(
      Buffer.from(payload.content.replace(/\s/g, ""), "base64").toString("utf-8"),
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

  const response = await fetch(githubContentsUrl(config, false), {
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
    const details = sanitizeText(await response.text(), 500);
    throw new ApiError(
      `Não foi possível guardar a fila no GitHub (HTTP ${response.status})${details ? `: ${details}` : "."}`,
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

async function ensureGitHubAsset(
  config: GitHubConfig,
  relativePath: string,
  bytes: Uint8Array,
): Promise<void> {
  for (let attempt = 0; attempt < MAX_STORAGE_RETRIES; attempt += 1) {
    const current = await fetch(githubFileUrl(config, relativePath, true), {
      headers: {
        Authorization: `Bearer ${config.token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Cache-Control": "no-cache",
      },
      cache: "no-store",
    });

    let sha: string | null = null;
    if (current.ok) {
      const payload = (await current.json()) as {
        content?: unknown;
        sha?: unknown;
      };
      sha = typeof payload.sha === "string" ? payload.sha : null;
      if (typeof payload.content === "string") {
        const existing = Buffer.from(
          payload.content.replace(/\s/g, ""),
          "base64",
        );
        if (existing.equals(Buffer.from(bytes))) return;
      }
    } else if (current.status !== 404) {
      throw new ApiError(
        `Não foi possível verificar ${relativePath} no GitHub (HTTP ${current.status}).`,
        502,
      );
    }

    const body: Record<string, unknown> = {
      message: `Store verified recommendation asset ${path.basename(relativePath)}`,
      content: Buffer.from(bytes).toString("base64"),
      branch: config.branch,
    };
    if (sha) body.sha = sha;
    const response = await fetch(githubFileUrl(config, relativePath, false), {
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
    if (response.ok) return;
    if (
      (response.status === 409 || response.status === 422) &&
      attempt < MAX_STORAGE_RETRIES - 1
    ) {
      continue;
    }
    throw new ApiError(
      `Não foi possível guardar ${relativePath} no GitHub (HTTP ${response.status}).`,
      502,
    );
  }
}

function normalizeManifestTitle(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/\p{Diacritic}/gu, "")
    .toLocaleLowerCase()
    .replace(/https?:\/\/\S+/g, " ")
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function canonicalManifestUrl(value: string): string {
  const normalized = normalizePublicUrl(value);
  if (!normalized) return "";
  const url = new URL(normalized);
  const query = new URLSearchParams();
  for (const [key, queryValue] of url.searchParams) {
    const lowered = key.toLocaleLowerCase();
    if (
      lowered.startsWith("utm_") ||
      ["fbclid", "gclid", "srsltid", "ref", "ref_"].includes(lowered)
    ) {
      continue;
    }
    query.append(key, queryValue);
  }
  const port =
    url.port &&
    !(
      (url.protocol === "https:" && url.port === "443") ||
      (url.protocol === "http:" && url.port === "80")
    )
      ? `:${url.port}`
      : "";
  const queryString = query.toString();
  return `${url.protocol}//${url.hostname.toLocaleLowerCase()}${port}${url.pathname || "/"}${queryString ? `?${queryString}` : ""}`;
}

type ResolvedForStorage = ResolvedRecommendation & {
  sourceImageUrl?: string;
};

async function persistRecommendationCover(
  resolved: ResolvedRecommendation,
): Promise<ResolvedForStorage> {
  if (resolved.type === "project") return resolved;

  const prepared = await prepareRecommendationCover(resolved.imageUrl);
  const coverHash = createHash("sha256")
    .update(prepared.bytes)
    .digest("hex");
  const entityId =
    resolved.verification.entityId ||
    resolved.verification.externalId ||
    resolved.link;
  if (!entityId) {
    throw new ApiError("A recomendação não tem uma identidade verificável.", 422);
  }
  const key = createHash("sha256")
    .update(
      `${resolved.type}|${normalizeManifestTitle(resolved.title)}|${entityId}|${coverHash}`,
    )
    .digest("hex")
    .slice(0, 32);
  const verifiedAt = new Date().toISOString();
  const publicImageUrl = `/covers/${key}.jpg`;
  const manifest = {
    cacheVersion: 2,
    type: resolved.type,
    title: resolved.title,
    titleNormalized: normalizeManifestTitle(resolved.title),
    canonicalLink: canonicalManifestUrl(resolved.link),
    entityId,
    source: resolved.verification.provider,
    score: resolved.verification.confidence,
    coverHash,
    coverSourceUrl: prepared.sourceUrl,
    coverSourceMime: prepared.sourceMime,
    width: prepared.width,
    height: prepared.height,
    verifiedAt,
    resolvedTitle: resolved.title,
    resolvedAuthor: resolved.authorOrMeta,
    sourcePublishedAt: resolved.sourcePublishedAt,
    expiryDate: resolved.expiryDate,
  };
  const manifestBytes = Buffer.from(
    JSON.stringify(manifest, null, 2),
    "utf-8",
  );
  const config = githubConfig();
  if (config) {
    await ensureGitHubAsset(
      config,
      `website/public/covers/${key}.jpg`,
      prepared.bytes,
    );
    await ensureGitHubAsset(
      config,
      `website/public/covers/${key}.json`,
      manifestBytes,
    );
  } else {
    const coversDirectory = path.join(process.cwd(), "public", "covers");
    fs.mkdirSync(coversDirectory, { recursive: true });
    fs.writeFileSync(path.join(coversDirectory, `${key}.jpg`), prepared.bytes);
    fs.writeFileSync(path.join(coversDirectory, `${key}.json`), manifestBytes);
  }

  return {
    ...resolved,
    imageUrl: publicImageUrl,
    sourceImageUrl: prepared.sourceUrl,
    verification: {
      ...resolved.verification,
      status: "verified",
      source: resolved.verification.provider,
      entityId,
      score: resolved.verification.confidence,
      coverHash,
      verifiedAt,
    },
  };
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
  throw new ApiError("Não foi possível atualizar a fila após várias tentativas.", 409);
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

function assertRequestSize(req: NextRequest): void {
  const contentLength = Number(req.headers.get("content-length") || "0");
  if (Number.isFinite(contentLength) && contentLength > MAX_REQUEST_BYTES) {
    throw new ApiError("O pedido excede o tamanho permitido.", 413);
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

function validateReplaceRecord(value: unknown, position: string): JsonRecord {
  if (!isJsonRecord(value)) {
    throw new ApiError(`${position}: registo inválido.`, 400);
  }
  const id = sanitizeText(value.id, 140);
  const title = sanitizeText(value.title, 220);
  if (!id || title.length < 3 || !isRecommendationType(value.type)) {
    throw new ApiError(`${position}: id, tipo ou título inválido.`, 400);
  }

  const rawLink = value.link;
  if (rawLink !== undefined && rawLink !== "" && !normalizePublicUrl(rawLink)) {
    throw new ApiError(`${position}: link não é um URL público válido.`, 400);
  }
  const rawImage = value.imageUrl;
  const validLocalCover =
    typeof rawImage === "string" &&
    /^\/covers\/[a-f0-9_-]+\.jpg$/i.test(rawImage);
  if (
    rawImage !== undefined &&
    rawImage !== "" &&
    !validLocalCover &&
    !normalizePublicUrl(rawImage)
  ) {
    throw new ApiError(`${position}: imageUrl não é válido.`, 400);
  }
  for (const field of ["sourcePublishedAt", "expiryDate"] as const) {
    const raw = value[field];
    if (
      raw !== undefined &&
      raw !== null &&
      (typeof raw !== "string" ||
        !Number.isFinite(new Date(raw).getTime()))
    ) {
      throw new ApiError(`${position}: ${field} não é uma data válida.`, 400);
    }
  }

  const normalized = {
    ...value,
    id,
    type: value.type,
    category:
      sanitizeText(value.category, 80) || categoryForType(value.type),
    title,
    link: typeof value.link === "string" ? value.link.trim() : "",
    authorOrMeta: sanitizeText(value.authorOrMeta, 260),
    description: sanitizeText(value.description, 1_000),
    imageUrl: typeof value.imageUrl === "string" ? value.imageUrl.trim() : "",
    sourcePublishedAt:
      typeof value.sourcePublishedAt === "string"
        ? new Date(value.sourcePublishedAt).toISOString()
        : null,
    expiryDate:
      typeof value.expiryDate === "string"
        ? new Date(value.expiryDate).toISOString()
        : null,
  };
  return position.startsWith("queue[") && isExpiredRecord(normalized)
    ? { ...normalized, status: "expired" }
    : normalized;
}

function validateReplacement(
  queueValue: unknown,
  historyValue: unknown,
): RecommendationDatabase {
  if (!Array.isArray(queueValue) || !Array.isArray(historyValue)) {
    throw new ApiError("queue e history têm de ser listas.", 400);
  }
  if (
    queueValue.length > MAX_RECORDS_PER_LIST ||
    historyValue.length > MAX_RECORDS_PER_LIST
  ) {
    throw new ApiError("A fila excede o número máximo de registos.", 413);
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

function verificationExternalId(item: JsonRecord): string {
  if (!isJsonRecord(item.verification)) return "";
  return sanitizeText(item.verification.externalId, 180);
}

function isDuplicate(
  existing: JsonRecord,
  recommendation: ResolvedRecommendation,
): boolean {
  if (existing.type !== recommendation.type) return false;

  const incomingExternalId = recommendation.verification.externalId ?? "";
  const existingExternalId = verificationExternalId(existing);
  if (
    incomingExternalId &&
    existingExternalId &&
    incomingExternalId === existingExternalId
  ) {
    return true;
  }

  const existingLink = normalizePublicUrl(existing.link);
  if (existingLink && existingLink === recommendation.link) return true;

  const existingTitle = sanitizeText(existing.title, 220);
  const existingAuthor = sanitizeText(existing.authorOrMeta, 260);
  return (
    titleSimilarity(existingTitle, recommendation.title) >= 0.97 &&
    (!existingAuthor ||
      titleSimilarity(existingAuthor, recommendation.authorOrMeta) >= 0.8)
  );
}

function createStoredRecommendation(
  resolved: ResolvedRecommendation,
): JsonRecord {
  return {
    id: `web_${resolved.type}_${randomUUID()}`,
    ...resolved,
    priority: 3,
    createdAt: new Date().toISOString(),
    status: "pending_approval",
    origin: "website",
  };
}

function truncateDiscord(value: unknown, maxLength: number): string {
  return sanitizeText(value, maxLength) || "—";
}

async function notifyDiscord(item: JsonRecord): Promise<string | null> {
  if (isExpiredRecord(item)) {
    console.warn(
      "[suggestions API] Sugestão expirada; notificação de aprovação bloqueada.",
    );
    return null;
  }
  const token = process.env.DISCORD_BOT_TOKEN?.trim();
  const channelId = process.env.DISCORD_REVIEW_CHANNEL_ID?.trim();
  if (!token || !channelId) {
    console.warn(
      "[suggestions API] Discord não configurado; sugestão mantida em pending_approval.",
    );
    return null;
  }

  const type = isRecommendationType(item.type) ? item.type : "project";
  const typeEmojis: Record<RecommendationType, string> = {
    book: "📚",
    podcast: "🎙️",
    movie: "🎬",
    highlight: "📰",
    project: "💡",
  };
  const typeColors: Record<RecommendationType, number> = {
    book: 0x2e86ab,
    podcast: 0x8338ec,
    movie: 0xe63946,
    highlight: 0xf77f00,
    project: 0x0099ff,
  };

  const link = normalizePublicUrl(item.link);
  const imageUrl =
    normalizePublicUrl(item.sourceImageUrl) || normalizePublicUrl(item.imageUrl);
  const verification = isJsonRecord(item.verification)
    ? item.verification
    : {};
  const verificationId = sanitizeText(
    verification.externalId || verification.entityId,
    180,
  );
  const isWholePodcast =
    type === "podcast" && verificationId.startsWith("apple:podcast:");
  const confidence =
    typeof verification.confidence === "number"
      ? `${Math.round(verification.confidence * 100)}%`
      : "—";

  const fields: Array<{ name: string; value: string; inline: boolean }> = [
    {
      name: "Tipo",
      value: truncateDiscord(item.category || type, 1_024),
      inline: true,
    },
    {
      name: "Autor / Fonte",
      value: truncateDiscord(item.authorOrMeta, 1_024),
      inline: true,
    },
    {
      name: "Verificação",
      value: `${truncateDiscord(verification.provider, 80)} · ${confidence}`,
      inline: true,
    },
  ];
  if (link) {
    fields.push({ name: "Link canónico", value: link, inline: false });
  }
  if (typeof item.expiryDate === "string") {
    fields.push({
      name: "Prazo de aprovação",
      value: new Date(item.expiryDate).toLocaleDateString("pt-PT"),
      inline: true,
    });
  }
  if (isWholePodcast) {
    fields.push({
      name: "Opções ao aprovar",
      value:
        "Recomendar uma vez · acompanhar episódios recentes · ou fazer ambos",
      inline: false,
    });
  }

  const embed: JsonRecord = {
    title: truncateDiscord(
      `${typeEmojis[type]} Sugestão: ${truncateDiscord(item.title, 220)}`,
      256,
    ),
    description: truncateDiscord(item.description, 4_096),
    color: typeColors[type],
    fields,
    footer: {
      text: truncateDiscord(
        `ID: ${item.id || "sem-id"} | Fonte validada no servidor`,
        2_048,
      ),
    },
    timestamp:
      typeof item.createdAt === "string"
        ? item.createdAt
        : new Date().toISOString(),
  };
  if (imageUrl && type !== "project") {
    embed.thumbnail = { url: imageUrl };
  }

  const messageBody = JSON.stringify({
    embeds: [embed],
    components: [
      {
        type: 1,
        components: [
          {
            type: 2,
            style: 3,
            label: isWholePodcast ? "Escolher aprovação" : "Aprovar",
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
        return typeof delivered.id === "string" ? delivered.id : null;
      }

      const details = sanitizeText(await response.text(), 500);
      console.error(
        `[suggestions API] Discord respondeu com HTTP ${response.status}:`,
        details,
      );
      // A 429 is an explicit non-delivery and is safe to retry. A 5xx can be
      // ambiguous, so the durable outbox will search Discord by item ID before
      // attempting it again; retrying here could create duplicate cards.
      const retryable = response.status === 429;
      if (!retryable || attempt === 3) return null;
      const retryAfterHeader = Number(response.headers.get("retry-after"));
      const retryAfterMs = Number.isFinite(retryAfterHeader)
        ? Math.min(2_000, Math.max(250, retryAfterHeader * 1_000))
        : attempt * 500;
      await new Promise((resolve) => setTimeout(resolve, retryAfterMs));
    } catch (error: unknown) {
      // An ambiguous network failure may have delivered the message; retrying
      // could create duplicate approval cards.
      console.error("[suggestions API] Falha ao notificar o Discord:", error);
      return null;
    }
  }
  return null;
}

async function appendSuggestion(payload: AppendPayload) {
  if (!payload.item || !isRecommendationType(payload.item.type)) {
    throw new ApiError("Tipo de recomendação inválido.", 400);
  }

  const title = sanitizeText(payload.item.title, 220);
  const link =
    typeof payload.item.link === "string" ? payload.item.link.trim() : "";
  const resolved = await resolveRecommendation({
    type: payload.item.type,
    title,
    link,
  });
  const resolutionWarning = resolved.verification.warnings[0];
  if (!validateResolvedRecommendation(resolved)) {
    throw new ApiError(
      resolutionWarning ||
        "Não foi possível confirmar a recomendação.",
      422,
    );
  }

  const current = await readStorageSnapshot();
  if (
    [...current.database.queue, ...current.database.history].some((item) =>
      isDuplicate(item, resolved),
    )
  ) {
    throw new ApiError(
      "Esta recomendação já existe na fila ou no histórico.",
      409,
    );
  }

  const readyToStore = await persistRecommendationCover(resolved);
  const storedItem = createStoredRecommendation(readyToStore);
  const persisted = await mutateDatabase((database) => {
    const duplicate = [...database.queue, ...database.history].some((item) =>
      isDuplicate(item, readyToStore),
    );
    if (duplicate) {
      throw new ApiError(
        "Esta recomendação já existe na fila ou no histórico.",
        409,
      );
    }
    database.queue.unshift(storedItem);
    return storedItem;
  });

  const discordMessageId = await notifyDiscord(storedItem);
  const notificationSent = Boolean(discordMessageId);
  let finalItem = storedItem;
  let finalSha = persisted.sha;
  let statusPersisted = false;

  if (notificationSent) {
    try {
      const marked = await mutateDatabase((database) => {
        const index = database.queue.findIndex(
          (item) => item.id === storedItem.id,
        );
        if (index < 0) {
          throw new ApiError(
            "A sugestão desapareceu da fila antes de ser marcada.",
            409,
          );
        }
        const current = database.queue[index];
        if (current.status !== "pending_approval") {
          return { item: current, changed: false };
        }
        const updated = {
          ...current,
          status: "pending_sent",
          notificationStatus: "sent",
          notificationAttempts:
            Number(current.notificationAttempts || 0) + 1,
          discordMessageId,
          discordNotifiedAt: new Date().toISOString(),
        };
        database.queue[index] = updated;
        return { item: updated, changed: true };
      });
      finalItem = marked.result.item;
      finalSha = marked.sha;
      statusPersisted = marked.result.changed;
    } catch (error: unknown) {
      console.error(
        "[suggestions API] Discord foi notificado, mas pending_sent não pôde ser persistido:",
        error,
      );
    }
  }
  if (!notificationSent) {
    try {
      const retryState = await mutateDatabase((database) => {
        const index = database.queue.findIndex(
          (item) => item.id === storedItem.id,
        );
        if (index < 0) return { item: storedItem, changed: false };
        const current = database.queue[index];
        if (current.status !== "pending_approval") {
          return { item: current, changed: false };
        }
        const attempts = Number(current.notificationAttempts || 0) + 1;
        const updated = {
          ...current,
          notificationStatus: "pending_retry",
          notificationAttempts: attempts,
          nextNotificationAttemptAt: new Date(
            Date.now() + 6 * 60 * 60 * 1_000,
          ).toISOString(),
        };
        database.queue[index] = updated;
        return { item: updated, changed: true };
      });
      finalItem = retryState.result.item;
      finalSha = retryState.sha;
    } catch (error: unknown) {
      console.error(
        "[suggestions API] Não foi possível gravar o estado de retry do Discord:",
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
      warning:
        notificationSent && !statusPersisted
          ? "A notificação foi enviada, mas o estado permaneceu pending_approval."
          : !notificationSent
            ? "A sugestão ficou guardada, mas o Discord não confirmou a notificação."
            : undefined,
    },
    { status: notificationSent && statusPersisted ? 201 : 202 },
  );
}

async function replaceDatabase(req: NextRequest, payload: ReplacePayload) {
  if (!isFullReplaceAuthorized(req)) {
    throw new ApiError(
      "A substituição integral da fila só está disponível localmente ou com token de administrador.",
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
    const status = error instanceof ApiError ? error.status : 500;
    return NextResponse.json(
      { error: errorMessage(error) },
      { status },
    );
  }
}

export async function POST(req: NextRequest) {
  try {
    assertRequestSize(req);
    const rateLimit = checkRecommendationRateLimit(
      req,
      "suggestions-write",
      isFullReplaceAuthorized(req) ? 30 : 8,
    );
    if (!rateLimit.allowed) {
      return NextResponse.json(
        { error: "Demasiados pedidos. Tenta novamente mais tarde." },
        {
          status: 429,
          headers: { "Retry-After": String(rateLimit.retryAfterSeconds) },
        },
      );
    }
    const payload: unknown = await req.json();
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
    throw new ApiError("Ação inválida.", 400);
  } catch (error: unknown) {
    console.error("[suggestions API] Falha ao guardar:", error);
    const status = error instanceof ApiError ? error.status : 500;
    return NextResponse.json(
      { error: resolutionErrorMessage(error) },
      { status },
    );
  }
}
