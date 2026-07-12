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
const WELCOME_CONTENT = `📘 **Bem-vindo ao Politómetro!** Sou um assistente neutro especializado em programas eleitorais portugueses, na Constituição da República e em Orçamentos do Estado.

📄 As respostas baseiam-se estritamente em documentos oficiais, indicando sempre a fonte, o ano e o contexto político das propostas.

⚠️ Como qualquer IA, posso cometer erros, pelo que as fontes originais são sempre citadas para verificação. O limite de utilização é diário.

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
  const [isMobile, setIsMobile] = useState(false);
  const [isScrolled, setIsScrolled] = useState(false);
  
  const messageListRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleResize = () => {
      setIsMobile(window.innerWidth < 640);
    };
    handleResize();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);



  // Hydrate conversation from sessionStorage on client-side mount
  useEffect(() => {
    if (typeof window !== "undefined") {
      const savedMap = sessionStorage.getItem("politometro_chat_map");
      const savedActiveId = sessionStorage.getItem("politometro_chat_active_id");
      if (savedMap && savedActiveId) {
        try {
          const parsedMap = JSON.parse(savedMap);
          if (parsedMap[welcomeMsgId]) {
            parsedMap[welcomeMsgId].content = WELCOME_CONTENT;
          }
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

  // Scroll listener to toggle top shadow fade effect when messages go out of view
  useEffect(() => {
    const list = messageListRef.current;
    if (!list) return;

    const handleScroll = () => {
      setIsScrolled(list.scrollTop > 5);
    };

    list.addEventListener("scroll", handleScroll);
    // Initial check
    handleScroll();

    return () => {
      list.removeEventListener("scroll", handleScroll);
    };
  }, [activePath]);

  const scrollToBottom = () => {
    const container = messageListRef.current;
    if (container) {
      container.scrollTop = container.scrollHeight;
    }
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
    
    // Retrieve or generate unique client identifier for rate limiting
    let clientId = "anonymous";
    try {
      let storedId = localStorage.getItem("politometro_client_id");
      if (!storedId) {
        storedId = Math.random().toString(36).substring(2, 15) + Math.random().toString(36).substring(2, 15);
        localStorage.setItem("politometro_client_id", storedId);
      }
      clientId = storedId;
    } catch (e) {
      console.warn("localStorage not available:", e);
    }
    
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Client-ID": clientId
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
        <div className={`${styles.chatBox} ${isScrolled ? styles.scrolled : ""} glass`}>
          <div ref={messageListRef} className={styles.messageList}>
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
                  <div className={styles.bubbleContainer} style={{ alignItems: msg.role === "user" ? "flex-end" : "flex-start" }}>
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
                        <div className={styles.messageContent}>
                          {renderMarkdown(msg.content)}
                        </div>
                      )}
                    </div>
                    
                    {!isEditing && (
                      <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", width: "100%", justifyContent: "flex-end", marginTop: "2px" }}>
                        {isBranch && (
                          <div className={styles.branchNav} style={{ margin: 0 }}>
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
                        
                        <div className={styles.bubbleActions} style={{ margin: 0 }}>
                          <button 
                            className={styles.actionIconBtn}
                            type="button"
                            onClick={() => handleCopy(msg.content, msg.id)}
                            title={copiedMessageId === msg.id ? "Copiado!" : "Copiar texto"}
                          >
                            {copiedMessageId === msg.id ? (
                              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--color-secondary)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
                            ) : (
                              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                            )}
                          </button>
                          
                          {msg.role === "user" && (
                            <button 
                              className={styles.actionIconBtn}
                              type="button"
                              onClick={() => {
                                setEditingMessageId(msg.id);
                                setEditInput(msg.content);
                              }}
                              disabled={isLoading}
                              title="Editar pergunta"
                            >
                              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4z"></path></svg>
                            </button>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          <form onSubmit={handleSend} className={styles.inputArea}>
            <input
              type="text"
              placeholder={isLoading ? "A aguardar..." : "Pergunta sobre os programas eleitorais."}
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
              {isLoading ? (
                "..."
              ) : isMobile ? (
                <svg 
                  xmlns="http://www.w3.org/2000/svg" 
                  width="18" 
                  height="18" 
                  viewBox="0 0 24 24" 
                  fill="none" 
                  stroke="currentColor" 
                  strokeWidth="2.5" 
                  strokeLinecap="round" 
                  strokeLinejoin="round"
                >
                  <line x1="12" y1="19" x2="12" y2="5"></line>
                  <polyline points="5 12 12 5 19 12"></polyline>
                </svg>
              ) : (
                "Enviar"
              )}
            </button>
          </form>
        </div>
      </main>
    </div>
  );
}
