"use client";
// Trigger Vercel build after settings update

import { useState, useEffect, useRef, useMemo } from "react";
import Header from "@/components/Header";
import styles from "./page.module.css";

interface MessageNode {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: any[];
  parentId: string | null;
  children: string[];
}

const welcomeMsgId = "welcome";
const WELCOME_CONTENT = `📘 **Bem-vindo! Sou o Politómetro**, um assistente especializado em programas eleitorais portugueses (sem discriminação de partidos) e em alguns documentos importantes, como a Constituição Portuguesa e Orçamentos do Estado.

📄 Todas as respostas que dou baseiam-se exclusivamente nos documentos oficiais desses programas, e, quando pertinente, na Constituição da República Portuguesa.

Podes perguntar-me sobre posições de partidos, comparações entre anos ou temas específicos — e eu indico-te sempre o ano e o contexto político das propostas.

⚠️ Como qualquer inteligência artificial, o Politómetro pode fornecer dados falsos, porém é sempre citada a fonte dos dados apresentadas e nunca é acrescentada nenhuma opinião nem mantida nenhuma afiliação partidária. 

⚠️ O site fornece um número de respostas limitadas por dia.

🗳️ **O que gostarias de saber?**`;

const initialMessagesMap: Record<string, MessageNode> = {
  [welcomeMsgId]: {
    id: welcomeMsgId,
    role: "assistant",
    content: WELCOME_CONTENT,
    parentId: null,
    children: []
  }
};

const getLeafNodeId = (nodeId: string, map: Record<string, MessageNode>): string => {
  let currentId = nodeId;
  while (map[currentId]?.children && map[currentId].children.length > 0) {
    currentId = map[currentId].children[0];
  }
  return currentId;
};

export default function Home() {
  const [messagesMap, setMessagesMap] = useState<Record<string, MessageNode>>(initialMessagesMap);
  const [activeMessageId, setActiveMessageId] = useState<string | null>(welcomeMsgId);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [editInput, setEditInput] = useState("");
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Hydrate conversation from sessionStorage on client-side mount
  useEffect(() => {
    if (typeof window !== "undefined") {
      const savedMap = sessionStorage.getItem("politometro_chat_map");
      const savedActiveId = sessionStorage.getItem("politometro_chat_active_id");
      if (savedMap && savedActiveId) {
        try {
          const parsedMap = JSON.parse(savedMap);
          if (parsedMap[savedActiveId]) {
            setMessagesMap(parsedMap);
            setActiveMessageId(savedActiveId);
          }
        } catch (e) {
          console.error("Failed to parse saved chat state:", e);
        }
      }
    }
  }, []);

  // Persist conversation to sessionStorage when it changes
  useEffect(() => {
    if (typeof window !== "undefined") {
      sessionStorage.setItem("politometro_chat_map", JSON.stringify(messagesMap));
    }
  }, [messagesMap]);

  useEffect(() => {
    if (typeof window !== "undefined" && activeMessageId) {
      sessionStorage.setItem("politometro_chat_active_id", activeMessageId);
    }
  }, [activeMessageId]);

  const activePath = useMemo(() => {
    if (!activeMessageId || !messagesMap[activeMessageId]) return [];
    const path: MessageNode[] = [];
    let currentId: string | null = activeMessageId;
    while (currentId) {
      const node: MessageNode | undefined = messagesMap[currentId];
      if (!node) break;
      path.unshift(node);
      currentId = node.parentId;
    }
    return path;
  }, [activeMessageId, messagesMap]);

  useEffect(() => {
    scrollToBottom();
  }, [activePath]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  const handleCopy = (text: string, msgId: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopiedMessageId(msgId);
      setTimeout(() => {
        setCopiedMessageId(null);
      }, 2000);
    }).catch(err => {
      console.error("Erro ao copiar texto:", err);
    });
  };

  const handleNavigateSibling = (nodeId: string, direction: number) => {
    const node: MessageNode | undefined = messagesMap[nodeId];
    if (!node || !node.parentId) return;
    const parentNode: MessageNode | undefined = messagesMap[node.parentId];
    if (!parentNode) return;
    const siblings = parentNode.children;
    const currentIndex = siblings.indexOf(nodeId);
    const nextIndex = currentIndex + direction;
    if (nextIndex >= 0 && nextIndex < siblings.length) {
      const siblingId = siblings[nextIndex];
      const leafId = getLeafNodeId(siblingId, messagesMap);
      setActiveMessageId(leafId);
    }
  };

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMessageId = Date.now().toString();
    const userMessageText = input.trim();
    setInput("");
    
    const currentParentId = activeMessageId;
    
    const newUserNode: MessageNode = {
      id: userMessageId,
      role: "user",
      content: userMessageText,
      parentId: currentParentId,
      children: []
    };
    
    const assistantMessageId = (Date.now() + 1).toString();
    const newAssistantNode: MessageNode = {
      id: assistantMessageId,
      role: "assistant",
      content: "",
      parentId: userMessageId,
      children: []
    };
    
    newUserNode.children.push(assistantMessageId);
    
    setMessagesMap(prev => {
      const nextMap = { ...prev };
      nextMap[userMessageId] = newUserNode;
      nextMap[assistantMessageId] = newAssistantNode;
      if (currentParentId && nextMap[currentParentId]) {
        nextMap[currentParentId] = {
          ...nextMap[currentParentId],
          children: [...nextMap[currentParentId].children, userMessageId]
        };
      }
      return nextMap;
    });
    
    setActiveMessageId(assistantMessageId);
    setIsLoading(true);

    const pathUpToNewUser: MessageNode[] = [];
    let currentId: string | null = currentParentId;
    while (currentId) {
      const node: MessageNode | undefined = messagesMap[currentId];
      if (!node) break;
      pathUpToNewUser.unshift(node);
      currentId = node.parentId;
    }
    pathUpToNewUser.push(newUserNode);
    
    const chatHistory = pathUpToNewUser.slice(1).map(node => ({
      role: node.role,
      content: node.content
    }));

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          messages: chatHistory
        })
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || "Ocorreu um erro no chat.");
      }

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
      let buffer = "";

      if (!reader) {
        throw new Error("Não foi possível estabelecer ligação de streaming.");
      }

      while (!done) {
        const { value, done: readerDone } = await reader.read();
        done = readerDone;
        
        if (value) {
          buffer += decoder.decode(value, { stream: !done });
          
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";
          
          for (const line of lines) {
            const trimmedLine = line.trim();
            if (!trimmedLine) continue;

            if (trimmedLine.startsWith("data: ")) {
              const dataStr = trimmedLine.slice(6).trim();
              if (dataStr === "[DONE]") {
                done = true;
                break;
              }
              try {
                const dataJson = JSON.parse(dataStr);
                const textChunk = dataJson.choices?.[0]?.delta?.content || "";
                accumulatedContent += textChunk;
                
                setMessagesMap(prev => ({
                  ...prev,
                  [assistantMessageId]: {
                    ...prev[assistantMessageId],
                    content: accumulatedContent,
                    sources
                  }
                }));
              } catch (e) {}
            }
          }
        }
      }

      if (buffer.trim()) {
        const trimmedLine = buffer.trim();
        if (trimmedLine.startsWith("data: ")) {
          const dataStr = trimmedLine.slice(6).trim();
          if (dataStr !== "[DONE]") {
            try {
              const dataJson = JSON.parse(dataStr);
              const textChunk = dataJson.choices?.[0]?.delta?.content || "";
              accumulatedContent += textChunk;
              
              setMessagesMap(prev => ({
                ...prev,
                [assistantMessageId]: {
                  ...prev[assistantMessageId],
                  content: accumulatedContent,
                  sources
                }
              }));
            } catch (e) {}
          }
        }
      }
    } catch (err: any) {
      console.error(err);
      setMessagesMap(prev => ({
        ...prev,
        [assistantMessageId]: {
          ...prev[assistantMessageId],
          content: `❌ **Erro**: ${err.message || "Não foi possível ligar à API. Por favor, tenta novamente."}`
        }
      }));
    } finally {
      setIsLoading(false);
    }
  };

  const handleEditSubmit = async (nodeId: string, newText: string) => {
    if (!newText.trim() || isLoading) return;
    
    const originalNode: MessageNode | undefined = messagesMap[nodeId];
    if (!originalNode) return;
    
    if (newText.trim() === originalNode.content.trim()) {
      setEditingMessageId(null);
      return;
    }
    
    const userMessageId = "edit-" + Date.now().toString();
    const parentId = originalNode.parentId;
    
    const newUserNode: MessageNode = {
      id: userMessageId,
      role: "user",
      content: newText.trim(),
      parentId,
      children: []
    };
    
    const assistantMessageId = "assistant-edit-" + (Date.now() + 1).toString();
    const newAssistantNode: MessageNode = {
      id: assistantMessageId,
      role: "assistant",
      content: "",
      parentId: userMessageId,
      children: []
    };
    
    newUserNode.children.push(assistantMessageId);
    
    setMessagesMap(prev => {
      const nextMap = { ...prev };
      nextMap[userMessageId] = newUserNode;
      nextMap[assistantMessageId] = newAssistantNode;
      if (parentId && nextMap[parentId]) {
        nextMap[parentId] = {
          ...nextMap[parentId],
          children: [...nextMap[parentId].children, userMessageId]
        };
      }
      return nextMap;
    });
    
    setEditingMessageId(null);
    setActiveMessageId(assistantMessageId);
    setIsLoading(true);
    
    const pathUpToNewUser: MessageNode[] = [];
    let currentId: string | null = parentId;
    while (currentId) {
      const node: MessageNode | undefined = messagesMap[currentId] || (parentId && currentId === parentId ? messagesMap[parentId] : undefined);
      if (!node) break;
      pathUpToNewUser.unshift(node);
      currentId = node.parentId;
    }
    pathUpToNewUser.push(newUserNode);
    
    const chatHistory = pathUpToNewUser.slice(1).map(node => ({
      role: node.role,
      content: node.content
    }));
    
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          messages: chatHistory
        })
      });
      
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || "Ocorreu um erro no chat.");
      }
      
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
      let buffer = "";
      
      if (!reader) {
        throw new Error("Não foi possível estabelecer ligação de streaming.");
      }
      
      while (!done) {
        const { value, done: readerDone } = await reader.read();
        done = readerDone;
        
        if (value) {
          buffer += decoder.decode(value, { stream: !done });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";
          
          for (const line of lines) {
            const trimmedLine = line.trim();
            if (!trimmedLine) continue;
            
            if (trimmedLine.startsWith("data: ")) {
              const dataStr = trimmedLine.slice(6).trim();
              if (dataStr === "[DONE]") {
                done = true;
                break;
              }
              try {
                const dataJson = JSON.parse(dataStr);
                const textChunk = dataJson.choices?.[0]?.delta?.content || "";
                accumulatedContent += textChunk;
                
                setMessagesMap(prev => ({
                  ...prev,
                  [assistantMessageId]: {
                    ...prev[assistantMessageId],
                    content: accumulatedContent,
                    sources
                  }
                }));
              } catch (e) {}
            }
          }
        }
      }
      
      if (buffer.trim()) {
        const trimmedLine = buffer.trim();
        if (trimmedLine.startsWith("data: ")) {
          const dataStr = trimmedLine.slice(6).trim();
          if (dataStr !== "[DONE]") {
            try {
              const dataJson = JSON.parse(dataStr);
              const textChunk = dataJson.choices?.[0]?.delta?.content || "";
              accumulatedContent += textChunk;
              
              setMessagesMap(prev => ({
                ...prev,
                [assistantMessageId]: {
                  ...prev[assistantMessageId],
                  content: accumulatedContent,
                  sources
                }
              }));
            } catch (e) {}
          }
        }
      }
    } catch (err: any) {
      console.error(err);
      setMessagesMap(prev => ({
        ...prev,
        [assistantMessageId]: {
          ...prev[assistantMessageId],
          content: `❌ **Erro**: ${err.message || "Não foi possível ligar à API. Por favor, tenta novamente."}`
        }
      }));
    } finally {
      setIsLoading(false);
    }
  };

  const renderMarkdown = (text: string) => {
    const lines = text.split("\n");
    return lines.map((line, idx) => {
      let content = line;
      
      content = content.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
      content = content.replace(/`(.*?)`/g, "<code>$1</code>");

      if (content.startsWith("### ")) {
        return <h3 key={idx} className={styles.mdH3}>{content.replace("### ", "")}</h3>;
      }
      if (content.startsWith("## ")) {
        return <h2 key={idx} className={styles.mdH2}>{content.replace("## ", "")}</h2>;
      }
      if (content.startsWith("🗳️ ")) {
        return <p key={idx} className={styles.mdParagraph} dangerouslySetInnerHTML={{ __html: content }}></p>;
      }
      
      if (content.startsWith("- ") || content.startsWith("* ")) {
        const bulletContent = content.slice(2);
        return (
          <ul key={idx} className={styles.mdUl}>
            <li dangerouslySetInnerHTML={{ __html: bulletContent }}></li>
          </ul>
        );
      }

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
        <div className={`${styles.chatBox} glass`}>
          <div className={styles.messageList}>
            {activePath.map((msg) => {
              const parentNode: MessageNode | undefined = msg.parentId ? messagesMap[msg.parentId] : undefined;
              const siblings = parentNode ? parentNode.children : [];
              const isBranch = siblings.length > 1;
              const siblingIndex = siblings.indexOf(msg.id);
              
              const isEditing = editingMessageId === msg.id;

              return (
                <div 
                  key={msg.id} 
                  className={`${styles.messageWrapper} ${msg.role === "user" ? styles.userWrapper : styles.assistantWrapper}`}
                >
                  <div className={styles.avatar}>
                    {msg.role === "user" ? "👤" : "🗳️"}
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "0.25rem", maxWidth: "calc(100% - 50px)" }}>
                    <div className={`${styles.messageBubble} ${msg.role === "user" ? styles.userBubble : styles.assistantBubble}`}>
                      {isEditing ? (
                        <div className={styles.editContainer}>
                          <textarea
                            className={styles.editTextarea}
                            value={editInput}
                            onChange={(e) => setEditInput(e.target.value)}
                            rows={3}
                            disabled={isLoading}
                          />
                          <div className={styles.editButtons}>
                            <button 
                              className={styles.cancelBtn}
                              type="button"
                              onClick={() => setEditingMessageId(null)}
                              disabled={isLoading}
                            >
                              Cancelar
                            </button>
                            <button 
                              className={styles.saveBtn}
                              type="button"
                              onClick={() => handleEditSubmit(msg.id, editInput)}
                              disabled={isLoading || !editInput.trim()}
                            >
                              Submeter
                            </button>
                          </div>
                        </div>
                      ) : (
                        <>
                          <div className={styles.messageContent}>
                            {renderMarkdown(msg.content)}
                          </div>

                          {msg.role === "assistant" && msg.sources && msg.sources.length > 0 && (
                            <div className={styles.sourcesContainer}>
                              <span className={styles.sourcesTitle}>Fontes Recuperadas:</span>
                              <div className={styles.sourcesList}>
                                {msg.sources.map((src: any, sIdx: number) => (
                                  <div 
                                    key={sIdx} 
                                    className={styles.sourceTag}
                                    title={`${src.filename || "Documento"}\nPontuação RAG: ${(src.score * 100).toFixed(1)}%`}
                                  >
                                    <span className={styles.sourceTagParty}>{src.party}</span>
                                    {src.category && <span className={styles.sourceTagCategory}>{src.category}</span>}
                                    <span>{src.year}</span>
                                    <span className={styles.sourceTagPage}>Pág. {src.page}</span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                          
                          <div className={styles.bubbleActions}>
                            <button 
                              className={styles.actionBtn}
                              type="button"
                              onClick={() => handleCopy(msg.content, msg.id)}
                              title="Copiar texto"
                            >
                              {copiedMessageId === msg.id ? "✓ Copiado!" : "📋 Copiar"}
                            </button>
                            
                            {msg.role === "user" && (
                              <button 
                                className={styles.actionBtn}
                                type="button"
                                onClick={() => {
                                  setEditingMessageId(msg.id);
                                  setEditInput(msg.content);
                                }}
                                disabled={isLoading}
                                title="Editar pergunta"
                              >
                                ✏️ Editar
                              </button>
                            )}
                          </div>
                        </>
                      )}
                    </div>
                    
                    {isBranch && !isEditing && (
                      <div className={`${styles.branchNav} ${msg.role === "user" ? styles.branchNavRight : ""}`}>
                        <button 
                          className={styles.branchBtn}
                          type="button"
                          onClick={() => handleNavigateSibling(msg.id, -1)}
                          disabled={siblingIndex === 0 || isLoading}
                          title="Ramo anterior"
                        >
                          ◀
                        </button>
                        <span>{siblingIndex + 1} / {siblings.length}</span>
                        <button 
                          className={styles.branchBtn}
                          type="button"
                          onClick={() => handleNavigateSibling(msg.id, 1)}
                          disabled={siblingIndex === siblings.length - 1 || isLoading}
                          title="Próximo ramo"
                        >
                          ▶
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
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
