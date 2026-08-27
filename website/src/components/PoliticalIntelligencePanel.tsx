"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import styles from "./PoliticalIntelligencePanel.module.css";

type Tab = "promessas" | "votacoes" | "noticias" | "europa" | "orcamentos";
type VotePosition = "favor" | "contra" | "abstencao" | "ausencia";
type VoteOutcomeKind = "aprovada" | "rejeitada" | "sem_resultado";

interface Party {
  id: string;
  name: string;
  kind: "party" | "coalition" | string;
}

interface Source {
  type?: string;
  title?: string;
  url?: string;
  publisher?: string;
  filename?: string;
  page?: number;
  year?: number | string;
  contest?: string;
}

interface ProposalMatch {
  initiativeId?: string;
  number?: string;
  title?: string;
  authors?: string[];
  authorRelationLabel?: string;
  sourceUrl?: string;
  source?: Source;
  matchKind?: "direta" | "aproximada" | string;
  approximate?: boolean;
  reviewRequired?: boolean;
  voteIds?: string[];
}

interface PresidentAction {
  kind?: string;
  date?: string | null;
  phaseLabel?: string;
  sourceUrl?: string;
}

interface VoteOutcome {
  initiativeId?: string;
  bid?: string;
  number?: string;
  title?: string;
  outcome?: VoteOutcomeKind | string;
  status?: string;
  presidentAction?: PresidentAction;
  positionsByParty?: Record<string, VotePosition | string>;
  votes?: Array<{ id?: string; date?: string; result?: string; phase?: string }>;
}

interface EuropeanMatch {
  initiativeId?: string;
  identifier?: string;
  title?: string;
  status?: string;
  date?: string;
  sourceUrl?: string;
  reviewRequired?: boolean;
}

interface BudgetMatch {
  budgetChunkId?: string;
  budgetDocId?: string;
  category?: string;
  year?: number | null;
  filename?: string;
  page?: number | null;
  rubricPreview?: string;
  governmentLabel?: string | null;
  reviewRequired?: boolean;
}

interface BudgetDocument {
  id: string;
  category?: string;
  year?: number | null;
  filename?: string;
  relPath?: string;
  chunkCount?: number;
  preview?: string;
  governmentLabel?: string | null;
  matchCount?: number;
}

interface PromiseItem {
  id: string;
  party: string;
  statement: string;
  origin: string;
  source: Source;
  proposalMatches: ProposalMatch[];
  voteOutcomes?: VoteOutcome[];
  europeanMatches?: EuropeanMatch[];
  budgetMatches?: BudgetMatch[];
  reviewRequired: boolean;
}

interface Vote {
  id: string;
  initiativeId?: string;
  legislature?: string;
  date?: string;
  subject?: string;
  phase?: string;
  result?: string;
  positions: Array<{ party: string; position: VotePosition | string }>;
  sourceUrl?: string;
}

interface ArticleEntity {
  id?: string;
  kind?: string;
  affiliations?: string[];
}

interface Article {
  id: string;
  source: string;
  url: string;
  title: string;
  summary: string;
  excerpt: string;
  publishedAt?: string;
  topics?: string[];
  entities?: ArticleEntity[];
}

interface StatisticRow {
  id: string;
  name: string;
  proposalsPresented: number;
  proposalsApproved: number;
  proposalsRejected: number;
  proposalsWithoutResult: number;
  votesFor: number;
  votesAgainst: number;
  abstentions: number;
  absences: number;
  observedVotes: number;
}

interface VoteStatistics {
  parties: StatisticRow[];
  governments: StatisticRow[];
  pairs: Array<{
    left: string;
    right: string;
    bothObserved: number;
    same: number;
    different: number;
    agreementRate: number | null;
  }>;
  initiativeCount: number;
  voteCount: number;
}

interface IntelligenceData {
  currentLegislature: string;
  parties: Party[];
  legislatures: string[];
  notices: string[];
  promises: PromiseItem[];
  votes: Vote[];
  articles: Article[];
  europeanUnion?: {
    initiativesMatched?: Array<{
      id?: string;
      identifier?: string;
      label?: string;
      title?: string;
      type?: string;
      status?: string;
      parliamentaryTerm?: number | null;
      sourceUrl?: string;
    }>;
    proceduresKnown?: number;
    votesKnown?: number;
  };
  budgets?: {
    documents?: BudgetDocument[];
    documentsKnown?: number;
  };
  statistics: {
    allTime: VoteStatistics;
    byLegislature: Record<string, VoteStatistics>;
  };
}

type IntelligenceShardManifest = {
  format?: string;
  inline?: Record<string, unknown>;
  shards?: Record<string, string[]>;
};

async function loadIntelligenceData(): Promise<IntelligenceData> {
  const response = await fetch("/political-intelligence.json", { cache: "no-store" });
  if (!response.ok) throw new Error("Não foi possível carregar o quadro público.");
  const payload = (await response.json()) as IntelligenceData | IntelligenceShardManifest;
  if ((payload as IntelligenceShardManifest).format !== "political-intelligence-shards") {
    return payload as IntelligenceData;
  }

  const manifest = payload as IntelligenceShardManifest;
  const shardEntries = Object.entries(manifest.shards ?? {});
  const arrays = await Promise.all(
    shardEntries.map(async ([key, paths]) => {
      const parts = await Promise.all(
        paths.map(async (path) => {
          const shardResponse = await fetch(`/${path}`, { cache: "no-store" });
          if (!shardResponse.ok) throw new Error(`Não foi possível carregar o shard ${path}.`);
          const shard = await shardResponse.json();
          return Array.isArray(shard) ? shard : [];
        }),
      );
      return [key, parts.flat()] as const;
    }),
  );
  return {
    ...(manifest.inline ?? {}),
    ...Object.fromEntries(arrays),
  } as unknown as IntelligenceData;
}

const POSITION_LABELS: Record<string, string> = {
  favor: "A favor",
  contra: "Contra",
  abstencao: "Abstenção",
  ausencia: "Ausência",
};

function formatDate(value?: string): string {
  if (!value) return "Data não publicada";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  return new Intl.DateTimeFormat("pt-PT", { dateStyle: "medium" }).format(date);
}

function partyName(id: string, parties: Party[]): string {
  return parties.find((party) => party.id === id)?.name ?? "Partido não identificado";
}

function sourceLabel(source: Source): string {
  if (source.publisher) return source.publisher;
  if (source.type === "programa_eleitoral") return "Ver programa eleitoral";
  return source.title || "Ver fonte";
}

function isApproximateMatch(proposal: ProposalMatch): boolean {
  return proposal.approximate === true || proposal.matchKind === "aproximada";
}

function proposalMatchLabel(proposal: ProposalMatch): string {
  return isApproximateMatch(proposal) ? "Proposta semelhante" : "Proposta correspondente";
}

function proposalTitle(proposal: ProposalMatch): string {
  return proposal.title || "Proposta relacionada";
}

function proposalSourceUrl(proposal: ProposalMatch): string | undefined {
  return proposal.sourceUrl || proposal.source?.url;
}

function outcomeLabel(outcome?: VoteOutcomeKind | string): string {
  switch (outcome) {
    case "aprovada":
      return "Aprovada ✓";
    case "rejeitada":
      return "Rejeitada ✗";
    default:
      return "Sem resultado";
  }
}

function outcomeClass(outcome?: VoteOutcomeKind | string): string {
  switch (outcome) {
    case "aprovada":
      return styles.outcomeApproved;
    case "rejeitada":
      return styles.outcomeRejected;
    default:
      return styles.outcomeNone;
  }
}

function presidentActionLabel(action?: PresidentAction, president?: string): string {
  if (!action) return "";
  const kind = action.kind === "promulgada"
    ? "Promulgada"
    : action.kind === "veto"
      ? "Veto"
      : (action.phaseLabel || "Decisão presidencial");
  const when = action.date ? ` em ${formatDate(action.date)}` : "";
  const who = president ? ` por ${president}` : "";
  return `${kind}${when}${who}`;
}

function positionLabel(position?: string): string {
  return position
    ? POSITION_LABELS[position] ?? "Posição não publicada"
    : "Sem posição publicada";
}

function BudgetDocCategoryLabel(category?: string): string {
  switch (category) {
    case "pt_estado":
      return "Orçamento do Estado";
    case "ue_bce":
      return "Orçamento UE — BCE";
    case "ue_mff":
      return "Quadro Financeiro Plurianual da UE";
    default:
      return category || "Orçamento";
  }
}

function positionClass(position?: string): string {
  switch (position) {
    case "favor":
      return styles.favor;
    case "contra":
      return styles.contra;
    case "abstencao":
      return styles.abstencao;
    case "ausencia":
      return styles.ausencia;
    default:
      return styles.unpublished;
  }
}

function articleMatchesParty(article: Article, partyId: string): boolean {
  return (article.entities ?? []).some((entity) => {
    if (entity.kind === "party" || entity.kind === "coalition") {
      return entity.id === partyId;
    }
    if (entity.kind === "person" || entity.kind === "youth_wing") {
      return entity.affiliations?.includes(partyId) ?? false;
    }
    return false;
  });
}

function VoteBox({ vote, parties }: { vote: Vote; parties: Party[] }) {
  const [expanded, setExpanded] = useState(false);
  const positionByParty = useMemo(
    () => new Map(vote.positions.map((position) => [position.party, position.position])),
    [vote.positions],
  );

  return (
    <section className={styles.voteBox}>
      <button
        type="button"
        className={styles.voteToggle}
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
      >
        <span>
          <strong>{vote.result || "Resultado ainda não publicado"}</strong>
          <small>{vote.date ? formatDate(vote.date) : "Data não publicada"}</small>
        </span>
        <span aria-hidden="true" className={expanded ? styles.chevronOpen : styles.chevron}>⌄</span>
      </button>

      {expanded && (
        <div className={styles.voteExpanded}>
          {vote.subject && <p className={styles.voteSubject}>{vote.subject}</p>}
          <div className={styles.positionGrid} aria-label="Sentidos de voto por partido">
            {parties.map((party) => {
              const position = positionByParty.get(party.id);
              return (
                <div className={styles.positionRow} key={party.id}>
                  <span>{party.name}</span>
                  <span className={`${styles.positionBadge} ${positionClass(position)}`}>
                    {positionLabel(position)}
                  </span>
                </div>
              );
            })}
          </div>
          <p className={styles.voteCaveat}>Quando não existe posição indicada, o quadro mostra apenas “Sem posição publicada”.</p>
          {vote.sourceUrl && (
            <a href={vote.sourceUrl} target="_blank" rel="noreferrer" className={styles.sourceLink}>
              Consultar registo oficial
            </a>
          )}
        </div>
      )}
    </section>
  );
}

function StatisticsTable({
  rows,
  title,
  showVotePositions = true,
}: {
  rows: StatisticRow[];
  title: string;
  showVotePositions?: boolean;
}) {
  if (rows.length === 0) {
    return <p className={styles.empty}>Ainda não há períodos ou resultados oficiais suficientes para este resumo.</p>;
  }

  return (
    <section className={styles.statisticsSection}>
      <h3>{title}</h3>
      <div className={styles.tableScroll}>
        <table className={styles.statisticsTable}>
          <thead>
            <tr>
              <th>Entidade</th>
              <th>Propostas</th>
              <th>Aprovadas</th>
              <th>Rejeitadas</th>
              <th>Sem resultado</th>
              {showVotePositions && <>
                <th>A favor</th>
                <th>Contra</th>
                <th>Abst.</th>
                <th>Aus.</th>
              </>}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <th scope="row">{row.name}</th>
                <td>{row.proposalsPresented}</td>
                <td>{row.proposalsApproved}</td>
                <td>{row.proposalsRejected}</td>
                <td>{row.proposalsWithoutResult}</td>
                {showVotePositions && <>
                  <td>{row.votesFor}</td>
                  <td>{row.votesAgainst}</td>
                  <td>{row.abstentions}</td>
                  <td>{row.absences}</td>
                </>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function PoliticalIntelligencePanel() {
  const rootRef = useRef<HTMLDivElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [isPinned, setIsPinned] = useState(false);
  const [tab, setTab] = useState<Tab>("promessas");
  const [data, setData] = useState<IntelligenceData | null>(null);
  const [loadError, setLoadError] = useState("");
  const [selectedParty, setSelectedParty] = useState("todos");
  const [selectedContest, setSelectedContest] = useState("todos");
  const [scope, setScope] = useState("atual");
  const [comparisonLeft, setComparisonLeft] = useState("");
  const [comparisonRight, setComparisonRight] = useState("");
  const [promiseLimit, setPromiseLimit] = useState(20);
  const [voteLimit, setVoteLimit] = useState(12);
  const [articleLimit, setArticleLimit] = useState(30);

  const close = () => {
    if (closeTimer.current) {
      clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
    setIsOpen(false);
    setIsPinned(false);
  };

  useEffect(() => {
    let active = true;
    loadIntelligenceData()
      .then((payload) => {
        if (!active) return;
        setData(payload);
        const firstParty = payload.parties[0]?.id ?? "";
        setComparisonLeft(firstParty);
        setComparisonRight(payload.parties[1]?.id ?? firstParty);
      })
      .catch((error: unknown) => {
        if (active) setLoadError(error instanceof Error ? error.message : "Não foi possível carregar os dados.");
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    const outside = (event: Event) => {
      const target = event.target;
      if (target instanceof Node && !rootRef.current?.contains(target)) close();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("touchmove", outside, { passive: true });
    document.addEventListener("scroll", outside, true);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("touchmove", outside);
      document.removeEventListener("scroll", outside, true);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [isOpen]);

  useEffect(() => () => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
  }, []);

  const canHover = () => typeof window !== "undefined"
    && window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  const openOnHover = () => {
    if (!canHover()) return;
    if (closeTimer.current) clearTimeout(closeTimer.current);
    setIsOpen(true);
  };
  const closeOnLeave = () => {
    if (!canHover() || isPinned) return;
    closeTimer.current = setTimeout(() => setIsOpen(false), 180);
  };

  const currentScope = scope === "atual" ? data?.currentLegislature : scope;
  const statistics = data
    ? (currentScope === "sempre" ? data.statistics.allTime : data.statistics.byLegislature[currentScope ?? ""])
    : undefined;
  const contests = useMemo(() => Array.from(new Set(
    (data?.promises ?? [])
      .map((promise) => promise.source?.contest)
      .filter((contest): contest is string => Boolean(contest)),
  )).sort((left, right) => left.localeCompare(right, "pt")), [data]);
  const promises = useMemo(() => (data?.promises ?? []).filter((promise) => (
    (selectedParty === "todos" || promise.party === selectedParty)
    && (selectedContest === "todos" || promise.source?.contest === selectedContest)
  )), [data, selectedParty, selectedContest]);
  const votes = useMemo(() => (data?.votes ?? []).filter((vote) => (
    (!currentScope || currentScope === "sempre" || vote.legislature === currentScope)
    && (selectedParty === "todos" || vote.positions.some((position) => position.party === selectedParty))
  )), [data, currentScope, selectedParty]);
  const articles = useMemo(() => (data?.articles ?? []).filter((article) => (
    selectedParty === "todos" || articleMatchesParty(article, selectedParty)
  )), [data, selectedParty]);
  const voteById = useMemo(() => new Map((data?.votes ?? []).map((vote) => [vote.id, vote])), [data]);
  const comparison = useMemo(() => {
    if (!statistics || !comparisonLeft || !comparisonRight || comparisonLeft === comparisonRight) return null;
    return statistics.pairs.find((pair) => (
      (pair.left === comparisonLeft && pair.right === comparisonRight)
      || (pair.left === comparisonRight && pair.right === comparisonLeft)
    )) ?? null;
  }, [statistics, comparisonLeft, comparisonRight]);

  const setPanelTab = (nextTab: Tab) => {
    setTab(nextTab);
    setPromiseLimit(20);
    setVoteLimit(12);
    setArticleLimit(30);
  };

  return (
    <div
      ref={rootRef}
      className={styles.root}
      onMouseEnter={openOnHover}
      onMouseLeave={closeOnLeave}
    >
      <button
        type="button"
        className={styles.trigger}
        aria-expanded={isOpen}
        aria-controls="political-intelligence-panel"
        onClick={() => {
          if (isOpen && isPinned) {
            close();
          } else {
            setIsOpen(true);
            setIsPinned(true);
          }
        }}
      >
        <span className={styles.triggerIcon} aria-hidden="true">▦</span>
        <span>
          <strong>Promessas &amp; votos</strong>
          <small>Dados e posições por partido</small>
        </span>
      </button>

      {isOpen && (
        <aside id="political-intelligence-panel" className={`${styles.panel} glass`} role="dialog" aria-label="Promessas e votações">
          <div className={styles.panelHeader}>
            <div>
              <p className={styles.eyebrow}>Politómetro em atualização</p>
              <h2>Promessas, propostas e votos</h2>
            </div>
            <button type="button" className={styles.closeButton} onClick={close} aria-label="Fechar quadro">×</button>
          </div>

          <p className={styles.intro}>Veja a promessa, a proposta relacionada e a posição de todos os partidos, sem sair desta página.</p>

          <div className={styles.tabs} role="tablist" aria-label="Secções do quadro">
            {([
              ["promessas", "Promessas"],
              ["votacoes", "Votações"],
              ["noticias", "Notícias"],
              ["europa", "Europa"],
              ["orcamentos", "Orçamentos"],
            ] as const).map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={tab === value}
                className={tab === value ? styles.activeTab : styles.tab}
                onClick={() => setPanelTab(value)}
              >
                {label}
              </button>
            ))}
          </div>

          <div className={styles.filters}>
            <label>
              Partido
              <select value={selectedParty} onChange={(event) => setSelectedParty(event.target.value)}>
                <option value="todos">Todos os partidos</option>
                {(data?.parties ?? []).map((party) => <option key={party.id} value={party.id}>{party.name}</option>)}
              </select>
            </label>
            {tab === "promessas" && contests.length > 0 && (
              <label>
                Eleição / origem
                <select value={selectedContest} onChange={(event) => setSelectedContest(event.target.value)}>
                  <option value="todos">Todos os concursos</option>
                  {contests.map((contest) => <option key={contest} value={contest}>{contest}</option>)}
                </select>
              </label>
            )}
            {tab === "votacoes" && (
              <label>
                Período
                <select value={scope} onChange={(event) => setScope(event.target.value)}>
                  <option value="atual">Legislatura atual{data?.currentLegislature ? ` (${data.currentLegislature})` : ""}</option>
                  <option value="sempre">Todos os tempos</option>
                  {(data?.legislatures ?? []).filter((value) => value !== data?.currentLegislature).map((value) => (
                    <option key={value} value={value}>{value} Legislatura</option>
                  ))}
                </select>
              </label>
            )}
          </div>

          <div className={styles.panelBody}>
            {loadError && <p className={styles.error}>{loadError}</p>}
            {!data && !loadError && <p className={styles.empty}>A preparar o quadro público…</p>}

            {data && tab === "promessas" && (
              <div className={styles.promiseList}>
                {promises.length === 0 && <p className={styles.empty}>Ainda não há promessas verificáveis neste filtro. A próxima sincronização irá preencher esta área.</p>}
                {promises.slice(0, promiseLimit).map((promise) => {
                  const relatedProposals = (promise.proposalMatches ?? []).map((proposal, index) => ({
                    proposal,
                    matchingVotes: (proposal.voteIds ?? [])
                      .map((id) => voteById.get(id))
                      .filter((vote): vote is Vote => Boolean(vote)),
                    key: `${promise.id}-proposal-${index}`,
                  }));
                  return (
                    <article key={promise.id} className={styles.promiseRow}>
                      <section className={styles.promiseCell}>
                        <span className={styles.partyTag}>{partyName(promise.party, data.parties)}</span>
                        <p>{promise.statement}</p>
                        <small>
                          {promise.origin === "noticia"
                            ? "Promessa identificada numa notícia"
                            : `Programa eleitoral${promise.source.contest ? ` — ${promise.source.contest}` : ""}`}
                        </small>
                        {promise.source.url ? (
                          <a className={styles.sourceLink} href={promise.source.url} target="_blank" rel="noreferrer">{sourceLabel(promise.source)}</a>
                        ) : <small>{sourceLabel(promise.source)}</small>}
                      </section>
                      <section className={styles.proposalCell}>
                        {relatedProposals.length > 0 ? relatedProposals.map(({ proposal, key }) => {
                          const officialSourceUrl = proposalSourceUrl(proposal);
                          const authorRelationLabel = proposal.authorRelationLabel?.trim();
                          const outcome = (promise.voteOutcomes ?? []).find(
                            (item) => item.initiativeId === proposal.initiativeId,
                          );
                          return (
                            <div className={styles.proposalMatch} key={key}>
                              <span className={`${styles.matchBadge} ${isApproximateMatch(proposal) ? styles.approximate : styles.direct}`}>
                                {proposalMatchLabel(proposal)}
                              </span>
                              {outcome ? (
                                <span className={`${styles.outcomeBadge} ${outcomeClass(outcome.outcome)}`}>
                                  {outcomeLabel(outcome.outcome)}
                                </span>
                              ) : null}
                              <p>{proposal.number && <strong>{proposal.number} · </strong>}{proposalTitle(proposal)}</p>
                              {authorRelationLabel && <small className={styles.authorRelation}>{authorRelationLabel}</small>}
                              {outcome?.presidentAction && (
                                <small className={styles.authorRelation}>
                                  {presidentActionLabel(outcome.presidentAction, "Presidente da República")}
                                </small>
                              )}
                              {outcome && outcome.positionsByParty && Object.keys(outcome.positionsByParty).length > 0 && (
                                <small className={styles.authorRelation}>
                                  Posições: {Object.entries(outcome.positionsByParty)
                                    .map(([party, position]) => `${party} ${positionLabel(position)}`).join(" · ")}
                                </small>
                              )}
                              {proposal.reviewRequired !== false && <small className={styles.review}>Esta ligação é informativa e está a ser confirmada pela equipa.</small>}
                              {officialSourceUrl && (
                                <a className={styles.sourceLink} href={officialSourceUrl} target="_blank" rel="noreferrer">
                                  Registo oficial da Assembleia
                                </a>
                              )}
                            </div>
                          );
                        }) : <p className={styles.noMatch}>Ainda não foi encontrada uma proposta relacionada.</p>}
                      </section>
                      <section className={styles.votingCell}>
                        {relatedProposals.length > 0 ? relatedProposals.map(({ proposal, matchingVotes, key }) => (
                          <div className={styles.proposalVotes} key={`${key}-votes`}>
                            <small className={styles.voteGroupLabel}>Votações associadas: {proposalTitle(proposal)}</small>
                            {matchingVotes.length > 0 ? matchingVotes.map((vote) => (
                              <VoteBox key={vote.id} vote={vote} parties={data.parties} />
                            )) : <p className={styles.noMatch}>Sem votação oficial associada até ao momento.</p>}
                          </div>
                        )) : <p className={styles.noMatch}>Sem votação oficial associada até ao momento.</p>}
                      </section>
                    </article>
                  );
                })}
                {promises.length > promiseLimit && <button type="button" className={styles.moreButton} onClick={() => setPromiseLimit((limit) => limit + 20)}>Mostrar mais promessas</button>}
              </div>
            )}

            {data && tab === "votacoes" && <>
              {statistics ? <>
                <div className={styles.summaryLine}>
                  <span>{statistics.initiativeCount} propostas</span>
                  <span>{statistics.voteCount} votações oficiais reunidas</span>
                </div>
                <StatisticsTable rows={statistics.parties} title="Resumo por partido" />
                <StatisticsTable rows={statistics.governments} title="Resumo por governo" showVotePositions={false} />
                <section className={styles.compareSection}>
                  <h3>Partido vs. partido</h3>
                  <div className={styles.compareControls}>
                    <select value={comparisonLeft} onChange={(event) => setComparisonLeft(event.target.value)} aria-label="Primeiro partido">
                      {data.parties.map((party) => <option key={party.id} value={party.id}>{party.name}</option>)}
                    </select>
                    <span>vs.</span>
                    <select value={comparisonRight} onChange={(event) => setComparisonRight(event.target.value)} aria-label="Segundo partido">
                      {data.parties.map((party) => <option key={party.id} value={party.id}>{party.name}</option>)}
                    </select>
                  </div>
                  {comparison ? <p className={styles.compareResult}><strong>{comparison.agreementRate ?? 0}%</strong> de concordância em {comparison.bothObserved} votações com posição observada por ambos ({comparison.same} iguais, {comparison.different} diferentes).</p> : <p className={styles.noMatch}>Não há ainda posições observadas em comum para esta comparação.</p>}
                </section>
              </> : <p className={styles.empty}>Ainda não há estatísticas para este período.</p>}

              <section className={styles.rawVotes}>
                <h3>Votações detalhadas</h3>
                {votes.length === 0 ? <p className={styles.empty}>Ainda não há votações detalhadas neste filtro.</p> : votes.slice(0, voteLimit).map((vote) => <VoteBox key={vote.id} vote={vote} parties={data.parties} />)}
                {votes.length > voteLimit && <button type="button" className={styles.moreButton} onClick={() => setVoteLimit((limit) => limit + 12)}>Mostrar mais votações</button>}
              </section>
            </>}

            {data && tab === "noticias" && (
              <div className={styles.articleList}>
                {articles.length === 0 && <p className={styles.empty}>Ainda não foram recolhidos excertos noticiosos permitidos.</p>}
                {articles.slice(0, articleLimit).map((article) => (
                  <article key={article.id} className={styles.articleCard}>
                    <div><span>{article.source}</span><time>{formatDate(article.publishedAt)}</time></div>
                    <h3>{article.title}</h3>
                    <p>{article.summary || article.excerpt}</p>
                    <a className={styles.sourceLink} href={article.url} target="_blank" rel="noreferrer">Ler na fonte</a>
                  </article>
                ))}
                {articles.length > articleLimit && (
                  <button type="button" className={styles.moreButton} onClick={() => setArticleLimit((limit) => limit + 30)}>
                    Mostrar mais notícias
                  </button>
                )}
              </div>
            )}

            {data && tab === "europa" && (
              <div>
                <p className={styles.empty}>
                  Sugestões automáticas entre promessas portuguesas e iniciativas do Parlamento Europeu (OEIL, dados oficiais). Tudo requer revisão humana.
                </p>
                {promises.filter((promise) => (promise.europeanMatches ?? []).length > 0).length === 0 ? (
                  <p className={styles.empty}>Ainda não há correspondências europeias sugeridas neste filtro.</p>
                ) : promises
                  .filter((promise) => (promise.europeanMatches ?? []).length > 0)
                  .slice(0, promiseLimit)
                  .map((promise) => (
                    <article key={promise.id} className={styles.promiseRow}>
                      <section className={styles.promiseCell}>
                        <span className={styles.partyTag}>{partyName(promise.party, data.parties)}</span>
                        <p>{promise.statement}</p>
                        <small>{promise.origin === "noticia" ? "Promessa identificada numa notícia" : "Programa eleitoral"}</small>
                      </section>
                      <section className={styles.proposalCell}>
                        {(promise.europeanMatches ?? []).map((eu, index) => (
                          <div className={styles.proposalMatch} key={`${promise.id}-eu-${index}`}>
                            <span className={`${styles.matchBadge} ${styles.approximate}`}>Proposta europeia</span>
                            <p>{eu.identifier && <strong>{eu.identifier} · </strong>}{eu.title || "Dossiê europeu"}</p>
                            {eu.status && <small className={styles.authorRelation}>Fase: {eu.status}</small>}
                            <small className={styles.review}>Ligação automática sujeita a confirmação pela equipa.</small>
                            {eu.sourceUrl && (
                              <a className={styles.sourceLink} href={eu.sourceUrl} target="_blank" rel="noreferrer">
                                Registo oficial do PE
                              </a>
                            )}
                          </div>
                        ))}
                      </section>
                    </article>
                  ))}
                {promises.filter((promise) => (promise.europeanMatches ?? []).length > 0).length > promiseLimit && (
                  <button type="button" className={styles.moreButton} onClick={() => setPromiseLimit((limit) => limit + 20)}>Mostrar mais correspondências</button>
                )}
              </div>
            )}

            {data && tab === "orcamentos" && (
              <div>
                <p className={styles.empty}>
                  Ligações automáticas entre promessas e rubricas dos Orçamentos do Estado e de documentação orçamental da UE (PDF em arquivo). Requerem sempre revisão humana.
                </p>
                {promises.filter((promise) => (promise.budgetMatches ?? []).length > 0).length === 0 ? (
                  <p className={styles.empty}>Ainda não há ligações orçamentais sugeridas neste filtro.</p>
                ) : promises
                  .filter((promise) => (promise.budgetMatches ?? []).length > 0)
                  .slice(0, promiseLimit)
                  .map((promise) => (
                    <article key={promise.id} className={styles.promiseRow}>
                      <section className={styles.promiseCell}>
                        <span className={styles.partyTag}>{partyName(promise.party, data.parties)}</span>
                        <p>{promise.statement}</p>
                        <small>{promise.origin === "noticia" ? "Promessa identificada numa notícia" : "Programa eleitoral"}</small>
                      </section>
                      <section className={styles.proposalCell}>
                        {(promise.budgetMatches ?? []).map((br, index) => (
                          <div className={styles.proposalMatch} key={`${promise.id}-br-${index}`}>
                            <span className={`${styles.matchBadge} ${styles.approximate}`}>{BudgetDocCategoryLabel(br.category)}</span>
                            <p>
                              <strong>{br.year ? `Orçamento ${br.year} · ` : ""}</strong>{br.filename || "Documento orçamental"}
                              {typeof br.page === "number" ? ` (pág. ${br.page})` : ""}
                            </p>
                            <small className={styles.authorRelation}>{br.governmentLabel || "Período por classificar"}</small>
                            {br.rubricPreview && <p className={styles.rubricPreview}>{br.rubricPreview}</p>}
                            <small className={styles.review}>Ligação automática sujeita a confirmação pela equipa.</small>
                          </div>
                        ))}
                      </section>
                    </article>
                  ))}
                {promises.filter((promise) => (promise.budgetMatches ?? []).length > 0).length > promiseLimit && (
                  <button type="button" className={styles.moreButton} onClick={() => setPromiseLimit((limit) => limit + 20)}>Mostrar mais ligações</button>
                )}
              </div>
            )}
          </div>

          {data && <footer className={styles.panelFooter}>
            <p>As propostas semelhantes, os resultados e as ligações são confirmados antes de serem tratados como conclusões.</p>
            <p>As notícias são apresentadas como resumos curtos com referência à fonte.</p>
            <p>A revisão humana das ligações (propostas, UE e orçamentos) é contínua.</p>
          </footer>}
        </aside>
      )}
    </div>
  );
}
