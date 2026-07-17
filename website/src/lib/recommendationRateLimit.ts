import type { NextRequest } from "next/server";

interface RateBucket {
  count: number;
  resetAt: number;
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

function clientAddress(req: NextRequest): string {
  return (
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
): RateLimitResult {
  const now = Date.now();
  if (buckets.size > 2_000) {
    for (const [key, bucket] of buckets) {
      if (bucket.resetAt <= now) buckets.delete(key);
    }
  }

  const key = `${scope}:${clientAddress(req)}`;
  const existing = buckets.get(key);
  if (!existing || existing.resetAt <= now) {
    buckets.set(key, { count: 1, resetAt: now + windowMs });
    return { allowed: true, retryAfterSeconds: 0 };
  }

  if (existing.count >= maxRequests) {
    return {
      allowed: false,
      retryAfterSeconds: Math.max(
        1,
        Math.ceil((existing.resetAt - now) / 1_000),
      ),
    };
  }
  existing.count += 1;
  return { allowed: true, retryAfterSeconds: 0 };
}
