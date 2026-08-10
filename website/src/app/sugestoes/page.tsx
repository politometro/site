"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Header from "@/components/Header";
import type { RecommendationType } from "@/lib/recommendationResolver";
import styles from "./page.module.css";

const dropdownOptions: Array<{
  value: RecommendationType;
  icon: string;
  label: string;
}> = [
  { value: "book", icon: "📚", label: "Livro ou publicação" },
  { value: "podcast", icon: "🎙️", label: "Podcast, canal ou episódio" },
  { value: "movie", icon: "🎬", label: "Filme, série ou documentário" },
  { value: "nostalgia", icon: "📼", label: "Nostalgia ou memória" },
  { value: "investigation", icon: "🔎", label: "Investigação ou reportagem" },
  { value: "highlight", icon: "📰", label: "Destaque ou artigo" },
  { value: "project", icon: "💡", label: "Projeto ou melhoria" },
];

function publicSubmissionError(status: number): string {
  if (status === 429) {
    return "Recebemos várias sugestões num curto espaço de tempo. Aguarda um pouco e tenta novamente.";
  }
  if (status === 413) {
    return "A sugestão é demasiado longa. Reduz o texto e tenta novamente.";
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

  const handleAddRecommendation = async (event: FormEvent) => {
    event.preventDefault();
    if (!title.trim()) return;

    setIsLoading(true);
    setFormStatus("A enviar a sugestão para revisão…");
    try {
      const response = await fetch("/api/suggestions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "append",
          item: { type, title, link },
        }),
      });
      const payload: unknown = await response.json().catch(() => null);

      if (!response.ok) {
        throw new Error(publicSubmissionError(response.status));
      }

      const notificationSent =
        typeof payload === "object" &&
        payload !== null &&
        "notificationSent" in payload &&
        payload.notificationSent === true;

      setTitle("");
      setLink("");
      setFormStatus(
        notificationSent
          ? "A sugestão foi enviada para o canal de aprovação no Discord. Obrigado pela contribuição."
          : "A sugestão foi recebida e será entregue no canal de aprovação antes de poder seguir para posts.",
      );
    } catch (error: unknown) {
      console.error(error);
      setFormStatus(
        error instanceof Error
          ? error.message
          : publicSubmissionError(500),
      );
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
          <h1 className={styles.title}>Sugerir conteúdo</h1>
          <p className={styles.subtitle}>
            Partilha conteúdos, referências ou melhorias para o próprio projeto
            do Politómetro. A ligação é opcional e a revisão acontece antes de
            qualquer publicação.
          </p>
        </div>

        <div className={styles.grid}>
          <div className={`${styles.formCard} glass`}>
            <h2>Adicionar sugestão</h2>

            <form onSubmit={handleAddRecommendation} className={styles.form}>
              <div className={styles.inputGroup}>
                <label className={styles.inputLabel} id="type-label">
                  Tipo
                </label>
                <div
                  className={styles.customDropdownContainer}
                  ref={dropdownRef}
                >
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
                      <span className={styles.optionIcon} aria-hidden="true">
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
                          <span className={styles.optionIcon} aria-hidden="true">
                            {option.icon}
                          </span>
                          <span>{option.label}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              <div className={styles.inputGroup}>
                <label className={styles.inputLabel} htmlFor="suggestion-title">
                  Título
                </label>
                <input
                  id="suggestion-title"
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
                <label className={styles.inputLabel} htmlFor="suggestion-link">
                  Link / URL (opcional)
                </label>
                <input
                  id="suggestion-link"
                  type="url"
                  placeholder="https://…"
                  className={styles.input}
                  value={link}
                  onChange={(event) => setLink(event.target.value)}
                  disabled={isLoading}
                  maxLength={2048}
                  inputMode="url"
                />
              </div>

              <button
                type="submit"
                className={styles.submitBtn}
                disabled={isLoading || !title.trim()}
              >
                {isLoading
                  ? "A enviar…"
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
