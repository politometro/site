"use client";

import { useState, useEffect } from "react";
import styles from "./SettingsModal.module.css";

interface Settings {
  groqApiKey: string;
  groqModel: string;
  pineconeApiKey: string;
  pineconeIndexName: string;
  githubToken: string;
  githubRepo: string;
  githubBranch: string;
}

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function SettingsModal({ isOpen, onClose }: SettingsModalProps) {
  const [settings, setSettings] = useState<Settings>({
    groqApiKey: "",
    groqModel: "llama-3.3-70b-versatile",
    pineconeApiKey: "",
    pineconeIndexName: "politometro",
    githubToken: "",
    githubRepo: "",
    githubBranch: "main",
  });

  const [isSaved, setIsSaved] = useState(false);

  useEffect(() => {
    if (typeof window !== "undefined") {
      const savedGroqKey = localStorage.getItem("politometro_groq_api_key") || "";
      const savedGroqModel = localStorage.getItem("politometro_groq_model") || "llama-3.3-70b-versatile";
      const savedPineconeKey = localStorage.getItem("politometro_pinecone_api_key") || "";
      const savedPineconeIndex = localStorage.getItem("politometro_pinecone_index_name") || "politometro";
      const savedGithubToken = localStorage.getItem("politometro_github_token") || "";
      const savedGithubRepo = localStorage.getItem("politometro_github_repo") || "";
      const savedGithubBranch = localStorage.getItem("politometro_github_branch") || "main";

      setSettings({
        groqApiKey: savedGroqKey,
        groqModel: savedGroqModel,
        pineconeApiKey: savedPineconeKey,
        pineconeIndexName: savedPineconeIndex,
        githubToken: savedGithubToken,
        githubRepo: savedGithubRepo,
        githubBranch: savedGithubBranch,
      });
    }
  }, [isOpen]);

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    if (typeof window !== "undefined") {
      localStorage.setItem("politometro_groq_api_key", settings.groqApiKey);
      localStorage.setItem("politometro_groq_model", settings.groqModel);
      localStorage.setItem("politometro_pinecone_api_key", settings.pineconeApiKey);
      localStorage.setItem("politometro_pinecone_index_name", settings.pineconeIndexName);
      localStorage.setItem("politometro_github_token", settings.githubToken);
      localStorage.setItem("politometro_github_repo", settings.githubRepo);
      localStorage.setItem("politometro_github_branch", settings.githubBranch);
    }
    setIsSaved(true);
    setTimeout(() => {
      setIsSaved(false);
      onClose();
    }, 1000);
  };

  if (!isOpen) return null;

  return (
    <div className={styles.overlay}>
      <div className={`${styles.modal} glass`}>
        <div className={styles.header}>
          <h2>Configurações da API</h2>
          <button className={styles.closeBtn} onClick={onClose}>&times;</button>
        </div>
        
        <form onSubmit={handleSave} className={styles.form}>
          <div className={styles.scrollContainer}>
            <div className={styles.section}>
              <h3>Groq Cloud</h3>
              <div className={styles.inputGroup}>
                <label htmlFor="groqApiKey">Chave de API do Groq</label>
                <input
                  id="groqApiKey"
                  type="password"
                  placeholder="gsk_..."
                  value={settings.groqApiKey}
                  onChange={(e) => setSettings({ ...settings, groqApiKey: e.target.value })}
                  required
                />
                <p className={styles.helpText}>Obtenha em console.groq.com</p>
              </div>
              
              <div className={styles.inputGroup}>
                <label htmlFor="groqModel">Modelo de Linguagem (LLM)</label>
                <select
                  id="groqModel"
                  value={settings.groqModel}
                  onChange={(e) => setSettings({ ...settings, groqModel: e.target.value })}
                >
                  <option value="llama-3.3-70b-versatile">Llama 3.3 70B (Recomendado - Melhor Raciocínio)</option>
                  <option value="meta-llama/llama-4-scout-17b-16e-instruct">Llama 4 Scout 17B (Nova Geração)</option>
                  <option value="llama-3.1-8b-instant">Llama 3.1 8B (Mais rápido)</option>
                </select>
              </div>
            </div>

            <div className={styles.section}>
              <h3>Pinecone (Base de Dados Vetorial)</h3>
              <div className={styles.inputGroup}>
                <label htmlFor="pineconeApiKey">Chave de API do Pinecone</label>
                <input
                  id="pineconeApiKey"
                  type="password"
                  placeholder="pcsk_..."
                  value={settings.pineconeApiKey}
                  onChange={(e) => setSettings({ ...settings, pineconeApiKey: e.target.value })}
                  required
                />
                <p className={styles.helpText}>Obtenha em app.pinecone.io</p>
              </div>

              <div className={styles.inputGroup}>
                <label htmlFor="pineconeIndexName">Nome do Índice</label>
                <input
                  id="pineconeIndexName"
                  type="text"
                  value={settings.pineconeIndexName}
                  onChange={(e) => setSettings({ ...settings, pineconeIndexName: e.target.value })}
                  required
                />
              </div>
            </div>

            <div className={styles.section}>
              <h3>GitHub Sync (Opcional - Filas de Publicação)</h3>
              <div className={styles.inputGroup}>
                <label htmlFor="githubToken">GitHub Personal Access Token (PAT)</label>
                <input
                  id="githubToken"
                  type="password"
                  placeholder="ghp_..."
                  value={settings.githubToken}
                  onChange={(e) => setSettings({ ...settings, githubToken: e.target.value })}
                />
                <p className={styles.helpText}>Token com permissões de 'repo' para atualizar a fila no GitHub</p>
              </div>

              <div className={styles.inputGroup}>
                <label htmlFor="githubRepo">Repositório GitHub (ex: user/repo)</label>
                <input
                  id="githubRepo"
                  type="text"
                  placeholder="luisf/Politometro"
                  value={settings.githubRepo}
                  onChange={(e) => setSettings({ ...settings, githubRepo: e.target.value })}
                />
              </div>

              <div className={styles.inputGroup}>
                <label htmlFor="githubBranch">Ramo (Branch)</label>
                <input
                  id="githubBranch"
                  type="text"
                  value={settings.githubBranch}
                  onChange={(e) => setSettings({ ...settings, githubBranch: e.target.value })}
                />
              </div>
            </div>
          </div>

          <div className={styles.actions}>
            {isSaved ? (
              <span className={styles.successMessage}>✓ Configurações salvas!</span>
            ) : (
              <>
                <button type="button" className={styles.cancelBtn} onClick={onClose}>Cancelar</button>
                <button type="submit" className={styles.saveBtn}>Salvar</button>
              </>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}
