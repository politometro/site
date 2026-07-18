"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Header from "@/components/Header";
import styles from "./page.module.css";
import type {
  NewsItem,
  NewsPayload,
  NewsSourceId,
  NewsSourceStatus,
} from "@/lib/news";

const ALL_SOURCES = "all";
const NEWS_BACKUP_STORAGE_KEY = "politometro-news-backup-v1";

interface StoredSourceBackup {
  savedAt: string;
  items: NewsItem[];
}

interface StoredNewsBackup {
  version: 1;
  sources: Partial<Record<NewsSourceId, StoredSourceBackup>>;
}

function readBrowserBackup(): StoredNewsBackup {
  try {
    const parsed = JSON.parse(
      window.localStorage.getItem(NEWS_BACKUP_STORAGE_KEY) ?? "",
    ) as StoredNewsBackup;
    if (
      parsed?.version === 1 &&
      parsed.sources &&
      typeof parsed.sources === "object"
    ) {
      return parsed;
    }
  } catch {
    // A cópia local é apenas uma salvaguarda e pode ser recriada.
  }
  return { version: 1, sources: {} };
}

function mergeBrowserBackup(nextPayload: NewsPayload): NewsPayload {
  const stored = readBrowserBackup();
  const itemsBySource = new Map<NewsSourceId, NewsItem[]>();
  for (const source of nextPayload.sources) {
    itemsBySource.set(
      source.id,
      nextPayload.items.filter((item) => item.sourceId === source.id),
    );
  }

  const sources = nextPayload.sources.map((source) => {
    const apiItems = itemsBySource.get(source.id) ?? [];
    if (source.available && !source.usingBackup && apiItems.length) {
      stored.sources[source.id] = {
        savedAt: source.lastSuccessfulAt ?? nextPayload.updatedAt,
        items: apiItems.slice(0, 20),
      };
      return source;
    }

    let local = stored.sources[source.id];
    const apiBackupTime = Date.parse(source.lastSuccessfulAt ?? "");
    let localBackupTime = Date.parse(local?.savedAt ?? "");
    if (
      apiItems.length &&
      Number.isFinite(apiBackupTime) &&
      (!local ||
        !Number.isFinite(localBackupTime) ||
        apiBackupTime > localBackupTime)
    ) {
      local = {
        savedAt: source.lastSuccessfulAt!,
        items: apiItems.slice(0, 20),
      };
      stored.sources[source.id] = local;
      localBackupTime = apiBackupTime;
    }
    if (
      local?.items.length &&
      (!apiItems.length ||
        (Number.isFinite(localBackupTime) &&
          (!Number.isFinite(apiBackupTime) ||
            localBackupTime > apiBackupTime)))
    ) {
      itemsBySource.set(source.id, local.items);
      return {
        ...source,
        available: false,
        usingBackup: true,
        itemCount: local.items.length,
        lastSuccessfulAt: local.savedAt,
        note: "Atualização temporariamente em pausa.",
      };
    }
    return source;
  });

  try {
    window.localStorage.setItem(
      NEWS_BACKUP_STORAGE_KEY,
      JSON.stringify(stored),
    );
  } catch {
    // O servidor continua a disponibilizar a sua própria cópia de segurança.
  }

  const seen = new Set<string>();
  const items = Array.from(itemsBySource.values())
    .flat()
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

  return { ...nextPayload, items, sources };
}

function relativeTime(value: string): string {
  const timestamp = Date.parse(value);
  const seconds = Math.round((timestamp - Date.now()) / 1000);
  if (seconds > 60) {
    return new Intl.DateTimeFormat("pt-PT", {
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "Europe/Lisbon",
    }).format(timestamp);
  }
  if (seconds >= -60) return "Agora";
  const formatter = new Intl.RelativeTimeFormat("pt-PT", { numeric: "auto" });
  const ranges: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
  ];
  for (const [unit, size] of ranges) {
    if (Math.abs(seconds) >= size || unit === "minute") {
      return formatter.format(Math.round(seconds / size), unit);
    }
  }
  return formatter.format(seconds, "second");
}

function SourceLogo({ source }: { source: NewsSourceStatus }) {
  const [failed, setFailed] = useState(false);
  return failed ? (
    <span className={styles.logoFallback} aria-hidden="true">
      {source.name
        .split(/\s+/)
        .map((word) => word[0])
        .join("")
        .slice(0, 3)}
    </span>
  ) : (
    // Os ícones vêm diretamente dos domínios editoriais e têm fallback local.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      className={styles.sourceLogo}
      src={source.logoUrl}
      alt={`Logótipo ${source.name}`}
      onError={() => setFailed(true)}
    />
  );
}

export default function NewsPage() {
  const [payload, setPayload] = useState<NewsPayload | null>(null);
  const [selected, setSelected] = useState<string>(ALL_SOURCES);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadNews = useCallback(async () => {
    try {
      const response = await fetch("/api/news");
      if (!response.ok) throw new Error("news_request_failed");
      setPayload(
        mergeBrowserBackup((await response.json()) as NewsPayload),
      );
      setError("");
    } catch {
      setError("Não foi possível atualizar as notícias neste momento.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const initialLoad = window.setTimeout(() => void loadNews(), 0);
    const interval = window.setInterval(() => void loadNews(), 120_000);
    return () => {
      window.clearTimeout(initialLoad);
      window.clearInterval(interval);
    };
  }, [loadNews]);

  const items = useMemo(
    () =>
      (payload?.items ?? []).filter(
        (item) => selected === ALL_SOURCES || item.sourceId === selected,
      ),
    [payload, selected],
  );

  const sourceMap = useMemo(
    () =>
      new Map<NewsSourceId, NewsSourceStatus>(
        (payload?.sources ?? []).map((source) => [source.id, source]),
      ),
    [payload],
  );
  const selectedSource =
    selected === ALL_SOURCES
      ? undefined
      : sourceMap.get(selected as NewsSourceId);
  const sourceUpdatePaused = Boolean(
    selectedSource && !selectedSource.available,
  );

  return (
    <div className={styles.container}>
      <Header />
      <main className={styles.main}>
        <section className={styles.intro}>
          <div>
            <h1>Notícias recentes</h1>
            <p>
              Acompanha num só lugar as notícias mais recentes publicadas pelos
              principais órgãos de comunicação social portugueses.
            </p>
          </div>
        </section>

        <div className={styles.filterArea}>
          <nav className={styles.filters} aria-label="Filtrar por fonte">
            <button
              type="button"
              className={selected === ALL_SOURCES ? styles.activeFilter : ""}
              onClick={() => setSelected(ALL_SOURCES)}
            >
              Todas
            </button>
            {payload?.sources.map((source) => (
              <button
                type="button"
                key={source.id}
                className={selected === source.id ? styles.activeFilter : ""}
                onClick={() => setSelected(source.id)}
              >
                <SourceLogo source={source} />
                {source.name}
              </button>
            ))}
          </nav>
          {sourceUpdatePaused && (
            <span className={styles.pauseNotice} role="status">
              Atualização em pausa · retomamos em breve
            </span>
          )}
        </div>

        {error && <p className={styles.error}>{error}</p>}
        {loading && !payload && <div className={styles.loading}>A carregar…</div>}

        <section className={styles.grid} aria-live="polite">
          {items.map((item: NewsItem) => {
            const source = sourceMap.get(item.sourceId);
            if (!source) return null;
            return (
              <article className={styles.card} key={item.id}>
                <a
                  className={styles.cardTarget}
                  href={item.link}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={`Ler “${item.title}” em ${item.source}`}
                />
                <div className={styles.cardMeta}>
                  <SourceLogo source={source} />
                  <span>{item.source}</span>
                  <time dateTime={item.publishedAt}>
                    {relativeTime(item.publishedAt)}
                  </time>
                </div>
                <h2>{item.title}</h2>
                <p>{item.description}</p>
                <span className={styles.cardLink}>
                  <span className={styles.cardLinkText}>
                    Ler na fonte original
                  </span>
                  <span aria-hidden="true">↗</span>
                </span>
              </article>
            );
          })}
        </section>

        {!loading && items.length === 0 && (
          <p className={styles.empty}>
            Ainda não há notícias desta fonte para apresentar.
          </p>
        )}

      </main>
    </div>
  );
}
