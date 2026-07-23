import { NextRequest, NextResponse } from "next/server";
import pdfIndex from "./pdf_index.json";

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

// Scans the static PDF index array instead of the local filesystem
function findPdf(pdfList: string[], party: string, category: string, year: number | null): string | null {
  for (const entry of pdfList) {
    const entryLower = entry.toLowerCase();

    // Check if this file belongs to the requested category folder
    const folderName = getCategoryFolder(category)
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, ""); // strip diacritics

    const normalizedEntry = entryLower.normalize("NFD").replace(/[\u0300-\u036f]/g, "");

    if (folderName && !normalizedEntry.includes("/" + folderName + "/")) {
      continue;
    }

    const filename = entry.split("/").pop() || "";
    const nameLower = filename.toLowerCase();

    // Check year match
    if (year) {
      const yearStr = String(year);
      const shortYear = yearStr.slice(2);
      if (!nameLower.includes(yearStr) && !nameLower.includes(shortYear)) {
        continue;
      }
    }

    // Check party match
    const partyLower = party.toLowerCase();
    let isMatch = false;

    if (partyLower === "psd" && (nameLower.includes("psd") || nameLower.includes("ppd"))) {
      isMatch = true;
    } else if (partyLower.includes("cdu") && (nameLower.includes("cdu") || nameLower.includes("pcp") || nameLower.includes("pev"))) {
      isMatch = true;
    } else if (
      (partyLower.includes("pctp") || partyLower.includes("mrpp")) &&
      (nameLower.includes("pctp") || nameLower.includes("mrpp"))
    ) {
      isMatch = true;
    } else if (partyLower.includes("adn") && (nameLower.includes("adn") || nameLower.includes("pdr"))) {
      isMatch = true;
    } else if (partyLower.includes("volt") && nameLower.includes("volt")) {
      isMatch = true;
    } else if (
      (partyLower.includes("ergue-te") || partyLower.includes("pnr")) &&
      (nameLower.includes("ergue-te") || nameLower.includes("pnr"))
    ) {
      isMatch = true;
    } else if (partyLower.includes("mpt") && (nameLower.includes("mpt") || nameLower.includes("alternativa 21") || nameLower.includes("alternativa21"))) {
      isMatch = true;
    } else if ((partyLower === "partido liberal social" || partyLower === "pls") && (nameLower.includes("pls") || nameLower.includes("liberal social"))) {
      isMatch = true;
    } else if (nameLower.includes(partyLower)) {
      isMatch = true;
    }

    if (!isMatch) {
      if (nameLower.includes("ad") && (partyLower.includes("psd") || partyLower.includes("cds") || partyLower.includes("ppm") || partyLower.includes("ad"))) {
        isMatch = true;
      } else if (nameLower.includes("paf") && (partyLower.includes("psd") || partyLower.includes("cds") || partyLower.includes("paf"))) {
        isMatch = true;
      }
    }

    if (isMatch) {
      return entry;
    }
  }
  return null;
}

// Shared logic: resolve the PDF path from query params using static index
function resolvePdfPath(req: NextRequest): { error?: string; status?: number; matchedRelPath?: string } {
  const { searchParams } = new URL(req.url);
  const party = searchParams.get("party");
  const col = searchParams.get("col");

  if (!party || !col) {
    return { error: "Parâmetros em falta (party, col).", status: 400 };
  }

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

  const matchedRelPath = findPdf(pdfIndex, party, category, year);

  if (!matchedRelPath) {
    return { error: `Ficheiro PDF não encontrado para: ${party} (${col})`, status: 404 };
  }

  return { matchedRelPath };
}

// HEAD handler: fast existence check without sending the file body.
// Checks if the file is present in our pre-indexed list.
export async function HEAD(req: NextRequest) {
  try {
    const result = resolvePdfPath(req);
    if (result.error) {
      return new Response(null, { status: result.status || 404 });
    }
    return new Response(null, {
      status: 200,
      headers: {
        "Content-Type": "application/pdf"
      },
    });
  } catch {
    return new Response(null, { status: 500 });
  }
}

// GET handler: redirects the user to the raw GitHub user content URL
export async function GET(req: NextRequest) {
  try {
    const result = resolvePdfPath(req);
    if (result.error) {
      return NextResponse.json({ error: result.error }, { status: result.status || 404 });
    }

    const repo = process.env.GITHUB_REPO || "politometro/site";
    const branch = process.env.GITHUB_BRANCH || "main";
    const githubUrl = `https://raw.githubusercontent.com/${repo}/${branch}/${result.matchedRelPath}`;

    return NextResponse.redirect(githubUrl, 302);
  } catch (err: any) {
    console.error("Erro no download de PDF:", err);
    return NextResponse.json(
      { error: `Erro no servidor: ${err.message}` },
      { status: 500 }
    );
  }
}
