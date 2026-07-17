"use client";

import { useEffect, useRef, useState } from "react";
import Header from "@/components/Header";
import type {
  RecommendationType,
  RecommendationVerification,
  ResolvedRecommendation,
} from "@/lib/recommendationResolver";
import styles from "./page.module.css";

const dropdownOptions: Array<{
  value: RecommendationType;
  icon: string;
  label: string;
}> = [
  { value: "book", icon: "📚", label: "Livro recomendado" },
  { value: "podcast", icon: "🎙️", label: "Podcast / Canal" },
  { value: "movie", icon: "🎬", label: "Filme / Série" },
  { value: "highlight", icon: "📰", label: "Destaque / Artigo" },
  {
    value: "project",
    icon: "💡",
    label: "Sugestão para o Projeto (Politómetro)",
  },
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
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
    !dropdownOptions.some((option) => option.value === value.type) ||
    value.resolutionStatus !== "verified" ||
    !text(value.category) ||
    !text(value.title) ||
    !isRecord(value.verification)
  ) {
    return null;
  }
  return value as unknown as ResolvedRecommendation & {
    verification: RecommendationVerification;
  };
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export default function SuggestionsPage() {
  const [type, setType] = useState<RecommendationType>("book");
  const [title, setTitle] = useState("");
  const [link, setLink] = useState("");
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [formStatus, setFormStatus] = useState("");
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    const closeOutside = (event: MouseEvent) => {
      if (!dropdownRef.current?.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", closeOutside);
    return () => document.removeEventListener("mousedown", closeOutside);
  }, [isOpen]);

  const selectedOption =
    dropdownOptions.find((option) => option.value === type) ??
    dropdownOptions[0];

  const handleAddRecommendation = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!title.trim()) return;

    setIsLoading(true);
    setFormStatus("A validar o título, o link e a imagem na fonte original…");
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

      setFormStatus("Fonte confirmada. A enviar para aprovação…");
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
          payloadError(appendPayload, "A sugestão não foi recebida."),
        );
      }

      setTitle("");
      setLink("");
      setFormStatus(
        appendPayload.notificationSent === true
          ? `“${resolved.title}” foi recebida e enviada para aprovação.`
          : payloadError(
              appendPayload,
              `“${resolved.title}” foi recebida e será encaminhada para aprovação.`,
            ),
      );
    } catch (error: unknown) {
      console.error(error);
      setFormStatus(`Não foi possível adicionar: ${errorMessage(error)}`);
    } finally {
      setIsLoading(false);
    }
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

        <div className={styles.grid}>
          <div className={`${styles.formCard} glass`}>
            <h2>Adicionar Sugestão</h2>

            <form onSubmit={handleAddRecommendation} className={styles.form}>
              <div className={styles.inputGroup}>
                <label className={styles.inputLabel} id="type-label">
                  Tipo
                </label>
                <div className={styles.customDropdownContainer} ref={dropdownRef}>
                  <button
                    type="button"
                    className={`${styles.select} ${styles.dropdownToggle}`}
                    onClick={() => setIsOpen((current) => !current)}
                    disabled={isLoading}
                    aria-labelledby="type-label"
                    aria-haspopup="listbox"
                    aria-expanded={isOpen}
                  >
                    <span className={styles.selectedOption}>
                      <span className={styles.optionIcon}>
                        {selectedOption.icon}
                      </span>
                      {selectedOption.label}
                    </span>
                    <span
                      className={`${styles.dropdownChevron} ${isOpen ? styles.chevronOpen : ""}`}
                      aria-hidden="true"
                    >
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        width="15"
                        height="15"
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
                    <div className={styles.dropdownMenu} role="listbox">
                      {dropdownOptions.map((option) => (
                        <button
                          type="button"
                          role="option"
                          aria-selected={type === option.value}
                          key={option.value}
                          className={`${styles.dropdownOption} ${type === option.value ? styles.activeOption : ""}`}
                          onClick={() => {
                            setType(option.value);
                            setIsOpen(false);
                          }}
                        >
                          <span className={styles.optionIcon}>{option.icon}</span>
                          <span>{option.label}</span>
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
                      ? "Descreve a sugestão…"
                      : "Título do conteúdo…"
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
                    placeholder="https://…"
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
                  ? "A validar fontes…"
                  : `Submeter sugestão ${selectedOption.icon}`}
              </button>

              {formStatus && (
                <p className={styles.formStatus} aria-live="polite">
                  {formStatus}
                </p>
              )}
            </form>
          </div>
        </div>
      </main>
    </div>
  );
}
