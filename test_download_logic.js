const fs = require("fs");
const path = require("path");

function getCategoryFolder(category) {
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

function findPdf(dir, party, category, year) {
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
      } else if (partyLower.includes("adn") && (nameLower.includes("adn") || nameLower.includes("pdr"))) {
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
        return fullPath;
      }
    }
  }
  return null;
}

// Find dataDir from cwd or parent
let dataDir = path.join(process.cwd(), "data");
if (!fs.existsSync(dataDir)) {
  dataDir = path.join(process.cwd(), "..", "data");
}

console.log("Using dataDir:", dataDir, "Exists:", fs.existsSync(dataDir));

const testCases = [
  { party: "PSD", category: "Legislativas", year: 2024 },
  { party: "PS", category: "Legislativas", year: 2024 },
  { party: "CHEGA", category: "Legislativas", year: 2024 },
  { party: "IL", category: "Legislativas", year: 2024 },
  { party: "CDU - PCP/PEV", category: "Legislativas", year: 2024 },
  { party: "PSD", category: "Açores", year: 2024 },
  { party: "PSD", category: "Legislativas", year: 1976 }
];

for (const tc of testCases) {
  const catFolder = getCategoryFolder(tc.category);
  const searchPath = catFolder ? path.join(dataDir, catFolder) : dataDir;
  const res = findPdf(searchPath, tc.party, tc.category, tc.year);
  console.log(`${tc.party} (${tc.category} - ${tc.year}) -> ${res}`);
}
