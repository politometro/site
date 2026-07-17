import { NextRequest, NextResponse } from "next/server";
import {
  isRecommendationType,
  RecommendationResolutionError,
  resolutionErrorMessage,
  resolveRecommendation,
  sanitizeText,
} from "@/lib/recommendationResolver";
import { checkRecommendationRateLimit } from "@/lib/recommendationRateLimit";

export const runtime = "nodejs";

interface EnrichPayload {
  title?: unknown;
  link?: unknown;
  type?: unknown;
}

export async function POST(req: NextRequest) {
  try {
    const rateLimit = checkRecommendationRateLimit(req, "suggestions-enrich");
    if (!rateLimit.allowed) {
      return NextResponse.json(
        { error: "Demasiados pedidos de validação. Tenta novamente mais tarde." },
        {
          status: 429,
          headers: { "Retry-After": String(rateLimit.retryAfterSeconds) },
        },
      );
    }
    const body = (await req.json()) as EnrichPayload;
    if (!isRecommendationType(body.type)) {
      return NextResponse.json(
        { error: "Tipo de recomendação inválido." },
        { status: 400 },
      );
    }

    const title = sanitizeText(body.title, 220);
    const link = typeof body.link === "string" ? body.link.trim() : "";
    const resolved = await resolveRecommendation({
      type: body.type,
      title,
      link,
    });

    return NextResponse.json(resolved, {
      status: resolved.resolutionStatus === "verified" ? 200 : 422,
    });
  } catch (error: unknown) {
    console.error("[suggestions/enrich] Resolution failed:", error);
    const status = error instanceof RecommendationResolutionError ? 400 : 500;
    return NextResponse.json(
      { error: resolutionErrorMessage(error) },
      { status },
    );
  }
}
