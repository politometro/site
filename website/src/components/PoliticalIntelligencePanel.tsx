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
  categoryLabel?: string;
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

interface EuropeanProcedure {
  id?: string;
  identifier?: string;
  label?: string;
  title?: string;
  type?: string;
  status?: string;
  parliamentaryTerm?: number;
  sourceUrl?: string;
}

interface IntelligenceInline {
  currentLegislature: string;
  parties: Party[];
  legislatures: string[];
  notices: string[];
  statistics: {
    allTime: VoteStatistics;
    byLegislature: Record<string, VoteStatistics>;
  };
  europeanUnion?: {
    initiativesMatched?: EuropeanProcedure[];
    proceduresKnown?: number;
    votesKnown?: number;
  };
  budgets?: {
    documents?: BudgetDocument[];
    documentsKnown?: number;
  };
}

interface IntelligenceShardManifest {
  format?: string;
  inline?: IntelligenceInline;
  shards?: Record<string, string[]>;
}

const ITEMS_PER_PAGE = 25;

/**
 * Lista preguiçosa sobre os shards públicos: descarrega shards progressivamente
 * mantendo todos os registos em memória e aplicando os filtros ativos de forma
 * instantânea e reativa.
 */
class LazyShardList<T> {
  private shardIndex = 0;
  private allLoaded: T[] = [];
  private seenKeys = new Set<string>();
  loading = false;
  exhausted = false;

  constructor(
    private readonly paths: string[],
    private readonly keyOf: (item: T) => string,
  ) {}

  getLoaded(filter?: (item: T) => boolean): T[] {
    return filter ? this.allLoaded.filter(filter) : this.allLoaded;
  }

  reset(): void {
    this.allLoaded = [];
    this.seenKeys.clear();
    this.shardIndex = 0;
    this.exhausted = false;
    this.loading = false;
  }

  async ensure(
    needed: number,
    matches: (item: T) => boolean,
    onBatch: () => void,
  ): Promise<T[]> {
    if (this.allLoaded.filter(matches).length >= needed) {
      return this.allLoaded.filter(matches);
    }
    if (this.exhausted) return this.allLoaded.filter(matches);

    this.loading = true;
    try {
      while (
        this.allLoaded.filter(matches).length < needed &&
        this.shardIndex < this.paths.length
      ) {
        const path = this.paths[this.shardIndex];
        const response = await fetch(`/${path}`, { cache: "force-cache" });
        if (!response.ok) {
          throw new Error(`Não foi possível carregar o ficheiro ${path}.`);
        }
        const shard = (await response.json()) as T[];
        this.shardIndex += 1;
        let freshCount = 0;
        for (const item of Array.isArray(shard) ? shard : []) {
          const key = this.keyOf(item);
          if (this.seenKeys.has(key)) continue;
          this.seenKeys.add(key);
          this.allLoaded.push(item);
          if (matches(item)) freshCount += 1;
        }
        if (freshCount > 0) {
          onBatch();
        }
      }
      if (this.shardIndex >= this.paths.length) {
        this.exhausted = true;
      }
    } finally {
      this.loading = false;
    }
    return this.allLoaded.filter(matches);
  }
}

function loadIntelligenceManifest(): Promise<{
  inline: IntelligenceInline;
  shards: Record<string, string[]>;
}> {
  return fetch("/political-intelligence.json", { cache: "no-store" }).then(
    async (response) => {
      if (!response.ok) {
        throw new Error("Não foi possível carregar o quadro público.");
      }
      const payload = (await response.json()) as
        | IntelligenceShardManifest
        | (IntelligenceInline & Record<string, unknown>);
      if ((payload as IntelligenceShardManifest).format !== "political-intelligence-shards") {
        const legacy = payload as IntelligenceInline & {
          promises?: PromiseItem[];
          votes?: Vote[];
          articles?: Article[];
        };
        return {
          inline: legacy,
          shards: {
            promises: legacy.promises ?? [],
            votes: legacy.votes ?? [],
            articles: legacy.articles ?? [],
          } as unknown as Record<string, string[]>,
        };
      }
      const manifest = payload as IntelligenceShardManifest;
      return {
        inline: (manifest.inline ?? {}) as IntelligenceInline,
        shards: manifest.shards ?? {},
      };
    },
  );
}

function formatDate(value?: string): string {
  if (!value) return "Data não publicada";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  return new Intl.DateTimeFormat("pt-PT", { dateStyle: "medium" }).format(date);
}

function partyName(id: string, parties: Party[]): string {
  return parties.find((party) => party.id === id)?.name ?? id;
}

function sourceLabel(source: Source): string {
  if (source.publisher) return source.publisher;
  if (source.type === "programa_eleitoral") return "Programa Eleitoral";
  return source.title || "Fonte oficial";
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
      return "Sem resultado oficial";
  }
}

function canonicalOutcome(value?: string): VoteOutcomeKind | "sem_resultado" {
  const norm = (value || "").toLowerCase();
  if (norm.includes("aprovad")) return "aprovada";
  if (norm.includes("rejeitad")) return "rejeitada";
  return "sem_resultado";
}

function outcomeClass(outcome?: VoteOutcomeKind | string): string {
  switch (outcome) {
    case "aprovada":
      return styles.outcomeApproved;
    case "rejeitada":
      return styles.outcomeRejected;
    default:
      return styles.outcomePending;
  }
}

function BudgetDocCategoryLabel(category?: string): string {
  switch (category) {
    case "pt_estado":
      return "OE (Estado)";
    case "ue_bce":
      return "UE (BCE)";
    case "ue_mff":
      return "UE (QFP/MFF)";
    case "ue_regulamento":
      return "UE (Regulamento Financeiro)";
    case "ue_recursos":
      return "UE (Recursos Próprios)";
    case "ue_eurlex":
      return "UE (EUR-Lex)";
    case "ue_metadados":
      return "UE (Metadados)";
    default:
      return category || "Documento Orçamental";
  }
}

function articleMatchesParty(article: Article, partyId: string): boolean {
  return (article.entities ?? []).some((entity) => {
    if (entity.id === partyId) return true;
    return (entity.affiliations ?? []).includes(partyId);
  });
}

/** Texto longo apresentado em bloco compacto, expansível a pedido. */
function ClampedText({
  text,
  className,
  lines = 3,
  threshold = 180,
}: {
  text: string;
  className: string;
  lines?: number;
  threshold?: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const long = (text || "").length > threshold;
  if (!text) return null;
  return (
    <div className={styles.clampedWrapper}>
      <p
        className={`${className} ${long && !expanded ? styles.clamped : ""}`}
        style={long && !expanded ? ({ "--clamp-lines": lines } as React.CSSProperties) : undefined}
      >
        {text}
      </p>
      {long && (
        <button
          type="button"
          className={styles.clampToggle}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? "Mostrar menos ↑" : "Ler mais ↓"}
        </button>
      )}
    </div>
  );
}

/** Cartão de votação detalhada com sentidos de voto agrupados em badges semânticos. */
function VoteBox({ vote, parties }: { vote: Vote; parties: Party[] }) {
  const partyMap = useMemo(() => new Map(parties.map((p) => [p.id, p.name])), [parties]);

  const grouped = useMemo(() => {
    const res: Record<string, Array<{ id: string; name: string }>> = {
      favor: [],
      contra: [],
      abstencao: [],
      ausencia: [],
    };
    for (const pos of vote.positions) {
      if (res[pos.position]) {
        res[pos.position].push({
          id: pos.party,
          name: partyMap.get(pos.party) || pos.party,
        });
      }
    }
    return res;
  }, [vote.positions, partyMap]);

  const outcome = canonicalOutcome(vote.result);

  return (
    <article className={styles.voteCard}>
      <header className={styles.voteCardHeader}>
        <div className={styles.voteCardMeta}>
          <span className={`${styles.outcomeBadge} ${outcomeClass(outcome)}`}>
            {vote.result || "Sem resultado"}
          </span>
          {vote.legislature && (
            <span className={styles.legPill}>{vote.legislature} Legislatura</span>
          )}
          <span className={styles.voteDate}>
            {vote.date ? formatDate(vote.date) : "Data não indicada"}
          </span>
        </div>
        <ClampedText
          text={vote.subject || "Assunto não publicado"}
          className={styles.voteSubjectText}
          lines={3}
          threshold={140}
        />
      </header>

      <div className={styles.votePositionsGrouped} aria-label="Sentidos de voto agrupados">
        {grouped.favor.length > 0 && (
          <div className={styles.posRow}>
            <span className={`${styles.posTitle} ${styles.posFavor}`}>A Favor:</span>
            <div className={styles.pillList}>
              {grouped.favor.map((p) => (
                <span key={p.id} className={styles.pillFavor} title={p.name}>
                  {p.id}
                </span>
              ))}
            </div>
          </div>
        )}
        {grouped.contra.length > 0 && (
          <div className={styles.posRow}>
            <span className={`${styles.posTitle} ${styles.posContra}`}>Contra:</span>
            <div className={styles.pillList}>
              {grouped.contra.map((p) => (
                <span key={p.id} className={styles.pillContra} title={p.name}>
                  {p.id}
                </span>
              ))}
            </div>
          </div>
        )}
        {grouped.abstencao.length > 0 && (
          <div className={styles.posRow}>
            <span className={`${styles.posTitle} ${styles.posAbstencao}`}>Abstenção:</span>
            <div className={styles.pillList}>
              {grouped.abstencao.map((p) => (
                <span key={p.id} className={styles.pillAbstencao} title={p.name}>
                  {p.id}
                </span>
              ))}
            </div>
          </div>
        )}
        {grouped.ausencia.length > 0 && (
          <div className={styles.posRow}>
            <span className={`${styles.posTitle} ${styles.posAusencia}`}>Ausência:</span>
            <div className={styles.pillList}>
              {grouped.ausencia.map((p) => (
                <span key={p.id} className={styles.pillAusencia} title={p.name}>
                  {p.id}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      <footer className={styles.voteCardFooter}>
        {vote.phase && <small className={styles.votePhase}>Fase: {vote.phase}</small>}
        {vote.sourceUrl && (
          <a
            href={vote.sourceUrl}
            target="_blank"
            rel="noreferrer"
            className={styles.sourceLink}
          >
            Registo Oficial AR ↗
          </a>
        )}
      </footer>
    </article>
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
        <table className={styles.statisticsTable} aria-label={title}>
          <thead>
            <tr>
              <th scope="col">Entidade</th>
              <th scope="col">Propostas</th>
              <th scope="col">Aprovadas</th>
              <th scope="col">Rejeitadas</th>
              <th scope="col">Sem resultado</th>
              {showVotePositions && (
                <>
                  <th scope="col">A favor</th>
                  <th scope="col">Contra</th>
                  <th scope="col">Abst.</th>
                  <th scope="col">Aus.</th>
                </>
              )}
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
                {showVotePositions && (
                  <>
                    <td>{row.votesFor}</td>
                    <td>{row.votesAgainst}</td>
                    <td>{row.abstentions}</td>
                    <td>{row.absences}</td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function normalisePromiseKey(promise: PromiseItem): string {
  return `${promise.party}::${promise.statement.replace(/\s+/g, " ").trim().toLowerCase()}`;
}

export default function PoliticalIntelligencePanel() {
  const rootRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [tab, setTab] = useState<Tab>("promessas");
  const [inline, setInline] = useState<IntelligenceInline | null>(null);
  const [loadError, setLoadError] = useState("");
  const [selectedParty, setSelectedParty] = useState("todos");
  const [selectedContest, setSelectedContest] = useState("todos");
  const [scope, setScope] = useState("atual");
  const [searchQuery, setSearchQuery] = useState("");
  const [subView, setSubView] = useState<"matches" | "catalog">("matches");
  const [comparisonLeft, setComparisonLeft] = useState("");
  const [comparisonRight, setComparisonRight] = useState("");
  const [promisePage, setPromisePage] = useState(1);
  const [votePage, setVotePage] = useState(1);
  const [articlePage, setArticlePage] = useState(1);
  const [dataTick, setDataTick] = useState(0);
  const [articlesLoading, setArticlesLoading] = useState(false);
  const [promisesLoading, setPromisesLoading] = useState(false);
  const [matchedPromises, setMatchedPromises] = useState<PromiseItem[] | null>(null);
  const [votesLoaded, setVotesLoaded] = useState(false);

  const listsRef = useRef<{
    articles?: LazyShardList<Article>;
    promises?: LazyShardList<PromiseItem>;
    matched?: LazyShardList<PromiseItem>;
  }>({});
  const shardsRef = useRef<Record<string, string[]>>({});
  const votesRef = useRef<Vote[] | null>(null);
  const votesLoadingRef = useRef(false);
  const bump = () => setDataTick((value) => value + 1);

  const close = () => {
    setIsOpen(false);
  };

  useEffect(() => {
    let active = true;
    loadIntelligenceManifest()
      .then(({ inline: payload, shards }) => {
        if (!active) return;
        setInline(payload);
        shardsRef.current = shards;
        listsRef.current = {
          articles: new LazyShardList<Article>(
            shards.articles ?? [],
            (article) => article.id,
          ),
          promises: new LazyShardList<PromiseItem>(
            shards.promises ?? [],
            normalisePromiseKey,
          ),
          matched: new LazyShardList<PromiseItem>(
            shards["promises-matched"] ?? [],
            (promise) => promise.id,
          ),
        };
        const firstParty = payload.parties?.[0]?.id ?? "";
        setComparisonLeft(firstParty);
        setComparisonRight(payload.parties?.[1]?.id ?? firstParty);
        bump();
      })
      .catch((error: unknown) => {
        if (active) setLoadError(error instanceof Error ? error.message : "Não foi possível carregar os dados.");
      });
    return () => {
      active = false;
    };
  }, []);

  // Tecla Escape para fechar
  useEffect(() => {
    if (!isOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [isOpen]);

  const parties = inline?.parties ?? [];

  const currentLegislatureHasData = useMemo(() => {
    if (!inline) return true;
    return Boolean(inline.statistics.byLegislature[inline.currentLegislature]);
  }, [inline]);

  const statistics = useMemo(() => {
    if (!inline) return undefined;
    const currentScope = scope === "atual"
      ? inline.currentLegislature
      : scope;
    if (currentScope === "sempre") return inline.statistics.allTime;
    return inline.statistics.byLegislature[currentScope ?? ""];
  }, [inline, scope]);

  const contests = useMemo(() => Array.from(new Set(
    (listsRef.current.promises?.getLoaded() ?? [])
      .map((promise) => promise.source?.contest)
      .filter((contest): contest is string => Boolean(contest)),
  )).sort((left, right) => left.localeCompare(right, "pt")), [inline, dataTick]);

  const promiseFilter = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return (promise: PromiseItem) => {
      const matchParty = selectedParty === "todos" || promise.party === selectedParty;
      const matchContest = selectedContest === "todos" || promise.source?.contest === selectedContest;
      const matchQuery = !q || promise.statement.toLowerCase().includes(q) || (promise.source?.title || "").toLowerCase().includes(q);
      return matchParty && matchContest && matchQuery;
    };
  }, [selectedParty, selectedContest, searchQuery]);

  // Carrega shards de promessas progressivamente
  useEffect(() => {
    const list = listsRef.current.promises;
    if (!list || !inline) return;
    let active = true;
    const needed = promisePage * ITEMS_PER_PAGE;
    setPromisesLoading(list.loading);
    list
      .ensure(
        needed,
        promiseFilter,
        () => {
          if (active) bump();
        },
      )
      .then(() => {
        if (active) setPromisesLoading(list.loading);
      })
      .catch((error: unknown) => {
        if (active) setLoadError(error instanceof Error ? error.message : "Falha ao carregar promessas.");
      });
    return () => {
      active = false;
    };
  }, [inline, promisePage, promiseFilter, tab, dataTick]);

  const filteredPromises = useMemo(
    () => listsRef.current.promises?.getLoaded(promiseFilter) ?? [],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [dataTick, promiseFilter],
  );
  const totalPromisePages = Math.max(1, Math.ceil(filteredPromises.length / ITEMS_PER_PAGE));
  const paginatedPromises = useMemo(
    () => filteredPromises.slice((promisePage - 1) * ITEMS_PER_PAGE, promisePage * ITEMS_PER_PAGE),
    [filteredPromises, promisePage],
  );

  // Carrega votações
  const ensureVotes = () => {
    if (votesRef.current || votesLoadingRef.current) return;
    const shardPaths = shardsRef.current?.votes ?? [];
    if (shardPaths.length === 0) return;
    votesLoadingRef.current = true;
    Promise.all(
      shardPaths.map(async (path) => {
        const response = await fetch(`/${path}`, { cache: "force-cache" });
        if (!response.ok) throw new Error(`Não foi possível carregar ${path}.`);
        return (await response.json()) as Vote[];
      }),
    )
      .then((parts) => {
        votesRef.current = parts.flat();
        setVotesLoaded(true);
        bump();
      })
      .catch((error: unknown) => {
        setLoadError(error instanceof Error ? error.message : "Falha ao carregar votações.");
      })
      .finally(() => {
        votesLoadingRef.current = false;
      });
  };

  const needsVotes = tab === "votacoes" || paginatedPromises.some(
    (promise) => (promise.proposalMatches ?? []).length > 0,
  );
  useEffect(() => {
    if (needsVotes) ensureVotes();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needsVotes, tab]);

  const voteById = useMemo(
    () => new Map((votesRef.current ?? []).map((vote) => [vote.id, vote])),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [votesLoaded, dataTick],
  );

  const voteFilter = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    const currentScope = scope === "atual" ? inline?.currentLegislature : scope;
    return (vote: Vote) => {
      const matchScope = currentScope === "sempre" || vote.legislature === currentScope;
      const matchParty = selectedParty === "todos" || vote.positions.some((position) => position.party === selectedParty);
      const matchQuery = !q || (vote.subject || "").toLowerCase().includes(q) || (vote.result || "").toLowerCase().includes(q);
      return matchScope && matchParty && matchQuery;
    };
  }, [inline, scope, selectedParty, searchQuery]);

  const filteredVotes = useMemo(
    () => (votesRef.current ?? []).filter(voteFilter),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [voteFilter, votesLoaded, dataTick],
  );
  const totalVotePages = Math.max(1, Math.ceil(filteredVotes.length / ITEMS_PER_PAGE));
  const paginatedVotes = useMemo(
    () => filteredVotes.slice((votePage - 1) * ITEMS_PER_PAGE, votePage * ITEMS_PER_PAGE),
    [filteredVotes, votePage],
  );

  const articleFilter = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return (article: Article) => {
      const matchParty = selectedParty === "todos" || articleMatchesParty(article, selectedParty);
      const matchQuery = !q || article.title.toLowerCase().includes(q) || article.summary.toLowerCase().includes(q) || (article.source || "").toLowerCase().includes(q);
      return matchParty && matchQuery;
    };
  }, [selectedParty, searchQuery]);

  // Carrega notícias
  useEffect(() => {
    const list = listsRef.current.articles;
    if (!list || !inline || tab !== "noticias") return;
    let active = true;
    const needed = articlePage * ITEMS_PER_PAGE;
    if (list.getLoaded(articleFilter).length < needed && !list.exhausted) {
      setArticlesLoading(true);
    }
    list
      .ensure(
        needed,
        articleFilter,
        () => {
          if (active) bump();
        },
      )
      .then(() => {
        if (active) setArticlesLoading(list.loading);
      })
      .catch((error: unknown) => {
        if (active) setLoadError(error instanceof Error ? error.message : "Falha ao carregar notícias.");
      });
    return () => {
      active = false;
    };
  }, [inline, articlePage, articleFilter, tab, dataTick]);

  const filteredArticles = useMemo(
    () => listsRef.current.articles?.getLoaded(articleFilter) ?? [],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [dataTick, articleFilter],
  );
  const totalArticlePages = Math.max(1, Math.ceil(filteredArticles.length / ITEMS_PER_PAGE));
  const paginatedArticles = useMemo(
    () => filteredArticles.slice((articlePage - 1) * ITEMS_PER_PAGE, articlePage * ITEMS_PER_PAGE),
    [filteredArticles, articlePage],
  );

  // Europa / Orçamentos
  useEffect(() => {
    if (tab !== "europa" && tab !== "orcamentos") return;
    const list = listsRef.current.matched;
    if (!list || matchedPromises) return;
    list
      .ensure(
        Number.MAX_SAFE_INTEGER,
        () => true,
        () => {
          setMatchedPromises([...list.getLoaded()]);
        },
      )
      .catch((error: unknown) => {
        setLoadError(error instanceof Error ? error.message : "Falha ao carregar correspondências.");
      });
  }, [tab, matchedPromises]);

  const matchedForTabs = useMemo(() => {
    const base = (matchedPromises ?? []).filter(promiseFilter);
    if (tab === "europa") {
      return base.filter((promise) => (promise.europeanMatches ?? []).length > 0);
    }
    if (tab === "orcamentos") {
      return base.filter((promise) => (promise.budgetMatches ?? []).length > 0);
    }
    return base;
  }, [matchedPromises, promiseFilter, tab]);

  const comparison = useMemo(() => {
    if (!statistics || !comparisonLeft || !comparisonRight) return undefined;
    return statistics.pairs.find(
      (pair) =>
        (pair.left === comparisonLeft && pair.right === comparisonRight) ||
        (pair.left === comparisonRight && pair.right === comparisonLeft),
    );
  }, [statistics, comparisonLeft, comparisonRight]);

  const promisesExhausted = listsRef.current.promises?.exhausted ?? false;

  return (
    <div className={styles.root} ref={rootRef}>
      <button
        type="button"
        className={styles.trigger}
        onClick={() => setIsOpen((prev) => !prev)}
        aria-expanded={isOpen}
        aria-haspopup="dialog"
      >
        <span className={styles.triggerIcon} aria-hidden="true">🏛️</span>
        <span>
          <strong>Quadro Político Oficial</strong>
          <small>Promessas, Votações, Orçamentos & Europa</small>
        </span>
      </button>

      {isOpen && (
        <>
          <div
            className={styles.backdrop}
            onClick={close}
            aria-hidden="true"
          />
          <aside
            className={styles.panel}
            role="dialog"
            aria-modal="true"
            aria-label="Quadro Político do Politómetro"
          >
            <header className={styles.panelHeader}>
              <div>
                <p className={styles.eyebrow}>Inteligência Política e Parlamentar</p>
                <h2>Acompanhamento Oficial de Mandato</h2>
              </div>
              <div className={styles.headerActions}>
                <button
                  type="button"
                  className={styles.closeButton}
                  onClick={close}
                  aria-label="Fechar quadro político"
                >
                  ✕
                </button>
              </div>
            </header>

            <nav className={styles.tabs} aria-label="Separadores de inteligência política">
              <button
                type="button"
                className={tab === "promessas" ? styles.tabActive : styles.tab}
                onClick={() => {
                  setTab("promessas");
                  setSearchQuery("");
                }}
              >
                Promessas vs. Propostas
              </button>
              <button
                type="button"
                className={tab === "votacoes" ? styles.tabActive : styles.tab}
                onClick={() => {
                  setTab("votacoes");
                  setSearchQuery("");
                }}
              >
                Votações Oficiais
              </button>
              <button
                type="button"
                className={tab === "noticias" ? styles.tabActive : styles.tab}
                onClick={() => {
                  setTab("noticias");
                  setSearchQuery("");
                }}
              >
                Notícias
              </button>
              <button
                type="button"
                className={tab === "europa" ? styles.tabActive : styles.tab}
                onClick={() => {
                  setTab("europa");
                  setSearchQuery("");
                  setSubView("matches");
                }}
              >
                Europa (PE)
              </button>
              <button
                type="button"
                className={tab === "orcamentos" ? styles.tabActive : styles.tab}
                onClick={() => {
                  setTab("orcamentos");
                  setSearchQuery("");
                  setSubView("matches");
                }}
              >
                Orçamentos (OE/UE)
              </button>
            </nav>

            <div className={styles.filters}>
              <label>
                Filtrar por partido
                <select
                  value={selectedParty}
                  onChange={(event) => {
                    setSelectedParty(event.target.value);
                    setPromisePage(1);
                    setVotePage(1);
                    setArticlePage(1);
                  }}
                >
                  <option value="todos">Todos os partidos</option>
                  {parties.map((party) => (
                    <option key={party.id} value={party.id}>
                      {party.name}
                    </option>
                  ))}
                </select>
              </label>

              {tab === "promessas" && contests.length > 0 && (
                <label>
                  Origem / Concurso
                  <select
                    value={selectedContest}
                    onChange={(event) => {
                      setSelectedContest(event.target.value);
                      setPromisePage(1);
                    }}
                  >
                    <option value="todos">Todos os concursos</option>
                    {contests.map((contest) => (
                      <option key={contest} value={contest}>
                        {contest}
                      </option>
                    ))}
                  </select>
                </label>
              )}

              {tab === "votacoes" && (
                <label>
                  Período
                  <select
                    value={scope}
                    onChange={(event) => {
                      setScope(event.target.value);
                      setVotePage(1);
                    }}
                  >
                    <option value="atual">
                      Legislatura atual{inline?.currentLegislature ? ` (${inline.currentLegislature})` : ""}
                      {!currentLegislatureHasData && " — dados a aguardar publicação"}
                    </option>
                    <option value="sempre">Todos os tempos</option>
                    {(inline?.legislatures ?? [])
                      .filter((value) => value !== inline?.currentLegislature)
                      .map((value) => (
                        <option key={value} value={value}>
                          {value}ª Legislatura
                        </option>
                      ))}
                  </select>
                </label>
              )}

              <label className={styles.searchLabel}>
                Pesquisa
                <input
                  type="search"
                  placeholder="Pesquisar por texto, diploma ou rubrica…"
                  value={searchQuery}
                  onChange={(event) => {
                    setSearchQuery(event.target.value);
                    setPromisePage(1);
                    setVotePage(1);
                    setArticlePage(1);
                  }}
                  className={styles.searchInput}
                />
              </label>

              {(tab === "europa" || tab === "orcamentos") && (
                <div className={styles.viewToggleGroup}>
                  <button
                    type="button"
                    className={subView === "matches" ? styles.viewBtnActive : styles.viewBtn}
                    onClick={() => setSubView("matches")}
                  >
                    Promessas Ligadas
                  </button>
                  <button
                    type="button"
                    className={subView === "catalog" ? styles.viewBtnActive : styles.viewBtn}
                    onClick={() => setSubView("catalog")}
                  >
                    {tab === "europa" ? "Catálogo OEIL" : "Catálogo Documentos"}
                  </button>
                </div>
              )}
            </div>

            <div className={styles.panelBody}>
              {loadError && <p className={styles.error}>{loadError}</p>}
              {!inline && !loadError && <p className={styles.empty}>A carregar o quadro público…</p>}

              {/* TAB: PROMESSAS */}
              {inline && tab === "promessas" && (
                <div className={styles.promiseList}>
                  {filteredPromises.length === 0 && promisesLoading && (
                    <p className={styles.empty}>A carregar promessas do arquivo…</p>
                  )}
                  {filteredPromises.length === 0 && !promisesLoading && (
                    <p className={styles.empty}>
                      {promisesExhausted
                        ? "Nenhuma promessa encontrada com os filtros selecionados."
                        : "A varrer arquivos em busca de promessas…"}
                    </p>
                  )}
                  {paginatedPromises.map((promise) => {
                    const relatedProposals = (promise.proposalMatches ?? []).map((proposal, index) => ({
                      proposal,
                      matchingVotes: (proposal.voteIds ?? [])
                        .map((id) => voteById.get(id))
                        .filter((vote): vote is Vote => Boolean(vote)),
                      pendingVotes: (proposal.voteIds ?? []).some((id) => !voteById.get(id)),
                      key: `${promise.id}-proposal-${index}`,
                    }));
                    return (
                      <article key={promise.id} className={styles.promiseRow}>
                        <section className={styles.promiseCell}>
                          <span className={styles.partyTag}>{partyName(promise.party, parties)}</span>
                          <ClampedText
                            text={promise.statement}
                            className={styles.promiseStatement}
                            lines={3}
                            threshold={160}
                          />
                          <small className={styles.sourceMeta}>
                            {promise.origin === "noticia" ? "Promessa referida em notícia" : "Programa Eleitoral"}
                            {promise.source?.year ? ` (${promise.source.year})` : ""}
                            {promise.source?.url && (
                              <> · <a href={promise.source.url} target="_blank" rel="noreferrer" className={styles.sourceLink}>{sourceLabel(promise.source)}</a></>
                            )}
                          </small>
                        </section>

                        <section className={styles.proposalCell}>
                          {relatedProposals.length === 0 ? (
                            <div className={styles.proposalEmpty}>
                              <span className={styles.emptyTag}>Sem proposta idêntica identificada</span>
                              <p>Nenhuma iniciativa legislativa identificada no Parlamento nesta legislatura.</p>
                            </div>
                          ) : (
                            relatedProposals.map(({ proposal, key }) => (
                              <div className={styles.proposalMatch} key={key}>
                                <div className={styles.matchBadgeRow}>
                                  <span className={`${styles.matchBadge} ${isApproximateMatch(proposal) ? styles.approximate : styles.direct}`}>
                                    {proposalMatchLabel(proposal)}
                                  </span>
                                  {proposal.number && <strong className={styles.proposalNumber}>{proposal.number}</strong>}
                                </div>
                                <ClampedText
                                  text={proposalTitle(proposal)}
                                  className={styles.proposalText}
                                  lines={2}
                                  threshold={120}
                                />
                                {proposal.authorRelationLabel && (
                                  <small className={styles.authorRelation}>{proposal.authorRelationLabel}</small>
                                )}
                                {proposalSourceUrl(proposal) && (
                                  <a
                                    className={styles.sourceLink}
                                    href={proposalSourceUrl(proposal)}
                                    target="_blank"
                                    rel="noreferrer"
                                  >
                                    Ver no portal da AR ↗
                                  </a>
                                )}
                              </div>
                            ))
                          )}
                        </section>

                        <section className={styles.votingCell}>
                          {relatedProposals.length === 0 ? (
                            <div className={styles.proposalEmpty}>
                              <span className={styles.emptyTag}>A aguardar votação</span>
                              <p>Sem votações formais registadas para esta promessa.</p>
                            </div>
                          ) : (
                            relatedProposals.map(({ proposal, matchingVotes, pendingVotes, key }) => (
                              <div className={styles.proposalVotes} key={`${key}-votes`}>
                                <span className={styles.voteGroupLabel}>
                                  {proposal.number ? `Votações de ${proposal.number}:` : "Votações associadas:"}
                                </span>
                                {matchingVotes.length > 0 ? (
                                  matchingVotes.map((vote) => (
                                    <VoteBox key={vote.id} vote={vote} parties={parties} />
                                  ))
                                ) : (
                                  <p className={styles.noVotes}>
                                    {pendingVotes ? "Votação pendente ou em fase de comissão." : "Ainda sem votação plenária."}
                                  </p>
                                )}
                              </div>
                            ))
                          )}
                        </section>
                      </article>
                    );
                  })}

                  {totalPromisePages > 1 && (
                    <div className={styles.pagination}>
                      <button
                        type="button"
                        className={styles.pageButton}
                        onClick={() => setPromisePage((p) => Math.max(1, p - 1))}
                        disabled={promisePage === 1}
                      >
                        ← Anterior
                      </button>
                      <span className={styles.pageInfo}>
                        Página {promisePage} de {totalPromisePages} ({filteredPromises.length} promessas)
                      </span>
                      <button
                        type="button"
                        className={styles.pageButton}
                        onClick={() => setPromisePage((p) => Math.min(totalPromisePages, p + 1))}
                        disabled={promisePage === totalPromisePages}
                      >
                        Próxima →
                      </button>
                    </div>
                  )}
                </div>
              )}

              {/* TAB: VOTAÇÕES */}
              {inline && tab === "votacoes" && (
                <div className={styles.votesContainer}>
                  {scope === "atual" && !currentLegislatureHasData && (
                    <div className={styles.infoBanner}>
                      ℹ️ A {inline.currentLegislature}ª Legislatura ainda aguarda dados oficiais publicados da Assembleia da República. Pode selecionar uma legislatura anterior no menu acima para consultar todo o histórico parlamentar.
                    </div>
                  )}

                  {statistics && (
                    <>
                      <div className={styles.summaryLine}>
                        <span>{statistics.initiativeCount} propostas no período</span>
                        <span>{statistics.voteCount} votações oficiais registadas</span>
                      </div>
                      <StatisticsTable rows={statistics.parties} title="Resumo Oficial por Partido" />
                      <StatisticsTable rows={statistics.governments} title="Resumo Oficial por Governo" showVotePositions={false} />
                      <section className={styles.compareSection}>
                        <h3>Comparador de Concordância Partidária</h3>
                        <div className={styles.compareControls}>
                          <select
                            value={comparisonLeft}
                            onChange={(event) => setComparisonLeft(event.target.value)}
                            aria-label="Primeiro partido"
                          >
                            {parties.map((party) => (
                              <option key={party.id} value={party.id}>{party.name}</option>
                            ))}
                          </select>
                          <span>vs.</span>
                          <select
                            value={comparisonRight}
                            onChange={(event) => setComparisonRight(event.target.value)}
                            aria-label="Segundo partido"
                          >
                            {parties.map((party) => (
                              <option key={party.id} value={party.id}>{party.name}</option>
                            ))}
                          </select>
                        </div>
                        {comparison ? (
                          <p className={styles.compareResult}>
                            <strong>{comparison.agreementRate ?? 0}%</strong> de concordância em {comparison.bothObserved} votações com posição observada por ambos ({comparison.same} votos iguais, {comparison.different} votos diferentes).
                          </p>
                        ) : (
                          <p className={styles.noMatch}>Ainda não há posições comuns registadas para este par neste período.</p>
                        )}
                      </section>
                    </>
                  )}

                  <section className={styles.rawVotes}>
                    <h3>Votações Oficiais Detalhadas</h3>
                    {!votesLoaded && <p className={styles.empty}>A carregar registos detalhados da Assembleia da República…</p>}
                    {votesLoaded && filteredVotes.length === 0 && (
                      <p className={styles.empty}>Não foram encontradas votações com os filtros selecionados.</p>
                    )}
                    <div className={styles.votesList}>
                      {paginatedVotes.map((vote) => (
                        <VoteBox key={vote.id} vote={vote} parties={parties} />
                      ))}
                    </div>

                    {totalVotePages > 1 && (
                      <div className={styles.pagination}>
                        <button
                          type="button"
                          className={styles.pageButton}
                          onClick={() => setVotePage((p) => Math.max(1, p - 1))}
                          disabled={votePage === 1}
                        >
                          ← Anterior
                        </button>
                        <span className={styles.pageInfo}>
                          Página {votePage} de {totalVotePages} ({filteredVotes.length} votações)
                        </span>
                        <button
                          type="button"
                          className={styles.pageButton}
                          onClick={() => setVotePage((p) => Math.min(totalVotePages, p + 1))}
                          disabled={votePage === totalVotePages}
                        >
                          Próxima →
                        </button>
                      </div>
                    )}
                  </section>
                </div>
              )}

              {/* TAB: NOTÍCIAS */}
              {inline && tab === "noticias" && (
                <div className={styles.articlesList}>
                  {filteredArticles.length === 0 && articlesLoading && (
                    <p className={styles.empty}>A carregar notícias do arquivo…</p>
                  )}
                  {filteredArticles.length === 0 && !articlesLoading && (
                    <p className={styles.empty}>Nenhuma notícia encontrada com os filtros selecionados.</p>
                  )}
                  {paginatedArticles.map((article) => (
                    <article key={article.id} className={styles.articleCard}>
                      <header className={styles.articleHeader}>
                        <div className={styles.articleMeta}>
                          <span className={styles.articleSource}>{article.source}</span>
                          <span className={styles.articleDate}>{formatDate(article.publishedAt)}</span>
                        </div>
                        <h4>
                          <a href={article.url} target="_blank" rel="noreferrer" className={styles.articleTitleLink}>
                            {article.title}
                          </a>
                        </h4>
                      </header>
                      <ClampedText
                        text={article.summary || article.excerpt}
                        className={styles.articleSummary}
                        lines={3}
                        threshold={180}
                      />
                      <footer className={styles.articleFooter}>
                        <div className={styles.topicPills}>
                          {(article.topics ?? []).map((topic) => (
                            <span key={topic} className={styles.topicPill}>#{topic}</span>
                          ))}
                        </div>
                        <a href={article.url} target="_blank" rel="noreferrer" className={styles.sourceLink}>
                          Ler artigo completo ↗
                        </a>
                      </footer>
                    </article>
                  ))}

                  {totalArticlePages > 1 && (
                    <div className={styles.pagination}>
                      <button
                        type="button"
                        className={styles.pageButton}
                        onClick={() => setArticlePage((p) => Math.max(1, p - 1))}
                        disabled={articlePage === 1}
                      >
                        ← Anterior
                      </button>
                      <span className={styles.pageInfo}>
                        Página {articlePage} de {totalArticlePages} ({filteredArticles.length} notícias)
                      </span>
                      <button
                        type="button"
                        className={styles.pageButton}
                        onClick={() => setArticlePage((p) => Math.min(totalArticlePages, p + 1))}
                        disabled={articlePage === totalArticlePages}
                      >
                        Próxima →
                      </button>
                    </div>
                  )}
                </div>
              )}

              {/* TAB: EUROPA */}
              {inline && tab === "europa" && (
                <div>
                  {subView === "catalog" ? (
                    <div className={styles.catalogView}>
                      <h3>Procedimentos Oficiais do Parlamento Europeu (OEIL)</h3>
                      <p className={styles.viewIntro}>
                        Dossiês legislativos comunitários monitorizados pelo Politómetro ({inline.europeanUnion?.initiativesMatched?.length ?? 0} procedimentos associados).
                      </p>
                      <div className={styles.catalogGrid}>
                        {(inline.europeanUnion?.initiativesMatched ?? []).map((proc, index) => (
                          <div key={proc.id ?? `proc-${index}`} className={styles.catalogCard}>
                            <div className={styles.catalogCardHeader}>
                              {proc.identifier && <strong className={styles.procRef}>{proc.identifier}</strong>}
                              {proc.parliamentaryTerm && <span className={styles.procTerm}>{proc.parliamentaryTerm}ª Leg. PE</span>}
                            </div>
                            <ClampedText
                              text={proc.title || proc.label || "Dossiê Europeu"}
                              className={styles.catalogCardTitle}
                              lines={3}
                              threshold={140}
                            />
                            {proc.status && <small className={styles.catalogStatus}>Estado: {proc.status}</small>}
                            {proc.sourceUrl && (
                              <a href={proc.sourceUrl} target="_blank" rel="noreferrer" className={styles.sourceLink}>
                                Ver no Europarl OEIL ↗
                              </a>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div>
                      <p className={styles.viewIntro}>
                        Correspondências entre compromissos eleitorais e dossiês no Parlamento Europeu.
                      </p>
                      {!matchedPromises && <p className={styles.empty}>A carregar correspondências europeias…</p>}
                      {matchedPromises && matchedForTabs.length === 0 && (
                        <p className={styles.empty}>Ainda não há correspondências europeias neste filtro.</p>
                      )}
                      <div className={styles.matchedList}>
                        {matchedForTabs.slice(0, 50).map((promise) => (
                          <article key={promise.id} className={styles.promiseRowMatched}>
                            <section className={styles.promiseCell}>
                              <span className={styles.partyTag}>{partyName(promise.party, parties)}</span>
                              <ClampedText
                                text={promise.statement}
                                className={styles.promiseStatement}
                                lines={3}
                                threshold={160}
                              />
                              <small className={styles.sourceMeta}>
                                {promise.origin === "noticia" ? "Promessa referida em notícia" : "Programa Eleitoral"}
                                {promise.source?.year ? ` (${promise.source.year})` : ""}
                              </small>
                            </section>
                            <section className={styles.matchedDetailsCell}>
                              {(promise.europeanMatches ?? []).map((eu, index) => (
                                <div className={styles.matchCardSingle} key={`${promise.id}-eu-${index}`}>
                                  <div className={styles.matchCardTop}>
                                    <span className={`${styles.matchBadge} ${styles.approximate}`}>Dossiê Europeu</span>
                                    {eu.identifier && <span className={styles.refPill}>{eu.identifier}</span>}
                                  </div>
                                  <ClampedText
                                    text={eu.title || "Dossiê Comunitário"}
                                    className={styles.matchCardText}
                                    lines={3}
                                    threshold={140}
                                  />
                                  {eu.status && <small className={styles.authorRelation}>Fase no PE: {eu.status}</small>}
                                  {eu.sourceUrl && (
                                    <a className={styles.sourceLink} href={eu.sourceUrl} target="_blank" rel="noreferrer">
                                      Consultar registo oficial no PE ↗
                                    </a>
                                  )}
                                </div>
                              ))}
                            </section>
                          </article>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* TAB: ORÇAMENTOS */}
              {inline && tab === "orcamentos" && (
                <div>
                  {subView === "catalog" ? (
                    <div className={styles.catalogView}>
                      <h3>Catálogo Oficial de Documentos Orçamentais</h3>
                      <p className={styles.viewIntro}>
                        Documentação oficial de Orçamentos do Estado (Portugal) e Orçamentos Europeus em arquivo ({inline.budgets?.documents?.length ?? 0} documentos catalogados).
                      </p>
                      <div className={styles.catalogGrid}>
                        {(inline.budgets?.documents ?? []).map((doc) => (
                          <div key={doc.id} className={styles.catalogCard}>
                            <div className={styles.catalogCardHeader}>
                              <span className={styles.categoryPill}>{BudgetDocCategoryLabel(doc.category)}</span>
                              {doc.year && <strong className={styles.docYear}>{doc.year}</strong>}
                            </div>
                            <h4 className={styles.docFilename}>{doc.filename}</h4>
                            {doc.governmentLabel && (
                              <small className={styles.govPill}>{doc.governmentLabel}</small>
                            )}
                            {doc.preview && (
                              <ClampedText text={doc.preview} className={styles.docPreview} lines={3} threshold={140} />
                            )}
                            <div className={styles.catalogCardFooter}>
                              <small>{doc.chunkCount ?? 0} secções catalogadas</small>
                              <small>{doc.matchCount ?? 0} promessas ligadas</small>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div>
                      <p className={styles.viewIntro}>
                        Ligações entre compromissos eleitorais e rubricas orçamentais (OE e UE) com excertos verificáveis.
                      </p>
                      {!matchedPromises && <p className={styles.empty}>A carregar correspondências orçamentais…</p>}
                      {matchedPromises && matchedForTabs.length === 0 && (
                        <p className={styles.empty}>Ainda não há correspondências orçamentais neste filtro.</p>
                      )}
                      <div className={styles.matchedList}>
                        {matchedForTabs.slice(0, 50).map((promise) => (
                          <article key={promise.id} className={styles.promiseRowMatched}>
                            <section className={styles.promiseCell}>
                              <span className={styles.partyTag}>{partyName(promise.party, parties)}</span>
                              <ClampedText
                                text={promise.statement}
                                className={styles.promiseStatement}
                                lines={3}
                                threshold={160}
                              />
                              <small className={styles.sourceMeta}>
                                {promise.origin === "noticia" ? "Promessa referida em notícia" : "Programa Eleitoral"}
                                {promise.source?.year ? ` (${promise.source.year})` : ""}
                              </small>
                            </section>
                            <section className={styles.matchedDetailsCell}>
                              {(promise.budgetMatches ?? []).map((br, index) => (
                                <div className={styles.matchCardSingle} key={`${promise.id}-br-${index}`}>
                                  <div className={styles.matchCardTop}>
                                    <span className={`${styles.matchBadge} ${styles.approximate}`}>
                                      {br.categoryLabel || BudgetDocCategoryLabel(br.category)}
                                    </span>
                                    {br.year && <span className={styles.refPill}>Ano {br.year}</span>}
                                  </div>
                                  <p className={styles.matchDocTitle}>
                                    <strong>{br.filename}</strong>
                                    {typeof br.page === "number" ? ` (pág. ${br.page})` : ""}
                                  </p>
                                  {br.governmentLabel && (
                                    <small className={styles.authorRelation}>{br.governmentLabel}</small>
                                  )}
                                  {br.rubricPreview && (
                                    <ClampedText
                                      text={br.rubricPreview}
                                      className={styles.rubricPreview}
                                      lines={3}
                                      threshold={140}
                                    />
                                  )}
                                </div>
                              ))}
                            </section>
                          </article>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            {inline && (
              <footer className={styles.panelFooter}>
                <p>As correspondências e dados oficiais são continuamente atualizados e auditados para rigor cívico.</p>
              </footer>
            )}
          </aside>
        </>
      )}
    </div>
  );
}
