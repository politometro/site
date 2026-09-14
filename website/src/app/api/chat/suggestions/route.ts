import { NextRequest, NextResponse } from "next/server";
import { redactSensitiveText } from "@/lib/chatSecurity";
import { checkRecommendationRateLimit } from "@/lib/recommendationRateLimit";
import { fallbackSuggestions } from "@/lib/suggestionPool";

export const runtime = "nodejs";

// Sugestões de acompanhamento (follow-up suggestions): três perguntas curtas
// apresentadas depois de cada resposta do bot, relacionadas com o que foi
// respondido. Geração via Groq (modelo barato) com reserva local por
// palavras-chave. Limites: 3 sugestões, ≤ 100 caracteres cada.

const MAX_QUESTION_CHARS = 1_000;
const MAX_ANSWER_CHARS = 4_000;
const SUGGESTION_COUNT = 3;
const MAX_SUGGESTION_LENGTH = 100;

function sanitize(value: unknown, maxLength: number): string {
  return typeof value === "string"
    ? redactSensitiveText(value).slice(0, maxLength).trim()
    : "";
}

function coerceSuggestions(payload: unknown): string[] {
  let raw: unknown;
  if (Array.isArray(payload)) {
    raw = payload;
  } else if (payload && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    raw = record.suggestions ?? record.perguntas ?? record.questions;
  }
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item): item is string => typeof item === "string")
    .map((item) =>
      redactSensitiveText(item)
        .replace(/\s+/g, " ")
        .trim()
        .replace(/[?!.]+$/, (match) => (match.includes("?") ? "?" : "")),
    )
    .filter((item) => item.length >= 12 && item.length <= MAX_SUGGESTION_LENGTH + 20)
    .slice(0, SUGGESTION_COUNT)
    .map((item) =>
      item.length > MAX_SUGGESTION_LENGTH
        ? `${item.slice(0, MAX_SUGGESTION_LENGTH - 1).trimEnd()}?`
        : item,
    );
}

export async function POST(req: NextRequest) {
  const rateLimit = checkRecommendationRateLimit(
    req,
    "chat-suggestions",
    40,
    10 * 60_000,
    60 * 60_000,
  );
  if (!rateLimit.allowed) {
    return NextResponse.json(
      { suggestions: [], source: "rate_limited" },
      { status: 429, headers: { "Retry-After": String(rateLimit.retryAfterSeconds) } },
    );
  }

  let lastQuestion = "";
  let lastAnswer = "";
  try {
    const body = (await req.json()) as { lastQuestion?: unknown; lastAnswer?: unknown };
    lastQuestion = sanitize(body?.lastQuestion, MAX_QUESTION_CHARS);
    lastAnswer = sanitize(body?.lastAnswer, MAX_ANSWER_CHARS);
  } catch {
    return NextResponse.json({ suggestions: [], source: "invalid" }, { status: 400 });
  }

  if (!lastAnswer && !lastQuestion) {
    return NextResponse.json({ suggestions: [], source: "empty" }, { status: 400 });
  }

  const apiKey = process.env.GROQ_API_KEY;
  if (!apiKey || apiKey.includes("your_actual")) {
    return NextResponse.json({
      suggestions: fallbackSuggestions(lastAnswer),
      source: "fallback",
    });
  }

  // Cadeia curta de alternativas: o modelo preferido pode estar reformado ou
  // em quota; as sugestões têm sempre de chegar ao utilizador.
  const models = Array.from(
    new Set([
      process.env.SUGGESTIONS_MODEL?.trim(),
      "llama-3.1-8b-instant",
      "llama-3.3-70b-versatile",
      "openai/gpt-oss-20b",
    ].filter((model): model is string => Boolean(model))),
  );

  for (const model of models) {
    try {
      const response = await fetch("https://api.groq.com/openai/v1/chat/completions", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${apiKey}`,
          "Content-Type": "application/json",
        },
        signal: AbortSignal.timeout(8_000),
        body: JSON.stringify({
          model,
          temperature: 0.65,
          max_completion_tokens: 220,
          response_format: { type: "json_object" },
          messages: [
            {
              role: "system",
              content:
                "Geras sugestões de perguntas de acompanhamento para um chat sobre programas eleitorais portugueses, promessas políticas, atividade parlamentar e Orçamentos do Estado. Escreve em português de Portugal (pt-PT). Devolve exatamente 3 perguntas curtas (máximo 100 caracteres cada), diferentes entre si, diretamente relacionadas com a última resposta, que convidem a aprofundar o tema (dados, comparações entre partidos, anos anteriores, cumprimento de promessas). Devolve apenas JSON no formato {\"suggestions\": [\"pergunta 1\", \"pergunta 2\", \"pergunta 3\"]}. Não inventes factos concretos nas perguntas; perguntas antes são neutras.",
            },
            {
              role: "user",
              content: JSON.stringify({
                ultima_pergunta: lastQuestion || "(sem pergunta)",
                ultima_resposta: lastAnswer,
              }),
            },
          ],
        }),
      });
      if (!response.ok) {
        continue;
      }
      const payload = (await response.json()) as {
        choices?: Array<{ message?: { content?: string } }>;
      };
      const content = payload.choices?.[0]?.message?.content;
      const suggestions = content
        ? coerceSuggestions(JSON.parse(content))
        : [];
      if (suggestions.length > 0) {
        return NextResponse.json({ suggestions, source: "model" });
      }
    } catch {
      continue;
    }
  }
  return NextResponse.json({
    suggestions: fallbackSuggestions(lastAnswer),
    source: "fallback",
  });
}
