import { NextResponse } from "next/server";
import fs from "fs";
import path from "path";

export async function GET() {
  try {
    // Bulletproof dataDir location (handles both root and website run folders)
    let dataDir = path.join(process.cwd(), "data");
    if (!fs.existsSync(dataDir)) {
      dataDir = path.join(process.cwd(), "..", "data");
    }

    const budgetsDir = path.join(dataDir, "Orçamentos de Estado");
    let budgetsCount = 0;

    if (fs.existsSync(budgetsDir)) {
      const files = fs.readdirSync(budgetsDir);
      // Count PDF files in the Orçamentos de Estado directory
      budgetsCount = files.filter((f) => f.toLowerCase().endsWith(".pdf")).length;
    }

    // Check if Constituição.pdf exists in the data directory
    const constitutionPath = path.join(dataDir, "Constituição.pdf");
    const constitutionCount = fs.existsSync(constitutionPath) ? 1 : 0;

    return NextResponse.json({
      budgetsCount,
      constitutionCount,
    });
  } catch (err: any) {
    console.error("Erro na API de estatísticas:", err);
    return NextResponse.json({ budgetsCount: 0, constitutionCount: 0 }, { status: 500 });
  }
}
