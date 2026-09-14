"use client";
// Trigger Vercel build after settings update

import {
  useState,
  useEffect,
  useRef,
  useMemo,
} from "react";
import Header from "@/components/Header";
import PoliticalIntelligencePanel from "@/components/PoliticalIntelligencePanel";
import { ChatMarkdown } from "@/components/ChatMarkdown";
import { conversationImagesPdf, downloadBlob } from "@/lib/conversationImages";
import {
  pickStarterSuggestions,
  fallbackSuggestions,
} from "@/lib/suggestionPool";
import styles from "./page.module.css";

interface MessageNode {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: unknown[];
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

function readableError(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Não foi possível ligar à API. Por favor, tenta novamente.";
}

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
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [shareLink, setShareLink] = useState("");
  const [shareState, setShareState] = useState<"idle" | "creating" | "done" | "error">("idle");
  const [shareError, setShareError] = useState("");
  const [sharePdfHint, setSharePdfHint] = useState("");
  const [sharePdfBusy, setSharePdfBusy] = useState(false);
  const [copiedShareLink, setCopiedShareLink] = useState(false);

  const messageListRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleResize = () => {
      setIsMobile(window.innerWidth < 768);
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
    // Sugestões iniciais: conjunto curado, variando entre visitas.
    setSuggestions(pickStarterSuggestions(3));
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

  const getClientId = (): string => {
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
    return clientId;
  };

  // Sugestões de acompanhamento: depois de cada resposta do bot, pede 3
  // perguntas curtas relacionadas com a resposta; se a geração falhar, usa a
  // reserva local por tema.
  const refreshSuggestions = async (question: string, answer: string) => {
    setSuggestionsLoading(true);
    try {
      const response = await fetch("/api/chat/suggestions", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Client-ID": getClientId() },
        body: JSON.stringify({ lastQuestion: question, lastAnswer: answer }),
      });
      const payload = (await response.json()) as { suggestions?: string[] };
      const list = (payload.suggestions ?? []).filter((item) => typeof item === "string" && item.trim());
      setSuggestions(list.length === 3 ? list : fallbackSuggestions(answer));
    } catch {
      setSuggestions(fallbackSuggestions(answer));
    } finally {
      setSuggestionsLoading(false);
    }
  };

  const createShare = async () => {
    setShareState("creating");
    setShareError("");
    setShareLink("");
    const activeMessages = activePath
      .filter((msg) => msg.role === "user" || msg.content.trim() !== "")
      .map((msg) => ({ role: msg.role, content: msg.content }));
    try {
      const response = await fetch("/api/share", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Client-ID": getClientId() },
        body: JSON.stringify({ messages: activeMessages }),
      });
      const payload = (await response.json()) as { id?: string; error?: string };
      if (!response.ok || !payload.id) {
        throw new Error(payload.error || "Não foi possível criar a partilha.");
      }
      const url = `${window.location.origin}/partilhar/${payload.id}`;
      setShareLink(url);
      setShareState("done");
    } catch (error: unknown) {
      setShareState("error");
      setShareError(error instanceof Error ? error.message : "Falha ao partilhar a conversa.");
    }
  };

  const copyShareLink = async () => {
    if (!shareLink) return;
    try {
      await navigator.clipboard.writeText(shareLink);
      setCopiedShareLink(true);
      setTimeout(() => setCopiedShareLink(false), 2000);
    } catch {
      console.error("Erro ao copiar link de partilha");
    }
  };

  const downloadConversationPdf = async () => {
    if (sharePdfBusy) return;
    setSharePdfBusy(true);
    setSharePdfHint("");
    const activeMessages = activePath
      .filter((msg) => msg.role === "user" || msg.content.trim() !== "")
      .map((msg) => ({ role: msg.role, content: msg.content }));
    try {
      const { blob, pageCount } = await conversationImagesPdf(activeMessages);
      downloadBlob(blob, `politometro-conversa-${Date.now()}.pdf`);
      setSharePdfHint(`PDF gerado com ${pageCount} imagem(ns) da conversa.`);
    } catch (error: unknown) {
      setSharePdfHint(error instanceof Error ? error.message : "Falha ao gerar o PDF.");
    } finally {
      setSharePdfBusy(false);
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

  const handleSend = async (e?: React.FormEvent, overrideText?: string) => {
    if (e) e.preventDefault();
    const userMessageText = (overrideText ?? input).trim();
    if (!userMessageText || isLoading) return;
    setInput("");
    setSuggestions([]);

    const userMessageId = Date.now().toString();
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
      let sources: unknown[] = [];
      if (encodedSources) {
        try {
          sources = JSON.parse(decodeURIComponent(encodedSources));
        } catch {
          console.error("Failed to parse sources header.");
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
              } catch {}
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
            } catch {}
          }
        }
      }

      if (accumulatedContent.trim()) {
        void refreshSuggestions(userMessageText, accumulatedContent);
      }
    } catch (err: unknown) {
      console.error(err);
      setMessagesMap(prev => ({
        ...prev,
        [assistantMessageId]: {
          ...prev[assistantMessageId],
          content: `❌ **Erro**: ${readableError(err)}`
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
      let sources: unknown[] = [];
      if (encodedSources) {
        try {
          sources = JSON.parse(decodeURIComponent(encodedSources));
        } catch {
          console.error("Failed to parse sources header.");
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
              } catch {}
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
            } catch {}
          }
        }
      }

      if (accumulatedContent.trim()) {
        void refreshSuggestions(newText.trim(), accumulatedContent);
      }
    } catch (err: unknown) {
      console.error(err);
      setMessagesMap(prev => ({
        ...prev,
        [assistantMessageId]: {
          ...prev[assistantMessageId],
          content: `❌ **Erro**: ${readableError(err)}`
        }
      }));
    } finally {
      setIsLoading(false);
    }
  };

  const handleClear = () => {
    if (window.confirm("Tens a certeza que desejas limpar o histórico da conversa?")) {
      setMessagesMap(initialMessagesMap);
      setActiveMessageId(welcomeMsgId);
      setEditingMessageId(null);
      if (typeof window !== "undefined") {
        sessionStorage.removeItem("politometro_chat_map");
        sessionStorage.removeItem("politometro_chat_active_id");
      }
    }
  };

  const canClear = Object.keys(messagesMap).length > 1;

  return (
    <div className={styles.container}>
      <Header />
      <PoliticalIntelligencePanel />
      
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
                    {msg.role === "user" ? (
                      "👤"
                    ) : (
                      <img 
                        src="/logo-chat.jpg" 
                        alt="Politómetro" 
                        className={styles.avatarImg} 
                      />
                    )}
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
                          {msg.role === "assistant" && msg.content.trim() === "" && isLoading ? (
                            <div className={styles.typingIndicator}>
                              <span></span>
                              <span></span>
                              <span></span>
                            </div>
                          ) : (
                            <ChatMarkdown
                              content={msg.content}
                              classes={{
                                mdH2: styles.mdH2,
                                mdH3: styles.mdH3,
                                mdParagraph: styles.mdParagraph,
                                mdUl: styles.mdUl,
                                mdSpacing: styles.mdSpacing,
                              }}
                            />
                          )}
                        </div>
                      )}
                    </div>
                    
                    {!isEditing && !(msg.role === "assistant" && msg.content.trim() === "" && isLoading) && (
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

          {!isLoading && input.trim() === "" && (suggestions.length > 0 || suggestionsLoading) && (
            <div className={styles.suggestionRow} role="group" aria-label="Sugestões do que perguntar">
              {suggestionsLoading && suggestions.length === 0 ? (
                <span className={styles.suggestionsHint}>A preparar sugestões…</span>
              ) : (
                <>
                  {suggestions.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      className={styles.suggestionChip}
                      onClick={() => handleSend(undefined, suggestion)}
                    >
                      {suggestion}
                    </button>
                  ))}
                  <button
                    type="button"
                    className={styles.suggestionRefresh}
                    title="Mostrar outras sugestões"
                    aria-label="Mostrar outras sugestões"
                    onClick={() => {
                      const leaf = activePath[activePath.length - 1];
                      if (leaf && leaf.role === "assistant" && leaf.content.trim()) {
                        const question = [...activePath].reverse().find((m) => m.role === "user");
                        void refreshSuggestions(question?.content ?? "", leaf.content);
                      } else {
                        setSuggestions(pickStarterSuggestions(3));
                      }
                    }}
                  >
                    ↻
                  </button>
                </>
              )}
            </div>
          )}

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
              type="button"
              onClick={() => {
                setShareOpen(true);
                setSharePdfHint("");
              }}
              disabled={isLoading || !canClear}
              className={styles.clearBtn}
              title="Partilhar conversa"
            >
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
                <circle cx="18" cy="5" r="3"></circle>
                <circle cx="6" cy="12" r="3"></circle>
                <circle cx="18" cy="19" r="3"></circle>
                <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line>
                <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line>
              </svg>
            </button>
            <button
              type="button"
              onClick={handleClear}
              disabled={isLoading || !canClear}
              className={styles.clearBtn}
              title="Limpar histórico da conversa"
            >
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
                <polyline points="3 6 5 6 21 6"></polyline>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                <line x1="10" y1="11" x2="10" y2="17"></line>
                <line x1="14" y1="11" x2="14" y2="17"></line>
              </svg>
            </button>
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

          {shareOpen && (
            <div
              className={styles.shareOverlay}
              role="dialog"
              aria-modal="true"
              aria-label="Partilhar conversa"
              onClick={(event) => {
                if (event.target === event.currentTarget) setShareOpen(false);
              }}
            >
              <div className={`${styles.shareModal} glass`}>
                <div className={styles.shareModalHeader}>
                  <strong>Partilhar conversa</strong>
                  <button
                    type="button"
                    className={styles.shareClose}
                    onClick={() => setShareOpen(false)}
                    aria-label="Fechar"
                  >
                    ×
                  </button>
                </div>

                <p className={styles.shareDescription}>
                  Gera um link com um identificador único que abre esta conversa
                  no próprio site, ou guarda a conversa como imagens num PDF.
                </p>

                <div className={styles.shareActions}>
                  <button
                    type="button"
                    className={styles.sharePrimary}
                    onClick={createShare}
                    disabled={shareState === "creating"}
                  >
                    {shareState === "creating" ? "A gerar link…" : "Gerar link de partilha"}
                  </button>
                  <button
                    type="button"
                    className={styles.shareSecondary}
                    onClick={downloadConversationPdf}
                    disabled={sharePdfBusy}
                  >
                    {sharePdfBusy ? "A gerar imagens…" : "Guardar como imagens (PDF)"}
                  </button>
                </div>

                {shareLink && (
                  <div className={styles.shareLinkRow}>
                    <input
                      type="text"
                      readOnly
                      value={shareLink}
                      className={styles.shareLinkInput}
                      onFocus={(event) => event.currentTarget.select()}
                    />
                    <button type="button" className={styles.shareSecondary} onClick={copyShareLink}>
                      {copiedShareLink ? "Copiado!" : "Copiar"}
                    </button>
                  </div>
                )}
                {shareState === "error" && shareError && (
                  <p className={styles.shareError}>{shareError}</p>
                )}
                {sharePdfHint && <p className={styles.shareNotice}>{sharePdfHint}</p>}

                <small className={styles.shareFootnote}>
                  As imagens são geradas com altura ajustada ao conteúdo: nenhuma
                  mensagem fica cortada a meio e não há espaços vazios grandes.
                </small>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
