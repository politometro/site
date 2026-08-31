import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import political_intelligence as intelligence

FIXTURES_DIR = Path(__file__).resolve().parents[1] / ".." / "scratch" / "fixtures"


class PoliticalIntelligenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entities = intelligence.json_load(intelligence.DEFAULT_ENTITIES, {})
        cls.config = intelligence.json_load(intelligence.DEFAULT_CONFIG, {})
        cls.matcher = intelligence.EntityMatcher(cls.entities)

    def test_article_parser_reads_jsonld_camel_case_dates(self):
        article = intelligence.parse_article_html(
            """<html><head><script type="application/ld+json">
            {"@type":"NewsArticle","headline":"PS apresenta proposta económica",
             "description":"Resumo curto", "datePublished":"2026-05-01T12:00:00Z",
             "articleSection":"Política", "url":"https://example.test/noticia"}
            </script></head><body><article><p>O Partido Socialista apresentou uma proposta sobre IRS.</p></article></body></html>""",
            "https://example.test/noticia",
            500,
        )
        self.assertEqual(article["title"], "PS apresenta proposta económica")
        self.assertEqual(article["publishedAt"], "2026-05-01T12:00:00Z")
        self.assertEqual(article["section"], "Política")

    def test_sitemap_parser_keeps_google_news_title_and_date(self):
        nested, entries = intelligence.parse_sitemap(
            """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
            xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
              <url><loc>https://example.test/politica</loc><news:news><news:publication_date>2026-05-01</news:publication_date><news:title>Governo apresenta orçamento</news:title></news:news></url>
            </urlset>""",
            "https://example.test/sitemap.xml",
        )
        self.assertEqual(nested, [])
        self.assertEqual(entries[0]["title"], "Governo apresenta orçamento")
        self.assertEqual(entries[0]["lastmod"], "2026-05-01")

    def test_unlimited_configuration_keeps_old_news_eligible(self):
        self.assertIsNone(self.config["crawl"]["maxUrlsPerSource"])
        self.assertIsNone(self.config["crawl"]["newsRetentionDays"])
        self.assertTrue(intelligence.recent_enough("2001-01-01", None))

    def test_monthly_archive_uses_confirmed_range_and_skips_known_gaps(self):
        source = {
            "id": "fixture",
            "homepage": "https://example.test/",
            "archiveSitemap": {
                "urlTemplate": "https://example.test/archive/{year}-{month}.xml",
                "firstAvailableMonth": "2026-01",
                "completedMonthsOnly": True,
                "refreshRecentMonths": 1,
            },
        }
        runtime_state = {
            "archiveSitemap": {
                "months": {
                    "2026-02": {"status": "unavailable"},
                    "2026-03": {"status": "complete"},
                }
            }
        }
        now = intelligence.dt.datetime(2026, 5, 18, tzinfo=intelligence.UTC)

        with mock.patch.object(intelligence, "utc_now", return_value=now):
            urls = intelligence.archive_sitemap_urls(source, runtime_state, None)

        self.assertEqual(
            urls,
            [
                ("https://example.test/archive/2026-4.xml", "2026-04"),
                ("https://example.test/archive/2026-1.xml", "2026-01"),
            ],
        )

    def test_missing_archive_month_is_remembered_after_a_confirmed_404(self):
        source = {
            "id": "fixture",
            "homepage": "https://example.test/",
            "archiveSitemap": {
                "urlTemplate": "https://example.test/archive/{year}-{month}.xml",
                "firstAvailableMonth": "2026-04",
                "completedMonthsOnly": True,
                "refreshRecentMonths": 0,
            },
        }
        runtime_state = {}

        class MissingClient:
            def text(self, url):
                raise intelligence.PipelineError(f"{url} respondeu com HTTP 404")

        now = intelligence.dt.datetime(2026, 6, 18, tzinfo=intelligence.UTC)
        with mock.patch.object(intelligence, "utc_now", return_value=now):
            records, errors = intelligence.discover_sitemap_records(
                source,
                MissingClient(),
                None,
                [],
                {"maxSitemapDepth": None, "maxSitemapsPerSource": None},
                None,
                source_runtime_state=runtime_state,
            )

        self.assertEqual(records, [])
        self.assertEqual(len(errors), 2)
        months = runtime_state["archiveSitemap"]["months"]
        self.assertEqual(months["2026-04"]["status"], "unavailable")
        self.assertEqual(months["2026-05"]["status"], "unavailable")

    def test_publico_uses_its_monthly_public_sitemap_template(self):
        publico = next(
            source for source in self.config["sources"] if source["id"] == "publico"
        )

        self.assertIn("https://www.publico.pt/sitemaps/sitemapindex.xml", publico["sitemapSeeds"])
        self.assertIn("https://www.publico.pt/sitemaps/news.xml", publico["sitemapSeeds"])
        self.assertEqual(publico["sitemapDateFloor"], "1998-01-01")

    def test_dated_sitemaps_outside_verified_range_never_reach_http(self):
        source = {
            "id": "fixture",
            "homepage": "https://example.test/",
            "sitemapSeeds": ["https://example.test/index.xml"],
            "sitemapDateFloor": "2024-03-01",
        }
        index = """<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <sitemap><loc>https://example.test/sitemap?yyyy=2024&amp;mm=02&amp;dd=29</loc></sitemap>
          <sitemap><loc>https://example.test/sitemap?yyyy=2024&amp;mm=03&amp;dd=01</loc></sitemap>
          <sitemap><loc>https://example.test/sitemap?yyyy=2026&amp;mm=05&amp;dd=19</loc></sitemap>
        </sitemapindex>"""
        leaf = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://example.test/politica/noticia</loc><lastmod>2024-03-01</lastmod></url>
        </urlset>"""

        class RecordingClient:
            def __init__(self):
                self.calls = []

            def text(self, url):
                self.calls.append(url)
                if url == "https://example.test/index.xml":
                    return index, {}, url
                if url == "https://example.test/sitemap?yyyy=2024&mm=03&dd=01":
                    return leaf, {}, url
                raise AssertionError(f"pedido HTTP proibido: {url}")

        client = RecordingClient()
        now = intelligence.dt.datetime(2026, 5, 18, tzinfo=intelligence.UTC)
        with mock.patch.object(intelligence, "utc_now", return_value=now):
            records, errors = intelligence.discover_sitemap_records(
                source,
                client,
                None,
                [],
                {"maxSitemapDepth": None, "maxSitemapsPerSource": None},
                None,
                source_runtime_state={},
            )

        self.assertEqual(errors, [])
        self.assertEqual([item["url"] for item in records], ["https://example.test/politica/noticia"])
        self.assertEqual(
            client.calls,
            [
                "https://example.test/index.xml",
                "https://example.test/sitemap?yyyy=2024&mm=03&dd=01",
            ],
        )

    def test_source_child_filters_avoid_redundant_daily_and_non_article_maps(self):
        sic = next(source for source in self.config["sources"] if source["id"] == "sic-noticias")
        observador = next(source for source in self.config["sources"] if source["id"] == "observador")
        now = intelligence.dt.datetime(2026, 5, 18, tzinfo=intelligence.UTC)
        with mock.patch.object(intelligence, "utc_now", return_value=now):
            self.assertTrue(intelligence.sitemap_child_allowed(sic, "https://sicnoticias.pt/sitemap/2015-01.xml"))
            self.assertFalse(intelligence.sitemap_child_allowed(sic, "https://sicnoticias.pt/sitemap/2015-01-01.xml"))
            self.assertFalse(intelligence.sitemap_child_allowed(sic, "https://sicnoticias.pt/sitemap/2014-12.xml"))
            self.assertTrue(intelligence.sitemap_child_allowed(observador, "https://observador.pt/wp-sitemap-posts-post-1.xml"))
            self.assertFalse(intelligence.sitemap_child_allowed(observador, "https://observador.pt/wp-sitemap-posts-podcast-1.xml"))

    def test_default_legislature_is_current_only(self):
        args = intelligence.parse_args(["news"])
        self.assertEqual(intelligence.chosen_legislatures(args, {"assembly": {"currentLegislature": "XVII"}}), ["XVII"])

    def test_assembly_does_not_mark_sync_complete_when_a_legislature_fails(self):
        state = intelligence.initial_state()
        config = {"assembly": {"enabled": True, "syncIntervalHours": 24}}
        with mock.patch.object(intelligence, "assembly_due", return_value=True), \
             mock.patch.object(intelligence, "fetch_open_data_records", side_effect=intelligence.PipelineError("timeout")), \
             mock.patch.object(intelligence, "safe_print"):
            statuses = intelligence.sync_assembly(
                state, config, self.matcher, object(), ["XVII", "XVI"], force=True
            )
        self.assertEqual([item["status"] for item in statuses], ["error", "error"])
        self.assertNotIn("lastSyncedAt", state["assembly"])

    def test_clear_news_processing_state_reopens_old_decisions(self):
        state = intelligence.initial_state()
        seen = intelligence.source_state(state, "tsf")["seen"]
        seen["old"] = {"filterVersion": "old", "decision": "article_irrelevant", "checkedAt": "x"}
        seen["current"] = {"filterVersion": intelligence.NEWS_FILTER_VERSION, "decision": "collected"}
        self.assertEqual(intelligence.clear_news_processing_state(state), 1)
        self.assertNotIn("decision", seen["old"])
        self.assertEqual(seen["current"]["decision"], "collected")

    def test_all_detail_pages_option_has_no_numeric_cap(self):
        args = intelligence.parse_args(["assembly", "--max-detail-pages", "all"])
        self.assertIsNone(args.max_detail_pages)

    def test_restart_skips_previously_checked_undated_url(self):
        state = intelligence.initial_state()
        source = {
            "id": "fixture",
            "name": "Fonte de teste",
            "homepage": "https://example.test/",
            "robotsUrl": "https://example.test/robots.txt",
            "sitemapSeeds": ["https://example.test/sitemap.xml"],
            "enabled": True,
        }
        article_id = intelligence.stable_id("news", "https://example.test/noticia")
        intelligence.source_state(state, "fixture")["seen"][article_id] = {
            "lastmod": None,
            "checkedAt": "2026-01-01T00:00:00Z",
            "filterVersion": intelligence.NEWS_FILTER_VERSION,
            "decision": "metadata_irrelevant",
        }
        config = {"crawl": {"maxUrlsPerSource": None, "newsRetentionDays": None}, "sources": [source]}
        with mock.patch.object(intelligence, "robots_policy", return_value=(None, [], None)), \
             mock.patch.object(intelligence, "iter_sitemap_records", return_value=iter([{"url": "https://example.test/noticia"}])), \
             mock.patch.object(intelligence, "fetch_article") as fetch_article:
            statuses = intelligence.sync_news(state, config, self.matcher, object(), since_days=0)
        fetch_article.assert_not_called()
        # Regressão: nenhuma fonte pode terminar em "error" (ex.: dependências
        # não inicializadas dentro do worker de cada fonte).
        self.assertEqual([s.get("status") for s in statuses], ["ok"])

    def test_rejected_news_are_printed_even_when_verbose_option_is_false(self):
        state = intelligence.initial_state()
        source = {
            "id": "fixture",
            "name": "Fonte de teste",
            "homepage": "https://example.test/",
            "enabled": True,
        }
        config = {
            "crawl": {
                "maxUrlsPerSource": None,
                "newsRetentionDays": None,
                "verboseRejections": False,
            },
            "sources": [source],
        }
        with mock.patch.object(intelligence, "robots_policy", return_value=(None, [], None)), \
             mock.patch.object(intelligence, "iter_sitemap_records", return_value=iter([{
                 "url": "https://example.test/desporto/jogo",
                 "title": "Jogo de futebol",
             }])), \
             mock.patch.object(intelligence, "safe_print") as printer:
            intelligence.sync_news(state, config, self.matcher, object(), since_days=0)

        self.assertTrue(any("Não guardada" in str(call.args[0]) for call in printer.call_args_list))

    def test_news_prefilter_rejects_irrelevant_sections_before_article_fetch(self):
        self.assertFalse(intelligence.candidate_may_be_relevant(
            {
                "url": "https://example.test/auto/governo-anuncia-novo-motor",
                "title": "Governo estrangeiro anuncia apoio ao setor automóvel",
            },
            self.matcher,
        ))
        self.assertTrue(intelligence.candidate_may_be_relevant(
            {
                "url": "https://example.test/politica/ps-apresenta-proposta",
                "title": "PS apresenta proposta sobre o Orçamento",
            },
            self.matcher,
        ))
        self.assertTrue(intelligence.candidate_may_be_relevant(
            {
                "url": "https://example.test/2026/05/medida",
                "title": "Governo reduz IRS das famílias",
            },
            self.matcher,
        ))

    def test_entities_and_vote_positions_are_conservative(self):
        entities = self.matcher.match("O PS ouviu a JSD e o Governo.")
        self.assertEqual({item["id"] for item in entities}, {"PS", "JSD", "GOVERNO"})
        jsd = next(item for item in entities if item["id"] == "JSD")
        self.assertEqual(jsd["affiliations"], ["PSD"])
        positions = intelligence.position_records(
            {
                "A Favor": "PS, PSD",
                "Contra": "CHEGA",
                "Abstenção": "BE",
                "Ausência": "IL",
            },
            self.matcher,
        )
        self.assertEqual(
            {item["party"]: item["position"] for item in positions},
            {"PS": "favor", "PSD": "favor", "CHEGA": "contra", "BE": "abstencao", "IL": "ausencia"},
        )
        self.assertIsNone(intelligence.canonical_vote_position("medida contra a pobreza"))
        self.assertEqual(
            intelligence.position_records(
                {"descricao": "Medida contra a pobreza", "resultadoVoto": "Aprovado com alterações"},
                self.matcher,
            ),
            [],
        )

    def test_real_assembly_schema_imports_government_and_nested_vote(self):
        raw = {
            "IniId": "987",
            "IniLeg": "XVII",
            "IniNr": "7",
            "IniDescTipo": "Proposta de Lei",
            "IniTitulo": "Reforça o apoio à habitação acessível",
            "IniAutorOutros": {"nome": "Governo", "sigla": "V"},
            "IniEventos": [
                {"Fase": "Entrada", "DataFase": "2026-01-10"},
                {
                    "Fase": "Aprovado",
                    "DataFase": "2026-02-01",
                    "Votacao": [{
                        "id": "4455",
                        "data": "2026-02-01",
                        "descricao": "Votação na generalidade",
                        "resultado": "Aprovado",
                        "detalhe": "A Favor: <I>PS</I>, <I>PSD</I><BR>Contra: <I>CHEGA</I><BR>Abstenção: <I>BE</I>",
                    }],
                },
            ],
        }
        initiative = intelligence.normalise_initiative(
            raw, self.matcher, "XVII", self.config["assembly"]
        )
        self.assertIsNotNone(initiative)
        assert initiative is not None
        self.assertEqual(initiative["authors"], ["GOVERNO"])
        self.assertEqual(initiative["status"], "Aprovado")
        self.assertEqual(initiative["submittedAt"], "2026-01-10T00:00:00Z")
        raw_vote = next(intelligence.nested_vote_records(raw))
        vote = intelligence.normalise_vote(raw_vote, self.matcher, "XVII", initiative)
        self.assertIsNotNone(vote)
        assert vote is not None
        self.assertEqual(vote["officialId"], "4455")
        self.assertEqual(
            {item["party"]: item["position"] for item in vote["positions"]},
            {"PS": "favor", "PSD": "favor", "CHEGA": "contra", "BE": "abstencao"},
        )

    def test_open_data_discovery_follows_only_exact_legislature_folder(self):
        main_url = "https://www.parlamento.pt/Cidadania/Paginas/DAIniciativas.aspx"
        folder_url = "https://www.parlamento.pt/Cidadania/Paginas/DAIniciativas.aspx?folder=xvii"
        json_url = "https://app.parlamento.pt/dados/IniciativasXVII_json.txt"

        class FakeClient:
            def __init__(self):
                self.calls = []

            def text(self, url):
                self.calls.append(url)
                if url == main_url:
                    return (
                        "<a href='DAComissoes.aspx?id=XVII'>Comissão XVII</a>"
                        "<a href='DAIniciativas.aspx?folder=xvii'>XVII Legislatura</a>",
                        {},
                        url,
                    )
                if url == folder_url:
                    return f"<a href='{json_url}'>IniciativasXVII_json.txt</a>", {}, url
                raise AssertionError(url)

        client = FakeClient()
        result = intelligence.discover_open_data_json(
            client,
            self.config["assembly"],
            "DAIniciativas.aspx",
            "XVII",
        )
        self.assertEqual(result, json_url)
        self.assertEqual(client.calls, [main_url, folder_url])

    def test_promise_match_and_statistics_keep_absence_separate(self):
        state = intelligence.initial_state()
        initiative = intelligence.normalise_initiative(
            {
                "BID": "12345",
                "iniLeg": "XVII",
                "iniNr": "12/XVII/1",
                "iniTipo": "Projeto de Lei",
                "iniTitulo": "Reduzir IRS para famílias",
                "iniAutorGrupos": "PS",
                "iniFase": "Aprovado",
            },
            self.matcher,
            "XVII",
            self.config["assembly"],
        )
        self.assertIsNotNone(initiative)
        assert initiative is not None
        state["initiatives"][initiative["id"]] = initiative
        vote = intelligence.normalise_vote(
            {
                "id": "vote-12345",
                "assunto": "Reduzir IRS para famílias",
                "resultado": "Aprovado",
                "data": "2026-05-01",
                "A Favor": "PS",
                "Contra": "PSD",
                "Abstenção": "BE",
                "Ausência": "IL",
            },
            self.matcher,
            "XVII",
            initiative,
        )
        self.assertIsNotNone(vote)
        assert vote is not None
        state["votes"][vote["id"]] = vote
        promise = intelligence.promise_record(
            "PS",
            "O PS promete reduzir IRS para famílias.",
            "programa_eleitoral",
            {"type": "programa_eleitoral", "title": "Programa de teste", "year": 2025},
        )
        state["promises"][promise["id"]] = promise

        self.assertEqual(intelligence.rebuild_promise_matches(state), 1)
        match = state["promises"][promise["id"]]["proposalMatches"][0]
        self.assertTrue(match["approximate"])
        self.assertTrue(match["reviewRequired"])
        self.assertEqual(match["voteIds"], [vote["id"]])

        statistics = intelligence.vote_statistics(state, self.matcher, legislature="XVII")
        by_party = {item["id"]: item for item in statistics["parties"]}
        self.assertEqual(by_party["PS"]["proposalsPresented"], 1)
        self.assertEqual(by_party["PS"]["proposalsApproved"], 1)
        self.assertEqual(by_party["PS"]["votesFor"], 1)
        self.assertEqual(by_party["BE"]["abstentions"], 1)
        self.assertEqual(by_party["IL"]["absences"], 1)

    def test_promise_match_rejects_older_proposal_and_labels_other_author(self):
        state = intelligence.initial_state()
        promise = intelligence.promise_record(
            "PS",
            "O PS promete criar uma rede pública de habitação acessível.",
            "noticia",
            {"type": "noticia", "url": "https://example.test/p", "publishedAt": "2026-05-01"},
        )
        state["promises"][promise["id"]] = promise
        for identifier, date, author in (
            ("old", "2025-01-01", "PS"),
            ("new", "2026-06-01", "PSD"),
        ):
            initiative = {
                "id": identifier,
                "bid": identifier,
                "number": identifier,
                "title": "Criar rede pública de habitação acessível",
                "type": "Projeto de Lei",
                "authors": [author],
                "submittedAt": date,
                "sourceUrl": "https://www.parlamento.pt/",
            }
            state["initiatives"][identifier] = initiative

        self.assertEqual(intelligence.rebuild_promise_matches(state), 1)
        matches = state["promises"][promise["id"]]["proposalMatches"]
        self.assertEqual([item["initiativeId"] for item in matches], ["new"])
        self.assertEqual(matches[0]["authorRelation"], "outro_partido")
        self.assertEqual(matches[0]["authorRelationLabel"], "Apresentada por outro partido")
        self.assertTrue(matches[0]["approximate"])

    def test_news_promises_are_attributed_per_sentence_and_keep_review(self):
        state = intelligence.initial_state()
        statement = "O PS promete reduzir o IRS das famílias portuguesas."
        evidence = f"{statement} PS e PSD prometem criar um novo apoio económico comum."
        state["articles"]["one"] = {
            "id": "one",
            "source": "Fonte",
            "url": "https://example.test/politica/one",
            "title": evidence,
            "summary": "",
            "excerpt": "",
            "publishedAt": "2026-05-01",
            "entities": self.matcher.match(evidence),
        }

        self.assertEqual(intelligence.rebuild_news_promises(state, self.matcher), 1)
        promise = next(iter(state["promises"].values()))
        self.assertEqual(promise["party"], "PS")
        promise["status"] = "confirmada"
        promise["reviewRequired"] = False
        promise["reviewedBy"] = "equipa"

        self.assertEqual(intelligence.rebuild_news_promises(state, self.matcher), 1)
        preserved = next(iter(state["promises"].values()))
        self.assertEqual(preserved["status"], "confirmada")
        self.assertFalse(preserved["reviewRequired"])
        self.assertEqual(preserved["reviewedBy"], "equipa")
        self.assertFalse(intelligence.promise_candidate(
            "O dirigente afirmou que vamos estar atentos ao debate parlamentar de amanhã."
        ))
        self.assertTrue(intelligence.promise_candidate(
            "O PS afirmou que vamos reduzir o IRS das famílias no próximo Orçamento."
        ))

    def test_programme_promise_extraction_has_no_old_500_or_4000_caps(self):
        parties = ["PS", "PSD", "CHEGA", "IL", "BE", "PCP", "LIVRE", "PAN"]
        chunks = []
        for party in parties:
            statements = [
                f"Criar uma rede municipal de habitação acessível com objetivo próprio {party} {index}."
                for index in range(520)
            ]
            chunks.append({
                "category": "Programa eleitoral legislativas",
                "party": party,
                "filename": f"programa-{party}.pdf",
                "rel_path": f"Legislativas/Legislativas 2025/programa-{party}.pdf",
                "year": 2025,
                "text": "\n".join(statements),
            })

        with tempfile.TemporaryDirectory() as temporary:
            corpus = Path(temporary) / "chunks.json"
            corpus.write_text(json.dumps(chunks, ensure_ascii=False), encoding="utf-8")
            state = intelligence.initial_state()
            with mock.patch.object(intelligence, "PROGRAM_CHUNK_FILES", (corpus,)):
                rebuilt = intelligence.rebuild_programme_promises(state, self.matcher)

        self.assertEqual(rebuilt, 4160)
        self.assertEqual(len(state["promises"]), 4160)

    def test_programme_promise_filter_rejects_narrative_and_pdf_fragments(self):
        self.assertTrue(
            intelligence.promise_candidate(
                "JUSTIÇA 15 Implementar a exclusividade no exercício do mandato de deputado.",
                programme=True,
            )
        )
        self.assertTrue(
            intelligence.promise_candidate(
                "Assim propomos um aumento de 100 euros no apoio mensal às famílias.",
                programme=True,
            )
        )
        self.assertFalse(
            intelligence.promise_candidate(
                "Trata-se de eleger uma Assembleia cujos membros fiscalizarão o Governo por forma a garantir a vontade popular.",
                programme=True,
            )
        )
        self.assertFalse(
            intelligence.promise_candidate(
                "Tolerou-se a criação de tribunais populares durante o período revolucionário.",
                programme=True,
            )
        )
        self.assertFalse(
            intelligence.promise_candidate(
                "INTRODUÇÃO Portugal vai ter finalmente eleições legislativas para escolher um novo Governo.",
                programme=True,
            )
        )
        self.assertFalse(
            intelligence.promise_candidate(
                "Estabelecimento de bonificações proporcionais ao número de membros da",
                programme=True,
            )
        )
        self.assertEqual(
            intelligence.clean_programme_statement("De- fendemos uma política de habita- ção acessível."),
            "Defendemos uma política de habitação acessível.",
        )
        self.assertEqual(
            intelligence.programme_chunk_metadata(
                {
                    "party": "Outro",
                    "filename": "AD 2025.pdf",
                    "rel_path": "Legislativas/Legislativas 2025/AD 2025.pdf",
                    "year": 2024,
                },
                self.matcher,
            ),
            ("AD", 2025, "Legislativas 2025"),
        )
        self.assertEqual(
            intelligence.programme_chunk_metadata(
                {
                    "party": "CHEGA",
                    "filename": "ficheiro-sem-catalogação.pdf",
                    "rel_path": "ficheiro-sem-catalogação.pdf",
                    "year": 2024,
                },
                self.matcher,
            ),
            (None, None, ""),
        )

    def test_article_pruning_removes_old_foreign_false_positive(self):
        state = intelligence.initial_state()
        state["articles"] = {
            "foreign": {
                "id": "foreign",
                "url": "https://example.test/mundo/governo-aprova-medida",
                "title": "Governo estrangeiro aprova medida económica",
                "summary": "O ministro anunciou novos impostos.",
            },
            "domestic": {
                "id": "domestic",
                "url": "https://example.test/politica/ps-apresenta-orcamento",
                "title": "PS apresenta proposta de Orçamento",
                "summary": "O Partido Socialista apresentou a medida no Parlamento.",
            },
        }
        self.assertEqual(intelligence.prune_irrelevant_articles(state, self.matcher), 1)
        self.assertEqual(set(state["articles"]), {"domestic"})
        self.assertIn("politica", state["articles"]["domestic"]["topics"])

    def test_latest_decisive_vote_wins_and_official_id_deduplicates(self):
        initiative = {"id": "ini", "status": ""}
        votes = [
            {"id": "first", "officialId": "55", "legislature": "XVII", "date": "2026-01-01", "result": "Rejeitado", "positions": []},
            {"id": "second", "officialId": "55", "legislature": "XVII", "date": "2026-02-01", "result": "Aprovado", "positions": [{"party": "PS", "position": "favor"}]},
        ]
        self.assertEqual(intelligence.initiative_outcome(initiative, votes), "aprovada")
        deduplicated = intelligence.deduplicated_votes(votes)
        self.assertEqual(len(deduplicated), 1)
        self.assertEqual(deduplicated[0]["id"], "second")

    def test_government_statistics_count_only_government_authored_proposals(self):
        state = intelligence.initial_state()
        state["initiatives"] = {
            "party": {
                "id": "party",
                "legislature": "XVII",
                "submittedAt": "2026-01-10",
                "authors": ["PS"],
                "status": "Aprovado",
            },
            "government": {
                "id": "government",
                "legislature": "XVII",
                "submittedAt": "2026-01-11",
                "authors": ["GOVERNO"],
                "status": "Rejeitado",
            },
        }
        periods = [{
            "id": "GC-XXV",
            "name": "XXV Governo Constitucional",
            "start": "2025-06-05",
        }]
        statistics = intelligence.vote_statistics(
            state, self.matcher, legislature="XVII", government_periods=periods
        )
        governments = {item["id"]: item for item in statistics["governments"]}
        self.assertEqual(governments["XXV Governo Constitucional"]["proposalsPresented"], 1)
        self.assertEqual(governments["XXV Governo Constitucional"]["proposalsRejected"], 1)
        parties = {item["id"]: item for item in statistics["parties"]}
        self.assertEqual(parties["PS"]["proposalsPresented"], 1)
        self.assertNotIn("GOVERNO", parties)

    def test_configured_government_periods_are_contiguous_and_current(self):
        periods = self.config["governmentPeriods"]
        self.assertEqual(periods[-1]["id"], "GC-XXV")
        self.assertNotIn("end", periods[-1])
        for current, following in zip(periods, periods[1:]):
            self.assertEqual(current["end"], following["start"])
        self.assertEqual(
            intelligence.government_label_for(
                {"authors": ["GOVERNO"], "submittedAt": "2025-06-05"}, periods
            ),
            "XXV Governo Constitucional — Luís Montenegro",
        )
        self.assertIsNone(
            intelligence.government_label_for(
                {"authors": ["PS"], "submittedAt": "2025-06-05"}, periods
            )
        )

    def test_party_comparison_does_not_treat_shared_absence_as_agreement(self):
        state = intelligence.initial_state()
        state["votes"]["absent"] = {
            "id": "absent",
            "legislature": "XVII",
            "positions": [
                {"party": "PS", "position": "ausencia"},
                {"party": "PSD", "position": "ausencia"},
            ],
        }
        statistics = intelligence.vote_statistics(state, self.matcher, legislature="XVII")
        self.assertFalse(any(
            {pair["left"], pair["right"]} == {"PS", "PSD"}
            for pair in statistics["pairs"]
        ))

    def test_public_promise_omits_internal_similarity_diagnostics(self):
        promise = intelligence.promise_record(
            "PS", "O PS promete reduzir o IRS das famílias.", "noticia", {"url": "https://example.test"}
        )
        promise["proposalMatches"] = [{
            "initiativeId": "one",
            "title": "Reduzir o IRS das famílias",
            "score": 0.8,
            "sharedTerms": ["reduzir", "irs", "familias"],
            "approximate": True,
            "authorRelationLabel": "Apresentada pelo mesmo partido",
        }]
        public = intelligence.public_promise(promise)
        match = public["proposalMatches"][0]
        self.assertNotIn("score", match)
        self.assertNotIn("sharedTerms", match)
        self.assertEqual(match["authorRelationLabel"], "Apresentada pelo mesmo partido")

    def test_memory_export_uses_short_attributed_fact_chunks(self):
        state = intelligence.initial_state()
        state["articles"]["news_one"] = {
            "id": "news_one",
            "source": "Fonte de teste",
            "url": "https://example.test/noticia",
            "title": "Governo anuncia medida",
            "summary": "O Governo anunciou uma medida económica.",
            "excerpt": "",
            "publishedAt": "2026-05-01T12:00:00Z",
            "entities": [{"id": "GOVERNO", "kind": "institution", "name": "Governo"}],
        }
        chunks = intelligence.build_memory_chunks(state)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["source_type"], "news")
        self.assertIn("Fonte de teste", chunks[0]["text"])
        self.assertLessEqual(len(chunks[0]["text"]), 2800)

    def test_assembly_sync_uses_discovered_resources_without_detail_requests(self):
        state = intelligence.initial_state()
        state["initiatives"]["stale-initiative"] = {
            "id": "stale-initiative", "legislature": "XVII", "title": "Registo antigo"
        }
        state["votes"]["stale-vote"] = {
            "id": "stale-vote", "legislature": "XVII", "subject": "Registo antigo"
        }
        config = json.loads(json.dumps(self.config))

        def fake_fetch(_client, _config, resource_page, _legislature, _hints):
            if "Iniciativas" in resource_page:
                return ([{
                    "BID": "100",
                    "iniLeg": "XVII",
                    "iniNr": "1/XVII/1",
                    "iniTitulo": "Criar apoio à habitação",
                    "iniAutorGrupos": "PS",
                }], "https://official.test/initiatives.json", "initiative-hash")
            return ([{
                "actId": "200",
                "actTipo": "Debate",
                "actDescricao": "Apoio à habitação",
                "VotacaoDebate": [{
                    "id": "vote-200",
                    "data": "2026-05-02",
                    "descricao": "Apoio à habitação",
                    "resultado": "Aprovado",
                    "detalhe": "A Favor: PS<BR>Contra: PSD",
                }],
            }], "https://official.test/activities.json", "activity-hash")

        with mock.patch.object(intelligence, "fetch_open_data_records", side_effect=fake_fetch):
            statuses = intelligence.sync_assembly(
                state,
                config,
                self.matcher,
                object(),
                ["XVII"],
                force=True,
                max_detail_pages=0,
            )

        self.assertEqual(statuses[0]["status"], "ok")
        self.assertEqual(len(state["initiatives"]), 1)
        self.assertEqual(len(state["votes"]), 1)
        self.assertNotIn("stale-initiative", state["initiatives"])
        self.assertNotIn("stale-vote", state["votes"])
        self.assertIn("initiatives:XVII", state["assembly"]["resourceSnapshots"])
    def test_transport_errors_are_not_reported_as_rejected_articles(self):
        candidate = {"lastmod": "2026-01-01", "url": "https://example.test/news", "title": "Notícia"}
        source = {"id": "fixture", "name": "Fonte", "homepage": "https://example.test/"}
        class FailingClient:
            def text(self, _url):
                raise intelligence.PipelineError("read timeout")
        with mock.patch.object(intelligence, "can_fetch", return_value=True), \
             mock.patch.object(intelligence, "safe_print"):
            result = intelligence.fetch_article(candidate, source, FailingClient(), None, {
                "maxArticleExcerptCharacters": 1200,
            }, self.matcher)
        self.assertIsNone(result)
        self.assertFalse(intelligence.classify_article({"title": "Notícia", "url": "https://example.test/news"}, self.matcher)[2])

    def test_is_blocked_non_political(self):
        # Betting / Odds / Casino / Sports match
        self.assertTrue(intelligence.is_blocked_non_political("Odds e prognostico chelsea vs tottenham premier league 19 05 2026"))
        self.assertTrue(intelligence.is_blocked_non_political("Casino portugal login"))
        self.assertTrue(intelligence.is_blocked_non_political("Liga betclic"))
        self.assertTrue(intelligence.is_blocked_non_political("Codigo promocional betano 2026"))
        self.assertTrue(intelligence.is_blocked_non_political("https://example.test/apostas/como-apostar"))
        self.assertTrue(intelligence.is_blocked_non_political("https://example.test/desporto/futebol-jogo-hoje"))

        # Legitimate political / economic / health news
        self.assertFalse(intelligence.is_blocked_non_political({
            "title": "Governo aprova novas regras para concessão de casinos",
            "url": "https://example.test/politica/governo-casinos",
            "section": "politica",
        }))
        self.assertFalse(intelligence.is_blocked_non_political({
            "title": "Estado de saúde do autarca com prognóstico reservado",
            "url": "https://example.test/nacional/autarca-prognostico-reservado",
            "section": "nacional",
        }))

    def test_prune_irrelevant_articles_removes_sports_and_betting(self):
        state = intelligence.initial_state()
        state["articles"]["art1"] = {
            "id": "art1",
            "title": "Odds e prognostico benfica vs sporting",
            "url": "https://example.test/odds-benfica-sporting",
            "summary": "Prognóstico para o jogo",
            "excerpt": "Odds da betclic para o derby",
            "section": "desporto",
            "publishedAt": "2026-05-01T12:00:00Z",
            "topics": ["politica"],
            "entities": [{"name": "Benfica", "kind": "other"}],
        }
        state["articles"]["art2"] = {
            "id": "art2",
            "title": "Governo aprova pacote de habitação e desagravamento de IRS",
            "url": "https://example.test/politica/governo-habitacao-irs",
            "summary": "Conselho de Ministros aprovou nova lei.",
            "excerpt": "O Governo aprovou medidas para o IRS.",
            "section": "politica",
            "publishedAt": "2026-05-01T12:00:00Z",
            "topics": ["politica", "economia"],
            "entities": [{"name": "Governo", "kind": "institution"}],
        }
        state["promises"]["prom1"] = {
            "id": "prom1",
            "origin": "noticia",
            "source": {"url": "https://example.test/odds-benfica-sporting"},
            "title": "Promessa de odds",
        }
        removed = intelligence.prune_irrelevant_articles(state, self.matcher)
        self.assertEqual(removed, 1)
        self.assertNotIn("art1", state["articles"])
        self.assertIn("art2", state["articles"])
        self.assertNotIn("prom1", state["promises"])

    def test_user_reported_news_are_classified_as_relevant(self):
        cases = [
            (
                "MP acusa presidente da Câmara de Albufeira de discriminação e incitamento ao ódio",
                "https://www.cmjornal.pt/portugal/detalhe/mp-acusa-presidente-da-camara-de-albufeira",
            ),
            (
                "Câmara de Vila Nova de Cerveira abre concurso para atribuir 3 casas sociais",
                "https://www.cmjornal.pt/portugal/detalhe/camara-de-cerveira-abre-concurso-habitacao",
            ),
            (
                "Preço eficiente sobe para 1984 euros na gasolina e 2102 euros no gasóleo",
                "https://www.cmjornal.pt/economia/detalhe/preco-eficiente-sobe-gasolina-gasoleo",
            ),
            (
                "UE mobiliza meios contra fogos na Bélgica e Espanha e ativa Copernicus",
                "https://www.cmjornal.pt/mundo/detalhe/ue-mobiliza-meios-contra-fogos-copernicus",
            ),
            (
                "Guterres insta Washington e Teerão a retomarem negociações",
                "https://www.cmjornal.pt/mundo/detalhe/guterres-insta-washington-teerao-negociacoes",
            ),
            (
                'Presidente da Câmara do Porto considera que episódios de violência na cidade refletem "falhanço do Estado"',
                "https://www.cmjornal.pt/portugal/detalhe/rui-moreira-falhanco-do-estado",
            ),
            (
                "União Europeia quer todos fora de Ceuta, incluindo menores",
                "https://www.cmjornal.pt/mundo/detalhe/uniao-europeia-ceuta-menores",
            ),
        ]
        for title, url in cases:
            article = {
                "title": title,
                "url": url,
                "summary": title,
                "excerpt": title,
                "section": "noticias",
            }
            topics, entities, relevant = intelligence.classify_article(article, self.matcher)
            self.assertTrue(
                relevant,
                f"Notícia deveria ser relevante mas foi rejeitada: {title} (topics={topics}, entities={[e['id'] for e in entities]})",
            )

    def test_football_coverage_without_political_anchor_is_rejected(self):
        rejected = [
            (
                "Campeão FC Porto isola-se na liderança ao bater Arouca",
                "https://example.test/desporto/campeao-fc-porto",
            ),
            (
                "João Palhinha é reforço do Benfica até 2030",
                "https://example.test/desporto/palhinha-benfica",
            ),
            (
                "Varandas afirma que Sporting não pagaria o valor que Benfica deu por Palhinha",
                "https://example.test/desporto/varandas-palhinha",
            ),
        ]
        kept = [
            (
                "Câmara de Lisboa prevê habitação acessível na Baixa e centros intergeracionais em Benfica",
                "https://example.test/portugal/habitacao-benfica",
            ),
            (
                "Freguesia de Benfica entrega 50 apartamentos a rendas acessíveis. Objetivo é chegar aos 300",
                "https://example.test/portugal/freguesia-benfica-rendas",
            ),
            (
                "Governo quer reforçar segurança nos estádios após distúrbios",
                "https://example.test/portugal/estadios-seguranca",
            ),
        ]
        for title, url in rejected:
            article = {
                "title": title,
                "url": url,
                "summary": title,
                "excerpt": "",
                "section": "noticias",
            }
            topics, entities, relevant = intelligence.classify_article(article, self.matcher)
            self.assertFalse(
                relevant,
                f"Cobertura futebolística deveria ser rejeitada: {title} (topics={topics}, entities={[e['id'] for e in entities]})",
            )
        for title, url in kept:
            article = {
                "title": title,
                "url": url,
                "summary": title,
                "excerpt": "",
                "section": "noticias",
            }
            topics, entities, relevant = intelligence.classify_article(article, self.matcher)
            self.assertTrue(
                relevant,
                f"Notícia com contexto municipal/político deveria ser mantida: {title} (topics={topics}, entities={[e['id'] for e in entities]})",
            )

    def test_checkpoint_manager_throttles_by_time_and_forces_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state = {"updatedAt": None, "articles": {}}
            mgr = intelligence.CheckpointManager(state_path, state, interval_seconds=60.0)

            # Initially not dirty, no file written
            mgr()
            self.assertFalse(state_path.exists())

            # Mark dirty but interval not passed -> still not written
            mgr.mark_dirty()
            mgr()
            self.assertFalse(state_path.exists())

            # Force save -> written immediately
            mgr(force=True)
            self.assertTrue(state_path.exists())
            loaded = intelligence.json_load(state_path, {})
            self.assertIsNotNone(loaded.get("updatedAt"))

    def test_sitemap_iteration_skips_already_processed_unchanged_articles(self):
        xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url>
            <loc>https://example.test/noticia-1</loc>
            <lastmod>2026-08-17T10:00:00Z</lastmod>
          </url>
          <url>
            <loc>https://example.test/noticia-2</loc>
            <lastmod>2026-08-17T11:00:00Z</lastmod>
          </url>
        </urlset>"""
        source = {
            "id": "test",
            "homepage": "https://example.test/",
            "sitemapSeeds": ["https://example.test/sitemap.xml"],
        }
        art1_id = intelligence.stable_id("news", "https://example.test/noticia-1")
        runtime_state = {
            "seen": {
                art1_id: {
                    "lastmod": "2026-08-17T10:00:00Z",
                    "filterVersion": intelligence.NEWS_FILTER_VERSION,
                    "decision": "collected",
                }
            }
        }

        class MockClient:
            def text(self, url):
                return xml, {}, url

        records = list(
            intelligence.iter_sitemap_records(
                source,
                MockClient(),
                None,
                [],
                {},
                None,
                runtime_state,
                [],
            )
        )
        # noticia-1 is already seen and unchanged -> only noticia-2 is yielded
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["url"], "https://example.test/noticia-2")

    def test_purely_foreign_news_are_rejected(self):
        foreign_cases = [
            ("Câmara do Rio de Janeiro aprova aumento de vereadores", "https://example.test/mundo/camara-rio-vereadores"),
            ("Prefeitura de São Paulo proíbe circulação de camiões no centro", "https://example.test/mundo/prefeitura-sp-camioes"),
            ("Câmara de Madrid aprova novas restrições ao tráfego", "https://example.test/mundo/camara-madrid-trafego"),
            ("Senado dos EUA aprova orçamento federal", "https://example.test/mundo/senado-eua-orcamento"),
            ("Governo espanhol aprova descida de impostos na eletricidade", "https://example.test/mundo/governo-espanhol-impostos"),
            ("STF do Brasil determina prisão de investigados", "https://example.test/mundo/stf-brasil-prisao"),
            ("Trump promete impor taxas aduaneiras a produtos canadianos", "https://example.test/mundo/trump-taxas-canada"),
            ("Lula da Silva critica taxa de juro do Banco Central brasileiro", "https://example.test/mundo/lula-critica-juros"),
            ("Macron anuncia demissão de ministro da Economia em França", "https://example.test/mundo/macron-demissao-ministro"),
            ("Presidente da Colômbia decreta estado de emergência após protestos em Bogotá", "https://example.test/mundo/colombia-emergencia"),
            ("Tribunal Supremo da Colômbia abre inquérito a ministro", "https://example.test/mundo/colombia-inquerito-ministro"),
            ("Gustavo Petro anuncia novo plano económico para a Colômbia", "https://example.test/mundo/petro-plano-colombia"),
        ]
        for title, url in foreign_cases:
            article = {
                "title": title,
                "url": url,
                "summary": title,
                "excerpt": title,
                "section": "mundo",
            }
            topics, entities, relevant = intelligence.classify_article(article, self.matcher)
            self.assertFalse(
                relevant,
                f"Notícia puramente estrangeira deveria ser rejeitada: {title} (topics={topics}, entities={[e['id'] for e in entities]})",
            )

    def test_portuguese_news_without_word_portugal_are_accepted(self):
        domestic_cases = [
            ("Câmara de Albufeira abre concurso para 3 casas sociais", "https://example.test/nacional/camara-albufeira"),
            ("Câmara de Vila Nova de Cerveira abre concurso para atribuir 3 casas sociais", "https://example.test/local/cerveira"),
            ("Preço eficiente sobe para 1984 euros na gasolina e 2102 euros no gasóleo", "https://example.test/economia/combustiveis"),
            ('Presidente da Câmara do Porto considera que episódios de violência na cidade refletem "falhanço do Estado"', "https://example.test/nacional/porto"),
            ("Carlos Moedas apresenta novo plano de habitação para a capital", "https://example.test/local/carlos-moedas"),
            ("Governo aprova aumento do salário mínimo para 870 euros", "https://example.test/nacional/governo-salario-minimo"),
            ("PS vota contra proposta de alteração ao IRS do PSD", "https://example.test/politica/ps-vota-contra-irs"),
            ("Tribunal da Relação confirma condenação de ex-autarca por peculato", "https://example.test/justica/tribunal-relacao-autarca"),
            ("Portugal e Colômbia assinam acordo de cooperação económica e comercial", "https://example.test/politica/portugal-colombia-acordo"),
            ("UE e Colômbia reforçam parceria para transição energética", "https://example.test/mundo/ue-colombia-parceria"),
            ("União Europeia apoia processo de paz na Colômbia", "https://example.test/mundo/uniao-europeia-paz-colombia"),
            ("Empresas portuguesas investem em projetos na Colômbia e no Brasil", "https://example.test/economia/empresas-portuguesas-colombia"),
        ]
        for title, url in domestic_cases:
            article = {
                "title": title,
                "url": url,
                "summary": title,
                "excerpt": title,
                "section": "noticias",
            }
            topics, entities, relevant = intelligence.classify_article(article, self.matcher)
            self.assertTrue(
                relevant,
                f"Notícia portuguesa/bilateral deveria ser aceite: {title} (topics={topics}, entities={[e['id'] for e in entities]})",
            )

    def test_prune_phase3_removes_foreign_and_keeps_bilateral(self):
        art_pure_colombia = {
            "id": "news_colombia_1",
            "url": "https://example.test/mundo/colombia-crise",
            "title": "Presidente da Colômbia anuncia remodelação governamental",
            "summary": "Crise política na Colômbia leva a mudanças ministeriais em Bogotá.",
            "excerpt": "Crise política na Colômbia leva a mudanças ministeriais em Bogotá.",
            "section": "mundo",
        }
        art_pt_colombia = {
            "id": "news_colombia_pt",
            "url": "https://example.test/politica/portugal-colombia",
            "title": "Governo português e Colômbia assinam acordo bilateral",
            "summary": "Luís Montenegro recebe delegação da Colômbia em São Bento.",
            "excerpt": "Luís Montenegro recebe delegação da Colômbia em São Bento.",
            "section": "politica",
        }
        state = {
            "articles": {
                "news_colombia_1": art_pure_colombia,
                "news_colombia_pt": art_pt_colombia,
            },
            "promises": {},
            "sources": {},
        }
        removed = intelligence.prune_irrelevant_articles(state, self.matcher)
        self.assertEqual(removed, 1)
        self.assertNotIn("news_colombia_1", state["articles"])
        self.assertIn("news_colombia_pt", state["articles"])

    def test_carlos_moedas_and_lisbon_mayor_entity_matching(self):
        matched = self.matcher.match("Carlos Moedas discursa na sessão solene")
        self.assertTrue(any(e["id"] == "carlos-moedas" for e in matched))

        matched_mayor = self.matcher.match("Presidente da Câmara de Lisboa inaugura novo centro de saúde")
        self.assertTrue(any(e["id"] == "carlos-moedas" for e in matched_mayor))

    def test_format_duration(self):
        self.assertEqual(intelligence.format_duration(0), "--")
        self.assertEqual(intelligence.format_duration(-5), "--")
        self.assertEqual(intelligence.format_duration(45), "45s")
        self.assertEqual(intelligence.format_duration(125), "2m 05s")
        self.assertEqual(intelligence.format_duration(3665), "1h 01m")

    # ------------------------------------------------------------------
    # Fase D — decisão presidencial / resultado por promessa (votado)
    # ------------------------------------------------------------------

    def test_presidential_action_from_events_picks_latest_terminal_phase(self):
        events = [
            {"Fase": "entrada", "DataFase": "2026-02-10"},
            {"Fase": "apreciação parlamentar", "DataFase": "2026-03-01"},
            {"Fase": "votação final global", "DataFase": "2026-04-05"},
            {"Fase": "promulgação", "DataFase": "2026-05-02"},
        ]
        action = intelligence.presidential_action_from_events(events)
        self.assertIsNotNone(action)
        self.assertEqual(action["kind"], "promulgada")
        self.assertTrue(str(action["date"]).startswith("2026-05-02"))

    def test_presidential_action_from_events_prefers_veto_when_later(self):
        events = [
            {"Fase": "promulgação", "DataFase": "2026-04-01"},
            {"Fase": "veto", "DataFase": "2026-05-03"},
            {"Fase": "apreciação parlamentar", "DataFase": "2026-05-15"},
        ]
        action = intelligence.presidential_action_from_events(events)
        self.assertEqual(action["kind"], "apreciacao_parlamentar")

    def test_normalise_initiative_stores_president_action_from_fixture(self):
        raw = intelligence.json_load(FIXTURES_DIR / "ar_inieventos.json", {})
        assembly = self.config["assembly"]
        initiative = intelligence.normalise_initiative(raw, self.matcher, "XVII", assembly)
        self.assertIsNotNone(initiative)
        self.assertEqual(initiative["presidentAction"]["kind"], "promulgada")

    def test_sync_presidential_actions_parses_lei_references_from_fixture(self):
        html_fixture = (FIXTURES_DIR / "pr_promulgacoes.html").read_text(encoding="utf-8")

        class _FakeClient:
            def __init__(self, html: str) -> None:
                self.html = html

            def text(self, url: str):
                # Só a página de promulgações contém referências; os vetos vêm vazios.
                if "promulgacoes" not in url and "promulgac" not in url:
                    return "", {}, url
                return self.html, {}, url

        client = _FakeClient(html_fixture)
        state = intelligence.initial_state()
        state["initiatives"] = {
            "ini_x": {"id": "ini_x", "title": "Lei n.º 12/2024 — regime do procedimento simplificado"}
        }
        with mock.patch.object(intelligence, "can_fetch", return_value=True):
            result = intelligence.sync_presidential_actions(state, self.config, client)
        self.assertEqual(result["promulgadas"], 2)
        self.assertEqual(result["vetos"], 0)
        self.assertEqual(result["novas"], 2)
        self.assertEqual(state["initiatives"]["ini_x"]["presidentAction"]["kind"], "promulgada")

    def test_public_vote_outcomes_merge_positions_by_party_and_president(self):
        votes = {
            "v1": {
                "id": "v1", "initiativeId": "ini", "date": "2026-04-05", "result": "Aprovado",
                "positions": [{"party": "PS", "position": "favor"}, {"party": "PSD", "position": "contra"}],
            },
            "v2": {
                "id": "v2", "initiativeId": "ini", "date": "2026-05-02", "result": "Aprovado",
                "positions": [{"party": "PS", "position": "abstencao"}],
            },
        }
        initiatives = {
            "ini": {"id": "ini", "status": "Promulgada",
                    "presidentAction": {"kind": "promulgada", "date": "2026-05-02"}}
        }
        promise = {"proposalMatches": [{"initiativeId": "ini", "voteIds": ["v1", "v2"]}]}
        outcomes = intelligence.promise_vote_outcomes(promise, initiatives, votes)
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["outcome"], "aprovada")
        self.assertEqual(outcomes[0]["positionsByParty"]["PS"], "abstencao")
        self.assertEqual(outcomes[0]["positionsByParty"]["PSD"], "contra")

    # ------------------------------------------------------------------
    # Fase E — iniciativas europeias (OEIL) e votações RCV
    # ------------------------------------------------------------------

    def test_normalise_eu_decision_from_fixture(self):
        raw = intelligence.json_load(FIXTURES_DIR / "ep_dec_event.json", {})
        records = raw.get("data") if isinstance(raw, dict) else []
        dec = intelligence.normalise_eu_decision(
            records[0], "https://data.europarl.europa.eu/api/v2", 10
        )
        self.assertIsNotNone(dec)
        self.assertEqual(dec["result"], "Rejeitada")
        self.assertTrue(dec["nominal"])
        self.assertEqual(dec["counts"]["against"], 430)
        self.assertEqual(dec["counts"]["favor"], 76)

    def test_apply_eu_procedure_detail_from_fixture(self):
        raw = intelligence.json_load(FIXTURES_DIR / "ep_procedure.json", {})
        state = {"euInitiatives": {}}
        _updated, created = intelligence.apply_eu_procedure_detail(state, raw, 10)
        self.assertTrue(created >= 1)
        record = next(iter(state["euInitiatives"].values()))
        self.assertEqual(record["identifier"], "2024-2526")
        self.assertTrue(record["title"])
        self.assertTrue(record["status"])

    # ------------------------------------------------------------------
    # Fase F — orçamentos PT/UE
    # ------------------------------------------------------------------

    def test_government_label_for_year_overlap(self):
        periods = [
            {"id": "GC-XXIV", "name": "XXIV Governo", "start": "2024-04-02", "end": "2025-06-05"},
            {"id": "GC-XXV", "name": "XXV Governo", "start": "2025-06-05"},
        ]
        self.assertEqual(intelligence.government_label_for_year(2025, periods), "XXV Governo")
        self.assertEqual(intelligence.government_label_for_year(2024, periods), "XXIV Governo")

    def test_sync_budget_evidence_groups_by_document_and_recovers_year(self):
        with tempfile.TemporaryDirectory() as temporary:
            corpus = Path(temporary) / "budget.json"
            corpus.write_text(
                json.dumps([
                    {"id": "a", "text": "Rubrica A", "category": "Orçamentos de Estado", "year": "2020",
                     "filename": "Orçamento do Estado 2020.pdf",
                     "rel_path": "Orçamentos de Estado\\Orçamento do Estado 2020.pdf"},
                    {"id": "b", "text": "Rubrica B", "category": "Orçamento UE (BCE)", "year": "2021",
                     "filename": "BCE - Relatório Anual 2021.pdf",
                     "rel_path": "Orçamentos de Estado Europeus\\BCE - Relatório Anual 2021.pdf"},
                ]),
                encoding="utf-8",
            )
            state = intelligence.initial_state()
            config = dict(self.config)
            config["budgets"] = {"enabled": True}
            with mock.patch.object(intelligence, "BUDGET_CHUNK_FILES", (corpus,)):
                result = intelligence.sync_budget_evidence(state, config)
            self.assertEqual(result["documentsKnown"], 2)
            self.assertEqual(result["chunks"], 2)
            by_rel = {item["relPath"]: item for item in state["budgetDocuments"].values()}
            pt = next(v for v in by_rel.values() if v["category"] == "pt_estado")
            self.assertEqual(pt["year"], 2020)

    def test_rebuild_budget_matches_uses_shared_terms_and_review_flag(self):
        with tempfile.TemporaryDirectory() as temporary:
            corpus = Path(temporary) / "budget.json"
            corpus.write_text(
                json.dumps([
                    {"id": "b1", "text": "Programa de apoio ao arrendamento acessível com meta de 100 mil casas e reforço do parque público de habitação",
                     "category": "Orçamentos de Estado", "year": "2025",
                     "filename": "Orçamento do Estado 2025.pdf",
                     "rel_path": "Orçamentos de Estado\\Orçamento do Estado 2025.pdf"},
                ]),
                encoding="utf-8",
            )
            state = intelligence.initial_state()
            state["promises"] = {
                "p1": {"id": "p1",
                       "statement": "Criar 100 mil casas de arrendamento acessível e reforçar o parque público de habitação",
                       "party": "PS"},
            }
            config = dict(self.config)
            config["budgets"] = {"enabled": True}
            with mock.patch.object(intelligence, "BUDGET_CHUNK_FILES", (corpus,)):
                matched = intelligence.rebuild_budget_matches(state, config)
            self.assertGreaterEqual(matched, 1)
            matches = state["promises"]["p1"]["budgetMatches"]
            self.assertTrue(matches)
            first = next(iter(matches))
            self.assertTrue(first["reviewRequired"])
            self.assertEqual(first["year"], 2025)

    def test_health_and_sns_news_are_classified_as_relevant(self):
        cases = [
            (
                "Tempos de espera nas urgências do SNS sobem para 14 horas no Hospital de Santa Maria",
                "https://example.test/sociedade/urgencias-sns-tempos-espera",
            ),
            (
                "Grávida perde bebé após fecho de urgência de obstetrícia e maternidade",
                "https://example.test/pais/gravida-urgencia-obstetricia-maternidade",
            ),
            (
                "Morte em ambulância à porta do hospital por falta de resposta rápida do INEM",
                "https://example.test/sociedade/inem-morte-ambulancia-hospital",
            ),
            (
                "Grávida dá à luz em ambulância a caminho do hospital devido a encerramento de maternidades",
                "https://example.test/nacional/gravida-ambulancia-maternidade",
            ),
            (
                "Greve dos médicos e enfermeiros paralisa consultas nos centros de saúde e hospitais",
                "https://example.test/portugal/greve-medicos-enfermeiros-sns",
            ),
        ]
        for title, url in cases:
            article = {
                "title": title,
                "url": url,
                "summary": title,
                "excerpt": title,
                "section": "sociedade",
            }
            topics, entities, relevant = intelligence.classify_article(article, self.matcher)
            self.assertTrue(
                relevant,
                f"Notícia de saúde deveria ser relevante: {title} (topics={topics}, entities={[e.get('id') for e in entities]})",
            )
            self.assertIn("politica", topics)

    def test_autoridade_tributaria_news_are_classified_as_relevant(self):
        cases = [
            (
                "Autoridade Tributária alerta para prazo de validação de faturas no e-fatura",
                "https://example.test/economia/at-alerta-faturas",
            ),
            (
                "Fisco começa a pagar primeiros reembolsos do IRS esta semana",
                "https://example.test/economia/fisco-reembolsos-irs",
            ),
            (
                "Administração Tributária avança com penhoras por dívidas fiscais não regularizadas",
                "https://example.test/economia/at-penhoras-dividas",
            ),
        ]
        for title, url in cases:
            article = {
                "title": title,
                "url": url,
                "summary": title,
                "excerpt": title,
                "section": "economia",
            }
            topics, entities, relevant = intelligence.classify_article(article, self.matcher)
            self.assertTrue(
                relevant,
                f"Notícia da AT deveria ser relevante: {title} (topics={topics}, entities={[e.get('id') for e in entities]})",
            )
            self.assertTrue(bool({"politica", "economia"} & set(topics)))

    def test_climaximo_activism_news_are_classified_as_relevant(self):
        cases = [
            (
                "Ativistas do Climáximo cortam trânsito na Segunda Circular em protesto pelo clima",
                "https://example.test/sociedade/climaximo-corta-segunda-circular",
            ),
            (
                "Coletivo Climáximo pinta fachada de ministério em Lisboa em ação de desobediência civil",
                "https://example.test/pais/climaximo-protesto-ministerio",
            ),
        ]
        for title, url in cases:
            article = {
                "title": title,
                "url": url,
                "summary": title,
                "excerpt": title,
                "section": "sociedade",
            }
            topics, entities, relevant = intelligence.classify_article(article, self.matcher)
            self.assertTrue(
                relevant,
                f"Notícia do Climáximo deveria ser relevante: {title} (topics={topics}, entities={[e.get('id') for e in entities]})",
            )
            self.assertIn("politica", topics)

    def test_utad_and_higher_education_news_are_classified_as_relevant(self):
        cases = [
            (
                "UTAD aprova novo plano de expansão do alojamento estudantil",
                "https://example.test/nacional/utad-alojamento-estudantil",
            ),
            (
                "Reitores das universidades debatem fim das propinas no Ensino Superior",
                "https://example.test/sociedade/reitores-propinas-ensino-superior",
            ),
        ]
        for title, url in cases:
            article = {
                "title": title,
                "url": url,
                "summary": title,
                "excerpt": title,
                "section": "sociedade",
            }
            topics, entities, relevant = intelligence.classify_article(article, self.matcher)
            self.assertTrue(
                relevant,
                f"Notícia de ensino superior/UTAD deveria ser relevante: {title} (topics={topics}, entities={[e.get('id') for e in entities]})",
            )
            self.assertIn("politica", topics)

    def test_nato_and_un_international_news_are_classified_as_relevant(self):
        cases = [
            (
                "Cimeira da NATO debate reforço da defesa coletiva e cumprimento do artigo 5",
                "https://example.test/mundo/cimeira-nato-defesa-artigo5",
            ),
            (
                "Conselho de Segurança da ONU aprova resolução sobre cessar-fogo com mediação internacional",
                "https://example.test/mundo/onu-conselho-seguranca-resolucao",
            ),
            (
                "Guterres apela a acordo global sobre ação climática e desarmamento",
                "https://example.test/mundo/guterres-apelo-clima-desarmamento",
            ),
        ]
        for title, url in cases:
            article = {
                "title": title,
                "url": url,
                "summary": title,
                "excerpt": title,
                "section": "mundo",
            }
            topics, entities, relevant = intelligence.classify_article(article, self.matcher)
            self.assertTrue(
                relevant,
                f"Notícia de NATO/ONU deveria ser relevante: {title} (topics={topics}, entities={[e.get('id') for e in entities]})",
            )
            self.assertIn("politica", topics)

    def test_pure_nobel_prize_and_music_festival_announcements_are_rejected(self):
        rejected = [
            (
                "Prémio Nobel da Economia é anunciado hoje em Estocolmo",
                "https://example.test/economia/premio-nobel-economia-anunciado",
            ),
            (
                "Francês Jean Tirole é o Nobel da Economia de 2014",
                "https://example.test/economia/jean-tirole-nobel-economia",
            ),
            (
                "Ig Nobel premeia as pesquisas científicas mais insólitas do ano",
                "https://example.test/ciencia/ig-nobel-premios",
            ),
            (
                "Câmara de Sesimbra apoia festival de música de verão com cartaz de luxo",
                "https://example.test/cultura/festival-musica-sesimbra",
            ),
            (
                "Bilhetes para o festival de música em Coimbra esgotam em poucas horas",
                "https://example.test/local/festival-musica-coimbra-bilhetes",
            ),
            (
                "Concerto de Capicua e de Vitorino anima as noites de verão na cidade",
                "https://example.test/cultura/concerto-vitorino-verao",
            ),
        ]
        kept_political_controversies = [
            (
                'Mariana Leitão elogia atribuição de Nobel a "símbolo de luta contra regimes opressores"',
                "https://example.test/politica/mariana-leitao-nobel",
            ),
            (
                "PSD congratula-se com atribuição do Nobel da Paz a María Corina Machado",
                "https://example.test/politica/psd-nobel-paz",
            ),
            (
                "Ventura critica ida de Montenegro a festival Alive",
                "https://example.test/politica/ventura-critica-montenegro-alive",
            ),
            (
                "DGS quer lugares sentados nos concertos do Avante, mas PCP quer público de pé",
                "https://example.test/politica/dgs-pcp-avante-concertos",
            ),
        ]
        for title, url in rejected:
            article = {
                "title": title,
                "url": url,
                "summary": title,
                "excerpt": title,
                "section": "cultura",
            }
            topics, entities, relevant = intelligence.classify_article(article, self.matcher)
            self.assertFalse(
                relevant,
                f"Falso positivo deveria ter sido rejeitado: {title} (topics={topics}, entities={[e.get('id') for e in entities]})",
            )
        for title, url in kept_political_controversies:
            article = {
                "title": title,
                "url": url,
                "summary": title,
                "excerpt": title,
                "section": "politica",
            }
            topics, entities, relevant = intelligence.classify_article(article, self.matcher)
            self.assertTrue(
                relevant,
                f"Notícia com controvérsia política deveria ser mantida: {title} (topics={topics}, entities={[e.get('id') for e in entities]})",
            )


if __name__ == "__main__":
    unittest.main()
