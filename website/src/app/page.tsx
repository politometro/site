"use client";
// Trigger Vercel build after settings update

import { useState, useEffect, useRef, useMemo, type ReactNode } from "react";
import Header from "@/components/Header";
import { stripUnsafeUnicode } from "@/lib/chatSecurity";
import styles from "./page.module.css";

interface MessageNode {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: ChatSource[];
  parentId: string | null;
  children: string[];
}

interface ChatSource {
  party: string;
  year: string;
  category: string;
}

const MAX_STORED_CHAT_NODES = 512;
const MAX_STORED_MESSAGE_CHARACTERS = 12_000;
const MAX_STORED_SOURCE_CHARACTERS = 120;
const SAFE_MESSAGE_ID = /^[A-Za-z0-9_-]{1,128}$/;
const MESSAGE_NODE_KEYS = new Set([
  "id",
  "role",
  "content",
  "sources",
  "parentId",
  "children",
]);
const SOURCE_KEYS = new Set(["party", "year", "category"]);

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isSafeMessageId = (value: string) =>
  SAFE_MESSAGE_ID.test(value) &&
  value !== "__proto__" &&
  value !== "constructor" &&
  value !== "prototype";

const storedText = (value: unknown, maxCharacters: number) => {
  if (typeof value !== "string" || value.length > maxCharacters) {
    return null;
  }
  return stripUnsafeUnicode(value);
};

const validateStoredSource = (value: unknown): ChatSource | null => {
  if (!isRecord(value)) return null;
  const keys = Object.keys(value);
  if (keys.length !== SOURCE_KEYS.size || keys.some(key => !SOURCE_KEYS.has(key))) {
    return null;
  }

  const party = storedText(value.party, MAX_STORED_SOURCE_CHARACTERS);
  const year = storedText(value.year, MAX_STORED_SOURCE_CHARACTERS);
  const category = storedText(value.category, MAX_STORED_SOURCE_CHARACTERS);
  if (party === null || year === null || category === null) return null;

  return { party, year, category };
};

const validateStoredChatMap = (
  value: unknown
): Record<string, MessageNode> | null => {
  if (!isRecord(value)) return null;

  const ids = Object.keys(value);
  if (ids.length === 0 || ids.length > MAX_STORED_CHAT_NODES) return null;

  const validatedMap: Record<string, MessageNode> = Object.create(null);
  for (const id of ids) {
    if (!isSafeMessageId(id)) return null;
    const storedNode = value[id];
    if (!isRecord(storedNode)) return null;

    const nodeKeys = Object.keys(storedNode);
    if (
      nodeKeys.some(key => !MESSAGE_NODE_KEYS.has(key)) ||
      !nodeKeys.includes("id") ||
      !nodeKeys.includes("role") ||
      !nodeKeys.includes("content") ||
      !nodeKeys.includes("parentId") ||
      !nodeKeys.includes("children")
    ) {
      return null;
    }

    if (
      storedNode.id !== id ||
      (storedNode.role !== "user" && storedNode.role !== "assistant") ||
      typeof storedNode.content !== "string" ||
      storedNode.content.length > MAX_STORED_MESSAGE_CHARACTERS ||
      (storedNode.parentId !== null &&
        (typeof storedNode.parentId !== "string" ||
          !isSafeMessageId(storedNode.parentId))) ||
      !Array.isArray(storedNode.children) ||
      storedNode.children.length > MAX_STORED_CHAT_NODES
    ) {
      return null;
    }

    const content = storedText(
      storedNode.content,
      MAX_STORED_MESSAGE_CHARACTERS
    );
    if (content === null) return null;

    const children: string[] = [];
    for (const child of storedNode.children) {
      if (
        typeof child !== "string" ||
        !isSafeMessageId(child) ||
        children.includes(child)
      ) {
        return null;
      }
      children.push(child);
    }

    let sources: ChatSource[] | undefined;
    if ("sources" in storedNode) {
      if (!Array.isArray(storedNode.sources) || storedNode.sources.length > 64) {
        return null;
      }
      sources = [];
      for (const source of storedNode.sources) {
        const validatedSource = validateStoredSource(source);
        if (!validatedSource) return null;
        sources.push(validatedSource);
      }
    }

    validatedMap[id] = {
      id,
      role: storedNode.role,
      content,
      ...(sources ? { sources } : {}),
      parentId: storedNode.parentId,
      children,
    };
  }

  const roots = ids.filter(id => validatedMap[id].parentId === null);
  if (
    roots.length !== 1 ||
    roots[0] !== welcomeMsgId ||
    validatedMap[welcomeMsgId].role !== "assistant"
  ) {
    return null;
  }

  for (const id of ids) {
    const node = validatedMap[id];
    if (node.parentId && !validatedMap[node.parentId]) return null;
    for (const childId of node.children) {
      const child = validatedMap[childId];
      if (!child || child.parentId !== id) return null;
    }
  }

  const visited = new Set<string>();
  const visiting = new Set<string>();
  const visit = (id: string): boolean => {
    if (visiting.has(id)) return false;
    if (visited.has(id)) return true;
    visiting.add(id);
    visited.add(id);
    const valid = validatedMap[id].children.every(visit);
    visiting.delete(id);
    return valid;
  };
  if (!visit(welcomeMsgId) || visited.size !== ids.length) return null;

  return validatedMap;
};

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

const currentTimestamp = () => Date.now();
const createClientId = () =>
  Math.random().toString(36).substring(2, 15) +
  Math.random().toString(36).substring(2, 15);

const isSafeHttpUrl = (value: string) => {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
};

const renderInlineMarkdown = (text: string): ReactNode[] => {
  const nodes: ReactNode[] = [];
  const tokenPattern = /\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\([^\s)]+\)/g;
  let lastIndex = 0;
  let tokenIndex = 0;

  for (const match of text.matchAll(tokenPattern)) {
    const token = match[0];
    const index = match.index ?? 0;
    if (index > lastIndex) {
      nodes.push(text.slice(lastIndex, index));
    }

    if (token.startsWith("**")) {
      nodes.push(
        <strong key={`strong-${tokenIndex}`}>
          {renderInlineMarkdown(token.slice(2, -2))}
        </strong>
      );
    } else if (token.startsWith("`")) {
      nodes.push(
        <code key={`code-${tokenIndex}`}>{token.slice(1, -1)}</code>
      );
    } else {
      const linkMatch = token.match(/^\[([^\]]+)\]\(([^\s)]+)\)$/);
      if (!linkMatch) {
        nodes.push(token);
      } else if (isSafeHttpUrl(linkMatch[2])) {
        nodes.push(
          <a
            key={`link-${tokenIndex}`}
            href={linkMatch[2]}
            target="_blank"
            rel="noopener noreferrer"
          >
            {renderInlineMarkdown(linkMatch[1])}
          </a>
        );
      } else {
        nodes.push(linkMatch[1]);
      }
    }

    lastIndex = index + token.length;
    tokenIndex += 1;
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }

  return nodes;
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
          const parsedValue: unknown = JSON.parse(savedMap);
          const validatedMap = validateStoredChatMap(parsedValue);
          if (validatedMap && validatedMap[savedActiveId]) {
            validatedMap[welcomeMsgId].content = WELCOME_CONTENT;
            // Session storage is an external source being hydrated on mount.
            // eslint-disable-next-line react-hooks/set-state-in-effect
            setMessagesMap(validatedMap);
            setActiveMessageId(savedActiveId);
          }
        } catch {
          console.error("Failed to parse saved chat state.");
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

  const scrollToBottom = () => {
    const container = messageListRef.current;
    if (container) {
      container.scrollTop = container.scrollHeight;
    }
  };

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

    const messageTimestamp = currentTimestamp();
    const userMessageId = messageTimestamp.toString();
    const userMessageText = stripUnsafeUnicode(input).trim();
    if (!userMessageText) return;
    setInput("");
    
    const currentParentId = activeMessageId;
    
    const newUserNode: MessageNode = {
      id: userMessageId,
      role: "user",
      content: userMessageText,
      parentId: currentParentId,
      children: []
    };
    
    const assistantMessageId = (messageTimestamp + 1).toString();
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
    
    const chatHistory = pathUpToNewUser.slice(1).filter(node => node.role === "user").map(node => ({
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
      let sources: ChatSource[] = [];
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
      const accumulatedContent = { current: "" };
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
                accumulatedContent.current += textChunk;
                
                setMessagesMap(prev => ({
                  ...prev,
                  [assistantMessageId]: {
                    ...prev[assistantMessageId],
                    content: accumulatedContent.current,
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
              accumulatedContent.current += textChunk;
              
              setMessagesMap(prev => ({
                ...prev,
                [assistantMessageId]: {
                  ...prev[assistantMessageId],
                  content: accumulatedContent.current,
                  sources
                }
              }));
            } catch {}
          }
        }
      }
    } catch (err: unknown) {
      console.error("Chat request failed.");
      const errorMessage =
        err instanceof Error ? err.message : "Não foi possível ligar à API.";
      setMessagesMap(prev => ({
        ...prev,
        [assistantMessageId]: {
          ...prev[assistantMessageId],
          content: `❌ **Erro**: ${errorMessage || "Não foi possível ligar à API. Por favor, tenta novamente."}`
        }
      }));
    } finally {
      setIsLoading(false);
    }
  };

  const handleEditSubmit = async (nodeId: string, newText: string) => {
    const sanitizedNewText = stripUnsafeUnicode(newText).trim();
    if (!sanitizedNewText || isLoading) return;
    
    const originalNode: MessageNode | undefined = messagesMap[nodeId];
    if (!originalNode) return;
    
    if (sanitizedNewText === originalNode.content.trim()) {
      setEditingMessageId(null);
      return;
    }
    
    const messageTimestamp = currentTimestamp();
    const userMessageId = "edit-" + messageTimestamp.toString();
    const parentId = originalNode.parentId;
    
    const newUserNode: MessageNode = {
      id: userMessageId,
      role: "user",
      content: sanitizedNewText,
      parentId,
      children: []
    };
    
    const assistantMessageId =
      "assistant-edit-" + (messageTimestamp + 1).toString();
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
    
    const chatHistory = pathUpToNewUser.slice(1).filter(node => node.role === "user").map(node => ({
      role: node.role,
      content: node.content
    }));
    
    // Retrieve or generate unique client identifier for rate limiting
    let clientId = "anonymous";
    try {
      let storedId = localStorage.getItem("politometro_client_id");
      if (!storedId) {
        storedId = createClientId();
        localStorage.setItem("politometro_client_id", storedId);
      }
      clientId = storedId;
    } catch {
      console.warn("localStorage not available.");
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
      let sources: ChatSource[] = [];
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
      const accumulatedContent = { current: "" };
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
                // eslint-disable-next-line react-hooks/immutability
                accumulatedContent.current += textChunk;
                
                setMessagesMap(prev => ({
                  ...prev,
                  [assistantMessageId]: {
                    ...prev[assistantMessageId],
                    content: accumulatedContent.current,
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
              // eslint-disable-next-line react-hooks/immutability
              accumulatedContent.current += textChunk;
              
              setMessagesMap(prev => ({
                ...prev,
                [assistantMessageId]: {
                  ...prev[assistantMessageId],
                  content: accumulatedContent.current,
                  sources
                }
              }));
            } catch {}
          }
        }
      }
    } catch (err: unknown) {
      console.error("Chat request failed.");
      const errorMessage =
        err instanceof Error ? err.message : "Não foi possível ligar à API.";
      setMessagesMap(prev => ({
        ...prev,
        [assistantMessageId]: {
          ...prev[assistantMessageId],
          content: `❌ **Erro**: ${errorMessage || "Não foi possível ligar à API. Por favor, tenta novamente."}`
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

  const renderMarkdown = (text: string) => {
    const lines = text.split("\n");
    return lines.map((line, idx) => {
      if (line.startsWith("### ")) {
        return (
          <h3 key={idx} className={styles.mdH3}>
            {renderInlineMarkdown(line.slice(4))}
          </h3>
        );
      }
      if (line.startsWith("## ")) {
        return (
          <h2 key={idx} className={styles.mdH2}>
            {renderInlineMarkdown(line.slice(3))}
          </h2>
        );
      }

      if (line.startsWith("- ") || line.startsWith("* ")) {
        return (
          <ul key={idx} className={styles.mdUl}>
            <li>{renderInlineMarkdown(line.slice(2))}</li>
          </ul>
        );
      }

      return (
        <p
          key={idx}
          className={line.trim() === "" ? styles.mdSpacing : styles.mdParagraph}
        >
          {line ? renderInlineMarkdown(line) : "\u00a0"}
        </p>
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
                            renderMarkdown(msg.content)
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
        </div>
      </main>
    </div>
  );
}
