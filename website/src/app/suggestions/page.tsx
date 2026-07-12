"use client";

import { useState, useEffect } from "react";
import Header from "@/components/Header";
import styles from "./page.module.css";

interface Recommendation {
  id: string;
  type: "book" | "podcast" | "movie" | "highlight" | "project";
  title: string;
  link: string;
  authorOrMeta: string;
  description: string;
  imageUrl: string;
  createdAt: string;
}

export default function SuggestionsPage() {
  const [queue, setQueue] = useState<Recommendation[]>([]);
  const [history, setHistory] = useState<Recommendation[]>([]);
  const [isAdmin, setIsAdmin] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [syncStatus, setSyncStatus] = useState("");
  const [gitSha, setGitSha] = useState<string | null>(null);

  // Form states
  const [type, setType] = useState<"book" | "podcast" | "movie" | "highlight" | "project">("book");
  const [title, setTitle] = useState("");
  const [link, setLink] = useState("");

  // Check admin role on mount (localhost check)
  useEffect(() => {
    if (typeof window !== "undefined") {
      const isLocal = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
      setIsAdmin(isLocal);
    }
    loadRecommendations();
  }, []);

  const loadRecommendations = async () => {
    setIsSyncing(true);
    setSyncStatus("A carregar recomendações...");
    try {
      const res = await fetch("/api/suggestions", { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        
        // Handle potential legacy format (groups) vs new flat format
        let rawQueue = data.queue || [];
        let rawHistory = data.history || [];

        // Simple migration logic if data is in legacy RecommendationSet format
        const migrate = (arr: any[]): Recommendation[] => {
          if (arr.length > 0 && "book" in arr[0]) {
            // Convert group sets to flat items
            const migrated: Recommendation[] = [];
            arr.forEach((set, idx) => {
              if (set.book && set.book.title) {
                migrated.push({
                  id: `${set.id}_book`,
                  type: "book",
                  title: set.book.title,
                  authorOrMeta: set.book.author,
                  description: "",
                  imageUrl: set.book.imageUrl,
                  link: set.book.link,
                  createdAt: new Date(Date.now() - idx * 3600000).toISOString()
                });
              }
              if (set.podcast && set.podcast.name) {
                migrated.push({
                  id: `${set.id}_pod`,
                  type: "podcast",
                  title: `${set.podcast.name} - ${set.podcast.episode}`,
                  authorOrMeta: "Podcast",
                  description: "",
                  imageUrl: set.podcast.imageUrl,
                  link: set.podcast.link,
                  createdAt: new Date(Date.now() - idx * 3600000).toISOString()
                });
              }
            });
            return migrated;
          }
          return arr as Recommendation[];
        };

        setQueue(migrate(rawQueue));
        setHistory(migrate(rawHistory));
        setGitSha(data.sha);
        
        if (data.source === "github") {
          setSyncStatus("Sincronizado com o GitHub!");
        } else {
          setSyncStatus("Modo Localhost (Guardando no disco local).");
        }
      }
    } catch (err: any) {
      console.error(err);
      setSyncStatus(`Erro ao carregar dados: ${err.message}`);
    } finally {
      setIsSyncing(false);
    }
  };

  const saveRecommendations = async (updatedQueue: Recommendation[], updatedHistory: Recommendation[] = history) => {
    setQueue(updatedQueue);
    setHistory(updatedHistory);
    setIsSyncing(true);
    setSyncStatus("A sincronizar dados...");
    try {
      const res = await fetch("/api/suggestions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          queue: updatedQueue,
          history: updatedHistory,
          sha: gitSha
        }),
      });

      if (res.ok) {
        const data = await res.json();
        setGitSha(data.sha);
        setSyncStatus(data.source === "github" ? "Sincronizado com o GitHub!" : "Guardado localmente!");
      }
    } catch (err: any) {
      console.error(err);
      setSyncStatus(`Falha ao guardar: ${err.message}`);
    } finally {
      setIsSyncing(false);
    }
  };

  const handleAddRecommendation = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;

    setIsLoading(true);
    setSyncStatus("A IA está a recolher detalhes sobre a recomendação...");

    try {
      // Call metadata enrichment endpoint
      const enrichRes = await fetch("/api/suggestions/enrich", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, link, type }),
      });

      let enrichedMeta = {
        authorOrMeta: type === "book" ? "Autor" : "Detalhes",
        description: `Link: ${link || "Não fornecido"}`,
        imageUrl: ""
      };

      if (enrichRes.ok) {
        enrichedMeta = await enrichRes.json();
      }

      // Default Unsplash placehoder images based on type if AI failed to provide a valid one
      let finalImg = enrichedMeta.imageUrl;
      if (!finalImg || !finalImg.startsWith("http")) {
        if (type === "book") finalImg = "https://images.unsplash.com/photo-1543002588-bfa74002ed7e?q=80&w=200";
        else if (type === "podcast") finalImg = "https://images.unsplash.com/photo-1478737270239-2f02b77fc618?q=80&w=200";
        else if (type === "movie") finalImg = "https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?q=80&w=200";
        else if (type === "highlight") finalImg = "https://images.unsplash.com/photo-1504711434969-e33886168f5c?q=80&w=200";
        else finalImg = "";
      }

      const newItem: Recommendation = {
        id: Date.now().toString(),
        type,
        title: title.trim(),
        link: type === "project" ? "" : link.trim(),
        authorOrMeta: enrichedMeta.authorOrMeta,
        description: enrichedMeta.description,
        imageUrl: finalImg,
        createdAt: new Date().toISOString()
      };

      // Place in the queue: prepend to show latest first
      const updatedQueue = [newItem, ...queue];
      await saveRecommendations(updatedQueue);

      // Reset form
      setTitle("");
      setLink("");
    } catch (err: any) {
      console.error(err);
      setSyncStatus(`Erro ao adicionar: ${err.message}`);
    } finally {
      setIsLoading(false);
    }
  };

  // Admin controls
  const handleArchive = async (id: string) => {
    const itemToArchive = queue.find(item => item.id === id);
    if (!itemToArchive) return;

    const updatedQueue = queue.filter(item => item.id !== id);
    const updatedHistory = [itemToArchive, ...history];
    await saveRecommendations(updatedQueue, updatedHistory);
  };

  const handleRestore = async (id: string) => {
    const itemToRestore = history.find(item => item.id === id);
    if (!itemToRestore) return;

    const updatedHistory = history.filter(item => item.id !== id);
    const updatedQueue = [...queue, itemToRestore];
    await saveRecommendations(updatedQueue, updatedHistory);
  };

  const handleDelete = async (id: string, fromQueue: boolean) => {
    if (!confirm("Tem a certeza que deseja eliminar esta recomendação permanentemente?")) return;

    if (fromQueue) {
      const updatedQueue = queue.filter(item => item.id !== id);
      await saveRecommendations(updatedQueue, history);
    } else {
      const updatedHistory = history.filter(item => item.id !== id);
      await saveRecommendations(queue, updatedHistory);
    }
  };

  // Helper to render type icon
  const getTypeEmoji = (t: Recommendation["type"]) => {
    switch (t) {
      case "book": return "📚";
      case "podcast": return "🎙️";
      case "movie": return "🎬";
      case "highlight": return "📰";
      case "project": return "💡";
    }
  };

  // Helper to render type badge label
  const getTypeLabel = (t: Recommendation["type"]) => {
    switch (t) {
      case "book": return "Livro";
      case "podcast": return "Podcast";
      case "movie": return "Filme/Série";
      case "highlight": return "Destaque";
      case "project": return "Sugestão Projeto";
    }
  };

  // Helper to render class for badge
  const getBadgeClass = (t: Recommendation["type"]) => {
    switch (t) {
      case "book": return styles.badgeBook;
      case "podcast": return styles.badgePodcast;
      case "movie": return styles.badgeMovie;
      case "highlight": return styles.badgeHighlight;
      case "project": return styles.badgeProject;
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
            Partilha livros, podcasts, artigos ou melhorias para o próprio projeto do Politómetro.
          </p>
        </div>
        <div className={styles.grid}>
          {/* Add Form Card */}
          <div className={`${styles.formCard} glass`}>
            <h2>Adicionar Sugestão</h2>
            
            <form onSubmit={handleAddRecommendation} className={styles.form}>
              <div className={styles.inputGroup}>
                <label className={styles.inputLabel}>Tipo</label>
                <select 
                  className={styles.select}
                  value={type}
                  onChange={(e) => setType(e.target.value as any)}
                  disabled={isLoading}
                >
                  <option value="book">📚 Livro recomendado</option>
                  <option value="podcast">🎙️ Podcast / Canal</option>
                  <option value="movie">🎬 Filme / Série</option>
                  <option value="highlight">📰 Destaque / Artigo</option>
                  <option value="project">💡 Sugestão para o Projeto (Politómetro)</option>
                </select>
              </div>

              <div className={styles.inputGroup}>
                <label className={styles.inputLabel}>Título</label>
                <input 
                  type="text" 
                  placeholder={type === "project" ? "Descreva a sugestão..." : "Título do conteúdo..."}
                  className={styles.input}
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
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
                    onChange={(e) => setLink(e.target.value)}
                    disabled={isLoading}
                  />
                </div>
              )}

              <button 
                type="submit" 
                className={styles.submitBtn}
                disabled={isLoading || !title.trim()}
              >
                {isLoading ? (
                  <>✨ A analisar com IA...</>
                ) : (
                  <>Submeter Sugestão {getTypeEmoji(type)}</>
                )}
              </button>
            </form>
          </div>

          {/* Recommendations Feed List (Admin only in localhost) */}
          {isAdmin && (
            <div className={styles.feedContainer} style={{ marginTop: "2rem" }}>
              <div className={styles.feedHeader}>
                <h2 className={styles.feedTitle}>Fila de Moderação ({queue.length})</h2>
                {isSyncing && <span className={styles.loadingSpinner}>⏳ A sincronizar...</span>}
              </div>

              {queue.length === 0 ? (
                <div className={styles.emptyState}>
                  <span className={styles.emptyIcon}>📭</span>
                  <p>Nenhuma recomendação pendente na fila.</p>
                </div>
              ) : (
                <div className={styles.feedList}>
                  {queue.map((item) => {
                    const isProject = item.type === "project";
                    return (
                      <div key={item.id} className={isProject ? styles.projectCard : styles.card}>
                        {isProject ? (
                          <div className={styles.projectIconWrapper}>💡</div>
                        ) : (
                          item.imageUrl && (
                            <div className={styles.coverImageWrapper}>
                              <img src={item.imageUrl} alt={item.title} className={styles.coverImage} />
                            </div>
                          )
                        )}
                        
                        <div className={styles.cardContent}>
                          <div className={styles.badgeRow}>
                            <span className={`${styles.badge} ${getBadgeClass(item.type)}`}>
                              {getTypeEmoji(item.type)} {getTypeLabel(item.type)}
                            </span>
                            <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                              {new Date(item.createdAt).toLocaleDateString("pt-PT")}
                            </span>
                          </div>
                          
                          <h3 className={styles.itemTitle}>{item.title}</h3>
                          {!isProject && item.authorOrMeta && (
                            <span className={styles.itemAuthor}>de {item.authorOrMeta}</span>
                          )}
                          <p className={styles.itemDesc}>{item.description}</p>
                          
                          {!isProject && item.link && (
                            <a href={item.link} target="_blank" rel="noopener noreferrer" className={styles.linkBtn}>
                              Ver conteúdo ↗
                            </a>
                          )}

                          <div className={styles.adminActions}>
                            <button onClick={() => handleArchive(item.id)} className={styles.adminBtn}>
                              📦 Aprovar e Arquivar (Histórico)
                            </button>
                            <button onClick={() => handleDelete(item.id, true)} className={`${styles.adminBtn} ${styles.deleteBtn}`}>
                              🗑️ Eliminar
                            </button>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* History Feed List (Admin only) */}
              {history.length > 0 && (
                <div className={styles.feedContainer} style={{ marginTop: "3rem" }}>
                  <div className={styles.feedHeader}>
                    <h2 className={styles.feedTitle} style={{ color: "var(--text-secondary)" }}>Histórico de Publicações ({history.length})</h2>
                  </div>

                  <div className={styles.feedList}>
                    {history.map((item) => {
                      const isProject = item.type === "project";
                      return (
                        <div key={item.id} className={isProject ? styles.projectCard : styles.card} style={{ opacity: 0.75 }}>
                          {isProject ? (
                            <div className={styles.projectIconWrapper}>💡</div>
                          ) : (
                            item.imageUrl && (
                              <div className={styles.coverImageWrapper}>
                                <img src={item.imageUrl} alt={item.title} className={styles.coverImage} />
                              </div>
                            )
                          )}
                          
                          <div className={styles.cardContent}>
                            <div className={styles.badgeRow}>
                              <span className={`${styles.badge} ${getBadgeClass(item.type)}`}>
                                {getTypeEmoji(item.type)} {getTypeLabel(item.type)}
                              </span>
                              <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                                Publicado
                              </span>
                            </div>
                            
                            <h3 className={styles.itemTitle}>{item.title}</h3>
                            {!isProject && item.authorOrMeta && (
                              <span className={styles.itemAuthor}>de {item.authorOrMeta}</span>
                            )}
                            <p className={styles.itemDesc}>{item.description}</p>
                            
                            <div className={styles.adminActions}>
                              <button onClick={() => handleRestore(item.id)} className={styles.adminBtn}>
                                ↩️ Restaurar na Fila
                              </button>
                              <button onClick={() => handleDelete(item.id, false)} className={`${styles.adminBtn} ${styles.deleteBtn}`}>
                                🗑️ Eliminar
                              </button>
                            </div>
                          </div>
                        </div>
                      );
                    })}
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
