"use client";

import { useCallback, useEffect, useState } from "react";
import Header from "@/components/Header";
import type {
  RecommendationType,
  RecommendationVerification,
  ResolvedRecommendation,
} from "@/lib/recommendationResolver";
import styles from "./page.module.css";

interface Recommendation extends Record<string, unknown> {
  id: string;
  type: RecommendationType;
  category: string;
  title: string;
  link: string;
  authorOrMeta: string;
  description: string;
  imageUrl: string;
  sourcePublishedAt: string | null;
  expiryDate: string | null;
  createdAt: string;
  status?: string;
  resolutionStatus?: "verified" | "unresolved";
  verification?: RecommendationVerification;
}

const dropdownOptions: Array<{
  value: RecommendationType;
  label: string;
}> = [
  { value: "book", label: "📚 Livro recomendado" },
  { value: "podcast", label: "🎙️ Podcast / Canal" },
  { value: "movie", label: "🎬 Filme / Série" },
  { value: "highlight", label: "📰 Destaque / Artigo" },
  { value: "project", label: "💡 Sugestão para o Projeto (Politómetro)" },
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function categoryFor(type: RecommendationType): string {
  switch (type) {
    case "book":
      return "Livro";
    case "podcast":
      return "Podcast";
    case "movie":
      return "Filme";
    case "highlight":
      return "Destaque";
    case "project":
      return "Sugestão para o Projeto";
  }
}

function isType(value: unknown): value is RecommendationType {
  return dropdownOptions.some((option) => option.value === value);
}

function parseVerification(
  value: unknown,
): RecommendationVerification | undefined {
  if (!isRecord(value)) return undefined;
  return {
    status: value.status === "unresolved" ? "unresolved" : "verified",
    provider: text(value.provider),
    confidence:
      typeof value.confidence === "number" ? value.confidence : 0,
    checkedAt: text(value.checkedAt),
    canonicalUrl: text(value.canonicalUrl) || undefined,
    externalId: text(value.externalId) || undefined,
    entityId: text(value.entityId) || undefined,
    coverHash: text(value.coverHash) || undefined,
    source: text(value.source) || undefined,
    score: typeof value.score === "number" ? value.score : undefined,
    verifiedAt: text(value.verifiedAt) || undefined,
    sourceTitle: text(value.sourceTitle) || undefined,
    titleScore:
      typeof value.titleScore === "number" ? value.titleScore : undefined,
    matchedFields: Array.isArray(value.matchedFields)
      ? value.matchedFields.filter(
          (item): item is string => typeof item === "string",
        )
      : [],
    warnings: Array.isArray(value.warnings)
      ? value.warnings.filter(
          (item): item is string => typeof item === "string",
        )
      : [],
  };
}

function parseRecommendation(value: unknown): Recommendation | null {
  if (!isRecord(value) || !isType(value.type)) return null;
  const id = text(value.id);
  const title = text(value.title);
  if (!id || !title) return null;

  const verification = parseVerification(value.verification);
  const resolutionStatus =
    value.resolutionStatus === "verified" ||
    value.resolutionStatus === "unresolved"
      ? value.resolutionStatus
      : undefined;
  return {
    ...value,
    id,
    type: value.type,
    category: text(value.category) || categoryFor(value.type),
    title,
    link: text(value.link),
    authorOrMeta: text(value.authorOrMeta),
    description: text(value.description),
    imageUrl: text(value.imageUrl),
    sourcePublishedAt: text(value.sourcePublishedAt) || null,
    expiryDate: text(value.expiryDate) || null,
    createdAt: text(value.createdAt) || new Date(0).toISOString(),
    status: text(value.status) || undefined,
    resolutionStatus,
    verification,
  };
}

function migrateRecommendations(value: unknown): Recommendation[] {
  if (!Array.isArray(value)) return [];

  if (value.length > 0 && isRecord(value[0]) && "book" in value[0]) {
    const migrated: Recommendation[] = [];
    value.forEach((rawSet, index) => {
      if (!isRecord(rawSet)) return;
      const setId = text(rawSet.id) || `legacy_${index}`;
      const createdAt =
        text(rawSet.createdAt) ||
        new Date(Date.UTC(2000, 0, 1, 0, index)).toISOString();
      if (isRecord(rawSet.book) && text(rawSet.book.title)) {
        migrated.push({
          id: `${setId}_book`,
          type: "book",
          category: "Livro",
          title: text(rawSet.book.title),
          authorOrMeta: text(rawSet.book.author),
          description: text(rawSet.book.description),
          imageUrl: text(rawSet.book.imageUrl),
          link: text(rawSet.book.link),
          sourcePublishedAt: null,
          expiryDate: null,
          createdAt,
        });
      }
      if (isRecord(rawSet.podcast) && text(rawSet.podcast.name)) {
        const episode = text(rawSet.podcast.episode);
        migrated.push({
          id: `${setId}_podcast`,
          type: "podcast",
          category: "Podcast",
          title: [text(rawSet.podcast.name), episode].filter(Boolean).join(" — "),
          authorOrMeta: text(rawSet.podcast.authorOrMeta) || "Podcast",
          description: text(rawSet.podcast.description),
          imageUrl: text(rawSet.podcast.imageUrl),
          link: text(rawSet.podcast.link),
          sourcePublishedAt: null,
          expiryDate: null,
          createdAt,
        });
      }
    });
    return migrated;
  }

  return value
    .map(parseRecommendation)
    .filter((item): item is Recommendation => Boolean(item));
}

async function responsePayload(
  response: Response,
): Promise<Record<string, unknown>> {
  const payload: unknown = await response.json().catch(() => ({}));
  return isRecord(payload) ? payload : {};
}

function payloadError(
  payload: Record<string, unknown>,
  fallback: string,
): string {
  return text(payload.error) || text(payload.warning) || fallback;
}

function resolvedFromPayload(
  value: Record<string, unknown>,
): ResolvedRecommendation | null {
  if (
    !isType(value.type) ||
    value.resolutionStatus !== "verified" ||
    !text(value.category) ||
    !text(value.title) ||
    !isRecord(value.verification)
  ) {
    return null;
  }
  return value as unknown as ResolvedRecommendation;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isExpired(item: Recommendation): boolean {
  if (!item.expiryDate) return false;
  const expiry = new Date(item.expiryDate).getTime();
  return !Number.isFinite(expiry) || expiry <= Date.now();
}

export default function SuggestionsPage() {
  const [queue, setQueue] = useState<Recommendation[]>([]);
  const [history, setHistory] = useState<Recommendation[]>([]);
  const [isAdmin, setIsAdmin] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [syncStatus, setSyncStatus] = useState("");
  const [gitSha, setGitSha] = useState<string | null>(null);
  const [type, setType] = useState<RecommendationType>("book");
  const [title, setTitle] = useState("");
  const [link, setLink] = useState("");
  const [isOpen, setIsOpen] = useState(false);

  const loadRecommendations = useCallback(async () => {
    setIsSyncing(true);
    setSyncStatus("A carregar recomendações...");
    try {
      const response = await fetch("/api/suggestions", { cache: "no-store" });
      const payload = await responsePayload(response);
      if (!response.ok) {
        throw new Error(
          payloadError(payload, "Não foi possível carregar as recomendações."),
        );
      }

      setQueue(migrateRecommendations(payload.queue));
      setHistory(migrateRecommendations(payload.history));
      setGitSha(typeof payload.sha === "string" ? payload.sha : null);
      setSyncStatus(
        payload.source === "github"
          ? "Sincronizado com o GitHub."
          : "Modo local: fila guardada no disco.",
      );
    } catch (error: unknown) {
      console.error(error);
      setSyncStatus(`Erro ao carregar dados: ${errorMessage(error)}`);
    } finally {
      setIsSyncing(false);
    }
  }, []);

  useEffect(() => {
    const adminCheck = window.setTimeout(() => {
      const hostname = window.location.hostname;
      setIsAdmin(hostname === "localhost" || hostname === "127.0.0.1");
    }, 0);
    const initialLoad = window.setTimeout(() => {
      void loadRecommendations();
    }, 0);
    return () => {
      window.clearTimeout(adminCheck);
      window.clearTimeout(initialLoad);
    };
  }, [loadRecommendations]);

  useEffect(() => {
    if (!isOpen) return;
    const handleOutsideClick = () => setIsOpen(false);
    window.addEventListener("click", handleOutsideClick);
    return () => window.removeEventListener("click", handleOutsideClick);
  }, [isOpen]);

  const saveRecommendations = async (
    updatedQueue: Recommendation[],
    updatedHistory: Recommendation[] = history,
  ) => {
    setIsSyncing(true);
    setSyncStatus("A sincronizar dados...");
    try {
      const response = await fetch("/api/suggestions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "replace",
          queue: updatedQueue,
          history: updatedHistory,
          sha: gitSha,
        }),
      });
      const payload = await responsePayload(response);
      if (!response.ok) {
        throw new Error(payloadError(payload, "Falha ao guardar a fila."));
      }
      setQueue(updatedQueue);
      setHistory(updatedHistory);
      setGitSha(typeof payload.sha === "string" ? payload.sha : null);
      setSyncStatus(
        payload.source === "github"
          ? "Sincronizado com o GitHub."
          : "Guardado localmente.",
      );
    } catch (error: unknown) {
      console.error(error);
      setSyncStatus(`Falha ao guardar: ${errorMessage(error)}`);
    } finally {
      setIsSyncing(false);
    }
  };

  const handleAddRecommendation = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!title.trim()) return;

    setIsLoading(true);
    setSyncStatus("A confirmar a identidade, o link e a imagem na fonte...");
    try {
      const enrichResponse = await fetch("/api/suggestions/enrich", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, link, type }),
      });
      const enrichPayload = await responsePayload(enrichResponse);
      const resolved = resolvedFromPayload(enrichPayload);
      if (!enrichResponse.ok || !resolved) {
        const verification = isRecord(enrichPayload.verification)
          ? enrichPayload.verification
          : {};
        const warning =
          Array.isArray(verification.warnings) &&
          typeof verification.warnings[0] === "string"
            ? verification.warnings[0]
            : "";
        throw new Error(
          warning ||
            payloadError(
              enrichPayload,
              "Não foi encontrada uma correspondência suficientemente segura.",
            ),
        );
      }

      setSyncStatus("Fonte confirmada. A guardar e a enviar para aprovação...");
      const appendResponse = await fetch("/api/suggestions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "append",
          item: {
            type: resolved.type,
            title: resolved.title,
            link: resolved.link,
          },
        }),
      });
      const appendPayload = await responsePayload(appendResponse);
      if (!appendResponse.ok) {
        throw new Error(
          payloadError(appendPayload, "A recomendação não foi guardada."),
        );
      }

      setTitle("");
      setLink("");
      await loadRecommendations();
      if (
        appendPayload.notificationSent === true &&
        appendPayload.statusPersisted === true
      ) {
        setSyncStatus(
          `“${resolved.title}” foi validado, guardado e enviado para aprovação no Discord.`,
        );
      } else {
        setSyncStatus(
          payloadError(
            appendPayload,
            `“${resolved.title}” foi validado e guardado, mas aguarda notificação no Discord.`,
          ),
        );
      }
    } catch (error: unknown) {
      console.error(error);
      setSyncStatus(`Não foi possível adicionar: ${errorMessage(error)}`);
    } finally {
      setIsLoading(false);
    }
  };

  const handleArchive = async (id: string) => {
    const item = queue.find((candidate) => candidate.id === id);
    if (!item) return;
    if (isExpired(item)) {
      setSyncStatus(
        "Esta recomendação expirou e não pode ser aprovada nem publicada.",
      );
      return;
    }
    await saveRecommendations(
      queue.filter((candidate) => candidate.id !== id),
      [item, ...history],
    );
  };

  const handleRestore = async (id: string) => {
    const item = history.find((candidate) => candidate.id === id);
    if (!item) return;
    await saveRecommendations(
      [...queue, item],
      history.filter((candidate) => candidate.id !== id),
    );
  };

  const handleDelete = async (id: string, fromQueue: boolean) => {
    if (!confirm("Tem a certeza de que deseja eliminar esta recomendação?")) {
      return;
    }
    await saveRecommendations(
      fromQueue
        ? queue.filter((item) => item.id !== id)
        : queue,
      fromQueue
        ? history
        : history.filter((item) => item.id !== id),
    );
  };

  const getTypeEmoji = (itemType: RecommendationType) =>
    dropdownOptions.find((option) => option.value === itemType)?.label.split(" ")[0] ??
    "📌";

  const getTypeLabel = (itemType: RecommendationType) => {
    switch (itemType) {
      case "book":
        return "Livro";
      case "podcast":
        return "Podcast";
      case "movie":
        return "Filme/Série";
      case "highlight":
        return "Destaque";
      case "project":
        return "Sugestão Projeto";
    }
  };

  const getBadgeClass = (itemType: RecommendationType) => {
    switch (itemType) {
      case "book":
        return styles.badgeBook;
      case "podcast":
        return styles.badgePodcast;
      case "movie":
        return styles.badgeMovie;
      case "highlight":
        return styles.badgeHighlight;
      case "project":
        return styles.badgeProject;
    }
  };

  const renderRecommendation = (
    item: Recommendation,
    inHistory: boolean,
  ) => {
    const isProject = item.type === "project";
    const expired = isExpired(item);
    return (
      <div
        key={item.id}
        className={isProject ? styles.projectCard : styles.card}
        style={inHistory ? { opacity: 0.75 } : undefined}
      >
        {isProject ? (
          <div className={styles.projectIconWrapper}>💡</div>
        ) : (
          item.imageUrl && (
            <div className={styles.coverImageWrapper}>
              {/* The source URL is verified dynamically by the server. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={item.imageUrl}
                alt={`Capa de ${item.title}`}
                className={styles.coverImage}
              />
            </div>
          )
        )}

        <div className={styles.cardContent}>
          <div className={styles.badgeRow}>
            <span className={`${styles.badge} ${getBadgeClass(item.type)}`}>
              {getTypeEmoji(item.type)} {getTypeLabel(item.type)}
            </span>
            <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
              {inHistory
                ? "Publicado"
                : expired
                  ? "Expirado"
                : new Date(item.createdAt).toLocaleDateString("pt-PT")}
            </span>
          </div>

          <h3 className={styles.itemTitle}>{item.title}</h3>
          {!isProject && item.authorOrMeta && (
            <span className={styles.itemAuthor}>de {item.authorOrMeta}</span>
          )}
          <p className={styles.itemDesc}>{item.description}</p>

          {!isProject && item.link && (
            <a
              href={item.link}
              target="_blank"
              rel="noopener noreferrer"
              className={styles.linkBtn}
            >
              Ver conteúdo ↗
            </a>
          )}

          <div className={styles.adminActions}>
            {inHistory ? (
              <button
                onClick={() => handleRestore(item.id)}
                className={styles.adminBtn}
              >
                ↩️ Restaurar na Fila
              </button>
            ) : (
              <button
                onClick={() => handleArchive(item.id)}
                className={styles.adminBtn}
                disabled={expired}
              >
                {expired ? "⛔ Aprovação bloqueada" : "📦 Aprovar e Arquivar"}
              </button>
            )}
            <button
              onClick={() => handleDelete(item.id, !inHistory)}
              className={`${styles.adminBtn} ${styles.deleteBtn}`}
            >
              🗑️ Eliminar
            </button>
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className={styles.container}>
      <Header />
      <div className="ambient-glow" />

      <main className={styles.main}>
        <div className={styles.titleSection}>
          <h1 className={styles.title}>Sugerir Conteúdo</h1>
          <p className={styles.subtitle}>
            Partilha livros, podcasts, artigos ou melhorias para o próprio
            projeto do Politómetro.
          </p>
        </div>

        {syncStatus && (
          <div className={`${styles.statusBanner} glass`} aria-live="polite">
            <span className={styles.statusText}>{syncStatus}</span>
            {isSyncing && <span className={styles.loadingSpinner}>⏳</span>}
          </div>
        )}

        <div className={styles.grid}>
          <div className={`${styles.formCard} glass`}>
            <h2>Adicionar Sugestão</h2>

            <form onSubmit={handleAddRecommendation} className={styles.form}>
              <div
                className={styles.inputGroup}
                onClick={(event) => event.stopPropagation()}
              >
                <label className={styles.inputLabel}>Tipo</label>
                <div className={styles.customDropdownContainer}>
                  <button
                    type="button"
                    className={`${styles.select} ${styles.dropdownToggle}`}
                    onClick={() => setIsOpen(!isOpen)}
                    disabled={isLoading}
                  >
                    <span>
                      {
                        dropdownOptions.find((option) => option.value === type)
                          ?.label
                      }
                    </span>
                    <span
                      className={`${styles.dropdownChevron} ${isOpen ? styles.chevronOpen : ""}`}
                    >
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        width="14"
                        height="14"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <polyline points="6 9 12 15 18 9" />
                      </svg>
                    </span>
                  </button>

                  {isOpen && (
                    <div className={styles.dropdownMenu}>
                      {dropdownOptions.map((option) => (
                        <button
                          type="button"
                          key={option.value}
                          className={`${styles.dropdownOption} ${type === option.value ? styles.activeOption : ""}`}
                          onClick={() => {
                            setType(option.value);
                            setIsOpen(false);
                          }}
                        >
                          {option.label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              <div className={styles.inputGroup}>
                <label className={styles.inputLabel}>Título</label>
                <input
                  type="text"
                  placeholder={
                    type === "project"
                      ? "Descreva a sugestão..."
                      : "Título do conteúdo..."
                  }
                  className={styles.input}
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  disabled={isLoading}
                  required
                />
              </div>

              {type !== "project" && (
                <div className={styles.inputGroup}>
                  <label className={styles.inputLabel}>Link / URL</label>
                  <input
                    type="url"
                    placeholder="https://..."
                    className={styles.input}
                    value={link}
                    onChange={(event) => setLink(event.target.value)}
                    disabled={isLoading}
                  />
                </div>
              )}

              <button
                type="submit"
                className={styles.submitBtn}
                disabled={isLoading || !title.trim()}
              >
                {isLoading
                  ? "🔎 A validar fontes..."
                  : `Submeter Sugestão ${getTypeEmoji(type)}`}
              </button>
            </form>
          </div>

          {isAdmin && (
            <div className={styles.feedContainer} style={{ marginTop: "2rem" }}>
              <div className={styles.feedHeader}>
                <h2 className={styles.feedTitle}>
                  Fila de Moderação ({queue.length})
                </h2>
                {isSyncing && (
                  <span className={styles.loadingSpinner}>
                    ⏳ A sincronizar...
                  </span>
                )}
              </div>

              {queue.length === 0 ? (
                <div className={styles.emptyState}>
                  <span className={styles.emptyIcon}>📭</span>
                  <p>Nenhuma recomendação pendente na fila.</p>
                </div>
              ) : (
                <div className={styles.feedList}>
                  {queue.map((item) => renderRecommendation(item, false))}
                </div>
              )}

              {history.length > 0 && (
                <div
                  className={styles.feedContainer}
                  style={{ marginTop: "3rem" }}
                >
                  <div className={styles.feedHeader}>
                    <h2
                      className={styles.feedTitle}
                      style={{ color: "var(--text-secondary)" }}
                    >
                      Histórico de Publicações ({history.length})
                    </h2>
                  </div>
                  <div className={styles.feedList}>
                    {history.map((item) =>
                      renderRecommendation(item, true),
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
