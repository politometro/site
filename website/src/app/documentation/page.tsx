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

  // Filter rows based on search
  const filteredRows = useMemo(() => {
    return rows.filter((row) =>
      row.party.toLowerCase().includes(search.toLowerCase())
    );
  }, [rows, search]);

  // Calculate statistics across all active cells in the matrix (excluding budget columns)
  const stats = useMemo(() => {
    let available = 0;

    rows.forEach((row) => {
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
                filteredRows.map((row, rIdx) => (
                  <tr key={rIdx} className={styles.tr}>
                    <td className={styles.tdParty}>{row.party}</td>
                    {row.cells
                      .filter((c) => filteredHeaders.includes(c.col))
                      .map((cell, cIdx) => {
                        if (cell.status === "available") {
                          return (
                            <td key={cIdx} className={styles.tdCell}>
                              <span
                                className={`${styles.statusBadge} ${styles.statusAvailable}`}
                                title={`${row.party} - ${cell.col}\nEstado: Disponível no sistema`}
                              >
                                ✓ Disp.
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
                ))
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
