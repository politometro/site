import { lookup } from "node:dns/promises";
import { isIP } from "node:net";

export const RECOMMENDATION_TYPES = [
  "book",
  "podcast",
  "movie",
  "nostalgia",
  "investigation",
  "highlight",
  "project",
] as const;

export type RecommendationType = (typeof RECOMMENDATION_TYPES)[number];
export type ResolutionStatus = "verified" | "unresolved";

export interface RecommendationVerification {
  status: ResolutionStatus;
  provider: string;
  confidence: number;
  checkedAt: string;
  canonicalUrl?: string;
  externalId?: string;
  entityId?: string;
  coverHash?: string;
  source?: string;
  score?: number;
  verifiedAt?: string;
  sourceTitle?: string;
  titleScore?: number;
  matchedFields: string[];
  warnings: string[];
}

export interface ResolvedRecommendation {
  type: RecommendationType;
  category: string;
  title: string;
  link: string;
  authorOrMeta: string;
  description: string;
  imageUrl: string;
  sourcePublishedAt: string | null;
  expiryDate: string | null;
  resolutionStatus: ResolutionStatus;
  verification: RecommendationVerification;
}

export interface ResolveRecommendationInput {
  type: RecommendationType;
  title: string;
  link?: string;
}

export interface ResolveRecommendationOptions {
  summarize?: boolean;
}

export interface PreparedRecommendationCover {
  bytes: Uint8Array;
  sourceUrl: string;
  sourceMime: string;
  width: number;
  height: number;
}

interface Candidate {
  provider: string;
  type: RecommendationType;
  title: string;
  link: string;
  authorOrMeta: string;
  description: string;
  imageUrl: string;
  externalId?: string;
  titleScore: number;
  confidence: number;
  matchedFields: string[];
  sourcePublishedAt?: string;
  cadenceDays?: number;
  warnings?: string[];
}

interface PageMetadata {
  finalUrl: string;
  canonicalUrl: string;
  title: string;
  author: string;
  description: string;
  imageUrl: string;
  publishedAt: string;
  externalId?: string;
}

interface CseItem {
  title?: string;
  link?: string;
  snippet?: string;
  displayLink?: string;
  pagemap?: {
    metatags?: Array<Record<string, string>>;
    cse_image?: Array<{ src?: string }>;
  };
}

const CATEGORY_BY_TYPE: Record<RecommendationType, string> = {
  book: "Livro",
  podcast: "Podcast",
  movie: "Filme",
  nostalgia: "Nostalgia",
  investigation: "Investigação",
  highlight: "Destaque",
  project: "Sugestão para o Projeto",
};

const BOOK_DOMAINS = [
  "wook.pt",
  "fnac.pt",
  "bertrand.pt",
  "leyaonline.com",
  "editorial-presenca.pt",
  "penguinlivros.pt",
  "openlibrary.org",
];

const HIGHLIGHT_DOMAINS = [
  "expresso.pt",
  "publico.pt",
  "observador.pt",
  "rtp.pt",
  "sicnoticias.pt",
  "cnnportugal.iol.pt",
  "dn.pt",
  "jornaldenegocios.pt",
  "youtube.com",
  "youtu.be",
];

const PROVIDED_LINK_DOMAINS: Record<
  Exclude<RecommendationType, "project">,
  string[]
> = {
  book: BOOK_DOMAINS,
  podcast: ["podcasts.apple.com"],
  movie: ["imdb.com"],
  nostalgia: ["rtp.pt", "youtube.com", "youtu.be"],
  investigation: [
    "rtp.pt",
    "sicnoticias.pt",
    "nowcanal.pt",
    "youtube.com",
    "youtu.be",
  ],
  highlight: HIGHLIGHT_DOMAINS,
};

const STOP_WORDS = new Set([
  "a",
  "ao",
  "aos",
  "as",
  "da",
  "das",
  "de",
  "do",
  "dos",
  "e",
  "em",
  "na",
  "nas",
  "no",
  "nos",
  "o",
  "os",
  "para",
  "por",
  "uma",
  "um",
]);

const MAX_TITLE_LENGTH = 220;
const MAX_LINK_LENGTH = 2_048;
const MAX_HTML_BYTES = 1_500_000;
const MAX_JSON_BYTES = 3_000_000;
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const MAX_IMAGE_PIXELS = 30_000_000;
const REQUEST_TIMEOUT_MS = 10_000;
const MIN_TEMPORAL_REVIEW_MS = 24 * 60 * 60 * 1_000;

export class RecommendationResolutionError extends Error {
  readonly code: string;

  constructor(message: string, code: string) {
    super(message);
    this.name = "RecommendationResolutionError";
    this.code = code;
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function isRecommendationType(value: unknown): value is RecommendationType {
  return (
    typeof value === "string" &&
    (RECOMMENDATION_TYPES as readonly string[]).includes(value)
  );
}

export function categoryForType(type: RecommendationType): string {
  return CATEGORY_BY_TYPE[type];
}

export function sanitizeText(value: unknown, maxLength = 600): string {
  if (typeof value !== "string") return "";
  return value
    .replace(/<[^>]*>/g, " ")
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, maxLength);
}

function normalizeDate(value: unknown): string {
  if (typeof value !== "string" && typeof value !== "number") return "";
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toISOString() : "";
}

function applePodcastExternalId(...values: string[]): string {
  for (const value of values) {
    try {
      const url = new URL(value);
      if (!isAllowedDomain(url.toString(), ["podcasts.apple.com"])) continue;
      const episodeId = url.searchParams.get("i")?.match(/^\d+$/)?.[0];
      if (episodeId) return `apple:episode:${episodeId}`;
      const collectionId = url.pathname.match(/\/id(\d+)(?:\/|$)/i)?.[1];
      if (collectionId) return `apple:podcast:${collectionId}`;
    } catch {
      continue;
    }
  }
  return "";
}

function addDays(date: Date, days: number): string {
  return new Date(date.getTime() + days * 86_400_000).toISOString();
}

function temporalFields(candidate: Candidate): {
  sourcePublishedAt: string | null;
  expiryDate: string | null;
} {
  const sourcePublishedAt = normalizeDate(candidate.sourcePublishedAt) || null;
  const publishedOrNow = sourcePublishedAt
    ? new Date(sourcePublishedAt)
    : new Date();
  if (candidate.type === "podcast") {
    if (!sourcePublishedAt) {
      return { sourcePublishedAt: null, expiryDate: null };
    }
    const cadence =
      typeof candidate.cadenceDays === "number" &&
      Number.isFinite(candidate.cadenceDays)
      ? candidate.cadenceDays
      : null;
    const ttlDays =
      cadence === null
        ? 10
        : cadence <= 2
          ? 3
          : cadence <= 8
            ? 10
            : cadence <= 15
              ? 14
              : 21;
    return {
      sourcePublishedAt,
      expiryDate: addDays(publishedOrNow, ttlDays),
    };
  }
  if (candidate.type === "highlight") {
    if (!sourcePublishedAt) {
      return { sourcePublishedAt: null, expiryDate: null };
    }
    return {
      sourcePublishedAt,
      expiryDate: addDays(publishedOrNow, 45),
    };
  }
  return { sourcePublishedAt, expiryDate: null };
}

function decodeHtmlEntities(value: string): string {
  const named: Record<string, string> = {
    amp: "&",
    apos: "'",
    gt: ">",
    hellip: "…",
    laquo: "«",
    ldquo: "“",
    lt: "<",
    nbsp: " ",
    quot: '"',
    raquo: "»",
    rdquo: "”",
  };

  return value
    .replace(/&#(\d+);/g, (_, code: string) =>
      String.fromCodePoint(Number.parseInt(code, 10)),
    )
    .replace(/&#x([0-9a-f]+);/gi, (_, code: string) =>
      String.fromCodePoint(Number.parseInt(code, 16)),
    )
    .replace(/&([a-z]+);/gi, (entity, name: string) => named[name] ?? entity);
}

function normalizeForMatch(value: string): string {
  return sanitizeText(value, 400)
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLocaleLowerCase("pt-PT")
    .replace(/[^\p{Letter}\p{Number}]+/gu, " ")
    .trim();
}

const HIGHLIGHT_EDITORIAL_MARKERS = [
  "opiniao",
  "editorial",
  "cronica",
  "analise",
  "investigacao",
  "grande reportagem",
  "reportagem especial",
  "entrevista",
  "explicador",
  "fact check",
  "documentario",
  "ensaio",
  "debate",
];

function isEligibleHighlightCandidate(candidate: {
  title: string;
  description: string;
  link: string;
}): boolean {
  let pathSegments: string[] = [];
  let querySegments: string[] = [];
  try {
    const parsed = new URL(candidate.link);
    pathSegments = parsed.pathname
      .split("/")
      .map((segment) => normalizeForMatch(decodeURIComponent(segment)))
      .filter(Boolean);
    querySegments = Array.from(parsed.searchParams.entries()).flatMap(
      ([key, value]) => [normalizeForMatch(key), normalizeForMatch(value)],
    );
  } catch {
    return false;
  }
  const titleLabel = sanitizeText(candidate.title, 400)
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLocaleLowerCase("pt-PT")
    .trim();
  const description = normalizeForMatch(candidate.description);
  const strongDescriptionMarkers = [
    "artigo de opiniao",
    "texto de opiniao",
    "jornalismo de investigacao",
    "grande reportagem",
    "reportagem especial",
    "entrevista completa",
    "analise aprofundada",
  ];
  return (
    HIGHLIGHT_EDITORIAL_MARKERS.some(
      (marker) =>
        pathSegments.includes(marker) || querySegments.includes(marker),
    ) ||
    HIGHLIGHT_EDITORIAL_MARKERS.some((marker) =>
      new RegExp(`^${marker.replace(/\s+/g, "\\s+")}\\s*(?::|\\||-|–|—)`).test(
        titleLabel,
      ),
    ) ||
    strongDescriptionMarkers.some((marker) => description.includes(marker))
  );
}

function matchTokens(value: string): string[] {
  return normalizeForMatch(value)
    .split(/\s+/)
    .filter((token) => token.length > 1 && !STOP_WORDS.has(token));
}

export function titleSimilarity(left: string, right: string): number {
  const normalizedLeft = normalizeForMatch(left);
  const normalizedRight = normalizeForMatch(right);
  if (!normalizedLeft || !normalizedRight) return 0;
  if (normalizedLeft === normalizedRight) return 1;
  if (
    normalizedLeft.length >= 5 &&
    normalizedRight.length >= 5 &&
    (normalizedLeft.includes(normalizedRight) ||
      normalizedRight.includes(normalizedLeft))
  ) {
    const lengthRatio =
      Math.min(normalizedLeft.length, normalizedRight.length) /
      Math.max(normalizedLeft.length, normalizedRight.length);
    return Math.min(0.98, 0.82 + lengthRatio * 0.16);
  }

  const leftSequence = matchTokens(left);
  const rightSequence = matchTokens(right);
  const leftTokens = new Set(leftSequence);
  const rightTokens = new Set(rightSequence);
  if (!leftTokens.size || !rightTokens.size) return 0;

  let intersection = 0;
  for (const token of leftTokens) {
    if (rightTokens.has(token)) intersection += 1;
  }
  const subsetScore = intersection / Math.min(leftTokens.size, rightTokens.size);
  const unionScore =
    intersection / (leftTokens.size + rightTokens.size - intersection);

  const rows = leftSequence.length + 1;
  const columns = rightSequence.length + 1;
  const matrix = Array.from({ length: rows }, () =>
    Array<number>(columns).fill(0),
  );
  for (let row = 1; row < rows; row += 1) {
    for (let column = 1; column < columns; column += 1) {
      matrix[row][column] =
        leftSequence[row - 1] === rightSequence[column - 1]
          ? matrix[row - 1][column - 1] + 1
          : Math.max(matrix[row - 1][column], matrix[row][column - 1]);
    }
  }
  const orderedScore =
    matrix[rows - 1][columns - 1] /
    Math.max(leftSequence.length, rightSequence.length);
  return Math.min(
    1,
    subsetScore * 0.45 + unionScore * 0.25 + orderedScore * 0.3,
  );
}

function isForbiddenHostname(hostname: string): boolean {
  const host = hostname.toLocaleLowerCase().replace(/^\[|\]$/g, "");
  if (
    host === "localhost" ||
    host === "localhost.localdomain" ||
    host.endsWith(".localhost") ||
    host.endsWith(".local") ||
    host === "0.0.0.0" ||
    host === "::" ||
    host === "::1"
  ) {
    return true;
  }

  if (host.includes(":")) {
    return (
      host.startsWith("fc") ||
      host.startsWith("fd") ||
      host.startsWith("fe8") ||
      host.startsWith("fe9") ||
      host.startsWith("fea") ||
      host.startsWith("feb")
    );
  }

  const ipv4 = host.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (!ipv4) return false;
  const octets = ipv4.slice(1).map(Number);
  if (octets.some((octet) => octet < 0 || octet > 255)) return true;
  const [a, b] = octets;
  return (
    a === 0 ||
    a === 10 ||
    a === 127 ||
    a >= 224 ||
    (a === 100 && b >= 64 && b <= 127) ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168)
  );
}

function isForbiddenAddress(address: string): boolean {
  const normalized = address.toLocaleLowerCase().split("%")[0];
  const mappedIpv4 = normalized.match(/^::ffff:(\d+\.\d+\.\d+\.\d+)$/)?.[1];
  if (mappedIpv4) return isForbiddenHostname(mappedIpv4);
  const family = isIP(normalized);
  if (family === 4) return isForbiddenHostname(normalized);
  if (family !== 6) return true;
  return (
    normalized === "::" ||
    normalized === "::1" ||
    normalized.startsWith("::ffff:") ||
    normalized.startsWith("fc") ||
    normalized.startsWith("fd") ||
    normalized.startsWith("fe") ||
    normalized.startsWith("ff")
  );
}

async function assertPublicDns(urlValue: string): Promise<void> {
  const hostname = new URL(urlValue).hostname.replace(/^\[|\]$/g, "");
  if (isIP(hostname)) {
    if (isForbiddenAddress(hostname)) {
      throw new RecommendationResolutionError(
        "O destino resolve para uma rede privada ou reservada.",
        "private_network",
      );
    }
    return;
  }

  let addresses: Array<{ address: string }>;
  try {
    addresses = await lookup(hostname, { all: true, verbatim: true });
  } catch {
    throw new RecommendationResolutionError(
      "Não foi possível confirmar o DNS da fonte.",
      "dns_lookup_failed",
    );
  }
  if (
    !addresses.length ||
    addresses.some((entry) => isForbiddenAddress(entry.address))
  ) {
    throw new RecommendationResolutionError(
      "O destino resolve para uma rede privada ou reservada.",
      "private_network",
    );
  }
}

export function normalizePublicUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > MAX_LINK_LENGTH) return null;

  try {
    const url = new URL(trimmed);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    if (url.username || url.password || isForbiddenHostname(url.hostname)) return null;
    url.hash = "";
    return url.toString();
  } catch {
    return null;
  }
}

export function isWikipediaUrl(value: string): boolean {
  const normalized = normalizePublicUrl(value);
  if (!normalized) return false;
  const hostname = new URL(normalized).hostname.toLocaleLowerCase();
  return (
    hostname === "wikipedia.org" ||
    hostname.endsWith(".wikipedia.org") ||
    hostname === "wikidata.org" ||
    hostname.endsWith(".wikidata.org")
  );
}

function hostnameMatches(hostname: string, domain: string): boolean {
  const normalizedHost = hostname.toLocaleLowerCase();
  const normalizedDomain = domain.toLocaleLowerCase();
  return (
    normalizedHost === normalizedDomain ||
    normalizedHost.endsWith(`.${normalizedDomain}`)
  );
}

function isAllowedDomain(url: string, domains: string[]): boolean {
  const hostname = new URL(url).hostname;
  return domains.some((domain) => hostnameMatches(hostname, domain));
}

function isYouTubeUrl(value: string): boolean {
  const normalized = normalizePublicUrl(value);
  if (!normalized) return false;
  const hostname = new URL(normalized).hostname.toLocaleLowerCase();
  return hostnameMatches(hostname, "youtube.com") || hostnameMatches(hostname, "youtu.be");
}

function isLikelyDetailUrl(type: RecommendationType, value: string): boolean {
  const url = new URL(value);
  if (type === "podcast") return true;
  if (isYouTubeUrl(value)) {
    return url.hostname.includes("youtu.be") || url.pathname === "/watch";
  }

  const segments = url.pathname.split("/").filter(Boolean);
  if (!segments.length) return false;
  const blockedSegments = new Set([
    "autor",
    "author",
    "authors",
    "categoria",
    "category",
    "pesquisa",
    "procurar",
    "search",
    "tag",
  ]);
  return !blockedSegments.has(segments[0].toLocaleLowerCase());
}

async function fetchPublic(
  initialUrl: string,
  init: RequestInit = {},
  maxRedirects = 4,
): Promise<{ response: Response; finalUrl: string }> {
  let currentUrl = normalizePublicUrl(initialUrl);
  if (!currentUrl) {
    throw new RecommendationResolutionError("URL inválido ou não público.", "invalid_url");
  }

  for (let redirect = 0; redirect <= maxRedirects; redirect += 1) {
    await assertPublicDns(currentUrl);
    const response = await fetch(currentUrl, {
      ...init,
      redirect: "manual",
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      headers: {
        "User-Agent":
          "Politometro/2.0 (+https://politometro.pt; recommendation verification)",
        Accept:
          "text/html,application/xhtml+xml,application/json,image/avif,image/webp,image/png,image/jpeg,*/*;q=0.7",
        "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.7",
        ...(init.headers ?? {}),
      },
      cache: "no-store",
    });

    if (
      response.status >= 300 &&
      response.status < 400 &&
      response.headers.get("location")
    ) {
      const target = new URL(response.headers.get("location")!, currentUrl).toString();
      currentUrl = normalizePublicUrl(target);
      if (!currentUrl) {
        throw new RecommendationResolutionError(
          "O redirecionamento aponta para um URL não permitido.",
          "unsafe_redirect",
        );
      }
      continue;
    }

    return { response, finalUrl: currentUrl };
  }

  throw new RecommendationResolutionError(
    "A fonte excedeu o limite de redirecionamentos.",
    "too_many_redirects",
  );
}

async function readLimitedBody(response: Response, maxBytes: number): Promise<Uint8Array> {
  const contentLength = Number(response.headers.get("content-length") || "0");
  if (contentLength > maxBytes) {
    throw new RecommendationResolutionError(
      "A resposta da fonte excede o tamanho permitido.",
      "response_too_large",
    );
  }

  if (!response.body) return new Uint8Array();
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maxBytes) {
        throw new RecommendationResolutionError(
          "A resposta da fonte excede o tamanho permitido.",
          "response_too_large",
        );
      }
      chunks.push(value);
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }

  const result = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return result;
}

async function fetchText(
  url: string,
  acceptedContentTypes: string[],
  maxBytes = MAX_HTML_BYTES,
): Promise<{ text: string; finalUrl: string; contentType: string }> {
  const { response, finalUrl } = await fetchPublic(url);
  if (!response.ok) {
    throw new RecommendationResolutionError(
      `A fonte respondeu com HTTP ${response.status}.`,
      "source_http_error",
    );
  }
  const contentType = response.headers.get("content-type")?.toLocaleLowerCase() ?? "";
  if (
    acceptedContentTypes.length &&
    !acceptedContentTypes.some((allowed) => contentType.includes(allowed))
  ) {
    throw new RecommendationResolutionError(
      `Tipo de conteúdo inesperado: ${contentType || "desconhecido"}.`,
      "unexpected_content_type",
    );
  }
  const body = await readLimitedBody(response, maxBytes);
  return {
    text: new TextDecoder("utf-8").decode(body),
    finalUrl,
    contentType,
  };
}

async function fetchJson<T>(url: string): Promise<T> {
  const { text } = await fetchText(
    url,
    [
      "application/json",
      "application/ld+json",
      "text/json",
      "text/javascript",
    ],
    MAX_JSON_BYTES,
  );
  return JSON.parse(text) as T;
}

function hasRasterMagic(bytes: Uint8Array): boolean {
  if (bytes.length < 12) return false;
  const isJpeg = bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff;
  const isPng =
    bytes[0] === 0x89 &&
    bytes[1] === 0x50 &&
    bytes[2] === 0x4e &&
    bytes[3] === 0x47;
  const isGif =
    bytes[0] === 0x47 &&
    bytes[1] === 0x49 &&
    bytes[2] === 0x46 &&
    bytes[3] === 0x38;
  const isWebp =
    bytes[0] === 0x52 &&
    bytes[1] === 0x49 &&
    bytes[2] === 0x46 &&
    bytes[3] === 0x46 &&
    bytes[8] === 0x57 &&
    bytes[9] === 0x45 &&
    bytes[10] === 0x42 &&
    bytes[11] === 0x50;
  const box = new TextDecoder("ascii").decode(bytes.slice(4, 16));
  const isAvif = box.includes("ftypavif") || box.includes("ftypavis");
  return isJpeg || isPng || isGif || isWebp || isAvif;
}

async function verifyImageUrl(value: string): Promise<string | null> {
  const normalized = normalizePublicUrl(value);
  if (!normalized) return null;

  try {
    const { response, finalUrl } = await fetchPublic(normalized, {
      headers: {
        Accept: "image/avif,image/webp,image/png,image/jpeg,image/gif;q=0.8",
        Range: "bytes=0-65535",
      },
    });
    if (!response.ok && response.status !== 206) return null;

    const contentType = response.headers.get("content-type")?.toLocaleLowerCase() ?? "";
    if (
      contentType.includes("svg") ||
      contentType.includes("html") ||
      contentType.includes("text/")
    ) {
      return null;
    }

    if (!response.body) return null;
    const reader = response.body.getReader();
    const { value } = await reader.read();
    await reader.cancel().catch(() => undefined);
    if (!value || !hasRasterMagic(value)) return null;
    return finalUrl;
  } catch {
    return null;
  }
}

export async function prepareRecommendationCover(
  value: string,
): Promise<PreparedRecommendationCover> {
  const normalized = normalizePublicUrl(value);
  if (!normalized) {
    throw new RecommendationResolutionError(
      "O URL da imagem não é público ou válido.",
      "invalid_image_url",
    );
  }
  const { response, finalUrl } = await fetchPublic(normalized, {
    headers: {
      Accept: "image/avif,image/webp,image/png,image/jpeg,image/gif;q=0.8",
    },
  });
  if (!response.ok) {
    throw new RecommendationResolutionError(
      `A imagem respondeu com HTTP ${response.status}.`,
      "image_http_error",
    );
  }
  const sourceMime =
    response.headers.get("content-type")?.split(";")[0]?.trim().toLowerCase() ??
    "";
  if (
    !sourceMime.startsWith("image/") ||
    sourceMime.includes("svg") ||
    sourceMime.includes("html")
  ) {
    throw new RecommendationResolutionError(
      "A capa não é uma imagem raster suportada.",
      "invalid_image_type",
    );
  }

  const sourceBytes = await readLimitedBody(response, MAX_IMAGE_BYTES);
  if (sourceBytes.byteLength < 1_024) {
    throw new RecommendationResolutionError(
      "A capa recebida é demasiado pequena.",
      "image_too_small",
    );
  }

  try {
    const sharp = (await import("sharp")).default;
    const bytes = await sharp(Buffer.from(sourceBytes), {
      failOn: "error",
      limitInputPixels: MAX_IMAGE_PIXELS,
    })
      .rotate()
      .resize({
        width: 1_600,
        height: 1_600,
        fit: "inside",
        withoutEnlargement: true,
      })
      .flatten({ background: "#ffffff" })
      .jpeg({ quality: 90, progressive: true, mozjpeg: true })
      .toBuffer();
    const metadata = await sharp(bytes).metadata();
    const width = metadata.width ?? 0;
    const height = metadata.height ?? 0;
    if (
      metadata.format !== "jpeg" ||
      width < 160 ||
      height < 160 ||
      width * height > MAX_IMAGE_PIXELS
    ) {
      throw new Error("dimensões ou formato inválidos");
    }
    return {
      bytes,
      sourceUrl: finalUrl,
      sourceMime,
      width,
      height,
    };
  } catch (error: unknown) {
    throw new RecommendationResolutionError(
      `Não foi possível normalizar a capa como JPEG: ${errorMessage(error)}`,
      "image_normalization_failed",
    );
  }
}

function htmlAttribute(tag: string, attribute: string): string {
  const quoted = tag.match(
    new RegExp(`${attribute}\\s*=\\s*(["'])(.*?)\\1`, "i"),
  );
  if (quoted) return decodeHtmlEntities(quoted[2].trim());
  const unquoted = tag.match(new RegExp(`${attribute}\\s*=\\s*([^\\s>]+)`, "i"));
  return unquoted ? decodeHtmlEntities(unquoted[1].trim()) : "";
}

function extractMeta(html: string, keys: string[]): string {
  const normalizedKeys = new Set(keys.map((key) => key.toLocaleLowerCase()));
  for (const match of html.matchAll(/<meta\b[^>]*>/gi)) {
    const tag = match[0];
    const key = (
      htmlAttribute(tag, "property") ||
      htmlAttribute(tag, "name") ||
      htmlAttribute(tag, "itemprop")
    ).toLocaleLowerCase();
    if (normalizedKeys.has(key)) {
      const content = htmlAttribute(tag, "content");
      if (content) return sanitizeText(decodeHtmlEntities(content), 2_000);
    }
  }
  return "";
}

function extractCanonicalUrl(html: string, baseUrl: string): string {
  for (const match of html.matchAll(/<link\b[^>]*>/gi)) {
    const tag = match[0];
    const rel = htmlAttribute(tag, "rel").toLocaleLowerCase().split(/\s+/);
    if (!rel.includes("canonical")) continue;
    const href = htmlAttribute(tag, "href");
    if (!href) continue;
    const absolute = normalizePublicUrl(new URL(href, baseUrl).toString());
    if (absolute) return absolute;
  }
  return baseUrl;
}

function extractTitleTag(html: string): string {
  const match = html.match(/<title\b[^>]*>([\s\S]*?)<\/title>/i);
  return match ? sanitizeText(decodeHtmlEntities(match[1]), 400) : "";
}

function jsonLdNodes(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) return value.flatMap(jsonLdNodes);
  if (!value || typeof value !== "object") return [];
  const objectValue = value as Record<string, unknown>;
  const graph = objectValue["@graph"];
  return [objectValue, ...(graph ? jsonLdNodes(graph) : [])];
}

function extractJsonLd(html: string): Record<string, unknown>[] {
  const nodes: Record<string, unknown>[] = [];
  for (const match of html.matchAll(
    /<script\b[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi,
  )) {
    const raw = match[1].replace(/^\s*<!--|-->\s*$/g, "").trim();
    if (!raw) continue;
    try {
      nodes.push(...jsonLdNodes(JSON.parse(raw)));
    } catch {
      // Invalid JSON-LD is ignored; OpenGraph remains available.
    }
  }
  return nodes;
}

function schemaTypes(node: Record<string, unknown>): string[] {
  const raw = node["@type"];
  return (Array.isArray(raw) ? raw : [raw])
    .filter((value): value is string => typeof value === "string")
    .map((value) => value.toLocaleLowerCase());
}

function nodeFitsType(node: Record<string, unknown>, type: RecommendationType): boolean {
  const types = schemaTypes(node);
  const expected: Record<RecommendationType, string[]> = {
    book: ["book", "product"],
    podcast: ["podcastepisode", "podcastseries", "audioobject"],
    movie: ["movie", "tvseries", "creativework"],
    nostalgia: ["episode", "tvepisode", "videoobject", "creativework"],
    investigation: [
      "article",
      "newsarticle",
      "reportagenewsarticle",
      "episode",
      "tvepisode",
      "videoobject",
    ],
    highlight: [
      "article",
      "newsarticle",
      "reportagenewsarticle",
      "videoobject",
    ],
    project: [],
  };
  return expected[type].some((candidate) => types.includes(candidate));
}

function textFromSchema(value: unknown): string {
  if (typeof value === "string") return sanitizeText(value, 1_500);
  if (Array.isArray(value)) {
    return value.map(textFromSchema).filter(Boolean).join(", ");
  }
  if (value && typeof value === "object") {
    const objectValue = value as Record<string, unknown>;
    return textFromSchema(
      objectValue.name ??
        objectValue.headline ??
        objectValue.url ??
        objectValue.contentUrl,
    );
  }
  return "";
}

function imageFromSchema(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    for (const item of value) {
      const image = imageFromSchema(item);
      if (image) return image;
    }
    return "";
  }
  if (value && typeof value === "object") {
    const objectValue = value as Record<string, unknown>;
    return imageFromSchema(
      objectValue.url ?? objectValue.contentUrl ?? objectValue.thumbnailUrl,
    );
  }
  return "";
}

function stripKnownSiteSuffix(value: string): string {
  return sanitizeText(value, 400)
    .replace(
      /\s+[|–—-]\s+(WOOK|Fnac(?:\.pt)?|Bertrand|Open Library|IMDb|YouTube|RTP|Expresso|PÚBLICO|Observador|SIC Notícias).*$/i,
      "",
    )
    .trim();
}

function extractPageMetadata(
  html: string,
  finalUrl: string,
  type: RecommendationType,
): PageMetadata {
  const nodes = extractJsonLd(html);
  const node =
    nodes.find((candidate) => nodeFitsType(candidate, type)) ??
    nodes.find((candidate) => candidate.name || candidate.headline) ??
    {};

  const canonicalUrl = extractCanonicalUrl(html, finalUrl);
  const title = stripKnownSiteSuffix(
    textFromSchema(node.headline ?? node.name) ||
      extractMeta(html, ["og:title", "twitter:title"]) ||
      extractTitleTag(html),
  );
  const author =
    textFromSchema(node.author ?? node.creator ?? node.director) ||
    extractMeta(html, [
      "author",
      "article:author",
      "book:author",
      "byl",
      "og:site_name",
    ]) ||
    textFromSchema(node.publisher);
  const description = sanitizeText(
    textFromSchema(node.description) ||
      extractMeta(html, ["description", "og:description", "twitter:description"]),
    1_200,
  );
  const publishedAt = normalizeDate(
    textFromSchema(
      node.datePublished ?? node.uploadDate ?? node.dateCreated,
    ) ||
      extractMeta(html, [
        "article:published_time",
        "datepublished",
        "date",
        "pubdate",
        "publish-date",
      ]),
  );
  const rawImage =
    imageFromSchema(node.image ?? node.thumbnailUrl) ||
    extractMeta(html, [
      "og:image",
      "og:image:url",
      "twitter:image",
      "twitter:image:src",
    ]);
  let imageUrl = "";
  if (rawImage) {
    try {
      imageUrl = new URL(rawImage, canonicalUrl).toString();
    } catch {
      imageUrl = "";
    }
  }

  let externalId = "";
  if (type === "book") {
    externalId = textFromSchema(node.isbn);
  } else if (type === "movie") {
    externalId = canonicalUrl.match(/\/title\/(tt\d+)/i)?.[1] ?? "";
  }

  return {
    finalUrl,
    canonicalUrl,
    title,
    author,
    description,
    imageUrl,
    publishedAt,
    externalId: externalId || undefined,
  };
}

async function pageMetadata(url: string, type: RecommendationType): Promise<PageMetadata> {
  const { text, finalUrl } = await fetchText(url, [
    "text/html",
    "application/xhtml+xml",
  ]);
  return extractPageMetadata(text, finalUrl, type);
}

function deterministicDescription(candidate: {
  type: RecommendationType;
  title: string;
  authorOrMeta: string;
  description: string;
}): string {
  const existing = sanitizeText(candidate.description, 600);
  if (existing.length >= 30) return existing;
  switch (candidate.type) {
    case "book":
      return `Livro “${candidate.title}”, de ${candidate.authorOrMeta}.`;
    case "podcast":
      return `Episódio ou programa “${candidate.title}”, de ${candidate.authorOrMeta}.`;
    case "movie":
      return `Obra audiovisual “${candidate.title}”, realizada por ${candidate.authorOrMeta}.`;
    case "nostalgia":
      return `Episódio de humor “${candidate.title}”, publicado por ${candidate.authorOrMeta}.`;
    case "investigation":
      return `Episódio de investigação “${candidate.title}”, publicado por ${candidate.authorOrMeta}.`;
    case "highlight":
      return `Conteúdo jornalístico “${candidate.title}”, publicado por ${candidate.authorOrMeta}.`;
    case "project":
      return `Sugestão da comunidade para o Politómetro: ${candidate.title}.`;
  }
}

async function summarizeGrounded(candidate: Candidate): Promise<string> {
  const fallback = deterministicDescription(candidate);
  const apiKey = process.env.GROQ_API_KEY;
  if (!apiKey || candidate.type === "project") return fallback;

  const model = process.env.GROQ_MODEL || "llama-3.3-70b-versatile";
  try {
    const response = await fetch("https://api.groq.com/openai/v1/chat/completions", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      body: JSON.stringify({
        model,
        temperature: 0.1,
        response_format: { type: "json_object" },
        messages: [
          {
            role: "system",
            content:
              "Escreve em português de Portugal uma descrição rigorosa de 1 ou 2 frases. Usa exclusivamente os factos fornecidos. Não inventes nomes, datas, links, prémios, conclusões ou contexto. Devolve apenas JSON com a chave description.",
          },
          {
            role: "user",
            content: JSON.stringify({
              type: candidate.type,
              canonicalTitle: candidate.title,
              authorOrPublisher: candidate.authorOrMeta,
              canonicalUrl: candidate.link,
              sourceDescription: fallback,
              externalId: candidate.externalId ?? "",
            }),
          },
        ],
      }),
    });
    if (!response.ok) return fallback;
    const payload = (await response.json()) as {
      choices?: Array<{ message?: { content?: string } }>;
    };
    const content = payload.choices?.[0]?.message?.content;
    if (!content) return fallback;
    const parsed = JSON.parse(content) as { description?: unknown };
    const description = sanitizeText(parsed.description, 600);
    return description.length >= 25 ? description : fallback;
  } catch {
    return fallback;
  }
}

async function resolveYouTube(
  url: string,
  requestedTitle: string,
  type: RecommendationType,
  provider = "youtube-oembed",
): Promise<Candidate | null> {
  const normalized = normalizePublicUrl(url);
  if (!normalized || !isYouTubeUrl(normalized)) return null;
  try {
    const data = await fetchJson<{
      title?: string;
      author_name?: string;
      thumbnail_url?: string;
    }>(
      `https://www.youtube.com/oembed?url=${encodeURIComponent(normalized)}&format=json`,
    );
    const title = sanitizeText(data.title, 300);
    const author = sanitizeText(data.author_name, 200);
    const image = data.thumbnail_url
      ? await verifyImageUrl(data.thumbnail_url)
      : null;
    const score = titleSimilarity(requestedTitle, title);
    if (!title || !author || !image || score < 0.58) return null;
    const videoId =
      new URL(normalized).searchParams.get("v") ??
      (new URL(normalized).hostname.includes("youtu.be")
        ? new URL(normalized).pathname.split("/").filter(Boolean)[0]
        : undefined);
    const publishedAt = await pageMetadata(normalized, type)
      .then((metadata) => metadata.publishedAt)
      .catch(() => "");
    return {
      provider,
      type,
      title,
      link: normalized,
      authorOrMeta: author,
      description: `Vídeo “${title}”, publicado pelo canal ${author}.`,
      imageUrl: image,
      externalId: videoId ? `youtube:${videoId}` : normalized,
      titleScore: score,
      confidence: Math.min(0.98, 0.78 + score * 0.2),
      matchedFields: ["title", "link", "author", "image", "youtube-oembed"],
      sourcePublishedAt: publishedAt || undefined,
    };
  } catch {
    return null;
  }
}

async function resolveFromProvidedLink(
  input: ResolveRecommendationInput,
): Promise<Candidate | null> {
  const normalized = normalizePublicUrl(input.link);
  if (!normalized) return null;
  if (input.type === "highlight" && isWikipediaUrl(normalized)) {
    throw new RecommendationResolutionError(
      "A Wikipédia não é aceite como destino de um destaque. Indica uma investigação jornalística ou vídeo editorial.",
      "wikipedia_blocked",
    );
  }
  if (
    input.type !== "project" &&
    !isAllowedDomain(normalized, PROVIDED_LINK_DOMAINS[input.type])
  ) {
    return null;
  }

  if (isYouTubeUrl(normalized)) {
    const candidate = await resolveYouTube(
      normalized,
      input.title,
      input.type,
      "provided-youtube",
    );
    if (
      candidate?.type === "highlight" &&
      !isEligibleHighlightCandidate(candidate)
    ) {
      return null;
    }
    return candidate;
  }
  if (!isLikelyDetailUrl(input.type, normalized)) return null;

  try {
    const metadata = await pageMetadata(normalized, input.type);
    if (
      input.type !== "project" &&
      (!isAllowedDomain(
        metadata.finalUrl,
        PROVIDED_LINK_DOMAINS[input.type],
      ) ||
        !isAllowedDomain(
          metadata.canonicalUrl,
          PROVIDED_LINK_DOMAINS[input.type],
        ))
    ) {
      return null;
    }
    if (
      input.type === "highlight" &&
      (isWikipediaUrl(metadata.finalUrl) || isWikipediaUrl(metadata.canonicalUrl))
    ) {
      throw new RecommendationResolutionError(
        "A Wikipédia não é aceite como destino de um destaque.",
        "wikipedia_blocked",
      );
    }

    const score = titleSimilarity(input.title, metadata.title);
    const minimumScore =
      input.type === "book" || input.type === "movie" ? 0.82 : 0.58;
    if (!metadata.title || score < minimumScore) return null;
    const image = metadata.imageUrl
      ? await verifyImageUrl(metadata.imageUrl)
      : null;
    if (!image) return null;

    let author = sanitizeText(metadata.author, 250);
    if (!author && input.type === "highlight") {
      author = new URL(metadata.canonicalUrl).hostname.replace(/^www\./, "");
    }
    if (!author) return null;
    if (
      input.type === "highlight" &&
      !isEligibleHighlightCandidate({
        title: metadata.title,
        description: metadata.description,
        link: metadata.canonicalUrl,
      })
    ) {
      throw new RecommendationResolutionError(
        "Notícias correntes não são aceites como Destaque. Indica um artigo de opinião, análise, investigação, entrevista, reportagem especial ou documentário.",
        "news_not_allowed",
      );
    }

    const podcastExternalId =
      input.type === "podcast"
        ? applePodcastExternalId(
            metadata.canonicalUrl,
            metadata.finalUrl,
            normalized,
          )
        : "";

    return {
      provider: "provided-link",
      type: input.type,
      title: metadata.title,
      link: metadata.canonicalUrl,
      authorOrMeta: author,
      description: metadata.description,
      imageUrl: image,
      externalId:
        podcastExternalId || metadata.externalId || metadata.canonicalUrl,
      titleScore: score,
      confidence: Math.min(0.97, 0.75 + score * 0.2),
      matchedFields: ["title", "canonical-link", "author", "image"],
      sourcePublishedAt: metadata.publishedAt || undefined,
    };
  } catch (error) {
    if (
      error instanceof RecommendationResolutionError &&
      error.code === "wikipedia_blocked"
    ) {
      throw error;
    }
    return null;
  }
}

async function searchGoogleCse(query: string): Promise<CseItem[]> {
  const apiKey = process.env.GOOGLE_CSE_API_KEY;
  const cx = process.env.GOOGLE_CSE_ID || process.env.GOOGLE_CSE_CX;
  if (!apiKey || !cx) return [];

  const url = new URL("https://www.googleapis.com/customsearch/v1");
  url.searchParams.set("key", apiKey);
  url.searchParams.set("cx", cx);
  url.searchParams.set("q", query);
  url.searchParams.set("num", "8");
  url.searchParams.set("gl", "pt");
  url.searchParams.set("lr", "lang_pt");
  url.searchParams.set("safe", "active");

  try {
    const payload = await fetchJson<{ items?: CseItem[] }>(url.toString());
    return Array.isArray(payload.items) ? payload.items : [];
  } catch {
    return [];
  }
}

function cseMeta(item: CseItem): Record<string, string> {
  return item.pagemap?.metatags?.[0] ?? {};
}

function cseImage(item: CseItem): string {
  const meta = cseMeta(item);
  return (
    meta["og:image"] ||
    meta["twitter:image"] ||
    item.pagemap?.cse_image?.[0]?.src ||
    ""
  );
}

async function candidateFromCse(
  item: CseItem,
  type: RecommendationType,
  requestedTitle: string,
): Promise<Candidate | null> {
  const link = normalizePublicUrl(item.link);
  if (!link || !isLikelyDetailUrl(type, link)) return null;
  if (type === "highlight" && isWikipediaUrl(link)) return null;

  if (isYouTubeUrl(link)) {
    return resolveYouTube(link, requestedTitle, type, "google-cse+youtube");
  }

  try {
    const direct = await resolveFromProvidedLink({
      type,
      title: requestedTitle,
      link,
    });
    if (direct) {
      return {
        ...direct,
        provider: "google-cse+page",
        confidence: Math.min(0.98, direct.confidence + 0.01),
      };
    }
  } catch {
    return null;
  }

  const meta = cseMeta(item);
  const title = stripKnownSiteSuffix(
    meta["og:title"] || meta["twitter:title"] || item.title || "",
  );
  const score = titleSimilarity(requestedTitle, title);
  if (!title || score < 0.68) return null;
  const rawImage = cseImage(item);
  const image = rawImage ? await verifyImageUrl(rawImage) : null;
  if (!image) return null;

  let author =
    sanitizeText(
      meta.author ||
        meta["article:author"] ||
        meta["og:site_name"] ||
        item.displayLink,
      250,
    ) || new URL(link).hostname.replace(/^www\./, "");
  if (type === "book" && !meta.author && !meta["book:author"]) return null;
  if (type === "book" && meta["book:author"]) {
    author = sanitizeText(meta["book:author"], 250);
  }

  return {
    provider: "google-cse-metadata",
    type,
    title,
    link,
    authorOrMeta: author,
    description:
      sanitizeText(
        meta["og:description"] || meta.description || item.snippet,
        1_000,
      ) || `Conteúdo “${title}”, publicado por ${author}.`,
    imageUrl: image,
    externalId: link,
    titleScore: score,
    confidence: Math.min(0.9, 0.67 + score * 0.2),
    matchedFields: ["title", "link", "author", "image", "cse-metadata"],
    sourcePublishedAt:
      normalizeDate(
        meta["article:published_time"] ||
          meta.datepublished ||
          meta.date ||
          meta.pubdate,
      ) || undefined,
  };
}

async function resolveBookWithCse(title: string): Promise<Candidate | null> {
  const domainQuery = BOOK_DOMAINS.filter((domain) => domain !== "openlibrary.org")
    .map((domain) => `site:${domain}`)
    .join(" OR ");
  const results = await searchGoogleCse(`"${title}" livro (${domainQuery})`);

  for (const result of results.slice(0, 5)) {
    const link = normalizePublicUrl(result.link);
    if (!link || !isAllowedDomain(link, BOOK_DOMAINS)) continue;
    const candidate = await candidateFromCse(result, "book", title);
    if (candidate) return candidate;
  }
  return null;
}

interface OpenLibraryDocument {
  key?: string;
  title?: string;
  author_name?: string[];
  isbn?: string[];
  cover_i?: number;
  first_publish_year?: number;
}

async function resolveBookWithOpenLibrary(title: string): Promise<Candidate | null> {
  const url = new URL("https://openlibrary.org/search.json");
  url.searchParams.set("title", title);
  url.searchParams.set(
    "fields",
    "key,title,author_name,isbn,cover_i,first_publish_year",
  );
  url.searchParams.set("limit", "12");
  url.searchParams.set("language", "por");

  try {
    const payload = await fetchJson<{ docs?: OpenLibraryDocument[] }>(url.toString());
    const ranked = (payload.docs ?? [])
      .map((document) => ({
        document,
        score: titleSimilarity(title, document.title ?? ""),
      }))
      .filter(
        ({ document, score }) =>
          score >= 0.84 &&
          Boolean(document.key) &&
          Boolean(document.author_name?.length) &&
          Boolean(document.cover_i),
      )
      .sort((left, right) => right.score - left.score);

    for (const { document, score } of ranked.slice(0, 5)) {
      const image = await verifyImageUrl(
        `https://covers.openlibrary.org/b/id/${document.cover_i}-L.jpg?default=false`,
      );
      if (!image) continue;
      const authors = sanitizeText(document.author_name!.slice(0, 4).join(", "), 260);
      const canonicalTitle = sanitizeText(document.title, 300);
      const year = document.first_publish_year;
      return {
        provider: "openlibrary",
        type: "book",
        title: canonicalTitle,
        link: `https://openlibrary.org${document.key}`,
        authorOrMeta: authors,
        description: year
          ? `Livro “${canonicalTitle}”, de ${authors}, publicado originalmente em ${year}.`
          : `Livro “${canonicalTitle}”, de ${authors}.`,
        imageUrl: image,
        externalId:
          document.isbn?.[0] ?? document.key?.replace(/^\/works\//, "openlibrary:"),
        titleScore: score,
        confidence: Math.min(0.94, 0.7 + score * 0.22),
        matchedFields: ["title", "authors", "work-id", "cover"],
        sourcePublishedAt: year ? normalizeDate(String(year)) : undefined,
      };
    }
  } catch {
    return null;
  }
  return null;
}

interface ApplePodcastResult {
  wrapperType?: string;
  kind?: string;
  trackId?: number;
  collectionId?: number;
  trackName?: string;
  collectionName?: string;
  artistName?: string;
  trackViewUrl?: string;
  collectionViewUrl?: string;
  artworkUrl600?: string;
  artworkUrl100?: string;
  description?: string;
  shortDescription?: string;
  releaseDate?: string;
}

async function appleSearch(
  title: string,
  entity: "podcastEpisode" | "podcast",
): Promise<ApplePodcastResult[]> {
  const url = new URL("https://itunes.apple.com/search");
  url.searchParams.set("term", title);
  url.searchParams.set("media", "podcast");
  url.searchParams.set("entity", entity);
  url.searchParams.set("country", "PT");
  url.searchParams.set("limit", "20");
  const payload = await fetchJson<{ results?: ApplePodcastResult[] }>(url.toString());
  return payload.results ?? [];
}

async function applePodcastCadenceDays(
  collectionId: number | undefined,
): Promise<number | undefined> {
  if (!collectionId) return undefined;
  try {
    const url = new URL("https://itunes.apple.com/lookup");
    url.searchParams.set("id", String(collectionId));
    url.searchParams.set("media", "podcast");
    url.searchParams.set("entity", "podcastEpisode");
    url.searchParams.set("limit", "20");
    url.searchParams.set("country", "PT");
    const payload = await fetchJson<{ results?: ApplePodcastResult[] }>(
      url.toString(),
    );
    const timestamps = (payload.results ?? [])
      .map((item) => normalizeDate(item.releaseDate))
      .filter(Boolean)
      .map((date) => new Date(date).getTime())
      .filter(Number.isFinite)
      .sort((left, right) => right - left);
    const gaps = timestamps
      .slice(0, -1)
      .map((timestamp, index) =>
        Math.round((timestamp - timestamps[index + 1]) / 86_400_000),
      )
      .filter((days) => days >= 1 && days <= 60)
      .sort((left, right) => left - right);
    if (!gaps.length) return undefined;
    return gaps[Math.floor(gaps.length / 2)];
  } catch {
    return undefined;
  }
}

async function resolvePodcastWithApple(title: string): Promise<Candidate | null> {
  try {
    const [episodes, shows] = await Promise.all([
      appleSearch(title, "podcastEpisode"),
      appleSearch(title, "podcast"),
    ]);

    const ranked = [
      ...episodes.map((result) => ({
        result,
        entity: "episode" as const,
        score: titleSimilarity(title, result.trackName ?? ""),
      })),
      ...shows.map((result) => ({
        result,
        entity: "show" as const,
        score: titleSimilarity(
          title,
          result.collectionName ?? result.trackName ?? "",
        ),
      })),
    ]
      .filter(({ entity, score }) =>
        entity === "episode" ? score >= 0.68 : score >= 0.84,
      )
      .sort((left, right) => {
        const scoreDifference = right.score - left.score;
        if (Math.abs(scoreDifference) > 0.01) return scoreDifference;
        if (left.entity === right.entity) return 0;
        return left.entity === "episode" && right.entity !== "episode" ? -1 : 1;
      });

    for (const { result, entity, score } of ranked.slice(0, 8)) {
      const canonicalTitle = sanitizeText(
        entity === "episode"
          ? result.trackName
          : result.collectionName ?? result.trackName,
        320,
      );
      const collection = sanitizeText(result.collectionName, 220);
      const artist = sanitizeText(result.artistName, 180);
      const author =
        collection && artist && normalizeForMatch(collection) !== normalizeForMatch(artist)
          ? `${collection} / ${artist}`
          : collection || artist;
      const link = normalizePublicUrl(
        entity === "episode" ? result.trackViewUrl : result.collectionViewUrl,
      );
      const rawImage = result.artworkUrl600 || result.artworkUrl100 || "";
      const image = rawImage
        ? await verifyImageUrl(rawImage.replace("100x100bb", "600x600bb"))
        : null;
      if (!canonicalTitle || !author || !link || !image) continue;
      const sourceDescription = sanitizeText(
        result.description || result.shortDescription,
        1_000,
      );
      const releaseYear = result.releaseDate
        ? new Date(result.releaseDate).getUTCFullYear()
        : undefined;
      const cadenceDays = await applePodcastCadenceDays(result.collectionId);
      return {
        provider: entity === "episode" ? "apple-podcast-episode" : "apple-podcast",
        type: "podcast",
        title: canonicalTitle,
        link,
        authorOrMeta: author,
        description:
          sourceDescription ||
          `${entity === "episode" ? "Episódio" : "Podcast"} “${canonicalTitle}”, de ${author}${releaseYear ? ` (${releaseYear})` : ""}.`,
        imageUrl: image,
        externalId:
          entity === "episode"
            ? `apple:episode:${result.trackId}`
            : `apple:podcast:${result.collectionId}`,
        titleScore: score,
        confidence: Math.min(
          0.97,
          (entity === "episode" ? 0.73 : 0.68) + score * 0.22,
        ),
        matchedFields: [
          "title",
          "apple-id",
          "publisher",
          "canonical-link",
          "artwork",
        ],
        sourcePublishedAt: normalizeDate(result.releaseDate) || undefined,
        cadenceDays,
      };
    }
  } catch {
    return null;
  }
  return null;
}

interface WikidataSearchResult {
  id?: string;
  label?: string;
  description?: string;
  aliases?: string[];
}

interface WikidataEntity {
  id?: string;
  labels?: Record<string, { value?: string }>;
  descriptions?: Record<string, { value?: string }>;
  aliases?: Record<string, Array<{ value?: string }>>;
  claims?: Record<
    string,
    Array<{
      mainsnak?: {
        datavalue?: {
          value?: unknown;
        };
      };
    }>
  >;
}

function claimValues(entity: WikidataEntity, property: string): unknown[] {
  return (entity.claims?.[property] ?? [])
    .map((claim) => claim.mainsnak?.datavalue?.value)
    .filter((value) => value !== undefined);
}

function claimString(entity: WikidataEntity, property: string): string {
  const value = claimValues(entity, property)[0];
  return typeof value === "string" ? value : "";
}

function claimEntityIds(entity: WikidataEntity, property: string): string[] {
  return claimValues(entity, property)
    .map((value) => {
      if (!value || typeof value !== "object") return "";
      const numericId = (value as { "numeric-id"?: unknown })["numeric-id"];
      return typeof numericId === "number" ? `Q${numericId}` : "";
    })
    .filter(Boolean);
}

function claimYear(entity: WikidataEntity, property: string): number | undefined {
  const value = claimValues(entity, property)[0];
  if (!value || typeof value !== "object") return undefined;
  const time = (value as { time?: unknown }).time;
  if (typeof time !== "string") return undefined;
  const match = time.match(/[+-](\d{4})-/);
  return match ? Number.parseInt(match[1], 10) : undefined;
}

async function wikidataEntities(ids: string[]): Promise<Record<string, WikidataEntity>> {
  if (!ids.length) return {};
  const url = new URL("https://www.wikidata.org/w/api.php");
  url.searchParams.set("action", "wbgetentities");
  url.searchParams.set("ids", ids.join("|"));
  url.searchParams.set("props", "labels|descriptions|aliases|claims");
  url.searchParams.set("languages", "pt|en");
  url.searchParams.set("format", "json");
  url.searchParams.set("origin", "*");
  const payload = await fetchJson<{ entities?: Record<string, WikidataEntity> }>(
    url.toString(),
  );
  return payload.entities ?? {};
}

async function wikidataLabels(ids: string[]): Promise<Record<string, string>> {
  if (!ids.length) return {};
  const entities = await wikidataEntities(ids);
  return Object.fromEntries(
    Object.entries(entities).map(([id, entity]) => [
      id,
      sanitizeText(
        entity.labels?.pt?.value ?? entity.labels?.en?.value ?? id,
        180,
      ),
    ]),
  );
}

async function imdbImage(imdbUrl: string): Promise<string | null> {
  try {
    const metadata = await pageMetadata(imdbUrl, "movie");
    return metadata.imageUrl ? verifyImageUrl(metadata.imageUrl) : null;
  } catch {
    return null;
  }
}

async function resolveMovieWithWikidata(title: string): Promise<Candidate | null> {
  const searchUrl = new URL("https://www.wikidata.org/w/api.php");
  searchUrl.searchParams.set("action", "wbsearchentities");
  searchUrl.searchParams.set("search", title);
  searchUrl.searchParams.set("language", "pt");
  searchUrl.searchParams.set("uselang", "pt");
  searchUrl.searchParams.set("type", "item");
  searchUrl.searchParams.set("limit", "10");
  searchUrl.searchParams.set("format", "json");
  searchUrl.searchParams.set("origin", "*");

  try {
    const search = await fetchJson<{ search?: WikidataSearchResult[] }>(
      searchUrl.toString(),
    );
    const ids = (search.search ?? [])
      .map((result) => result.id)
      .filter((id): id is string => Boolean(id))
      .slice(0, 8);
    const entities = await wikidataEntities(ids);

    const ranked = ids
      .map((id) => {
        const entity = entities[id];
        if (!entity) return null;
        const canonicalTitle = sanitizeText(
          entity.labels?.pt?.value ?? entity.labels?.en?.value,
          300,
        );
        const aliases = [
          ...(entity.aliases?.pt ?? []),
          ...(entity.aliases?.en ?? []),
        ]
          .map((alias) => sanitizeText(alias.value, 300))
          .filter(Boolean);
        const score = Math.max(
          titleSimilarity(title, canonicalTitle),
          ...aliases.map((alias) => titleSimilarity(title, alias)),
        );
        const imdbId = claimString(entity, "P345");
        const description = sanitizeText(
          entity.descriptions?.pt?.value ?? entity.descriptions?.en?.value,
          500,
        );
        const looksAudiovisual =
          /filme|film|s[ée]rie|series|document[áa]rio|documentary|televis/i.test(
            description,
          );
        if (score < 0.5 || !/^tt\d+$/.test(imdbId) || !looksAudiovisual) {
          return null;
        }
        return { id, entity, canonicalTitle, score, imdbId, description };
      })
      .filter(
        (
          value,
        ): value is {
          id: string;
          entity: WikidataEntity;
          canonicalTitle: string;
          score: number;
          imdbId: string;
          description: string;
        } => Boolean(value),
      )
      .sort((left, right) => right.score - left.score);

    for (const match of ranked.slice(0, 4)) {
      const directorIds = claimEntityIds(match.entity, "P57");
      const directors = await wikidataLabels(directorIds);
      const author = directorIds
        .map((id) => directors[id])
        .filter(Boolean)
        .slice(0, 4)
        .join(", ");
      if (!author) continue;

      const imdbUrl = `https://www.imdb.com/title/${match.imdbId}/`;
      let image = await imdbImage(imdbUrl);
      if (!image) {
        const commonsFile = claimString(match.entity, "P18");
        if (commonsFile) {
          image = await verifyImageUrl(
            `https://commons.wikimedia.org/wiki/Special:Redirect/file/${encodeURIComponent(commonsFile)}?width=800`,
          );
        }
      }
      if (!image) continue;

      const year = claimYear(match.entity, "P577");
      return {
        provider: "wikidata+imdb",
        type: "movie",
        title: match.canonicalTitle,
        link: imdbUrl,
        authorOrMeta: author,
        description:
          match.description ||
          `Filme “${match.canonicalTitle}”, realizado por ${author}${year ? ` e lançado em ${year}` : ""}.`,
        imageUrl: image,
        externalId: match.imdbId,
        titleScore: match.score,
        confidence: Math.min(0.98, 0.78 + match.score * 0.18),
        matchedFields: [
          "wikidata-entity",
          "title",
          "director",
          "imdb-id",
          "image",
        ],
        sourcePublishedAt: year ? normalizeDate(String(year)) : undefined,
      };
    }
  } catch {
    return null;
  }
  return null;
}

async function resolveHighlightWithCse(title: string): Promise<Candidate | null> {
  const domainQuery = HIGHLIGHT_DOMAINS.map((domain) => `site:${domain}`).join(
    " OR ",
  );
  const results = await searchGoogleCse(`"${title}" (${domainQuery})`);

  for (const result of results.slice(0, 8)) {
    const link = normalizePublicUrl(result.link);
    if (
      !link ||
      isWikipediaUrl(link) ||
      !isAllowedDomain(link, HIGHLIGHT_DOMAINS)
    ) {
      continue;
    }
    const candidate = await candidateFromCse(result, "highlight", title);
    if (candidate && isEligibleHighlightCandidate(candidate)) {
      return candidate;
    }
  }
  return null;
}

function unresolved(
  input: ResolveRecommendationInput,
  warning: string,
  provider = "none",
  temporal: {
    sourcePublishedAt: string | null;
    expiryDate: string | null;
  } = { sourcePublishedAt: null, expiryDate: null },
): ResolvedRecommendation {
  return {
    type: input.type,
    category: categoryForType(input.type),
    title: sanitizeText(input.title, MAX_TITLE_LENGTH),
    link: "",
    authorOrMeta: "",
    description: "",
    imageUrl: "",
    sourcePublishedAt: temporal.sourcePublishedAt,
    expiryDate: temporal.expiryDate,
    resolutionStatus: "unresolved",
    verification: {
      status: "unresolved",
      provider,
      confidence: 0,
      checkedAt: new Date().toISOString(),
      matchedFields: [],
      warnings: [sanitizeText(warning, 500)],
    },
  };
}

function validateInput(input: ResolveRecommendationInput): ResolveRecommendationInput {
  if (!isRecommendationType(input.type)) {
    throw new RecommendationResolutionError(
      "Tipo de recomendação inválido.",
      "invalid_type",
    );
  }
  const title = sanitizeText(input.title, MAX_TITLE_LENGTH);
  if (title.length < 3) {
    throw new RecommendationResolutionError(
      "O título deve ter pelo menos 3 caracteres.",
      "invalid_title",
    );
  }

  const rawLink = typeof input.link === "string" ? input.link.trim() : "";
  if (rawLink && !normalizePublicUrl(rawLink)) {
    throw new RecommendationResolutionError(
      "O link deve ser um URL HTTP(S) público e válido.",
      "invalid_url",
    );
  }
  return { type: input.type, title, link: rawLink };
}

export async function resolveRecommendation(
  rawInput: ResolveRecommendationInput,
  options: ResolveRecommendationOptions = {},
): Promise<ResolvedRecommendation> {
  const input = validateInput(rawInput);

  if (input.type === "project") {
    const candidate: Candidate = {
      provider: "user-project",
      type: "project",
      title: input.title,
      link: "",
      authorOrMeta: "Comunidade Politómetro",
      description: `Sugestão da comunidade para o Politómetro: ${input.title}.`,
      imageUrl: "",
      externalId: undefined,
      titleScore: 1,
      confidence: 1,
      matchedFields: ["user-input"],
    };
    return {
      type: candidate.type,
      category: categoryForType(candidate.type),
      title: candidate.title,
      link: "",
      authorOrMeta: candidate.authorOrMeta,
      description: candidate.description,
      imageUrl: "",
      sourcePublishedAt: null,
      expiryDate: null,
      resolutionStatus: "verified",
      verification: {
        status: "verified",
        provider: candidate.provider,
        confidence: candidate.confidence,
        checkedAt: new Date().toISOString(),
        sourceTitle: candidate.title,
        entityId: candidate.externalId,
        titleScore: 1,
        matchedFields: candidate.matchedFields,
        warnings: [],
      },
    };
  }

  if (input.type === "highlight" && input.link && isWikipediaUrl(input.link)) {
    return unresolved(
      input,
      "A Wikipédia não é aceite para destaques. Usa uma investigação jornalística ou um vídeo editorial.",
      "blocked-source",
    );
  }

  let candidate: Candidate | null = null;
  if (input.link) {
    candidate = await resolveFromProvidedLink(input);
    if (!candidate) {
      return unresolved(
        input,
        "Não foi possível validar a recomendação com o link indicado. Confirma se o endereço corresponde exatamente ao título e ao tipo selecionado ou remove o link para tentarmos localizar a fonte adequada.",
        "provided-link",
      );
    }
  }

  if (!candidate) {
    switch (input.type) {
      case "book": {
        const [cse, openLibrary] = await Promise.all([
          resolveBookWithCse(input.title),
          resolveBookWithOpenLibrary(input.title),
        ]);
        candidate =
          [cse, openLibrary]
            .filter((value): value is Candidate => Boolean(value))
            .sort((left, right) => right.confidence - left.confidence)[0] ?? null;
        break;
      }
      case "podcast":
        candidate = await resolvePodcastWithApple(input.title);
        break;
      case "movie":
        candidate = await resolveMovieWithWikidata(input.title);
        break;
      case "highlight":
        candidate = await resolveHighlightWithCse(input.title);
        break;
    }
  }

  if (!candidate) {
    const providerHint: Record<Exclude<RecommendationType, "project">, string> = {
      book: "Open Library/Google CSE",
      podcast: "Apple Podcasts",
      movie: "Wikidata/IMDb",
      nostalgia: "RTP/YouTube/fonte fornecida",
      investigation: "RTP/SIC/NOW/YouTube/fonte fornecida",
      highlight: "Google CSE/fonte fornecida",
    };
    return unresolved(
      input,
      "Não foi possível identificar esta recomendação com segurança apenas pelo título. Revê o título ou adiciona o link direto para a página oficial do conteúdo.",
      providerHint[input.type],
    );
  }

  const canonicalLink = normalizePublicUrl(candidate.link);
  const canonicalImage = normalizePublicUrl(candidate.imageUrl);
  if (
    !canonicalLink ||
    !canonicalImage ||
    !candidate.title ||
    !candidate.authorOrMeta ||
    candidate.titleScore < 0.45 ||
    (candidate.type === "highlight" && isWikipediaUrl(canonicalLink))
  ) {
    return unresolved(
      input,
      "A fonte encontrada não cumpriu todos os critérios de identidade, link e imagem.",
      candidate.provider,
    );
  }
  if (
    candidate.type === "highlight" &&
    !isEligibleHighlightCandidate({
      title: candidate.title,
      description: candidate.description,
      link: canonicalLink,
    })
  ) {
    return unresolved(
      input,
      "Notícias correntes não são aceites como Destaque. Escolhe um artigo de opinião, análise, investigação, entrevista, reportagem especial ou documentário.",
      candidate.provider,
    );
  }

  const temporal = temporalFields(candidate);
  if (
    (candidate.type === "podcast" || candidate.type === "highlight") &&
    (!temporal.sourcePublishedAt || !temporal.expiryDate)
  ) {
    return unresolved(
      input,
      "O conteúdo foi encontrado, mas a fonte não confirma a data de publicação; não é seguro tratá-lo como atual.",
      candidate.provider,
      temporal,
    );
  }
  if (
    temporal.expiryDate &&
    new Date(temporal.expiryDate).getTime() <=
      Date.now() + MIN_TEMPORAL_REVIEW_MS
  ) {
    return unresolved(
      input,
      "A fonte foi identificada, mas o prazo de relevância deste conteúdo já terminou.",
      candidate.provider,
      temporal,
    );
  }
  const description =
    options.summarize === false
      ? deterministicDescription(candidate)
      : await summarizeGrounded(candidate);

  return {
    type: candidate.type,
    category: categoryForType(candidate.type),
    title: sanitizeText(candidate.title, MAX_TITLE_LENGTH),
    link: canonicalLink,
    authorOrMeta: sanitizeText(candidate.authorOrMeta, 260),
    description,
    imageUrl: canonicalImage,
    sourcePublishedAt: temporal.sourcePublishedAt,
    expiryDate: temporal.expiryDate,
    resolutionStatus: "verified",
    verification: {
      status: "verified",
      provider: candidate.provider,
      confidence: Number(candidate.confidence.toFixed(3)),
      checkedAt: new Date().toISOString(),
      canonicalUrl: canonicalLink,
      externalId: candidate.externalId,
      entityId: candidate.externalId,
      sourceTitle: sanitizeText(candidate.title, MAX_TITLE_LENGTH),
      titleScore: Number(candidate.titleScore.toFixed(3)),
      matchedFields: candidate.matchedFields,
      warnings: candidate.warnings ?? [],
    },
  };
}

export function validateResolvedRecommendation(
  value: unknown,
): value is ResolvedRecommendation {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<ResolvedRecommendation>;
  if (
    !isRecommendationType(item.type) ||
    typeof item.category !== "string" ||
    typeof item.title !== "string" ||
    typeof item.link !== "string" ||
    typeof item.authorOrMeta !== "string" ||
    typeof item.description !== "string" ||
    typeof item.imageUrl !== "string" ||
    !(
      item.sourcePublishedAt === null ||
      typeof item.sourcePublishedAt === "string"
    ) ||
    !(item.expiryDate === null || typeof item.expiryDate === "string") ||
    item.resolutionStatus !== "verified" ||
    !item.verification ||
    typeof item.verification !== "object" ||
    item.verification.status !== "verified"
  ) {
    return false;
  }
  if (
    item.sourcePublishedAt &&
    !Number.isFinite(new Date(item.sourcePublishedAt).getTime())
  ) {
    return false;
  }
  if (
    item.expiryDate &&
    (!Number.isFinite(new Date(item.expiryDate).getTime()) ||
      new Date(item.expiryDate).getTime() <=
        Date.now() + MIN_TEMPORAL_REVIEW_MS)
  ) {
    return false;
  }
  if (item.type === "project") {
    return item.title.trim().length >= 3 && item.expiryDate === null;
  }
  return Boolean(
    normalizePublicUrl(item.link) &&
      normalizePublicUrl(item.imageUrl) &&
      item.authorOrMeta.trim() &&
      item.description.trim() &&
      (item.type === "podcast" || item.type === "highlight"
        ? Boolean(item.sourcePublishedAt && item.expiryDate)
        : item.expiryDate === null) &&
      !(item.type === "highlight" && isWikipediaUrl(item.link)),
  );
}

export function resolutionErrorMessage(error: unknown): string {
  if (error instanceof RecommendationResolutionError) return error.message;
  return `Não foi possível validar a recomendação: ${errorMessage(error)}`;
}
