import { XMLParser } from "fast-xml-parser";
import { unstable_cache } from "next/cache";

export type NewsSourceId =
  | "cnn"
  | "rtp"
  | "expresso"
  | "observador"
  | "publico";

export interface NewsSource {
  id: NewsSourceId;
  name: string;
  recentUrl: string;
  logoUrl: string;
}

export interface NewsItem {
  id: string;
  sourceId: NewsSourceId;
  source: string;
  title: string;
  description: string;
  link: string;
  publishedAt: string;
}

export interface NewsSourceStatus extends NewsSource {
  available: boolean;
  itemCount: number;
  usingBackup: boolean;
  lastSuccessfulAt?: string;
  note?: string;
}

export interface NewsPayload {
  updatedAt: string;
  refreshSeconds: number;
  items: NewsItem[];
  sources: NewsSourceStatus[];
}

type FeedSource = NewsSource & {
  feedUrl: string;
  domains: string[];
};

type FeedResult = {
  items: NewsItem[];
  error?: string;
};

type FeedSnapshot = {
  items: NewsItem[];
  fetchedAt: string;
};

const FEED_SOURCES: FeedSource[] = [
  {
    id: "cnn",
    name: "CNN Portugal",
    feedUrl:
      process.env.CNN_NEWS_FEED_URL?.trim() ||
      "https://cnnportugal.iol.pt/rss.xml",
    recentUrl: "https://cnnportugal.iol.pt/ultimas",
    logoUrl: "https://cnnportugal.iol.pt/favicon.ico",
    domains: ["cnnportugal.iol.pt"],
  },
  {
    id: "rtp",
    name: "RTP Notícias",
    feedUrl: "https://www.rtp.pt/noticias/rss",
    recentUrl: "https://www.rtp.pt/noticias/",
    logoUrl: "https://www.rtp.pt/favicon.ico",
    domains: ["rtp.pt", "www.rtp.pt"],
  },
  {
    id: "expresso",
    name: "Expresso",
    feedUrl: "https://feeds.feedburner.com/expresso-geral",
    recentUrl: "https://expresso.pt/ultimas",
    logoUrl: "https://expresso.pt/favicon.ico",
    domains: ["expresso.pt", "www.expresso.pt"],
  },
  {
    id: "observador",
    name: "Observador",
    feedUrl: "https://observador.pt/rss/ultimas/",
    recentUrl: "https://observador.pt/ultimas/",
    logoUrl: "https://observador.pt/favicon.ico",
    domains: ["observador.pt", "www.observador.pt"],
  },
  {
    id: "publico",
    name: "Público",
    feedUrl: "https://feeds.feedburner.com/PublicoRSS",
    recentUrl: "https://www.publico.pt/ultimas",
    logoUrl: "https://static.publico.pt/favicon.ico",
    domains: ["publico.pt", "www.publico.pt"],
  },
];

const parser = new XMLParser({
  ignoreAttributes: false,
  processEntities: true,
  trimValues: true,
});

function values<T>(value: T | T[] | undefined): T[] {
  if (value === undefined) return [];
  return Array.isArray(value) ? value : [value];
}

function decodeHtmlEntities(value: string): string {
  let decoded = value;
  for (let pass = 0; pass < 3; pass += 1) {
    const previous = decoded;
    decoded = decoded
      .replace(/&#x([0-9a-f]+);/gi, (_, hex: string) =>
        String.fromCodePoint(Number.parseInt(hex, 16)),
      )
      .replace(/&#(\d+);/g, (_, number: string) =>
        String.fromCodePoint(Number(number)),
      )
      .replace(/&nbsp;/gi, " ")
      .replace(/&amp;/gi, "&")
      .replace(/&quot;/gi, '"')
      .replace(/&apos;|&#39;/gi, "'")
      .replace(/&lt;/gi, "<")
      .replace(/&gt;/gi, ">");
    if (decoded === previous) break;
  }
  return decoded;
}

function plainText(value: unknown, maxLength: number): string {
  const raw =
    typeof value === "string"
      ? value
      : value && typeof value === "object" && "#text" in value
        ? String((value as { "#text": unknown })["#text"])
        : "";
  return decodeHtmlEntities(raw)
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, maxLength);
}

function safeLink(value: unknown, domains: string[]): string {
  const candidate =
    typeof value === "string"
      ? value
      : value && typeof value === "object"
        ? String(
            (value as Record<string, unknown>)["@_href"] ??
              (value as Record<string, unknown>)["#text"] ??
              "",
          )
        : "";
  try {
    const url = new URL(candidate.trim());
    if (url.protocol !== "https:" && url.protocol !== "http:") return "";
    const hostname = url.hostname.toLowerCase();
    if (!domains.some((domain) => hostname === domain || hostname.endsWith(`.${domain}`))) {
      return "";
    }
    url.hash = "";
    return url.toString();
  } catch {
    return "";
  }
}

function lisbonOffsetMilliseconds(date: Date): number {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/Lisbon",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  })
    .formatToParts(date)
    .reduce<Record<string, number>>((result, part) => {
      if (part.type !== "literal") result[part.type] = Number(part.value);
      return result;
    }, {});
  const representedAsUtc = Date.UTC(
    parts.year,
    parts.month - 1,
    parts.day,
    parts.hour,
    parts.minute,
    parts.second,
  );
  return representedAsUtc - date.getTime();
}

function isoDate(value: unknown, sourceId: NewsSourceId): string {
  const parsed = new Date(String(value ?? ""));
  if (Number.isNaN(parsed.getTime())) return "";
  // O RSS da RTP soma a hora de Lisboa ao relógio e volta a declarar o
  // respetivo offset. Retirar o offset corrige esse desvio sem afetar o
  // inverno, quando Lisboa está em UTC.
  if (sourceId === "rtp") {
    parsed.setTime(parsed.getTime() - lisbonOffsetMilliseconds(parsed));
  }
  const now = Date.now();
  const difference = parsed.getTime() - now;
  if (difference > 120_000) return "";
  return difference > 0 ? new Date(now).toISOString() : parsed.toISOString();
}

function itemId(sourceId: string, link: string): string {
  let hash = 2166136261;
  const value = `${sourceId}:${link}`;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `${sourceId}-${(hash >>> 0).toString(16)}`;
}

async function fetchFeed(
  source: FeedSource,
): Promise<FeedResult> {
  try {
    const response = await fetch(source.feedUrl, {
      headers: {
        Accept: "application/rss+xml, application/xml, text/xml;q=0.9",
        "User-Agent": "PolitometroNews/1.0 (+https://politometro.politiza-te.pt)",
      },
      cache: "no-store",
      signal: AbortSignal.timeout(12_000),
    });
    if (!response.ok) {
      return { items: [], error: `A fonte respondeu com HTTP ${response.status}.` };
    }
    const parsed = parser.parse(await response.text());
    const rawItems = values<Record<string, unknown>>(
      parsed?.rss?.channel?.item ?? parsed?.feed?.entry,
    );
    const items = rawItems
      .map((item): NewsItem | null => {
        const link = safeLink(item.link ?? item.guid, source.domains);
        const title = plainText(item.title, 240);
        const description = plainText(
          item.description ?? item.summary ?? item.content,
          420,
        );
        const publishedAt = isoDate(
          item.pubDate ?? item.published ?? item.updated ?? item["dc:date"],
          source.id,
        );
        if (!link || !title || !publishedAt) return null;
        return {
          id: itemId(source.id, link),
          sourceId: source.id,
          source: source.name,
          title,
          description: description || "Sem descrição disponibilizada pela fonte.",
          link,
          publishedAt,
        };
      })
      .filter((item): item is NewsItem => item !== null)
      .sort(
        (left, right) =>
          Date.parse(right.publishedAt) - Date.parse(left.publishedAt),
      )
      .slice(0, 20);
    return items.length
      ? { items }
      : { items: [], error: "O feed não devolveu notícias válidas." };
  } catch {
    return { items: [], error: "Não foi possível consultar esta fonte agora." };
  }
}

type CachedFeedLoader = () => Promise<FeedSnapshot>;

function cachedFeedLoader(source: FeedSource): CachedFeedLoader {
  return unstable_cache(
    async () => {
      const result = await fetchFeed(source);
      if (!result.items.length) {
        throw new Error(result.error ?? "feed_unavailable");
      }
      return {
        items: result.items,
        fetchedAt: new Date().toISOString(),
      };
    },
    ["politometro-news-backup-v1", source.id],
    { revalidate: 120 },
  );
}

const CACHED_FEEDS = new Map<NewsSourceId, CachedFeedLoader>(
  FEED_SOURCES.map((source) => [source.id, cachedFeedLoader(source)]),
);

export async function getLatestNews(): Promise<NewsPayload> {
  const sourceResults = await Promise.all(
    FEED_SOURCES.map(async (source) => {
      const backupLoader = CACHED_FEEDS.get(source.id);
      const [liveResult, backupResult] = await Promise.all([
        fetchFeed(source),
        backupLoader
          ? backupLoader().catch(() => null)
          : Promise.resolve(null),
      ]);
      const liveAvailable = liveResult.items.length > 0;
      const backup =
        !liveAvailable && backupResult?.items.length
          ? backupResult
          : null;
      const items = liveAvailable
        ? liveResult.items
        : (backup?.items ?? []);
      const status: NewsSourceStatus = {
        id: source.id,
        name: source.name,
        recentUrl: source.recentUrl,
        logoUrl: source.logoUrl,
        available: liveAvailable,
        usingBackup: Boolean(backup),
        itemCount: items.length,
        lastSuccessfulAt: liveAvailable
          ? new Date().toISOString()
          : backup?.fetchedAt,
        note: liveAvailable
          ? undefined
          : "Atualização temporariamente em pausa.",
      };
      return { items, status };
    }),
  );
  const statuses = sourceResults.map((result) => result.status);

  const seen = new Set<string>();
  const items = sourceResults
    .flatMap((result) => result.items)
    .filter((item) => {
      if (seen.has(item.link)) return false;
      seen.add(item.link);
      return true;
    })
    .sort(
      (left, right) =>
        Date.parse(right.publishedAt) - Date.parse(left.publishedAt),
    )
    .slice(0, 100);

  return {
    updatedAt: new Date().toISOString(),
    refreshSeconds: 120,
    items,
    sources: statuses,
  };
}
