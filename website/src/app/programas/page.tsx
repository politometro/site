"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./page.module.css";
import {
  classifyStatement,
  OTHER_CATEGORY_LABEL,
  PROGRAM_CATEGORIES,
} from "@/lib/programCategories";

interface ProgramIndexEntry {
  party: string;
  year: string;
  filename: string;
  relPath: string;
  contest: string;
  promiseCount: number;
}

interface ProgramIndex {
  schemaVersion: number;
  programmes: ProgramIndexEntry[];
}

interface PromiseItem {
  id: string;
  party: string;
  statement: string;
  origin: string;
  source: {
    type?: string;
    filename?: string;
    relPath?: string;
    year?: number | string;
    contest?: string;
  };
}

interface LoadedProgram {
  key: string;
  entry: ProgramIndexEntry;
  proposals: PromiseItem[];
  byCategory: Map<string, PromiseItem[]>;
}

const MAX_PROGRAMS = 4;
const MAX_PROGRAMS_MOBILE = 2;
const PROPOSAL_STEP = 8;
const PROPOSAL_STEP_INITIAL = 8;

function programmeKey(entry: ProgramIndexEntry): string {
  return `${entry.party}|${entry.year}|${entry.relPath}`;
}

function entryLabel(entry: ProgramIndexEntry): string {
  return `${entry.contest || "Programa eleitoral"} · ${entry.year}`;
}

function categoryLabel(categoryId: string): string {
  return (
    PROGRAM_CATEGORIES.find((category) => category.id === categoryId)?.label ??
    OTHER_CATEGORY_LABEL
  );
}

function normalise(text: string): string {
  return text
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase();
}

export default function ProgramasPage() {
  const [index, setIndex] = useState<ProgramIndex | null>(null);
  const [loadError, setLoadError] = useState("");
  const [columns, setColumns] = useState<(ProgramIndexEntry | null)[]>([null, null]);
  const [programmes, setProgrammes] = useState<LoadedProgram[]>([]);
  const [loadingStep, setLoadingStep] = useState(0);
  const [loadingTotal, setLoadingTotal] = useState(0);
  const [isMobile, setIsMobile] = useState(false);
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());
  const [visibleByCategory, setVisibleByCategory] = useState<Record<string, number>>({});
  const loadRunRef = useRef(0);

  useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth < 768);
    handleResize();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  const maxPrograms = isMobile ? MAX_PROGRAMS_MOBILE : MAX_PROGRAMS;

  useEffect(() => {
    let active = true;
    fetch("/programas-index.json", { cache: "force-cache" })
      .then((response) => {
        if (!response.ok) throw new Error("Não foi possível carregar o índice de programas.");
        return response.json() as Promise<ProgramIndex>;
      })
      .then((payload) => {
        if (active) setIndex(payload);
      })
      .catch((error: unknown) => {
        if (active) {
          setLoadError(error instanceof Error ? error.message : "Falha ao carregar índice.");
        }
      });
    return () => {
      active = false;
    };
  }, []);

  // Ajusta o número de colunas ao limite do ecrã.
  useEffect(() => {
    setColumns((current) => {
      const filled = current.filter(Boolean).length;
      const targetSlots = Math.max(Math.min(filled + 1, maxPrograms), 2);
      if (current.length === targetSlots) return current;
      const next = current.slice(0, targetSlots);
      while (next.length < targetSlots) next.push(null);
      return next;
    });
  }, [maxPrograms]);

  const selectedKeys = useMemo(
    () =>
      columns
        .filter((entry): entry is ProgramIndexEntry => Boolean(entry))
        .map(programmeKey),
    [columns],
  );

  const usedKeys = useMemo(() => new Set(selectedKeys), [selectedKeys]);

  // Carrega as propostas automaticamente com 2+ programas escolhidos.
  useEffect(() => {
    if (!index || selectedKeys.length < 2) {
      setProgrammes([]);
      return;
    }
    const runId = loadRunRef.current + 1;
    loadRunRef.current = runId;
    let cancelled = false;
    (async () => {
      try {
        const wanted = new Map(
          index.programmes.map((entry) => [programmeKey(entry), entry]),
        );
        const wantedSelected = selectedKeys
          .map((key) => wanted.get(key))
          .filter((entry): entry is ProgramIndexEntry => Boolean(entry));
        const shards = await discoverPromiseShards();
        if (cancelled || loadRunRef.current !== runId) return;
        setLoadingTotal(shards.length);
        setLoadingStep(0);
        const collected = new Map<string, PromiseItem[]>();
        wantedSelected.forEach((entry) => collected.set(programmeKey(entry), []));
        for (let step = 0; step < shards.length; step += 1) {
          const response = await fetch(`/${shards[step]}`, { cache: "force-cache" });
          if (!response.ok) throw new Error("Falha ao carregar o arquivo de promessas.");
          const shard = (await response.json()) as PromiseItem[];
          for (const promise of Array.isArray(shard) ? shard : []) {
            if (promise.origin !== "programa_eleitoral") continue;
            const key = `${promise.party}|${String(promise.source?.year ?? "")}|${String(
              promise.source?.relPath ?? "",
            )}`;
            const bucket = collected.get(key);
            if (bucket) bucket.push(promise);
          }
          if (cancelled || loadRunRef.current !== runId) return;
          setLoadingStep(step + 1);
        }
        const loaded: LoadedProgram[] = wantedSelected.map((entry) => {
          const proposals = (collected.get(programmeKey(entry)) ?? []).sort((left, right) =>
            left.statement.localeCompare(right.statement, "pt"),
          );
          const byCategory = new Map<string, PromiseItem[]>();
          for (const proposal of proposals) {
            const category = classifyStatement(proposal.statement);
            const bucket = byCategory.get(category.id) ?? [];
            bucket.push(proposal);
            byCategory.set(category.id, bucket);
          }
          return { key: programmeKey(entry), entry, proposals, byCategory };
        });
        if (!cancelled && loadRunRef.current === runId) {
          setProgrammes(loaded);
          setLoadingStep(0);
          setLoadingTotal(0);
        }
      } catch (error: unknown) {
        if (!cancelled) {
          setLoadError(error instanceof Error ? error.message : "Falha ao carregar promessas.");
          setLoadingStep(0);
          setLoadingTotal(0);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [index, selectedKeys]);

  const setColumn = (slot: number, entry: ProgramIndexEntry | null) => {
    setColumns((current) => {
      const next = [...current];
      next[slot] = entry;
      // Se todas as colunas estão preenchidas e ainda há espaço, acrescenta
      // uma nova caixa "Comparar com".
      if (entry && next.every(Boolean) && next.length < maxPrograms) {
        next.push(null);
      }
      return next;
    });
  };

  const removeColumn = (slot: number) => {
    setColumns((current) => {
      const next = current.filter((_, index) => index !== slot);
      if (next.length < 2) next.push(null);
      return next;
    });
  };

  const toggleCategory = (categoryId: string) => {
    setExpandedCategories((current) => {
      const next = new Set(current);
      if (next.has(categoryId)) {
        next.delete(categoryId);
      } else {
        next.add(categoryId);
      }
      return next;
    });
    setVisibleByCategory((current) =>
      current[categoryId] === undefined
        ? { ...current, [categoryId]: PROPOSAL_STEP_INITIAL }
        : current,
    );
  };

  const loadMoreCategory = useCallback((categoryId: string) => {
    setVisibleByCategory((current) => ({
      ...current,
      [categoryId]: (current[categoryId] ?? PROPOSAL_STEP_INITIAL) + PROPOSAL_STEP,
    }));
  }, []);

  // Categorias ordenadas por volume conjunto; "Outras medidas" no fim.
  const categoryOrder = useMemo(() => {
    const totals = new Map<string, number>();
    programmes.forEach((programme) => {
      programme.byCategory.forEach((proposals, categoryId) => {
        totals.set(categoryId, (totals.get(categoryId) ?? 0) + proposals.length);
      });
    });
    const ordered = [...totals.entries()]
      .sort((left, right) => right[1] - left[1])
      .map(([categoryId]) => categoryId);
    const fallbackIndex = ordered.indexOf("outras");
    if (fallbackIndex >= 0) {
      ordered.splice(fallbackIndex, 1);
      ordered.push("outras");
    }
    return ordered;
  }, [programmes]);

  return (
    <main className={styles.main}>
      <section className={`${styles.card} glass`}>
        <p className={styles.eyebrow}>Comparação eleitoral</p>
        <h1>Programas eleitorais lado a lado</h1>
        <p className={styles.intro}>
          Escolha os programas a comparar — entre <strong>2 e {maxPrograms}</strong>.
          As propostas são apresentadas no próprio site, por categoria temática,
          com o texto integral de cada medida.
          {isMobile && " No telemóvel o limite é de 2 programas."}
        </p>

        {loadError && <p className={styles.error}>{loadError}</p>}
        {!index && !loadError && <p className={styles.empty}>A carregar o índice de programas…</p>}

        {index && (
          <>
            <div
              className={styles.searchRow}
              style={{ gridTemplateColumns: `repeat(${columns.length}, minmax(0, 1fr))` }}
            >
              {columns.map((entry, slot) => (
                <ProgrammeSearch
                  key={slot}
                  slot={slot}
                  entry={entry}
                  index={index}
                  usedKeys={usedKeys}
                  label={slot === 0 ? "Programa" : "Comparar com"}
                  onSelect={(selected) => setColumn(slot, selected)}
                  onClear={() => setColumn(slot, null)}
                  onRemove={
                    columns.length > 2 && selectedKeys.length > 2
                      ? () => removeColumn(slot)
                      : undefined
                  }
                />
              ))}
            </div>

            {loadingStep > 0 && (
              <div className={styles.progressRow} role="status">
                <span>A carregar programas… ({loadingStep}/{loadingTotal})</span>
                <div className={styles.progressTrack}>
                  <div
                    className={styles.progressFill}
                    style={{
                      width: `${Math.round((loadingStep / Math.max(1, loadingTotal)) * 100)}%`,
                    }}
                  />
                </div>
              </div>
            )}

            {selectedKeys.length === 1 && (
              <p className={styles.notice}>
                Selecione pelo menos mais um programa para comparar.
              </p>
            )}

            {programmes.length >= 2 && (
              <div className={styles.results}>
                <div
                  className={styles.columnHeaders}
                  style={{ gridTemplateColumns: `repeat(${programmes.length}, minmax(0, 1fr))` }}
                >
                  {programmes.map((programme) => (
                    <div key={programme.key} className={styles.columnHeader}>
                      <strong>{programme.entry.party}</strong>
                      <small>{entryLabel(programme.entry)}</small>
                      <small>{programme.proposals.length} propostas no arquivo</small>
                    </div>
                  ))}
                </div>

                {categoryOrder.map((categoryId) => {
                  const expanded = expandedCategories.has(categoryId);
                  const visibleCount =
                    visibleByCategory[categoryId] ?? PROPOSAL_STEP_INITIAL;
                  const total = programmes.reduce(
                    (sum, programme) =>
                      sum + (programme.byCategory.get(categoryId)?.length ?? 0),
                    0,
                  );
                  return (
                    <section key={categoryId} className={styles.categoryBlock}>
                      <button
                        type="button"
                        className={`${styles.categoryToggle} ${expanded ? styles.categoryToggleOpen : ""}`}
                        aria-expanded={expanded}
                        onClick={() => toggleCategory(categoryId)}
                      >
                        <span aria-hidden="true" className={styles.categoryChevron}>⌄</span>
                        <span className={styles.categoryName}>{categoryLabel(categoryId)}</span>
                        <small className={styles.categoryCount}>
                          {total} {total === 1 ? "proposta" : "propostas"}
                        </small>
                      </button>
                      {expanded && (
                        <div
                          className={styles.categoryGrid}
                          style={{
                            gridTemplateColumns: `repeat(${
                              isMobile ? Math.min(2, programmes.length) : programmes.length
                            }, minmax(0, 1fr))`,
                          }}
                        >
                          {programmes.map((programme) => {
                            const proposals = programme.byCategory.get(categoryId) ?? [];
                            const visible = proposals.slice(0, visibleCount);
                            const hasMore = proposals.length > visible.length;
                            return (
                              <div key={programme.key} className={styles.categoryColumn}>
                                <span className={styles.partyTag}>{programme.entry.party}</span>
                                {proposals.length === 0 ? (
                                  <p className={styles.emptyCell}>Sem propostas nesta categoria.</p>
                                ) : (
                                  <>
                                    <div
                                      className={`${styles.proposalScroll} ${hasMore ? styles.fadeBottom : ""}`}
                                    >
                                      <ul className={styles.proposalList}>
                                        {visible.map((proposal) => (
                                          <ExpandableProposal
                                            key={proposal.id}
                                            statement={proposal.statement}
                                          />
                                        ))}
                                      </ul>
                                    </div>
                                    {hasMore ? (
                                      <button
                                        type="button"
                                        className={styles.loadMoreButton}
                                        onClick={() => loadMoreCategory(categoryId)}
                                      >
                                        Carregar mais
                                      </button>
                                    ) : (
                                      <span
                                        className={styles.loadMorePlaceholder}
                                        aria-hidden="true"
                                      />
                                    )}
                                  </>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </section>
                  );
                })}
              </div>
            )}
          </>
        )}
      </section>
    </main>
  );
}

/** Caixa de pesquisa com dropdown imediato ao foco (estilo do site). */
function ProgrammeSearch({
  slot,
  entry,
  index,
  usedKeys,
  label,
  onSelect,
  onClear,
  onRemove,
}: {
  slot: number;
  entry: ProgramIndexEntry | null;
  index: ProgramIndex;
  usedKeys: Set<string>;
  label: string;
  onSelect: (entry: ProgramIndexEntry) => void;
  onClear: () => void;
  onRemove?: () => void;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  const options = useMemo(() => {
    const needle = normalise(query.trim());
    return index.programmes.filter((candidate) => {
      if (usedKeys.has(programmeKey(candidate))) return false;
      if (!needle) return true;
      return normalise(
        `${candidate.party} ${candidate.year} ${candidate.contest}`,
      ).includes(needle);
    });
  }, [index, query, usedKeys]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query, open]);

  const choose = (candidate: ProgramIndexEntry) => {
    onSelect(candidate);
    setQuery("");
    setOpen(false);
    inputRef.current?.blur();
  };

  return (
    <div className={styles.searchBox} ref={rootRef}>
      <div className={styles.searchLabelRow}>
        <label className={styles.searchLabel} htmlFor={`programa-${slot}`}>
          {label}
        </label>
        {onRemove && (
          <button
            type="button"
            className={styles.linkButton}
            onClick={onRemove}
            title="Remover esta coluna"
          >
            remover
          </button>
        )}
      </div>
      {entry ? (
        <div className={styles.selectedCard}>
          <button
            type="button"
            className={styles.selectedProgram}
            onClick={() => {
              onClear();
              inputRef.current?.focus();
            }}
            title="Trocar de programa"
          >
            <strong>{entry.party}</strong>
            <small>{entryLabel(entry)}</small>
            <span className={styles.selectedSwap} aria-hidden="true">⇄</span>
          </button>
        </div>
      ) : (
        <div className={styles.searchWrapper}>
          <input
            id={`programa-${slot}`}
            ref={inputRef}
            type="text"
            className={styles.searchInput}
            placeholder="Pesquisar programa…"
            value={query}
            autoComplete="off"
            onChange={(event) => {
              setQuery(event.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setOpen(true);
                setActiveIndex((current) =>
                  Math.min(current + 1, options.length - 1),
                );
              } else if (event.key === "ArrowUp") {
                event.preventDefault();
                setActiveIndex((current) => Math.max(current - 1, 0));
              } else if (event.key === "Enter" && open && options[activeIndex]) {
                event.preventDefault();
                choose(options[activeIndex]);
              } else if (event.key === "Escape") {
                setOpen(false);
              }
            }}
          />
          {open && (
            <div className={styles.dropdown} role="listbox" aria-label={`${label} — opções`}>
              {options.length === 0 && (
                <p className={styles.dropdownEmpty}>Nenhum programa encontrado.</p>
              )}
              {options.map((candidate, optionIndex) => (
                <button
                  type="button"
                  key={programmeKey(candidate)}
                  role="option"
                  aria-selected={optionIndex === activeIndex}
                  className={`${styles.dropdownOption} ${
                    optionIndex === activeIndex ? styles.dropdownOptionActive : ""
                  }`}
                  onMouseEnter={() => setActiveIndex(optionIndex)}
                  onClick={() => choose(candidate)}
                >
                  <strong>{candidate.party}</strong>
                  <span>{entryLabel(candidate)}</span>
                  <small>{candidate.promiseCount} propostas</small>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ExpandableProposal({ statement }: { statement: string }) {
  const [expanded, setExpanded] = useState(false);
  const long = statement.length > 220;
  return (
    <li className={styles.proposal}>
      <p className={long && !expanded ? styles.clamped : undefined}>
        {statement}
      </p>
      {long && (
        <button
          type="button"
          className={styles.linkButton}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? "Mostrar menos" : "Ler mais"}
        </button>
      )}
    </li>
  );
}

async function discoverPromiseShards(): Promise<string[]> {
  const response = await fetch("/political-intelligence.json", { cache: "no-store" });
  if (!response.ok) throw new Error("Não foi possível carregar o manifesto de dados.");
  const manifest = (await response.json()) as {
    shards?: Record<string, string[]>;
  };
  return manifest.shards?.promises ?? [];
}
