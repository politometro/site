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
      setPayload((await response.json()) as NewsPayload);
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
              {!source.available && (
                <span className={styles.unavailableMark} title={source.note}>
                  !
                </span>
              )}
            </button>
          ))}
        </nav>

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
                  Ler na fonte original <span aria-hidden="true">↗</span>
                </span>
              </article>
            );
          })}
        </section>

        {!loading && items.length === 0 && (
          <p className={styles.empty}>
            Esta fonte não devolveu notícias válidas neste momento.
          </p>
        )}

      </main>
    </div>
  );
}
