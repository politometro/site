"use client";

import { useEffect, useRef, useState } from "react";
import Header from "@/components/Header";
import type { RecommendationType } from "@/lib/recommendationResolver";
import styles from "./page.module.css";

const dropdownOptions: Array<{
  value: RecommendationType;
  icon: string;
  label: string;
}> = [
  { value: "book", icon: "📚", label: "Livro / Publicação" },
  { value: "podcast", icon: "🎙️", label: "Podcast / Áudio" },
  { value: "movie", icon: "🎬", label: "Filme / Série / Vídeo" },
  { value: "nostalgia", icon: "📼", label: "Humor / Arquivo" },
  {
    value: "investigation",
    icon: "🔎",
    label: "Investigação / Documentário",
  },
  { value: "highlight", icon: "📰", label: "Destaque / Artigo" },
  {
    value: "project",
    icon: "💡",
    label: "Ideia / Outro conteúdo",
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

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function publicSubmissionError(error: unknown): string {
  const original = errorMessage(error);
  const message = original.toLowerCase();
  if (message.includes("prazo de relevância") || message.includes("expir")) {
    return "Este conteúdo já não é suficientemente recente para ser recomendado. Escolhe uma publicação mais atual e tenta novamente.";
  }
  if (message.includes("já existe") || message.includes("histórico")) {
    return "Esta sugestão já foi recebida anteriormente. Obrigado pela contribuição.";
  }
  if (
    message.includes("demasiad") ||
    message.includes("limite") ||
    message.includes("muitas sugestões")
  ) {
    return "Recebemos várias sugestões num curto espaço de tempo. Aguarda um pouco antes de tentares novamente.";
  }
  if (
    message.includes("link indicado") ||
    message.includes("link fornecido") ||
    message.includes("apenas pelo título")
  ) {
    return original;
  }
  if (message.includes("tipo de recomendação")) {
    return "Seleciona um tipo de conteúdo válido e tenta novamente.";
  }
  return "Não foi possível concluir a submissão neste momento. Confirma os dados e tenta novamente dentro de alguns minutos.";
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
    setFormStatus("A enviar a sugestão para aprovação…");
    try {
      const appendResponse = await fetch("/api/suggestions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "append",
          item: {
            type,
            title,
            link,
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
      const storedItem = isRecord(appendPayload.item)
        ? appendPayload.item
        : {};
      const submittedTitle = text(storedItem.title) || title.trim();
      const notificationSent = appendPayload.notificationSent === true;
      setFormStatus(
        notificationSent
          ? `“${submittedTitle}” foi enviada para aprovação no Discord. Obrigado pela contribuição.`
          : `“${submittedTitle}” foi recebida e será entregue no canal de aprovação antes de poder seguir para posts.`,
      );
    } catch (error: unknown) {
      console.error(error);
      setFormStatus(publicSubmissionError(error));
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
            Partilha qualquer conteúdo ou ideia: o link é opcional e pode ser
            de qualquer site público, independentemente da categoria.
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
                  maxLength={220}
                  required
                />
              </div>

              <div className={styles.inputGroup}>
                <label className={styles.inputLabel}>Link / URL (opcional)</label>
                <input
                  type="url"
                  placeholder="https://…"
                  className={styles.input}
                  value={link}
                  onChange={(event) => setLink(event.target.value)}
                  disabled={isLoading}
                  maxLength={2048}
                />
              </div>

              <button
                type="submit"
                className={styles.submitBtn}
                disabled={isLoading || !title.trim()}
              >
                {isLoading
                  ? "A enviar para aprovação…"
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
