import type { NextRequest } from "next/server";
import { timingSafeEqual } from "node:crypto";

interface RateBucket {
  count: number;
  resetAt: number;
  blockedUntil: number;
}

interface RateLimitResult {
  allowed: boolean;
  retryAfterSeconds: number;
}

type RateLimitGlobal = typeof globalThis & {
  __politometroRecommendationRateLimits?: Map<string, RateBucket>;
};

const rateLimitGlobal = globalThis as RateLimitGlobal;
const buckets =
  rateLimitGlobal.__politometroRecommendationRateLimits ??
  new Map<string, RateBucket>();
rateLimitGlobal.__politometroRecommendationRateLimits = buckets;

function equalSecret(left: string, right: string): boolean {
  const leftBytes = Buffer.from(left);
  const rightBytes = Buffer.from(right);
  return (
    leftBytes.length === rightBytes.length &&
    timingSafeEqual(leftBytes, rightBytes)
  );
}

function clientAddress(req: NextRequest): string {
  const configuredSecret = process.env.DISCORD_SUBMISSION_SECRET?.trim() ?? "";
  const suppliedSecret =
    req.headers.get("x-discord-submission-secret")?.trim() ?? "";
  const discordClient = req.headers
    .get("x-client-id")
    ?.trim()
    .match(/^discord-recommendation:(\d{5,30})$/)?.[1];
  if (
    configuredSecret &&
    suppliedSecret &&
    discordClient &&
    equalSecret(configuredSecret, suppliedSecret)
  ) {
    return `discord-user:${discordClient}`;
  }

  return (
    req.headers.get("x-vercel-forwarded-for")?.split(",")[0]?.trim() ||
    req.headers.get("cf-connecting-ip")?.trim() ||
    req.headers.get("x-real-ip")?.trim() ||
    req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    "unknown"
  ).slice(0, 120);
}

export function checkRecommendationRateLimit(
  req: NextRequest,
  scope: string,
  maxRequests = 8,
  windowMs = 10 * 60_000,
  blockMs = 6 * 60 * 60_000,
): RateLimitResult {
  const now = Date.now();
  if (buckets.size > 2_000) {
    for (const [key, bucket] of buckets) {
      if (Math.max(bucket.resetAt, bucket.blockedUntil) <= now) {
        buckets.delete(key);
      }
    }
  }

  const key = `${scope}:${clientAddress(req)}`;
  const existing = buckets.get(key);
  if (existing?.blockedUntil && existing.blockedUntil > now) {
    return {
      allowed: false,
      retryAfterSeconds: Math.max(
        1,
        Math.ceil((existing.blockedUntil - now) / 1_000),
      ),
    };
  }

  if (!existing || existing.resetAt <= now) {
    buckets.set(key, {
      count: 1,
      resetAt: now + windowMs,
      blockedUntil: 0,
    });
    return { allowed: true, retryAfterSeconds: 0 };
  }

  if (existing.count >= maxRequests) {
    existing.blockedUntil = now + blockMs;
    return {
      allowed: false,
      retryAfterSeconds: Math.max(
        1,
        Math.ceil((existing.blockedUntil - now) / 1_000),
      ),
    };
  }
  existing.count += 1;
  return { allowed: true, retryAfterSeconds: 0 };
}
