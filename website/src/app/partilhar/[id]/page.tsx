"use client";

import { use, useEffect, useState } from "react";
import Header from "@/components/Header";
import { ChatMarkdown } from "@/components/ChatMarkdown";
import { conversationImagesPdf, downloadBlob } from "@/lib/conversationImages";
import styles from "../../page.module.css";

interface SharedMessage {
  role: "user" | "assistant";
  content: string;
}

interface ShareRecord {
  id: string;
  createdAt: string;
  messages: SharedMessage[];
}

export default function PartilhaPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [record, setRecord] = useState<ShareRecord | null>(null);
  const [error, setError] = useState("");
  const [generating, setGenerating] = useState(false);
  const [generateHint, setGenerateHint] = useState("");

  useEffect(() => {
    let active = true;
    fetch(`/api/share?id=${encodeURIComponent(id)}`, { cache: "no-store" })
      .then(async (response) => {
        const payload = (await response.json()) as ShareRecord & { error?: string };
        if (!response.ok) throw new Error(payload.error || "Não foi possível carregar a conversa.");
        return payload;
      })
      .then((payload) => {
        if (active) setRecord(payload);
      })
      .catch((loadError: unknown) => {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : "Falha ao carregar a conversa partilhada.");
        }
      });
    return () => {
      active = false;
    };
  }, [id]);

  const downloadPdf = async () => {
    if (!record || generating) return;
    setGenerating(true);
    setGenerateHint("");
    try {
      const { blob, pageCount } = await conversationImagesPdf(record.messages);
      downloadBlob(blob, `politometro-conversa-${record.id}.pdf`);
      setGenerateHint(`PDF gerado com ${pageCount} imagem(ns).`);
    } catch (pdfError: unknown) {
      setGenerateHint(
        pdfError instanceof Error ? pdfError.message : "Falha ao gerar o PDF.",
      );
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className={styles.container}>
      <Header />
      <main className={styles.main}>
        <div className={`${styles.chatBox} glass`}>
          <div className={styles.shareHeader}>
            <div>
              <strong>Conversa partilhada</strong>
              <small>
                {record
                  ? `ID ${record.id} · ${new Date(record.createdAt).toLocaleDateString("pt-PT")}`
                  : `ID ${id}`}
              </small>
            </div>
            <button
              type="button"
              className={styles.pdfButton}
              onClick={downloadPdf}
              disabled={!record || generating}
            >
              {generating ? "A gerar imagens…" : "Guardar como imagens (PDF)"}
            </button>
          </div>
          {generateHint && <p className={styles.shareNotice}>{generateHint}</p>}
          {error && <p className={styles.shareError}>{error}</p>}
          <div className={styles.messageList}>
            {!record && !error && <p className={styles.shareNotice}>A carregar a conversa partilhada…</p>}
            {record?.messages.map((message, index) => (
              <div
                key={`${message.role}-${index}`}
                className={`${styles.messageWrapper} ${message.role === "user" ? styles.userWrapper : styles.assistantWrapper}`}
              >
                <div className={styles.avatar}>{message.role === "user" ? "👤" : "🗳️"}</div>
                <div
                  className={styles.bubbleContainer}
                  style={{ alignItems: message.role === "user" ? "flex-end" : "flex-start" }}
                >
                  <div
                    className={`${styles.messageBubble} ${message.role === "user" ? styles.userBubble : styles.assistantBubble}`}
                  >
                    <div className={styles.messageContent}>
                      <ChatMarkdown
                        content={message.content}
                        classes={{
                          mdH2: styles.mdH2,
                          mdH3: styles.mdH3,
                          mdParagraph: styles.mdParagraph,
                          mdUl: styles.mdUl,
                          mdSpacing: styles.mdSpacing,
                        }}
                      />
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </main>
    </div>
  );
}
