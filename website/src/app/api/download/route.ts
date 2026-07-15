import { NextRequest, NextResponse } from "next/server";
import fs from "fs";
import path from "path";

// Helper to determine the specific folder name under data/ for a category
function getCategoryFolder(category: string): string {
  const catLower = category.toLowerCase();
  if (catLower.includes("açores") || catLower.includes("acores")) {
    return "Açores";
  }
  if (catLower.includes("madeira")) {
    return "Madeira";
  }
  if (catLower.includes("legislativas")) {
    return "Legislativas";
  }
  if (catLower.includes("europeias")) {
    return "Europeias";
  }
  if (catLower.includes("princípios") || catLower.includes("principios") || catLower.includes("declara")) {
    return "Declaração de Princípios";
  }
  if (catLower.includes("orçamento") || catLower.includes("orcamento")) {
    return "Orçamentos de Estado";
  }
  return "";
}

// Shared memory helper to recursively scan data directory
function findPdf(dir: string, party: string, category: string, year: number | null): string | null {
  if (!fs.existsSync(dir)) return null;
  const entries = fs.readdirSync(dir, { withFileTypes: true });

  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      const found = findPdf(fullPath, party, category, year);
      if (found) return found;
    } else if (entry.isFile() && entry.name.toLowerCase().endsWith(".pdf")) {
      const nameLower = entry.name.toLowerCase();

      // Check year match
      if (year) {
        const yearStr = String(year);
        const shortYear = yearStr.slice(2);
        // Exclude if it doesn't mention the year
        if (!nameLower.includes(yearStr) && !nameLower.includes(shortYear)) {
          continue;
        }
      }

      // Check party match
      const partyLower = party.toLowerCase();
      let isMatch = false;

      // Handle common abbreviations and aliases
      if (partyLower === "psd" && (nameLower.includes("psd") || nameLower.includes("ppd"))) {
        isMatch = true;
      } else if (partyLower.includes("cdu") && (nameLower.includes("cdu") || nameLower.includes("pcp") || nameLower.includes("pev"))) {
        isMatch = true;
      } else if (partyLower.includes("adn") && (nameLower.includes("adn") || nameLower.includes("pdr"))) {
        isMatch = true;
      } else if (partyLower.includes("mpt") && (nameLower.includes("mpt") || nameLower.includes("alternativa 21") || nameLower.includes("alternativa21"))) {
        isMatch = true;
      } else if ((partyLower === "partido liberal social" || partyLower === "pls") && (nameLower.includes("pls") || nameLower.includes("liberal social"))) {
        isMatch = true;
      } else if (nameLower.includes(partyLower)) {
        isMatch = true;
      }

      // Coalition check fallbacks
      if (!isMatch) {
        if (nameLower.includes("ad") && (partyLower.includes("psd") || partyLower.includes("cds") || partyLower.includes("ppm") || partyLower.includes("ad"))) {
          isMatch = true;
        } else if (nameLower.includes("paf") && (partyLower.includes("psd") || partyLower.includes("cds") || partyLower.includes("paf"))) {
          isMatch = true;
        }
      }

      if (isMatch) {
        return fullPath;
      }
    }
  }
  return null;
}

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const party = searchParams.get("party");
    const col = searchParams.get("col");

    if (!party || !col) {
      return NextResponse.json({ error: "Parâmetros em falta (party, col)." }, { status: 400 });
    }

    // Determine category and year
    let category = col;
    let year: number | null = null;

    if (col.includes(" - ")) {
      const parts = col.split(" - ");
      category = parts[0];
      try {
        year = parseInt(parts[1], 10);
      } catch (e) {}
    } else if (col.includes(" 1999")) {
      category = "Europeias";
      year = 1999;
    }

    // data/ folder is inside the website project root
    const dataDir = path.join(process.cwd(), "data");

    const catFolder = getCategoryFolder(category);
    const searchPath = catFolder ? path.join(dataDir, catFolder) : dataDir;

    const matchedPath = findPdf(searchPath, party, category, year);

    if (!matchedPath || !fs.existsSync(matchedPath)) {
      return NextResponse.json(
        { error: `Ficheiro PDF não encontrado para: ${party} (${col}) em ${searchPath}` },
        { status: 404 }
      );
    }

    // Read the file buffer
    const fileBuffer = fs.readFileSync(matchedPath);
    const filename = path.basename(matchedPath);

    // Return the PDF response with headers forcing instant attachment download
    // Use RFC 5987 filename* for proper Unicode (Portuguese) character support
    const encodedFilename = encodeURIComponent(filename).replace(/'/g, "%27");
    return new Response(fileBuffer, {
      headers: {
        "Content-Type": "application/pdf",
        "Content-Disposition": `attachment; filename="${filename}"; filename*=UTF-8''${encodedFilename}`,
        "Content-Length": String(fileBuffer.length),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-cache",
      },
    });
  } catch (err: any) {
    console.error("Erro no download de PDF:", err);
    return NextResponse.json(
      { error: `Erro no servidor: ${err.message}` },
      { status: 500 }
    );
  }
}
