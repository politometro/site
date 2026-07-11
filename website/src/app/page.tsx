"use client";
// Trigger Vercel build after settings update

import { useState, useEffect, useRef } from "react";
import Header from "@/components/Header";
import styles from "./page.module.css";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: any[];
}

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Check config on mount
  useEffect(() => {
    
    // Set initial greeting message
    setMessages([
      {
        id: "welcome",
        role: "assistant",
        content: `📘 **Bem-vindo! Sou o Politómetro**, um assistente especializado em programas eleitorais portugueses (sem discriminação de partidos) e em alguns documentos importantes, como a Constituição Portuguesa e Orçamentos do Estado.

📄 Todas as respostas que dou baseiam-se exclusivamente nos documentos oficiais desses programas, e, quando pertinente, na Constituição da República Portuguesa.

Podes perguntar-me sobre posições de partidos, comparações entre anos ou temas específicos — e eu indico-te sempre o ano e o contexto político das propostas.

⚠️ Como qualquer inteligência artificial, o Politómetro pode fornecer dados falsos, porém é sempre citada a fonte dos dados apresentadas e nunca é acrescentada nenhuma opinião nem mantida nenhuma afiliação partidária. 

⚠️ O site fornece um número de respostas limitadas por dia.

🗳️ **O que gostarias de saber?**`
      }
    ]);
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages]);



  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMessageId = Date.now().toString();
    const userMessageText = input.trim();
    setInput("");
    
    const newMessages: Message[] = [
      ...messages,
      { id: userMessageId, role: "user", content: userMessageText }
    ];
    
    setMessages(newMessages);
    setIsLoading(true);

    const assistantMessageId = (Date.now() + 1).toString();
    setMessages((prev) => [
      ...prev,
      { id: assistantMessageId, role: "assistant", content: "" }
    ]);

    try {
      // Call Next.js Chat API
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          messages: newMessages.map(m => ({ role: m.role, content: m.content })).slice(1) // omit welcome message
        })
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || "Ocorreu um erro no chat.");
      }

      // Read sources from header
      const encodedSources = response.headers.get("X-Sources");
      let sources: any[] = [];
      if (encodedSources) {
        try {
          sources = JSON.parse(decodeURIComponent(encodedSources));
        } catch (e) {
          console.error("Failed to parse sources header:", e);
        }
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      let done = false;
      let accumulatedContent = "";

      if (!reader) {
        throw new Error("Não foi possível estabelecer ligação de streaming.");
      }

      while (!done) {
        const { value, done: readerDone } = await reader.read();
        done = readerDone;
        
        if (value) {
          const chunk = decoder.decode(value, { stream: !done });
          
          // Parse SSE stream format
          const lines = chunk.split("\n");
          for (const line of lines) {
            if (line.startsWith("data: ")) {
              const dataStr = line.slice(6).trim();
              if (dataStr === "[DONE]") {
                done = true;
                break;
              }
              try {
                const dataJson = JSON.parse(dataStr);
                const textChunk = dataJson.choices?.[0]?.delta?.content || "";
                accumulatedContent += textChunk;
                
                setMessages((prev) =>
                  prev.map((msg) =>
                    msg.id === assistantMessageId
                      ? { ...msg, content: accumulatedContent, sources }
                      : msg
                  )
                );
              } catch (e) {
                // Ignore non-JSON chunks
              }
            }
          }
        }
      }
    } catch (err: any) {
      console.error(err);
      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === assistantMessageId
            ? {
                ...msg,
                content: `❌ **Erro**: ${err.message || "Não foi possível ligar à API. Por favor, verifica as tuas chaves de API nas Definições."}`
              }
            : msg
        )
      );
    } finally {
      setIsLoading(false);
    }
  };

  // Simple custom renderer to parse markdown highlights/bullets into HTML
  const renderMarkdown = (text: string) => {
    const lines = text.split("\n");
    return lines.map((line, idx) => {
      let content = line;
      
      // Parse strong tags **text** -> <strong>text</strong>
      content = content.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
      
      // Parse inline code `text` -> <code>text</code>
      content = content.replace(/`(.*?)`/g, "<code>$1</code>");

      // Bolding lists or titles
      if (content.startsWith("### ")) {
        return <h3 key={idx} className={styles.mdH3}>{content.replace("### ", "")}</h3>;
      }
      if (content.startsWith("## ")) {
        return <h2 key={idx} className={styles.mdH2}>{content.replace("## ", "")}</h2>;
      }
      if (content.startsWith("🗳️ ")) {
        return <p key={idx} className={styles.mdParagraph} dangerouslySetInnerHTML={{ __html: content }}></p>;
      }
      
      // Lists
      if (content.startsWith("- ") || content.startsWith("* ")) {
        const bulletContent = content.slice(2);
        return (
          <ul key={idx} className={styles.mdUl}>
            <li dangerouslySetInnerHTML={{ __html: bulletContent }}></li>
          </ul>
        );
      }

      // Default paragraph
      return (
        <p 
          key={idx} 
          className={content.trim() === "" ? styles.mdSpacing : styles.mdParagraph}
          dangerouslySetInnerHTML={{ __html: content || "&nbsp;" }}
        />
      );
    });
  };

  return (
    <div className={styles.container}>
      <Header />
      
      <main className={styles.main}>
        <div className={styles.bannerContainer}>
          <img src="/banner.jpg" alt="Politómetro - O teu voto. A tua voz. A tua informação." className={styles.bannerImage} />
        </div>

        <div className={`${styles.chatBox} glass`}>
          <div className={styles.messageList}>
            {messages.map((msg) => (
              <div 
                key={msg.id} 
                className={`${styles.messageWrapper} ${msg.role === "user" ? styles.userWrapper : styles.assistantWrapper}`}
              >
                <div className={styles.avatar}>
                  {msg.role === "user" ? "👤" : "🗳️"}
                </div>
                <div className={`${styles.messageBubble} ${msg.role === "user" ? styles.userBubble : styles.assistantBubble}`}>
                  <div className={styles.messageContent}>
                    {renderMarkdown(msg.content)}
                  </div>
                  
                  
                </div>
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>

          <form onSubmit={handleSend} className={styles.inputArea}>
            <input
              type="text"
              placeholder={isLoading ? "A aguardar resposta..." : "Pergunta sobre os programas eleitorais..."}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={isLoading}
              className={styles.input}
            />
            <button 
              type="submit" 
              disabled={isLoading || !input.trim()} 
              className={styles.sendBtn}
            >
              {isLoading ? "..." : "Enviar"}
            </button>
          </form>
        </div>
      </main>
    </div>
  );
}
