"use client";

import { useState, useMemo } from "react";
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
  foundedYear?: number;
  isExtinct?: boolean;
  extinctionElection?: string;
  extinctionNote?: string;
  coalitions?: { [election: string]: string };
}

const PARTIES_METADATA: { [party: string]: PartyMetadata } = {
  "PSD": {
    firstElection: "Legislativas - 1999",
    foundedYear: 1974,
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
    firstElection: "Legislativas - 1999",
    foundedYear: 1973
  },
  "CHEGA": {
    firstElection: "Europeias - 2019",
    foundedYear: 2019,
    coalitions: {
      "Europeias - 2019": "BASTA!"
    }
  },
  "IL": {
    firstElection: "Europeias - 2019",
    foundedYear: 2017
  },
  "BE": {
    firstElection: "Europeias 1999",
    foundedYear: 1999
  },
  "CDU - PCP/PEV": {
    firstElection: "Europeias 1999",
    foundedYear: 1987
  },
  "LIVRE": {
    firstElection: "Europeias - 2014",
    foundedYear: 2014
  },
  "PAN": {
    firstElection: "Legislativas - 2011",
    foundedYear: 2009
  },
  "CDS": {
    firstElection: "Legislativas - 1999",
    foundedYear: 1974,
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
    foundedYear: 2014
  },
  "R.I.R": {
    firstElection: "Legislativas - 2019",
    foundedYear: 2019
  },
  "JPP": {
    firstElection: "Madeira - 2015",
    foundedYear: 2015
  },
  "NOVA DIREITA": {
    firstElection: "Legislativas - 2024",
    foundedYear: 2023
  },
  "PCTP/MRPP": {
    firstElection: "Europeias 1999",
    foundedYear: 1970
  },
  "VOLT PORTUGAL": {
    firstElection: "Legislativas - 2022",
    foundedYear: 2020
  },
  "ERGUE-TE/PNR": {
    firstElection: "Legislativas - 2002",
    foundedYear: 2000,
    isExtinct: true,
    extinctionNote: "extinto em agosto de 2025 pelo Tribunal Constitucional"
  },
  "MPT/ALTERNATIVA 21": {
    firstElection: "Europeias 1999",
    foundedYear: 1993,
    coalitions: {
      "Legislativas - 2024": "Alternativa 21",
      "Europeias - 2024": "Alternativa 21"
    }
  },
  "PTP": {
    firstElection: "Europeias - 2009",
    foundedYear: 2009
  },
  "NÓS, CIDADÃOS!": {
    firstElection: "Legislativas - 2015",
    foundedYear: 2015
  },
  "PPM": {
    firstElection: "Europeias 1999",
    foundedYear: 1974,
    coalitions: {
      "Legislativas - 2025": "Aliança Democrática (AD)",
      "Legislativas - 2024": "Aliança Democrática (AD)",
      "Açores - 2024": "Aliança Democrática (AD)",
      "Europeias - 2019": "BASTA!"
    }
  },
  "MAS": {
    firstElection: "Europeias - 2014",
    foundedYear: 2013
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
    firstElection: "Europeias 1999",
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
    isExtinct: true,
    extinctionElection: "Europeias 1999"
  },
  "Partido Libertário": {
    firstElection: "Legislativas - 2022",
    isExtinct: true,
    extinctionElection: "Legislativas - 2022"
  }
};

const normalizePartyName = (name: string) => {
  return name
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, "");
};

const PARTIES_METADATA_NORM = Object.keys(PARTIES_METADATA).reduce((acc, key) => {
  acc[normalizePartyName(key)] = PARTIES_METADATA[key];
  return acc;
}, {} as { [key: string]: PartyMetadata });

export default function DocumentationPage() {
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("all");

  const rows = docsData.rows as Row[];

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

        {/* Stats Grid Dashboard - Only shows available count */}
        <div className={styles.statsGrid}>
          <div className={`${styles.statCard} glass`} style={{ borderLeft: "4px solid var(--color-secondary)", maxWidth: "350px", margin: "0 auto" }}>
            <div className={styles.statIcon} style={{ background: "rgba(12, 92, 54, 0.1)", color: "var(--color-secondary)" }}>
              ✓
            </div>
            <div className={styles.statInfo}>
              <span className={styles.statValue}>{stats.available}</span>
              <span className={styles.statLabel}>Programas Disponíveis</span>
            </div>
          </div>
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
                        {row.party}
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

                          if (isFirstElection) {
                            const creationText = meta && meta.foundedYear ? `Criado em ${meta.foundedYear}` : "Criado aqui";
                            if (cell.status === "available") {
                              return (
                                <td key={cIdx} className={styles.tdCell}>
                                  <span
                                    className={`${styles.statusBadge} ${styles.statusAvailable}`}
                                    title={`${row.party} - ${cell.col}\nEstado: Disponível (Primeira participação)`}
                                  >
                                    ✓ Disponível
                                  </span>
                                  <div className={styles.cellNoteGreen}>{creationText}</div>
                                  {coalitionName && (
                                    <div className={styles.cellNote}>integrou a coligação {coalitionName}</div>
                                  )}
                                </td>
                              );
                            } else {
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
                          }

                          if (cell.status === "available") {
                            return (
                              <td key={cIdx} className={styles.tdCell}>
                                <span
                                  className={`${styles.statusBadge} ${styles.statusAvailable}`}
                                  title={`${row.party} - ${cell.col}\nEstado: Disponível no sistema`}
                                >
                                  ✓ Disponível
                                </span>
                                {coalitionName && (
                                  <div className={styles.cellNote}>integrou a coligação {coalitionName}</div>
                                )}
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
