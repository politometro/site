import { randomBytes } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { NextRequest, NextResponse } from "next/server";
import { checkRecommendationRateLimit } from "@/lib/recommendationRateLimit";
import { redactSensitiveText } from "@/lib/chatSecurity";

export const runtime = "nodejs";

// A conversa partilhada fica guardada como ficheiro JSON no repositório
// (website/public/shared/<id>.json) via GitHub Contents API — o mesmo padrão
// da fila de recomendações. Em desenvolvimento usa o sistema de ficheiros.
const STORAGE_DIR = "website/public/shared";
const SHARED_ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789";
const SHARED_ID_LENGTH = 12;
const MAX_MESSAGES = 120;
const MAX_MESSAGE_CHARS = 6_000;
const MAX_TOTAL_CHARS = 120_000;

interface SharedMessage {
  role: "user" | "assistant";
  content: string;
}

interface ShareRecord {
  id: string;
  createdAt: string;
  messages: SharedMessage[];
}

interface GitHubConfig {
  token: string;
  repo: string;
  branch: string;
}

function githubConfig(): GitHubConfig | null {
  const token = process.env.GITHUB_TOKEN?.trim();
  const repo = process.env.GITHUB_REPO?.trim();
  if (!token || !repo || token.includes("your_actual")) return null;
  return {
    token,
    repo,
    branch: process.env.GITHUB_BRANCH?.trim() || "main",
  };
}

function sharedPath(id: string): string {
  return `${STORAGE_DIR}/${id}.json`;
}

function contentsUrl(config: GitHubConfig, filePath: string, includeRef: boolean): string {
  const encodedPath = filePath
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  const base = `https://api.github.com/repos/${config.repo}/contents/${encodedPath}`;
  return includeRef
    ? `${base}?ref=${encodeURIComponent(config.branch)}`
    : base;
}

function newShareId(): string {
  const bytes = randomBytes(SHARED_ID_LENGTH);
  let id = "";
  for (let index = 0; index < SHARED_ID_LENGTH; index += 1) {
    id += SHARED_ID_ALPHABET[bytes[index] % SHARED_ID_ALPHABET.length];
  }
  return id;
}

function sanitizeMessages(raw: unknown): SharedMessage[] {
  if (!Array.isArray(raw)) throw new Error("Mensagens inválidas.");
  const messages: SharedMessage[] = [];
  let total = 0;
  for (const entry of raw) {
    if (messages.length >= MAX_MESSAGES) break;
    const record = entry as Record<string, unknown>;
    const role = record?.role === "user" ? "user" : record?.role === "assistant" ? "assistant" : null;
    const content = typeof record?.content === "string" ? record.content : "";
    if (!role || !content.trim()) continue;
    const safe = redactSensitiveText(content).slice(0, MAX_MESSAGE_CHARS).trim();
    if (!safe) continue;
    total += safe.length;
    if (total > MAX_TOTAL_CHARS) break;
    messages.push({ role, content: safe });
  }
  if (messages.length === 0) throw new Error("A conversa está vazia.");
  return messages;
}

async function readRemote(config: GitHubConfig, id: string): Promise<ShareRecord | null> {
  const response = await fetch(contentsUrl(config, sharedPath(id), true), {
    headers: {
      Authorization: `Bearer ${config.token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "Cache-Control": "no-cache",
    },
    cache: "no-store",
  });
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error(`Não foi possível ler a conversa partilhada (HTTP ${response.status}).`);
  }
  const payload = (await response.json()) as { content?: unknown };
  if (typeof payload.content !== "string") return null;
  return JSON.parse(
    Buffer.from(payload.content.replace(/\s/g, ""), "base64").toString("utf-8"),
  ) as ShareRecord;
}

async function writeRemote(config: GitHubConfig, record: ShareRecord): Promise<void> {
  const filePath = sharedPath(record.id);
  let sha: string | null = null;
  const current = await fetch(contentsUrl(config, filePath, true), {
    headers: {
      Authorization: `Bearer ${config.token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "Cache-Control": "no-cache",
    },
    cache: "no-store",
  });
  if (current.ok) {
    const payload = (await current.json()) as { sha?: unknown };
    if (typeof payload.sha === "string") sha = payload.sha;
  }
  const body: Record<string, unknown> = {
    message: `Share conversation ${record.id} [website]`,
    content: Buffer.from(JSON.stringify(record, null, 2)).toString("base64"),
    branch: config.branch,
  };
  if (sha) body.sha = sha;
  const response = await fetch(contentsUrl(config, filePath, false), {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${config.token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`Não foi possível guardar a conversa partilhada (HTTP ${response.status}).`);
  }
}

function localPath(id: string): string {
  return path.join(process.cwd(), "public", "shared", `${id}.json`);
}

function readLocal(id: string): ShareRecord | null {
  const filePath = localPath(id);
  if (!fs.existsSync(filePath)) return null;
  return JSON.parse(fs.readFileSync(filePath, "utf-8")) as ShareRecord;
}

function writeLocal(record: ShareRecord): void {
  const filePath = localPath(record.id);
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(record, null, 2), "utf-8");
}

export async function POST(req: NextRequest) {
  try {
    const rateLimit = checkRecommendationRateLimit(req, "share-create", 6, 10 * 60_000, 60 * 60_000);
    if (!rateLimit.allowed) {
      return NextResponse.json(
        { error: "Demasiadas conversas partilhadas. Tenta novamente mais tarde." },
        { status: 429, headers: { "Retry-After": String(rateLimit.retryAfterSeconds) } },
      );
    }
    const body = (await req.json()) as { messages?: unknown };
    const messages = sanitizeMessages(body?.messages);
    const config = githubConfig();

    // ID único: verificar colisões antes de gravar (raro, mas garantido).
    for (let attempt = 0; attempt < 5; attempt += 1) {
      const id = newShareId();
      const existing = config
        ? await readRemote(config, id)
        : readLocal(id);
      if (existing) continue;
      const record: ShareRecord = {
        id,
        createdAt: new Date().toISOString(),
        messages,
      };
      if (config) {
        await writeRemote(config, record);
      } else {
        writeLocal(record);
      }
      return NextResponse.json({ success: true, id }, { status: 201 });
    }
    return NextResponse.json(
      { error: "Não foi possível gerar um identificador único. Tenta novamente." },
      { status: 500 },
    );
  } catch (error: unknown) {
    console.error("[share API] Falha ao partilhar:", error);
    const message = error instanceof Error ? error.message : "Falha ao partilhar a conversa.";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

export async function GET(req: NextRequest) {
  try {
    const id = req.nextUrl.searchParams.get("id")?.trim() ?? "";
    if (!/^[a-z0-9]{6,24}$/.test(id)) {
      return NextResponse.json({ error: "Identificador inválido." }, { status: 400 });
    }
    const config = githubConfig();
    const record = config ? await readRemote(config, id) : readLocal(id);
    if (!record) {
      return NextResponse.json(
        { error: "Esta conversa partilhada não existe ou já foi removida." },
        { status: 404 },
      );
    }
    return NextResponse.json(record, {
      headers: { "Cache-Control": "public, max-age=60" },
    });
  } catch (error: unknown) {
    console.error("[share API] Falha ao ler:", error);
    return NextResponse.json({ error: "Falha ao carregar a conversa partilhada." }, { status: 500 });
  }
}
