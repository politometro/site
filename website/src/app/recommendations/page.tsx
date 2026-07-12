"use client";

import { useState, useEffect } from "react";
import Header from "@/components/Header";
import styles from "./page.module.css";

interface Recommendation {
  id: string;
  type: "book" | "podcast" | "movie" | "highlight" | "project" | "series" | "documentary" | "article";
  category: string;
  title: string;
  link: string;
  authorOrMeta: string;
  description: string;
  imageUrl: string;
  createdAt: string;
}

export default function RecommendationsPage() {
  const [queue, setQueue] = useState<Recommendation[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    loadRecommendations();
  }, []);

  const loadRecommendations = async () => {
    setIsLoading(true);
    try {
      const res = await fetch("/api/suggestions", { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        // We only show items in the queue that are NOT suggestions of projects (only content recommendations)
        const filteredQueue = (data.queue || []).filter(
          (item: Recommendation) => item.type !== "project"
        );
        setQueue(filteredQueue);
      }
    } catch (err) {
      console.error("Erro ao carregar recomendações:", err);
    } finally {
      setIsLoading(false);
    }
  };

  const getTypeEmoji = (t: Recommendation["type"]) => {
    switch (t) {
      case "book": return "📚";
      case "podcast": return "🎙️";
      case "movie": return "🎬";
      case "series": return "📺";
      case "documentary": return "🎥";
      case "article": return "📰";
      case "highlight": return "⭐";
      default: return "💡";
    }
  };

  const getBadgeClass = (t: Recommendation["type"]) => {
    switch (t) {
      case "book": return styles.badgeBook;
      case "podcast": return styles.badgePodcast;
      case "movie": case "series": return styles.badgeMovie;
      case "documentary": return styles.badgeDocumentary;
      case "article": return styles.badgeArticle;
      case "highlight": return styles.badgeHighlight;
      default: return styles.badgeDefault;
    }
  };

  return (
    <div className={styles.container}>
      <Header />
      <div className="ambient-glow" />

      <main className={styles.main}>
        <div className={styles.titleSection}>
          <h1 className={styles.title}>Recomendações Politómetro</h1>
          <p className={styles.subtitle}>
            Livros, podcasts, documentários, séries e artigos sobre política, economia e sociedade.
          </p>
        </div>

        <div className={styles.grid}>
          {isLoading ? (
            <div className={styles.loadingState}>
              <span className={styles.loadingSpinner}>⏳</span> A carregar recomendações da fila...
            </div>
          ) : queue.length === 0 ? (
            <div className={styles.emptyState}>
              <span className={styles.emptyIcon}>📭</span>
              <p>Não existem recomendações ativas na fila neste momento.</p>
            </div>
          ) : (
            <div className={styles.feedList}>
              {queue.map((item) => {
                return (
                  <div key={item.id} className={styles.card}>
                    {item.imageUrl && (
                      <div className={styles.coverImageWrapper}>
                        <img src={item.imageUrl} alt={item.title} className={styles.coverImage} />
                      </div>
                    )}
                    
                    <div className={styles.cardContent}>
                      <div className={styles.badgeRow}>
                        <span className={`${styles.badge} ${getBadgeClass(item.type)}`}>
                          {getTypeEmoji(item.type)} {item.category || item.type}
                        </span>
                        <span className={styles.date}>
                          {new Date(item.createdAt).toLocaleDateString("pt-PT")}
                        </span>
                      </div>
                      
                      <h3 className={styles.itemTitle}>{item.title}</h3>
                      {item.authorOrMeta && (
                        <span className={styles.itemAuthor}>de {item.authorOrMeta}</span>
                      )}
                      <p className={styles.itemDesc}>{item.description}</p>
                      
                      {item.link && (
                        <a href={item.link} target="_blank" rel="noopener noreferrer" className={styles.linkBtn}>
                          Ver conteúdo ↗
                        </a>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
