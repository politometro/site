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

interface IntelligenceInline {
  currentLegislature: string;
  parties: Party[];
  legislatures: string[];
  notices: string[];
  statistics: {
    allTime: VoteStatistics;
    byLegislature: Record<string, VoteStatistics>;
  };
}

type IntelligenceShardManifest = {
  format?: string;
  inline?: IntelligenceInline;
  shards?: Record<string, string[]>;
};

const ITEMS_PER_PAGE = 25;

/**
 * Lista preguiçosa sobre os shards públicos: só vai buscar mais shards ao
 * servidor quando a página atual precisa de mais itens que correspondam ao
 * filtro. Evita carregar os ~660 MB do arquivo completo no browser.
 */
class LazyShardList<T> {
  private shardIndex = 0;
  private buffer: T[] = [];
  private seenKeys = new Set<string>();
  loading = false;
  exhausted = false;

  constructor(
    private readonly paths: string[],
    private readonly keyOf: (item: T) => string,
  ) {}

  get loadedItems(): T[] {
    return this.buffer;
  }

  reset(): void {
    this.buffer = [];
    this.seenKeys.clear();
    this.shardIndex = 0;
    this.exhausted = false;
  }

  async ensure(
    needed: number,
    matches: (item: T) => boolean,
    onBatch: (items: T[]) => void,
  ): Promise<T[]> {
    if (this.buffer.length >= needed && !this.needsScan(matches, needed)) {
      return this.buffer;
    }
    if (this.exhausted) return this.buffer;
    this.loading = true;
    try {
      while (this.buffer.length < needed && this.shardIndex < this.paths.length) {
        const path = this.paths[this.shardIndex];
        const response = await fetch(`/${path}`, { cache: "force-cache" });
        if (!response.ok) {
          throw new Error(`Não foi possível carregar o ficheiro ${path}.`);
        }
        const shard = (await response.json()) as T[];
        this.shardIndex += 1;
        const fresh: T[] = [];
        for (const item of Array.isArray(shard) ? shard : []) {
          if (!matches(item)) continue;
          const key = this.keyOf(item);
          if (this.seenKeys.has(key)) continue;
          this.seenKeys.add(key);
          this.buffer.push(item);
          fresh.push(item);
        }
        if (fresh.length > 0) onBatch(this.buffer);
      }
      if (this.shardIndex >= this.paths.length) this.exhausted = true;
    } finally {
      this.loading = false;
    }
    return this.buffer;
  }

  /**
   * True quando o buffer visível pode crescer mesmo depois de atingir
   * `needed` — por exemplo, se um filtro aplicado ao buffer já carregado
   * ainda não varreu todos os shards. Como `ensure` já filtra ao carregar,
   * basta comparar com `needed`.
   */
  private needsScan(_matches: (item: T) => boolean, needed: number): boolean {
    return this.buffer.length < needed;
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
        // Compatibilidade com exportações antigas em ficheiro único.
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

/** Texto longo apresentado em bloco compacto, expansível a pedido. */
function ClampedText({
  text,
  className,
  lines = 4,
  threshold = 200,
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
    <>
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
          {expanded ? "Mostrar menos" : "Ler mais"}
        </button>
      )}
    </>
  );
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
          <ClampedText text={vote.subject || ""} className={styles.voteSubject} lines={3} threshold={180} />
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

function normalisePromiseKey(promise: PromiseItem): string {
  return `${promise.party}::${promise.statement.replace(/\s+/g, " ").trim().toLowerCase()}`;
}

export default function PoliticalIntelligencePanel() {
  const rootRef = useRef<HTMLDivElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [isPinned, setIsPinned] = useState(false);
  const [tab, setTab] = useState<Tab>("promessas");
  const [inline, setInline] = useState<IntelligenceInline | null>(null);
  const [loadError, setLoadError] = useState("");
  const [selectedParty, setSelectedParty] = useState("todos");
  const [selectedContest, setSelectedContest] = useState("todos");
  const [scope, setScope] = useState("atual");
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
    if (closeTimer.current) {
      clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
    setIsOpen(false);
    setIsPinned(false);
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
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("touchmove", outside);
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

  const parties = inline?.parties ?? [];

  // A legislatura corrente pode ainda não ter dados oficiais publicados.
  // Nesse caso mostramos a legislatura mais recente com dados e um aviso.
  const availableLegislatures = useMemo(
    () => Object.keys(inline?.statistics.byLegislature ?? {}),
    [inline],
  );
  const currentLegislatureHasData = useMemo(() => {
    if (!inline) return true;
    return Boolean(inline.statistics.byLegislature[inline.currentLegislature]);
  }, [inline]);
  const fallbackScope = availableLegislatures[0] ?? "";
  const effectiveScope = scope === "atual" && !currentLegislatureHasData
    ? fallbackScope
    : scope;

  const statistics = useMemo(() => {
    if (!inline) return undefined;
    const currentScope = effectiveScope === "atual"
      ? inline.currentLegislature
      : effectiveScope;
    if (currentScope === "sempre") return inline.statistics.allTime;
    return inline.statistics.byLegislature[currentScope ?? ""];
  }, [inline, effectiveScope]);

  const contests = useMemo(() => Array.from(new Set(
    (listsRef.current.promises?.loadedItems ?? [])
      .map((promise) => promise.source?.contest)
      .filter((contest): contest is string => Boolean(contest)),
  )).sort((left, right) => left.localeCompare(right, "pt")), [inline, dataTick]);

  const promiseFilter = useMemo(() => {
    return (promise: PromiseItem) =>
      (selectedParty === "todos" || promise.party === selectedParty)
      && (selectedContest === "todos" || promise.source?.contest === selectedContest);
  }, [selectedParty, selectedContest]);

  // Carrega shards de promessas progressivamente até preencher a página.
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

  const promises = listsRef.current.promises?.loadedItems ?? [];
  const totalPromisePages = Math.max(1, Math.ceil(promises.length / ITEMS_PER_PAGE));
  const paginatedPromises = useMemo(
    () => promises.slice((promisePage - 1) * ITEMS_PER_PAGE, promisePage * ITEMS_PER_PAGE),
    [promises, promisePage, dataTick],
  );

  // Carrega votações quando são realmente necessárias (separador Votações
  // ou caixas de voto em promessas com propostas associadas).
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
    [votesLoaded, dataTick],
  );

  const votes = useMemo(() => (votesRef.current ?? []).filter((vote) => {
    const currentScope = effectiveScope === "atual"
      ? inline?.currentLegislature
      : effectiveScope;
    return (currentScope === "sempre" || vote.legislature === currentScope)
      && (selectedParty === "todos" || vote.positions.some((position) => position.party === selectedParty));
  }), [inline, effectiveScope, selectedParty, votesLoaded, dataTick]);
  const totalVotePages = Math.max(1, Math.ceil(votes.length / ITEMS_PER_PAGE));
  const paginatedVotes = useMemo(
    () => votes.slice((votePage - 1) * ITEMS_PER_PAGE, votePage * ITEMS_PER_PAGE),
    [votes, votePage],
  );

  const articleFilter = useMemo(() => {
    return (article: Article) =>
      selectedParty === "todos" || articleMatchesParty(article, selectedParty);
  }, [selectedParty]);

  // Varre os shards de artigos em segundo plano até a página estar cheia.
  // Só arranca quando o separador Notícias é aberto (evita tráfego desnecessário).
  useEffect(() => {
    const list = listsRef.current.articles;
    if (!list || !inline || tab !== "noticias") return;
    let active = true;
    const needed = articlePage * ITEMS_PER_PAGE;
    if (list.loadedItems.length < needed && !list.exhausted) {
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

  const articles = listsRef.current.articles?.loadedItems ?? [];
  const totalArticlePages = Math.max(1, Math.ceil(articles.length / ITEMS_PER_PAGE));
  const paginatedArticles = useMemo(
    () => articles.slice((articlePage - 1) * ITEMS_PER_PAGE, articlePage * ITEMS_PER_PAGE),
    [articles, articlePage, dataTick],
  );

  // Europa/Orçamentos usam o ficheiro compacto de promessas com ligações.
  useEffect(() => {
    if (tab !== "europa" && tab !== "orcamentos") return;
    const list = listsRef.current.matched;
    if (!list || matchedPromises) return;
    list
      .ensure(
        Number.MAX_SAFE_INTEGER,
        () => true,
        (items) => {
          setMatchedPromises([...items]);
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
    if (!statistics || !comparisonLeft || !comparisonRight || comparisonLeft === comparisonRight) return null;
    return statistics.pairs.find((pair) => (
      (pair.left === comparisonLeft && pair.right === comparisonRight)
      || (pair.left === comparisonRight && pair.right === comparisonLeft)
    )) ?? null;
  }, [statistics, comparisonLeft, comparisonRight]);

  const setPanelTab = (nextTab: Tab) => {
    setTab(nextTab);
    setPromisePage(1);
    setVotePage(1);
    setArticlePage(1);
  };

  const articlesExhausted = listsRef.current.articles?.exhausted ?? false;
  const promisesExhausted = listsRef.current.promises?.exhausted ?? false;

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
              <select value={selectedParty} onChange={(event) => {
                setSelectedParty(event.target.value);
                setPromisePage(1);
                setVotePage(1);
                setArticlePage(1);
              }}>
                <option value="todos">Todos os partidos</option>
                {parties.map((party) => <option key={party.id} value={party.id}>{party.name}</option>)}
              </select>
            </label>
            {tab === "promessas" && contests.length > 0 && (
              <label>
                Eleição / origem
                <select value={selectedContest} onChange={(event) => {
                  setSelectedContest(event.target.value);
                  setPromisePage(1);
                }}>
                  <option value="todos">Todos os concursos</option>
                  {contests.map((contest) => <option key={contest} value={contest}>{contest}</option>)}
                </select>
              </label>
            )}
            {tab === "votacoes" && (
              <label>
                Período
                <select value={effectiveScope} onChange={(event) => setScope(event.target.value)}>
                  <option value="atual">
                    Legislatura atual{inline?.currentLegislature ? ` (${inline.currentLegislature})` : ""}
                    {!currentLegislatureHasData && " — sem dados"}
                  </option>
                  <option value="sempre">Todos os tempos</option>
                  {(inline?.legislatures ?? [])
                    .filter((value) => value !== inline?.currentLegislature)
                    .map((value) => (
                      <option key={value} value={value}>{value} Legislatura</option>
                    ))}
                </select>
              </label>
            )}
          </div>

          <div className={styles.panelBody}>
            {loadError && <p className={styles.error}>{loadError}</p>}
            {!inline && !loadError && <p className={styles.empty}>A preparar o quadro público…</p>}

            {tab === "votacoes" && inline && !currentLegislatureHasData && effectiveScope !== "sempre" && (
              <p className={styles.empty}>
                Ainda não existem dados oficiais publicados para a {inline.currentLegislature}ª Legislatura
                {" "}(a sincronização semanal procura-os automaticamente). A mostrar a {fallbackScope}ª Legislatura,
                a mais recente com dados.
              </p>
            )}

            {inline && tab === "promessas" && (
              <div className={styles.promiseList}>
                {promises.length === 0 && promisesLoading && (
                  <p className={styles.empty}>A carregar promessas do arquivo…</p>
                )}
                {promises.length === 0 && !promisesLoading && promisesExhausted && (
                  <p className={styles.empty}>Ainda não há promessas verificáveis neste filtro. A próxima sincronização irá preencher esta área.</p>
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
                          lines={5}
                          threshold={260}
                        />
                        <small>
                          {promise.origin === "noticia"
                            ? "Promessa identificada numa notícia"
                            : `Programa eleitoral${promise.source?.contest ? ` — ${promise.source.contest}` : ""}`}
                        </small>
                        {promise.source?.url ? (
                          <a className={styles.sourceLink} href={promise.source.url} target="_blank" rel="noreferrer">{sourceLabel(promise.source)}</a>
                        ) : <small>{sourceLabel(promise.source ?? {})}</small>}
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
                              <ClampedText
                                text={proposalTitle(proposal)}
                                className={styles.proposalTitle}
                                lines={3}
                                threshold={180}
                              />
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
                        {relatedProposals.length > 0 ? relatedProposals.map(({ proposal, matchingVotes, pendingVotes, key }) => (
                          <div className={styles.proposalVotes} key={`${key}-votes`}>
                            <small className={styles.voteGroupLabel}>Votações associadas: {proposalTitle(proposal)}</small>
                            {matchingVotes.length > 0 ? matchingVotes.map((vote) => (
                              <VoteBox key={vote.id} vote={vote} parties={parties} />
                            )) : pendingVotes && !votesLoaded ? (
                              <p className={styles.noMatch}>A carregar votações associadas…</p>
                            ) : (
                              <p className={styles.noMatch}>Sem votação oficial associada até ao momento.</p>
                            )}
                          </div>
                        )) : <p className={styles.noMatch}>Sem votação oficial associada até ao momento.</p>}
                      </section>
                    </article>
                  );
                })}
                {promisesLoading && promises.length > 0 && (
                  <p className={styles.empty}>A carregar mais promessas do arquivo…</p>
                )}
                {totalPromisePages > 1 && (
                  <div className={styles.pagination}>
                    <button
                      type="button"
                      className={styles.pageButton}
                      onClick={() => setPromisePage((p) => Math.max(1, p - 1))}
                      disabled={promisePage === 1}
                    >
                      Anterior
                    </button>
                    <span className={styles.pageInfo}>
                      Página {promisePage} de {totalPromisePages} ({promises.length}{promisesExhausted ? "" : "+"} promessas)
                    </span>
                    <button
                      type="button"
                      className={styles.pageButton}
                      onClick={() => setPromisePage((p) => Math.min(totalPromisePages, p + 1))}
                      disabled={promisePage === totalPromisePages}
                    >
                      Próxima
                    </button>
                  </div>
                )}
              </div>
            )}

            {inline && tab === "votacoes" && <>
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
                      {parties.map((party) => <option key={party.id} value={party.id}>{party.name}</option>)}
                    </select>
                    <span>vs.</span>
                    <select value={comparisonRight} onChange={(event) => setComparisonRight(event.target.value)} aria-label="Segundo partido">
                      {parties.map((party) => <option key={party.id} value={party.id}>{party.name}</option>)}
                    </select>
                  </div>
                  {comparison ? <p className={styles.compareResult}><strong>{comparison.agreementRate ?? 0}%</strong> de concordância em {comparison.bothObserved} votações com posição observada por ambos ({comparison.same} iguais, {comparison.different} diferentes).</p> : <p className={styles.noMatch}>Não há ainda posições observadas em comum para esta comparação.</p>}
                </section>
              </> : <p className={styles.empty}>Ainda não há estatísticas para este período.</p>}

              <section className={styles.rawVotes}>
                <h3>Votações detalhadas</h3>
                {!votesLoaded && <p className={styles.empty}>A carregar votações detalhadas…</p>}
                {votesLoaded && votes.length === 0 && <p className={styles.empty}>Ainda não há votações detalhadas neste filtro.</p>}
                {paginatedVotes.map((vote) => <VoteBox key={vote.id} vote={vote} parties={parties} />)}
                {totalVotePages > 1 && (
                  <div className={styles.pagination}>
                    <button
                      type="button"
                      className={styles.pageButton}
                      onClick={() => setVotePage((p) => Math.max(1, p - 1))}
                      disabled={votePage === 1}
                    >
                      Anterior
                    </button>
                    <span className={styles.pageInfo}>
                      Página {votePage} de {totalVotePages} ({votes.length} votações)
                    </span>
                    <button
                      type="button"
                      className={styles.pageButton}
                      onClick={() => setVotePage((p) => Math.min(totalVotePages, p + 1))}
                      disabled={votePage === totalVotePages}
                    >
                      Próxima
                    </button>
                  </div>
                )}
              </section>
            </>}

            {inline && tab === "noticias" && (
              <div className={styles.articleList}>
                {articles.length === 0 && articlesLoading && <p className={styles.empty}>A carregar notícias do arquivo…</p>}
                {articles.length === 0 && !articlesLoading && articlesExhausted && (
                  <p className={styles.empty}>Ainda não foram recolhidos excertos noticiosos permitidos.</p>
                )}
                {paginatedArticles.map((article) => (
                  <article key={article.id} className={styles.articleCard}>
                    <div><span>{article.source}</span><time>{formatDate(article.publishedAt)}</time></div>
                    <h3>{article.title}</h3>
                    <ClampedText
                      text={article.summary || article.excerpt}
                      className={styles.articleSummary}
                      lines={3}
                      threshold={180}
                    />
                    <a className={styles.sourceLink} href={article.url} target="_blank" rel="noreferrer">Ler na fonte</a>
                  </article>
                ))}
                {articlesLoading && articles.length > 0 && (
                  <p className={styles.empty}>A carregar mais notícias do arquivo…</p>
                )}
                {totalArticlePages > 1 && (
                  <div className={styles.pagination}>
                    <button
                      type="button"
                      className={styles.pageButton}
                      onClick={() => setArticlePage((p) => Math.max(1, p - 1))}
                      disabled={articlePage === 1}
                    >
                      Anterior
                    </button>
                    <span className={styles.pageInfo}>
                      Página {articlePage} de {totalArticlePages} ({articles.length}{articlesExhausted ? "" : "+"} notícias)
                    </span>
                    <button
                      type="button"
                      className={styles.pageButton}
                      onClick={() => setArticlePage((p) => Math.min(totalArticlePages, p + 1))}
                      disabled={articlePage === totalArticlePages}
                    >
                      Próxima
                    </button>
                  </div>
                )}
              </div>
            )}

            {inline && tab === "europa" && (
              <div>
                <p className={styles.empty}>
                  Sugestões automáticas entre promessas portuguesas e iniciativas do Parlamento Europeu (OEIL, dados oficiais). Tudo requer revisão humana.
                </p>
                {!matchedPromises && <p className={styles.empty}>A carregar correspondências europeias…</p>}
                {matchedPromises && matchedForTabs.length === 0 && (
                  <p className={styles.empty}>Ainda não há correspondências europeias sugeridas neste filtro.</p>
                )}
                {matchedForTabs.slice(0, 50).map((promise) => (
                  <article key={promise.id} className={styles.promiseRow}>
                    <section className={styles.promiseCell}>
                      <span className={styles.partyTag}>{partyName(promise.party, parties)}</span>
                      <ClampedText
                        text={promise.statement}
                        className={styles.promiseStatement}
                        lines={4}
                        threshold={240}
                      />
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
              </div>
            )}

            {inline && tab === "orcamentos" && (
              <div>
                <p className={styles.empty}>
                  Ligações automáticas entre promessas e rubricas dos Orçamentos do Estado e de documentação orçamental da UE (PDF em arquivo). Requerem sempre revisão humana.
                </p>
                {!matchedPromises && <p className={styles.empty}>A carregar ligações orçamentais…</p>}
                {matchedPromises && matchedForTabs.length === 0 && (
                  <p className={styles.empty}>Ainda não há ligações orçamentais sugeridas neste filtro.</p>
                )}
                {matchedForTabs.slice(0, 50).map((promise) => (
                  <article key={promise.id} className={styles.promiseRow}>
                    <section className={styles.promiseCell}>
                      <span className={styles.partyTag}>{partyName(promise.party, parties)}</span>
                      <ClampedText
                        text={promise.statement}
                        className={styles.promiseStatement}
                        lines={4}
                        threshold={240}
                      />
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
              </div>
            )}
          </div>

          {inline && <footer className={styles.panelFooter}>
            <p>As propostas semelhantes, os resultados e as ligações são confirmados antes de serem tratados como conclusões.</p>
            <p>As notícias são apresentadas como resumos curtos com referência à fonte.</p>
            <p>A revisão humana das ligações (propostas, UE e orçamentos) é contínua.</p>
          </footer>}
        </aside>
      )}
    </div>
  );
}
