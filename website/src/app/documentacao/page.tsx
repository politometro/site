"use client";

import { useState, useMemo, useEffect, useCallback } from "react";
import Header from "@/components/Header";
import docsData from "@/data/political_docs.json";
import styles from "./page.module.css";

interface Cell {
  col: string;
  excel_status: "available" | "not_found" | "not_searched";
  status: "available" | "not_found" | "not_searched";
  value: string | null;
  on_disk: boolean;
}

interface Row {
  party: string;
  cells: Cell[];
}

interface PartyMetadata {
  firstElection: string;
  foundedYear?: number | string;
  isExtinct?: boolean;
  extinctionElection?: string;
  extinctionNote?: string;
  coalitions?: { [election: string]: string };
  website?: string;
}

const PARTIES_METADATA: { [party: string]: PartyMetadata } = {
  "PSD": {
    firstElection: "Legislativas - 1975",
    foundedYear: 1974,
    website: "https://www.psd.pt",
    coalitions: {
      "Legislativas - 2025": "Aliança Democrática (AD)",
      "Europeias - 2024": "Aliança Democrática (AD)",
      "Madeira - 2024": "PSD/CDS-PP",
      "Legislativas - 2024": "Aliança Democrática (AD)",
      "Açores - 2024": "Aliança Democrática (AD)",
      "Legislativas - 2015": "Portugal à Frente (PaF)",
      "Europeias - 2014": "Aliança Portugal",
      "Madeira - 2023": "Somos Madeira"
    }
  },
  "PS": {
    firstElection: "Legislativas - 1975",
    foundedYear: 1973,
    website: "https://www.ps.pt"
  },
  "CHEGA": {
    firstElection: "Europeias - 2019",
    foundedYear: 2019,
    website: "https://partidochega.pt",
    coalitions: {
      "Europeias - 2019": "BASTA!"
    }
  },
  "IL": {
    firstElection: "Europeias - 2019",
    foundedYear: 2017,
    website: "https://iniciativaliberal.pt"
  },
  "BE": {
    firstElection: "Europeias 1999",
    foundedYear: 1999,
    website: "https://www.bloco.org"
  },
  "CDU - PCP/PEV": {
    firstElection: "Legislativas - 1975",
    foundedYear: "1921 (PCP) / 1987 (CDU)",
    website: "https://www.cdu.pt"
  },
  "LIVRE": {
    firstElection: "Europeias - 2014",
    foundedYear: 2014,
    website: "https://partidolivre.pt"
  },
  "PAN": {
    firstElection: "Legislativas - 2011",
    foundedYear: 2009,
    website: "https://www.pan.com.pt"
  },
  "CDS": {
    firstElection: "Legislativas - 1975",
    foundedYear: 1974,
    website: "https://www.cds.pt",
    coalitions: {
      "Legislativas - 2025": "Aliança Democrática (AD)",
      "Europeias - 2024": "Aliança Democrática (AD)",
      "Madeira - 2024": "PSD/CDS-PP",
      "Legislativas - 2024": "Aliança Democrática (AD)",
      "Açores - 2024": "Aliança Democrática (AD)",
      "Legislativas - 2015": "Portugal à Frente (PaF)",
      "Europeias - 2014": "Aliança Portugal",
      "Madeira - 2023": "Somos Madeira"
    }
  },
  "ADN/PDR": {
    firstElection: "Legislativas - 2015",
    foundedYear: "2014 (PDR) / 2022 (ADN)",
    website: "https://partidoadn.pt"
  },
  "R.I.R": {
    firstElection: "Legislativas - 2019",
    foundedYear: 2019,
    website: "https://rir.com.pt"
  },
  "JPP": {
    firstElection: "Madeira - 2015",
    foundedYear: 2015,
    website: "https://www.juntospelopovo.pt"
  },
  "NOVA DIREITA": {
    firstElection: "Legislativas - 2024",
    foundedYear: 2023,
    website: "https://novadireita.pt"
  },
  "PCTP/MRPP": {
    firstElection: "Legislativas - 1975",
    foundedYear: 1970,
    website: "https://www.pctpmrpp.org"
  },
  "VOLT PORTUGAL": {
    firstElection: "Legislativas - 2022",
    foundedYear: 2020,
    website: "https://www.voltportugal.org"
  },
  "ERGUE-TE/PNR": {
    firstElection: "Legislativas - 2002",
    foundedYear: 2000,
    isExtinct: true,
    website: "https://www.ergue-te.pt",
    extinctionNote: "extinto em agosto de 2025 pelo Tribunal Constitucional"
  },
  "MPT/ALTERNATIVA 21": {
    firstElection: "Legislativas - 1995",
    foundedYear: "1993 (MPT) / 2024 (Alternativa 21)",
    website: "https://mpt.pt",
    coalitions: {
      "Legislativas - 2024": "Alternativa 21",
      "Europeias - 2024": "Alternativa 21"
    }
  },
  "PTP": {
    firstElection: "Europeias - 2009",
    foundedYear: 2009,
    website: "https://ptp.org.pt"
  },
  "NÓS, CIDADÃOS!": {
    firstElection: "Legislativas - 2015",
    foundedYear: 2015,
    website: "https://noscidadaos.pt"
  },
  "PPM": {
    firstElection: "Legislativas - 1975",
    foundedYear: 1974,
    website: "https://ppm.pt",
    coalitions: {
      "Legislativas - 2025": "Aliança Democrática (AD)",
      "Legislativas - 2024": "Aliança Democrática (AD)",
      "Açores - 2024": "Aliança Democrática (AD)",
      "Europeias - 2019": "BASTA!"
    }
  },
  "MAS": {
    firstElection: "Europeias - 2014",
    foundedYear: 2013,
    website: "https://mas.org.pt"
  },
  "PURP/(A)TUA": {
    firstElection: "Legislativas - 2015",
    isExtinct: true,
    extinctionElection: "Europeias - 2024"
  },
  "MEP": {
    firstElection: "Europeias - 2009",
    isExtinct: true,
    extinctionElection: "Açores - 2012"
  },
  "PND": {
    firstElection: "Europeias - 2004",
    isExtinct: true,
    extinctionElection: "Legislativas - 2015"
  },
  "PPV": {
    firstElection: "Europeias - 2009",
    isExtinct: true,
    extinctionElection: "Açores - 2020",
    extinctionNote: "fundiu-se com o CHEGA"
  },
  "POUS": {
    firstElection: "Legislativas - 1999",
    isExtinct: true,
    extinctionElection: "Açores - 2020"
  },
  "PDA": {
    firstElection: "Legislativas - 1999",
    isExtinct: true,
    extinctionElection: "Legislativas - 2015"
  },
  "P.H.": {
    firstElection: "Europeias 1999",
    isExtinct: true,
    extinctionElection: "Legislativas - 2015"
  },
  "MMS": {
    firstElection: "Europeias - 2009",
    isExtinct: true,
    extinctionElection: "Legislativas - 2011"
  },
  "PSN": {
    firstElection: "Legislativas - 1999",
    isExtinct: true,
    extinctionElection: "Legislativas - 2002"
  },
  "UDP": {
    firstElection: "Legislativas - 1975",
    isExtinct: true,
    extinctionElection: "Legislativas - 2005",
    extinctionNote: "fundiu-se no Bloco de Esquerda"
  },
  "MD": {
    firstElection: "Europeias 1999",
    isExtinct: true,
    extinctionElection: "Legislativas - 1999"
  },
  "Partido Liberal Social": {
    firstElection: "Europeias 1999",
    website: "https://www.partidoliberalsocial.pt"
  },
  "Partido Libertário": {
    firstElection: "Legislativas - 2022",
    website: "https://www.partidolibertario.pt"
  }
};

const normalizePartyName = (name: string) => {
  return name
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, "");
};

const getYearFromCol = (col: string): number => {
  if (col.toLowerCase().includes("princípios") || col.toLowerCase().includes("principios") || col.toLowerCase().includes("declara")) {
    return 9999;
  }
  const match = col.match(/\d{4}/);
  return match ? parseInt(match[0], 10) : 1975;
};

const PARTIES_METADATA_NORM = Object.keys(PARTIES_METADATA).reduce((acc, key) => {
  acc[normalizePartyName(key)] = PARTIES_METADATA[key];
  return acc;
}, {} as { [key: string]: PartyMetadata });

export default function DocumentationPage() {
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [extraStats, setExtraStats] = useState({ budgetsCount: 28, constitutionCount: 1 });

  const rows = docsData.rows as Row[];

  // Download handler: fetches the PDF via the API, validates the response,
  // and triggers a real file download. Shows an alert if the PDF is not found.
  const handleDownload = useCallback(async (downloadUrl: string, party: string, col: string) => {
    try {
      const res = await fetch(downloadUrl);
      if (!res.ok) {
        alert(`Não foi possível descarregar o programa de ${party} (${col}). O ficheiro não foi encontrado no servidor.`);
        return;
      }
      const contentType = res.headers.get("Content-Type") || "";
      if (contentType.includes("application/json")) {
        alert(`Não foi possível descarregar o programa de ${party} (${col}). O ficheiro não foi encontrado no servidor.`);
        return;
      }
      const blob = await res.blob();
      // Extract filename from Content-Disposition header or fallback
      const disposition = res.headers.get("Content-Disposition") || "";
      let filename = `${party} - ${col}.pdf`;
      const filenameMatch = disposition.match(/filename="?([^"\n;]+)"?/);
      if (filenameMatch) {
        filename = filenameMatch[1];
      }
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
    } catch {
      alert(`Erro ao descarregar o programa de ${party} (${col}). Tente novamente mais tarde.`);
    }
  }, []);

  useEffect(() => {
    fetch("/api/stats", { cache: "no-store" })
      .then((res) => res.json())
      .then((data) => {
        setExtraStats({
          budgetsCount: data.budgetsCount || 0,
          constitutionCount: data.constitutionCount || 0,
        });
      })
      .catch((err) => console.error("Erro ao carregar estatísticas extra:", err));
  }, []);

  // Exclude the 4 budget columns and any unnamed columns from the matrix headers completely
  const headers = useMemo(() => {
    const BUDGET_COLUMNS = ["Orçamento de Estado", "Orçamento da UE", "Orçamento - Madeira", "Orçamento - Açores"];
    return (docsData.headers as string[]).filter(
      (h) => !BUDGET_COLUMNS.includes(h) && !h.toLowerCase().startsWith("unnamed")
    );
  }, []);

  // Filter columns based on category selection
  const filteredHeaders = useMemo(() => {
    if (categoryFilter === "all") return headers;
    
    return headers.filter((header) => {
      const lower = header.toLowerCase();
      if (categoryFilter === "legislativas") {
        return lower.includes("legislativas") || lower.includes("princípios") || lower.includes("principios");
      }
      if (categoryFilter === "europeias") {
        return lower.includes("europeias");
      }
      if (categoryFilter === "regioes") {
        return lower.includes("madeira") || lower.includes("açores") || lower.includes("aores");
      }
      return true;
    });
  }, [headers, categoryFilter]);

  // Filter rows based on search and active status
  const filteredRows = useMemo(() => {
    return rows
      .filter((row) => {
        const normName = normalizePartyName(row.party);
        const meta = PARTIES_METADATA_NORM[normName];
        
        // Exclude if extinct AND has no available documents in the headers
        const hasAvailableDocs = row.cells.some(
          (c) => headers.includes(c.col) && c.status === "available"
        );
        if (meta?.isExtinct && !hasAvailableDocs) {
          return false;
        }
        return true;
      })
      .filter((row) =>
        row.party.toLowerCase().includes(search.toLowerCase())
      );
  }, [rows, search, headers]);

  // Calculate statistics across all active cells in the matrix (excluding budget columns)
  const stats = useMemo(() => {
    let available = 0;

    rows.forEach((row) => {
      // Check if row is not filtered out
      const normName = normalizePartyName(row.party);
      const meta = PARTIES_METADATA_NORM[normName];
      const hasAvailableDocs = row.cells.some(
        (c) => headers.includes(c.col) && c.status === "available"
      );
      if (meta?.isExtinct && !hasAvailableDocs) {
        return; // skip stats for excluded rows
      }

      row.cells.forEach((cell) => {
        if (headers.includes(cell.col)) {
          if (cell.status === "available") available++;
        }
      });
    });

    return { available };
  }, [rows, headers]);

  return (
    <div className={styles.container}>
      <Header />
      
      {/* Background decoration */}
      <div className="ambient-glow" />

      <main className={styles.main}>
        <div className={styles.titleSection}>
          <h1 className={styles.title}>Matriz de Documentação</h1>
          <p className={styles.subtitle}>
            Lista de programas políticos eleitorais catalogados e indexados no Politómetro (a Constituição e os Orçamentos do Estado também estão incluídos).
          </p>
        </div>

        {/* Stats Grid Dashboard */}
        <div className={styles.statsGrid}>
          {/* Card 1: Programas Disponíveis */}
          <div className={`${styles.statCard} glass`}>
            <div className={styles.statIcon}>
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" />
                <polyline points="14 2 14 8 20 8" />
                <path d="m9 15 2 2 4-4" />
              </svg>
            </div>
            <div className={styles.statInfo}>
              <span className={styles.statValue}>{stats.available}</span>
              <span className={styles.statLabel}>Programas Disponíveis</span>
            </div>
          </div>

          {/* Card 2: Orçamentos de Estado */}
          <a 
            href="https://www.parlamento.pt/OrcamentoEstado/Paginas/OrcamentoEstadoanosanteriores.aspx" 
            target="_blank" 
            rel="noopener noreferrer" 
            className={`${styles.statCard} ${styles.statCardLink} glass`}
            title="Ver arquivo oficial de Orçamentos do Estado no Parlamento"
          >
            <div className={styles.statIcon}>
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="3" y1="22" x2="21" y2="22" />
                <line x1="6" y1="18" x2="6" y2="11" />
                <line x1="10" y1="18" x2="10" y2="11" />
                <line x1="14" y1="18" x2="14" y2="11" />
                <line x1="18" y1="18" x2="18" y2="11" />
                <polygon points="12 2 2 7 22 7 12 2" />
              </svg>
            </div>
            <div className={styles.statInfo}>
              <span className={styles.statValue}>
                {extraStats.budgetsCount}
                <svg 
                  xmlns="http://www.w3.org/2000/svg" 
                  width="12" 
                  height="12" 
                  viewBox="0 0 24 24" 
                  fill="none" 
                  stroke="currentColor" 
                  strokeWidth="2.5" 
                  strokeLinecap="round" 
                  strokeLinejoin="round" 
                  style={{ marginLeft: "6px", verticalAlign: "middle", opacity: 0.6 }}
                >
                  <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
                  <polyline points="15 3 21 3 21 9"></polyline>
                  <line x1="10" y1="14" x2="21" y2="3"></line>
                </svg>
              </span>
              <span className={styles.statLabel}>Orçamentos de Estado</span>
            </div>
          </a>

          {/* Card 3: Constituição */}
          <a 
            href="https://www.parlamento.pt/Legislacao/Paginas/ConstituicaoRepublicaPortuguesa.aspx" 
            target="_blank" 
            rel="noopener noreferrer" 
            className={`${styles.statCard} ${styles.statCardLink} glass`}
            title="Ver Constituição da República Portuguesa no Parlamento"
          >
            <div className={styles.statIcon}>
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 3v17" />
                <path d="M12 20H8m4 0h4" />
                <path d="M5 7h14" />
                <path d="M5 7l-2 6a3 3 0 0 0 6 0z" />
                <path d="M19 7l-2 6a3 3 0 0 0 6 0z" />
              </svg>
            </div>
            <div className={styles.statInfo}>
              <span className={styles.statValue}>
                {extraStats.constitutionCount > 0 ? "Disponível" : "Não Disponível"}
                <svg 
                  xmlns="http://www.w3.org/2000/svg" 
                  width="12" 
                  height="12" 
                  viewBox="0 0 24 24" 
                  fill="none" 
                  stroke="currentColor" 
                  strokeWidth="2.5" 
                  strokeLinecap="round" 
                  strokeLinejoin="round" 
                  style={{ marginLeft: "6px", verticalAlign: "middle", opacity: 0.6 }}
                >
                  <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
                  <polyline points="15 3 21 3 21 9"></polyline>
                  <line x1="10" y1="14" x2="21" y2="3"></line>
                </svg>
              </span>
              <span className={styles.statLabel}>Constituição da República</span>
            </div>
          </a>
        </div>

        {/* Filters and Controls */}
        <div className={`${styles.controls} glass`}>
          <div className={styles.searchWrapper}>
            <span className={styles.searchIcon}>🔍</span>
            <input
              type="text"
              placeholder="Pesquisar por partido..."
              className={styles.searchInput}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <div className={styles.filterGroup}>
            <select
              className={styles.filterSelect}
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value)}
            >
              <option value="all">Todas as Eleições</option>
              <option value="legislativas">Legislativas & Princípios</option>
              <option value="europeias">Europeias</option>
              <option value="regioes">Regionais (Madeira/Açores)</option>
            </select>
          </div>

          <div className={styles.legend}>
            <div className={styles.legendItem}>
              <span className={`${styles.dot} styles.dotGreen`} style={{ background: "var(--color-secondary)" }} />
              <span>Programa Disponível</span>
            </div>
            <div className={styles.legendItem}>
              <span className={`${styles.dot} styles.dotGray`} style={{ background: "#6c757d" }} />
              <span>Primeira Participação / Criado</span>
            </div>
            <div className={styles.legendItem}>
              <span style={{ color: "var(--text-muted)", fontWeight: "bold", paddingLeft: "0.25rem" }}>---</span>
              <span style={{ marginLeft: "0.25rem" }}>Não disponível / Não participou</span>
            </div>
          </div>
        </div>

        {/* Document Matrix Grid Table */}
        <div className={styles.tableContainer}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={`${styles.th} ${styles.thParty}`}>Partido</th>
                {filteredHeaders.map((h, idx) => (
                  <th key={idx} className={styles.th}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filteredRows.length > 0 ? (
                filteredRows.map((row, rIdx) => {
                  const normName = normalizePartyName(row.party);
                  const meta = PARTIES_METADATA_NORM[normName];

                  return (
                    <tr key={rIdx} className={styles.tr}>
                      <td className={styles.tdParty}>
                        {meta && meta.website ? (
                          <a
                            href={meta.website}
                            target="_blank"
                            rel="noopener noreferrer"
                            className={styles.partyLink}
                            title={`Visitar site oficial do ${row.party}`}
                          >
                            {row.party}
                            <svg 
                              xmlns="http://www.w3.org/2000/svg" 
                              width="10" 
                              height="10" 
                              viewBox="0 0 24 24" 
                              fill="none" 
                              stroke="currentColor" 
                              strokeWidth="2.5" 
                              strokeLinecap="round" 
                              strokeLinejoin="round" 
                              className={styles.partyLinkIcon}
                            >
                              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
                              <polyline points="15 3 21 3 21 9"></polyline>
                              <line x1="10" y1="14" x2="21" y2="3"></line>
                            </svg>
                          </a>
                        ) : (
                          row.party
                        )}
                        {meta && meta.isExtinct && (
                          <span 
                            style={{ 
                              fontSize: "0.65rem", 
                              color: "#c22926", 
                              background: "rgba(194, 41, 38, 0.08)", 
                              padding: "2px 6px", 
                              borderRadius: "4px", 
                              marginLeft: "8px",
                              border: "1px solid rgba(194, 41, 38, 0.2)",
                              display: "inline-block",
                              fontWeight: "800",
                              textTransform: "uppercase"
                            }}
                            title={meta.extinctionNote || "Este partido foi extinto."}
                          >
                            Extinto
                          </span>
                        )}
                      </td>
                      {row.cells
                        .filter((c) => filteredHeaders.includes(c.col))
                        .map((cell, cIdx) => {
                          const isFirstElection = meta && meta.firstElection === cell.col;
                          const isExtinctionElection = meta && meta.isExtinct && meta.extinctionElection === cell.col;
                          const coalitionName = meta && meta.coalitions && meta.coalitions[cell.col];

                          // Check if cell year is before first election (meaning before party existed)
                          const firstYear = meta ? getYearFromCol(meta.firstElection) : 1975;
                          const cellYear = getYearFromCol(cell.col);
                          if (meta && cellYear < firstYear) {
                            return <td key={cIdx} className={styles.tdCell}></td>; // Empty cell
                          }

                          if (isExtinctionElection) {
                            return (
                              <td key={cIdx} className={styles.tdCell}>
                                <span
                                  className={`${styles.statusBadge} ${styles.statusExtinct}`}
                                  title={`${row.party} - ${cell.col}\nEstado: Extinto`}
                                >
                                  Extinto
                                </span>
                                {meta.extinctionNote && (
                                  <div className={styles.cellNote}>{meta.extinctionNote}</div>
                                )}
                              </td>
                            );
                          }

                          if (cell.status === "available") {
                            const downloadUrl = `/api/download?party=${encodeURIComponent(row.party)}&col=${encodeURIComponent(cell.col)}`;

                            if (isFirstElection) {
                              const creationText = meta && meta.foundedYear ? `Criado em ${meta.foundedYear}` : "Criado aqui";
                              return (
                                <td key={cIdx} className={styles.tdCell}>
                                  <a
                                    href="#"
                                    onClick={(e) => { e.preventDefault(); handleDownload(downloadUrl, row.party, cell.col); }}
                                    className={styles.downloadLink}
                                    title="Descarregar programa (PDF)"
                                    role="button"
                                  >
                                    <span
                                      className={`${styles.statusBadge} ${styles.statusAvailable}`}
                                    >
                                      <svg 
                                        xmlns="http://www.w3.org/2000/svg" 
                                        width="12" 
                                        height="12" 
                                        viewBox="0 0 24 24" 
                                        fill="none" 
                                        stroke="currentColor" 
                                        strokeWidth="2.5" 
                                        strokeLinecap="round" 
                                        strokeLinejoin="round" 
                                        style={{ marginRight: "4px", flexShrink: 0 }}
                                      >
                                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                                        <polyline points="7 10 12 15 17 10"></polyline>
                                        <line x1="12" y1="15" x2="12" y2="3"></line>
                                      </svg>
                                      Disponível
                                    </span>
                                  </a>
                                  <div className={styles.cellNoteGreen}>{creationText}</div>
                                  {coalitionName && (
                                    <div className={styles.cellNote}>integrou a coligação {coalitionName}</div>
                                  )}
                                </td>
                              );
                            } else {
                              return (
                                <td key={cIdx} className={styles.tdCell}>
                                  <a
                                    href="#"
                                    onClick={(e) => { e.preventDefault(); handleDownload(downloadUrl, row.party, cell.col); }}
                                    className={styles.downloadLink}
                                    title="Descarregar programa (PDF)"
                                    role="button"
                                  >
                                    <span
                                      className={`${styles.statusBadge} ${styles.statusAvailable}`}
                                    >
                                      <svg 
                                        xmlns="http://www.w3.org/2000/svg" 
                                        width="12" 
                                        height="12" 
                                        viewBox="0 0 24 24" 
                                        fill="none" 
                                        stroke="currentColor" 
                                        strokeWidth="2.5" 
                                        strokeLinecap="round" 
                                        strokeLinejoin="round" 
                                        style={{ marginRight: "4px", flexShrink: 0 }}
                                      >
                                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                                        <polyline points="7 10 12 15 17 10"></polyline>
                                        <line x1="12" y1="15" x2="12" y2="3"></line>
                                      </svg>
                                      Disponível
                                    </span>
                                  </a>
                                  {coalitionName && (
                                    <div className={styles.cellNote}>integrou a coligação {coalitionName}</div>
                                  )}
                                </td>
                              );
                            }
                          }

                          if (isFirstElection) {
                            const creationText = meta && meta.foundedYear ? `Criado em ${meta.foundedYear}` : "Criado aqui";
                            return (
                              <td key={cIdx} className={styles.tdCell}>
                                <span
                                  className={`${styles.statusBadge} ${styles.statusFirstUnavailable}`}
                                  title={`${row.party} - ${cell.col}\nEstado: Primeira participação (sem programa)`}
                                >
                                  {creationText}
                                </span>
                              </td>
                            );
                          }

                          // Return --- for not_found and not_searched cells
                          return (
                            <td key={cIdx} className={styles.tdCell} style={{ color: "var(--text-muted)", fontWeight: "500" }}>
                              ---
                            </td>
                          );
                        })}
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td
                    colSpan={filteredHeaders.length + 1}
                    style={{ textAlign: "center", padding: "3rem", color: "var(--text-muted)" }}
                  >
                    Nenhum partido encontrado para o termo pesquisado.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </main>
    </div>
  );
}
