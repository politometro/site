import { NextResponse } from "next/server";
import fs from "fs";
import path from "path";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    // Bulletproof dataDir location (handles both root and website run folders)
    let dataDir = path.join(process.cwd(), "data");
    if (!fs.existsSync(dataDir)) {
      dataDir = path.join(process.cwd(), "..", "data");
    }

    let budgetsCount = 0;
    let constitutionCount = 0;

    if (fs.existsSync(dataDir)) {
      const filesInDocs = fs.readdirSync(dataDir);

      // Resolve budgets folder dynamically to prevent encoding mismatches
      const budgetsFolder = filesInDocs.find(
        (f) => f.toLowerCase().includes("orçamento") || f.toLowerCase().includes("orcamento")
      );
      const budgetsDir = budgetsFolder ? path.join(dataDir, budgetsFolder) : null;

      if (budgetsDir && fs.existsSync(budgetsDir)) {
        const files = fs.readdirSync(budgetsDir);
        budgetsCount = files.filter((f) => f.toLowerCase().endsWith(".pdf")).length;
      }

      // Resolve Constitution file dynamically
      const constitutionFile = filesInDocs.find(
        (f) => f.toLowerCase().includes("constitui") || f.toLowerCase().startsWith("constit")
      );
      constitutionCount = constitutionFile ? 1 : 0;
    }

    return new Response(
      JSON.stringify({
        budgetsCount,
        constitutionCount,
      }),
      {
        headers: {
          "Content-Type": "application/json",
          "Cache-Control": "no-store, max-age=0, must-revalidate",
        },
      }
    );
  } catch (err: any) {
    console.error("Erro na API de estatísticas:", err);
    return new Response(
      JSON.stringify({ budgetsCount: 0, constitutionCount: 0 }),
      {
        status: 500,
        headers: {
          "Content-Type": "application/json",
          "Cache-Control": "no-store, max-age=0, must-revalidate",
        },
      }
    );
  }
}
