import { NextResponse } from "next/server";
import { getLatestNews } from "@/lib/news";

export const runtime = "nodejs";
export const revalidate = 120;

export async function GET() {
  const payload = await getLatestNews();
  return NextResponse.json(payload, {
    headers: {
      "Cache-Control":
        "public, s-maxage=120, stale-while-revalidate=300",
    },
  });
}
