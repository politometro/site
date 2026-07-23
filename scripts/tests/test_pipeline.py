import copy
import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import auto_populate_ai
import generate_post
import recover_weekly_generation


def verified_item(media_type, suffix, status="queue"):
    categories = {
        "book": "Livro",
        "podcast": "Podcast",
        "movie": "Filme",
        "nostalgia": "Nostalgia",
        "investigation": "Investigação",
        "highlight": "Destaque",
    }
    link = f"https://example.com/{media_type}/{suffix}"
    description = f"Descrição verificada {suffix}."
    if media_type == "highlight":
        link = f"https://example.com/investigacao/{suffix}"
        description = f"Jornalismo de investigação verificado {suffix}."
    item = {
        "id": f"{media_type}_{suffix}",
        "type": media_type,
        "category": categories[media_type],
        "title": f"{categories[media_type]} {suffix}",
        "authorOrMeta": f"Autor {suffix}",
        "description": description,
        "link": link,
        "imageUrl": f"/covers/{media_type}_{suffix}.jpg",
        "status": status,
        "resolutionStatus": "verified",
        "verification": {
            "status": "verified",
            "source": "test-source",
            "entityId": f"entity-{media_type}-{suffix}",
            "coverHash": f"hash-{media_type}-{suffix}",
        },
    }
    if media_type in {"podcast", "highlight"}:
        now = datetime.datetime.now(datetime.timezone.utc)
        item["sourcePublishedAt"] = (now - datetime.timedelta(hours=6)).isoformat()
        item["expiryDate"] = (now + datetime.timedelta(days=3)).isoformat()
    return item


class ZeroStatePopulationTests(unittest.TestCase):
    def test_podcast_discovery_uses_supplied_atom_feed_links(self):
        published = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=1)
        ).isoformat()
        feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
  <entry>
    <id>yt:video:recent123</id>
    <title>Conteudo do Bataguas EP99</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=recent123" />
    <published>{published}</published>
    <summary>Satira politica recente.</summary>
    <media:thumbnail url="https://i.ytimg.com/vi/recent123/hqdefault.jpg" />
  </entry>
</feed>""".encode("utf-8")
        response = mock.Mock()
        response.content = feed
        response.raise_for_status.return_value = None
        watchlist = {
            "podcasts": [
                {
                    "name": "Conteudo do Bataguas",
                    "author": "Diogo Bataguas",
                    "feedUrl": "https://www.youtube.com/feeds/videos.xml?channel_id=UCQ2IleutNd89c9F-LjrwwwQ",
                }
            ]
        }

        with mock.patch.object(
            auto_populate_ai.requests, "get", return_value=response
        ):
            candidates = auto_populate_ai.discover_podcast_candidates(
                watchlist, set(), set(), 1
            )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0]["link"],
            "https://www.youtube.com/watch?v=recent123",
        )
        self.assertEqual(candidates[0]["_discovery"]["kind"], "rss")

    def test_invalid_rotated_rss_highlight_can_be_recovered(self):
        stale = verified_item("highlight", "rotated", status="invalid")
        stale["resolutionStatus"] = "rejected"
        stale["validationError"] = (
            "HIGHLIGHT_NOT_FOUND [Destaque rotated]: "
            "O artigo deixou de constar do RSS autorizado."
        )
        recovered = copy.deepcopy(stale)
        recovered["status"] = "queue"
        recovered["resolutionStatus"] = "verified"
        recovered["expiryDate"] = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=7)
        ).isoformat()

        with mock.patch.object(
            auto_populate_ai,
            "resolve_recommendation",
            return_value=recovered,
        ):
            changed = auto_populate_ai._recover_recheckable_invalid_queue(
                [stale]
            )

        self.assertTrue(changed)
        self.assertEqual(stale["status"], "queue")
        self.assertEqual(stale["resolutionStatus"], "verified")

    def test_verified_catalogue_has_a_multiweek_margin_per_type(self):
        candidates = auto_populate_ai.VERIFIED_CATALOGUE_CANDIDATES
        for media_type in ("book", "movie"):
            relevant = [
                item for item in candidates if item["type"] == media_type
            ]
            self.assertGreaterEqual(len(relevant), 12)
            identities = {
                (
                    auto_populate_ai._normalise(item["title"]),
                    auto_populate_ai._normalise(item["authorOrMeta"]),
                )
                for item in relevant
            }
            self.assertEqual(len(identities), len(relevant))

    def test_empty_database_is_filled_only_with_verified_entities(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec_path = Path(tmp) / "recommendations.json"
            watch_path = Path(tmp) / "watchlist.json"
            rec_path.write_text('{"queue": [], "history": []}\n', encoding="utf-8")
            episode_candidates = [
                {
                    "id": f"watch_{media_type}_{index}",
                    "type": media_type,
                    "title": f"{media_type} episode {index}",
                    "authorOrMeta": f"Publisher {index}",
                    "description": "Grounded episode.",
                    "link": f"https://example.com/{media_type}/{index}",
                }
                for media_type in ("nostalgia", "investigation")
                for index in range(auto_populate_ai.TARGET_PER_TYPE)
            ]
            watch_path.write_text(
                json.dumps(
                    {
                        "podcasts": [],
                        "episodeCandidates": episode_candidates,
                    }
                ),
                encoding="utf-8",
            )

            podcasts = [
                verified_item("podcast", str(index))
                for index in range(auto_populate_ai.TARGET_PER_TYPE)
            ]
            highlights = [
                verified_item("highlight", str(index))
                for index in range(auto_populate_ai.TARGET_PER_TYPE)
            ]
            catalogue = [
                {
                    "type": media_type,
                    "title": f"{media_type} candidate {index}",
                    "authorOrMeta": f"Creator {index}",
                    "description": "Grounded candidate.",
                }
                for media_type in ("book", "movie")
                for index in range(auto_populate_ai.TARGET_PER_TYPE)
            ]

            def fake_resolve(item, force=False):
                resolved = copy.deepcopy(item)
                suffix = resolved["id"]
                resolved["link"] = f"https://example.com/content/{suffix}"
                resolved["imageUrl"] = f"/covers/{suffix}.jpg"
                resolved["description"] = "Descrição derivada da fonte."
                if resolved["type"] == "highlight":
                    resolved["link"] = (
                        f"https://example.com/investigacao/{suffix}"
                    )
                    resolved["description"] = (
                        "Jornalismo de investigação derivado da fonte."
                    )
                resolved["externalId"] = f"external-{suffix}"
                resolved["resolutionStatus"] = "verified"
                resolved["verification"] = {
                    "status": "verified",
                    "source": "fixture",
                    "entityId": f"external-{suffix}",
                    "coverHash": f"cover-{suffix}",
                }
                return resolved

            env = {
                "GROQ_API_KEY": "test",
                "GOOGLE_CSE_API_KEY": "test",
                "GOOGLE_CSE_ID": "test",
            }
            with (
                mock.patch.object(auto_populate_ai, "REC_FILE", str(rec_path)),
                mock.patch.object(auto_populate_ai, "WATCHLIST_FILE", str(watch_path)),
                mock.patch.object(
                    auto_populate_ai,
                    "discover_podcast_candidates",
                    return_value=podcasts,
                ),
                mock.patch.object(
                    auto_populate_ai,
                    "discover_highlight_candidates",
                    return_value=highlights,
                ),
                mock.patch.object(
                    auto_populate_ai,
                    "_groq_catalogue_candidates",
                    return_value=catalogue,
                ),
                mock.patch.object(
                    auto_populate_ai, "resolve_recommendation", side_effect=fake_resolve
                ),
                mock.patch.dict(os.environ, env, clear=False),
            ):
                auto_populate_ai.auto_populate()

            data = json.loads(rec_path.read_text(encoding="utf-8"))
            self.assertEqual(data["history"], [])
            self.assertEqual(
                len(data["queue"]),
                len(auto_populate_ai.ALLOWED_TYPES)
                * auto_populate_ai.TARGET_PER_TYPE,
            )
            for media_type in auto_populate_ai.ALLOWED_TYPES:
                items = [
                    item
                    for item in data["queue"]
                    if item["type"] == media_type
                ]
                self.assertEqual(len(items), auto_populate_ai.TARGET_PER_TYPE)
                self.assertTrue(
                    all(
                        item["status"] == "queue"
                        and item["resolutionStatus"] == "verified"
                        and item["link"]
                        and item["imageUrl"]
                        for item in items
                    )
                )


class RollingFreshnessTests(unittest.TestCase):
    def test_new_episode_replaces_same_show_even_with_same_artwork(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        old = verified_item("podcast", "old")
        old.update(
            {
                "sourceSeriesId": "show-123",
                "sourceSeriesTitle": "Podcast Semanal",
                "sourcePublishedAt": (
                    now - datetime.timedelta(days=7)
                ).isoformat(),
                "expiryDate": (now + datetime.timedelta(days=2)).isoformat(),
            }
        )
        new = verified_item("podcast", "new")
        new.update(
            {
                "sourceSeriesId": "show-123",
                "sourceSeriesTitle": "Podcast Semanal",
                "sourcePublishedAt": (
                    now - datetime.timedelta(hours=3)
                ).isoformat(),
                "expiryDate": (now + datetime.timedelta(days=9)).isoformat(),
            }
        )
        old["verification"]["coverHash"] = "reused-show-artwork"
        new["verification"]["coverHash"] = "reused-show-artwork"
        history = [copy.deepcopy(old)]
        history[0]["status"] = "published"
        queue = [old]
        needed = {
            media_type: 0 for media_type in auto_populate_ai.ALLOWED_TYPES
        }

        with mock.patch.object(
            auto_populate_ai,
            "resolve_recommendation",
            return_value=copy.deepcopy(new),
        ):
            changed = auto_populate_ai._upsert_latest_podcast(
                new, queue, history, needed
            )

        self.assertTrue(changed)
        self.assertEqual([item["id"] for item in queue], ["podcast_new"])

    def test_published_episode_watermark_blocks_changed_guid_and_url(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        published = verified_item("podcast", "published-guid")
        published.update(
            {
                "status": "published",
                "sourceSeriesId": "show-456",
                "sourceSeriesTitle": "Podcast Diário",
                "sourcePublishedAt": (now - datetime.timedelta(days=1)).isoformat(),
            }
        )
        candidate = verified_item("podcast", "changed-guid")
        candidate.update(
            {
                "externalId": "a-new-guid-for-the-same-episode",
                "link": "https://example.com/a-different-episode-url",
                "sourceSeriesId": "show-456",
                "sourceSeriesTitle": "Podcast Diário",
                "sourcePublishedAt": published["sourcePublishedAt"],
            }
        )
        queue = []
        needed = {
            media_type: auto_populate_ai.TARGET_PER_TYPE
            for media_type in auto_populate_ai.ALLOWED_TYPES
        }

        with mock.patch.object(
            auto_populate_ai,
            "resolve_recommendation",
            side_effect=AssertionError("watermarked episode must not resolve"),
        ):
            changed = auto_populate_ai._upsert_latest_podcast(
                candidate, queue, [published], needed
            )

        self.assertFalse(changed)
        self.assertEqual(queue, [])

    def test_recency_dominates_priority_when_trimming_expiring_pool(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        items = []
        for index in range(5):
            item = verified_item("podcast", f"trim-{index}")
            item["sourcePublishedAt"] = (
                now - datetime.timedelta(hours=index + 1)
            ).isoformat()
            item["expiryDate"] = (now + datetime.timedelta(days=2)).isoformat()
            item["priority"] = 3
            items.append(item)
        oldest = items[-1]
        oldest["priority"] = 4

        changed = auto_populate_ai._trim_time_sensitive_pool(
            items, "podcast", limit=4
        )

        self.assertTrue(changed)
        self.assertNotIn(oldest, items)
        self.assertEqual(len(items), 4)

    def test_near_expiry_item_does_not_count_as_publishable_reserve(self):
        item = verified_item("podcast", "near-expiry")
        item["expiryDate"] = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=23)
        ).isoformat()

        self.assertFalse(auto_populate_ai._is_publishable_record(item))


class RecoveryWindowTests(unittest.TestCase):
    def test_weekly_generation_has_redundant_independent_schedules(self):
        repository_root = SCRIPTS_DIR.parent
        generate_workflow = (
            repository_root / ".github/workflows/instagram_generate.yml"
        ).read_text(encoding="utf-8")
        preflight_workflow = (
            repository_root / ".github/workflows/recommendations_preflight.yml"
        ).read_text(encoding="utf-8")
        keep_alive_workflow = (
            repository_root / ".github/workflows/keep_alive.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("7,17,27,37,47 19 * * 6", generate_workflow)
        self.assertIn("12,32,47 19 * * 6", preflight_workflow)
        self.assertIn(
            "python scripts/recover_weekly_generation.py",
            keep_alive_workflow,
        )
        self.assertIn("*/15 * * * *", keep_alive_workflow)
        self.assertIn("group: politometro-space-keepalive", keep_alive_workflow)
        self.assertIn("HF_TOKEN: ${{ secrets.HF_TOKEN }}", keep_alive_workflow)
        self.assertIn("Hugging Face backend HTTP:", keep_alive_workflow)

    def test_recovery_is_bounded_and_test_draft_never_blocks_production(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            draft_path = tmp_path / "review_draft.json"
            notification_path = tmp_path / "review_notification.json"
            publication_path = tmp_path / "instagram_publication.json"
            saturday = datetime.datetime(
                2026, 7, 18, 19, 20, tzinfo=datetime.timezone.utc
            )
            draft = {
                "draft_id": "draft_test",
                "content_hash": "hash_test",
                "created_at": saturday.isoformat(),
                "is_test": True,
                "approval": {"approved": True},
            }
            notification = {
                "draft_id": "draft_test",
                "content_hash": "hash_test",
                "review_message_id": "1",
                "caption_message_id": "2",
            }
            draft_path.write_text(json.dumps(draft), encoding="utf-8")
            notification_path.write_text(
                json.dumps(notification), encoding="utf-8"
            )
            with (
                mock.patch.object(
                    recover_weekly_generation, "DRAFT_PATH", str(draft_path)
                ),
                mock.patch.object(
                    recover_weekly_generation,
                    "NOTIFICATION_PATH",
                    str(notification_path),
                ),
                mock.patch.object(
                    recover_weekly_generation,
                    "PUBLICATION_PATH",
                    str(publication_path),
                ),
            ):
                needed, _ = recover_weekly_generation._generation_needed(saturday)
                self.assertTrue(needed)

                draft["is_test"] = False
                draft_path.write_text(json.dumps(draft), encoding="utf-8")
                needed, reason = recover_weekly_generation._generation_needed(
                    saturday
                )
                self.assertFalse(needed)
                self.assertIn("Discord", reason)

                too_late = saturday.replace(hour=19, minute=51)
                needed, reason = recover_weekly_generation._generation_needed(
                    too_late
                )
                self.assertFalse(needed)
                self.assertTrue(reason.startswith("Outside"))


class PostQualityGateTests(unittest.TestCase):
    def test_compact_text_keeps_largest_complete_prefix_without_ellipsis(self):
        text = (
            "Primeira frase informativa. "
            "Segunda frase com contexto adicional. "
            "Terceira frase demasiado longa para o espaço disponível."
        )

        compact = generate_post._compact_text(text, 72)

        self.assertEqual(
            compact,
            "Primeira frase informativa. Segunda frase com contexto adicional.",
        )
        self.assertNotIn("…", compact)
        self.assertNotIn("...", compact)

    def test_card_description_limits_prevent_text_from_exceeding_cover(self):
        self.assertLess(
            generate_post.DESCRIPTION_CHAR_LIMITS["q2"],
            generate_post.DESCRIPTION_CHAR_LIMITS["q1"],
        )
        self.assertEqual(
            generate_post.DESCRIPTION_LINE_LIMITS["q2"], 8
        )
        self.assertEqual(
            generate_post.DESCRIPTION_LINE_LIMITS["q4"], 8
        )
        self.assertLessEqual(
            generate_post.DESCRIPTION_LINE_LIMITS["q4"] * 18,
            192,
        )

    def test_news_cannot_fill_highlight_quadrant(self):
        queue = [
            verified_item("book", "approved"),
            verified_item("podcast", "approved"),
            verified_item("movie", "approved"),
            verified_item("highlight", "ordinary-news"),
            verified_item("highlight", "opinion"),
        ]
        queue[3].update(
            {
                "title": "Líder partidário reage às notícias do dia",
                "description": "A declaração foi feita esta manhã.",
                "link": "https://www.rtp.pt/noticias/politica/declaracao_n1",
            }
        )
        queue[4].update(
            {
                "title": "O futuro da democracia",
                "description": "Reflexão do autor.",
                "link": "https://www.publico.pt/opiniao/futuro-democracia",
            }
        )
        with (
            mock.patch.object(
                generate_post,
                "resolve_recommendation",
                side_effect=lambda item, force=False: item,
            ),
            mock.patch.object(
                generate_post,
                "load_cover_for_item",
                return_value=Image.new("RGB", (300, 300), "navy"),
            ),
            mock.patch.object(
                generate_post,
                "_revalidate_reviewed_source",
                return_value=None,
            ),
        ):
            selected, _ = generate_post.get_recommendations_with_valid_covers(
                queue
            )

        self.assertEqual(selected["q4"]["id"], "highlight_opinion")


class PodcastEditorialDescriptionTests(unittest.TestCase):
    def test_wednesday_edition_selects_exactly_one_nostalgia_episode(self):
        queue = [
            verified_item("nostalgia", "classic"),
            verified_item("book", "book"),
            verified_item("podcast", "podcast"),
            verified_item("movie", "movie"),
            verified_item("highlight", "highlight"),
        ]

        with (
            mock.patch.object(
                generate_post,
                "resolve_recommendation",
                side_effect=lambda item, force=False: item,
            ),
            mock.patch.object(
                generate_post,
                "load_cover_for_item",
                return_value=Image.new("RGB", (300, 300), "navy"),
            ),
            mock.patch.object(
                generate_post,
                "_revalidate_reviewed_source",
                return_value=None,
            ),
        ):
            selected, _ = generate_post.get_recommendations_with_valid_covers(
                queue,
                history=[],
                post_type="wednesday_nostalgia",
            )

        self.assertEqual(list(selected), ["w1"])
        self.assertEqual(selected["w1"]["type"], "nostalgia")

    title = (
        "Exames nacionais: de “tropeção em tropeção”, "
        "o que há ainda para correr mal?"
    )
    source_description = (
        "Os exames nacionais, este ano, andam de “tropeção em tropeção”. "
        "Mas na noite em que este programa é emitido finalmente chegaram às "
        "escolas as notas finais. Contudo, muitas ainda não foram afixadas. "
        "Há alunos que já sabem que notas tiveram, outros que o saberão amanhã "
        "e outros só mais tarde, ainda com prazo indefinido."
    )
    editorial_description = (
        "Análise sobre a chegada tardia das notas às escolas, os resultados "
        "ainda por afixar e a falta de prazo definido para muitos alunos."
    )

    def test_groq_copy_is_grounded_and_cached_separately_from_source(self):
        item = verified_item("podcast", "editorial")
        item.update(
            {
                "title": self.title,
                "sourceSeriesTitle": "Expresso da Meia-Noite",
                "description": self.source_description,
                "sourceHint": "podcast-rss",
            }
        )
        item["verification"]["sourceDescription"] = self.source_description
        response = mock.Mock()
        response.ok = True
        response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "title": (
                                    "Falhas e atrasos nos exames nacionais"
                                ),
                                "description": self.editorial_description,
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

        with mock.patch.object(
            auto_populate_ai.requests, "post", return_value=response
        ) as post:
            changed = auto_populate_ai._editorialize_podcast_item(
                item, "groq-test-key"
            )

        self.assertTrue(changed)
        self.assertEqual(item["description"], self.editorial_description)
        self.assertEqual(
            item["verification"]["sourceDescription"],
            self.source_description,
        )
        self.assertEqual(
            item["verification"]["editorialDescription"],
            self.editorial_description,
        )
        self.assertEqual(
            item["verification"]["editorialDescriptionVersion"],
            auto_populate_ai.PODCAST_DESCRIPTION_VERSION,
        )
        post.assert_called_once()

        with mock.patch.object(auto_populate_ai.requests, "post") as repeated:
            self.assertFalse(
                auto_populate_ai._editorialize_podcast_item(
                    item, "groq-test-key"
                )
            )
        repeated.assert_not_called()

    def test_promotional_model_copy_uses_complete_extractive_fallback(self):
        response = mock.Mock()
        response.ok = True
        response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "description": (
                                    "Neste episódio, ouve tudo agora..."
                                )
                            }
                        )
                    }
                }
            ]
        }
        with mock.patch.object(
            auto_populate_ai.requests, "post", return_value=response
        ):
            generated = (
                auto_populate_ai._request_podcast_editorial_description(
                    "groq-test-key",
                    title=self.title,
                    show="Expresso da Meia-Noite",
                    source_description=self.source_description,
                )
            )

        self.assertEqual(generated, "")
        fallback = auto_populate_ai._extractive_podcast_description(
            self.title, self.source_description
        )
        self.assertTrue(
            auto_populate_ai._podcast_description_is_valid(
                fallback, self.title, self.source_description
            )
        )
        self.assertIn("notas", fallback.casefold())
        self.assertNotIn("…", fallback)
        self.assertNotIn("...", fallback)

    def test_participant_only_feed_gets_a_complete_title_grounded_fallback(self):
        title = "Eleições"
        source = (
            "Com Ana Silva e João Costa. Moderação de Maria Sousa. "
            "See omnystudio.com/listener for privacy information."
        )

        fallback = auto_populate_ai._extractive_podcast_description(
            title, source
        )

        self.assertTrue(
            auto_populate_ai._podcast_description_is_valid(
                fallback, title, source
            )
        )
        self.assertIn("eleições", fallback.casefold())
        self.assertNotIn("moderação", fallback.casefold())

    def test_review_generation_writes_timestamped_draft_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            rec_path = tmp_path / "recommendations.json"
            template_path = tmp_path / "template.jpg"
            output_path = tmp_path / "current_post.jpg"
            caption_path = tmp_path / "current_caption.txt"
            draft_path = tmp_path / "review_draft.json"

            selected = {
                qkey: verified_item(media_type, f"draft-{qkey}")
                for qkey, media_type in generate_post.REQUIRED_TYPES.items()
            }
            rec_path.write_text(
                json.dumps(
                    {"queue": list(selected.values()), "history": []}
                ),
                encoding="utf-8",
            )
            Image.new("RGB", (1080, 1350), "white").save(
                template_path, format="JPEG"
            )
            covers = {
                qkey: Image.new("RGB", (400, 500), (40, 80, 120))
                for qkey in selected
            }

            with (
                mock.patch.object(
                    generate_post, "SCRIPT_DIR", str(tmp_path)
                ),
                mock.patch.object(
                    generate_post, "REC_FILE", str(rec_path)
                ),
                mock.patch.object(
                    generate_post, "TEMPLATE_PATH", str(template_path)
                ),
                mock.patch.object(
                    generate_post, "OUTPUT_PATH", str(output_path)
                ),
                mock.patch.object(
                    generate_post,
                    "OUTPUT_CAPTION_PATH",
                    str(caption_path),
                ),
                mock.patch.object(generate_post, "ensure_fonts"),
                mock.patch.object(
                    generate_post,
                    "get_recommendations_with_valid_covers",
                    return_value=(selected, covers),
                ),
                mock.patch.object(
                    sys, "argv", ["generate_post.py", "--review"]
                ),
            ):
                generate_post.generate_production_post()

            self.assertTrue(output_path.is_file())
            self.assertTrue(caption_path.is_file())
            self.assertTrue(draft_path.is_file())
            draft = json.loads(draft_path.read_text(encoding="utf-8"))
            created_at = datetime.datetime.fromisoformat(
                draft["created_at"].replace("Z", "+00:00")
            )
            self.assertIsNotNone(created_at.tzinfo)
            self.assertFalse(draft["approval"]["approved"])
            self.assertTrue(draft["draft_id"].startswith("draft_"))

    def test_caption_uses_template_and_fixed_human_hashtags(self):
        selected = {
            "q1": verified_item("book", "Historia de Portugal"),
            "q2": verified_item("podcast", "Economia sem filtros"),
            "q3": verified_item("movie", "Capitaes de Abril"),
            "q4": verified_item("highlight", "Investigacao submarinos"),
        }
        selected["q1"]["title"] = "Portugal: Uma História"
        selected["q1"]["description"] = "Uma viagem pela história de Portugal."
        selected["q2"]["title"] = "A Crise Económica em Portugal"
        selected["q2"]["description"] = "Um episódio sobre economia e inflação."
        selected["q3"]["title"] = "Capitães de Abril"
        selected["q3"]["description"] = "Um filme histórico sobre o 25 de Abril."
        selected["q4"]["title"] = "Investigação ao Caso dos Submarinos"
        selected["q4"]["description"] = "Jornalismo de investigação."

        caption = generate_post.build_caption(selected)

        self.assertTrue(
            caption.startswith("📣 RECOMENDAÇÕES DA SEMANA")
        )
        self.assertIn("@_.davstrango._", caption)
        self.assertIn("@luisflmaximo", caption)
        self.assertIn(
            "Desenvolvido por @_.davstrango._ e @luisflmaximo no âmbito do projeto @politiza.te",
            caption,
        )
        self.assertIn("📖 LIVRO:", caption)
        self.assertIn("🎙️ PODCAST:", caption)
        self.assertIn("🎞️ FILME:", caption)
        self.assertIn("📰 DESTAQUE:", caption)
        self.assertNotIn("📊 PODCAST:", caption)
        self.assertNotIn("🏛️ FILME:", caption)
        self.assertNotIn("🔎 DESTAQUE:", caption)
        self.assertIn("Qual destes vais espreitar primeiro?", caption)
        self.assertIn(
            "#Portugal #PolitizaTe #Recomendacoes #Sugestoes "
            "#Politometro #Politica #Livro #Podcast #Filme",
            caption,
        )
        self.assertNotIn("#PortugalUmaHistoria", caption)
        self.assertNotIn("#CapitaesDeAbril", caption)
        self.assertNotIn("#InvestigacaoCasoSubmarinos", caption)
        self.assertNotIn("#Economia", caption)
        self.assertNotIn("#25deAbril", caption)
        self.assertNotIn("#documentarios", caption.lower())
        self.assertNotIn("escrutínio", caption.lower())
        self.assertNotIn("#escrutinio", caption.lower())
        self.assertNotIn("👉", caption)

    def test_caption_preserves_canonical_podcast_title(self):
        selected = {
            qkey: verified_item(media_type, "conteudo")
            for qkey, media_type in generate_post.REQUIRED_TYPES.items()
        }
        selected["q2"]["title"] = (
            "Exames nacionais: de “tropeção em tropeção”, "
            "o que há ainda para correr mal?"
        )

        caption = generate_post.build_caption(selected)

        self.assertIn(
            "🎙️ PODCAST: Exames nacionais: de “tropeção em tropeção”, "
            "o que há ainda para correr mal?",
            caption,
        )

    def test_caption_does_not_infer_topic_hashtags(self):
        selected = {
            qkey: verified_item(media_type, "conteudo")
            for qkey, media_type in generate_post.REQUIRED_TYPES.items()
        }
        caption = generate_post.build_caption(selected)

        self.assertIn("#Portugal", caption)
        self.assertIn("#PolitizaTe", caption)
        self.assertIn("#Recomendacoes", caption)
        self.assertIn("#Sugestoes", caption)
        self.assertIn("#Politometro", caption)
        self.assertIn("#Politica", caption)
        self.assertIn("#Livro", caption)
        self.assertIn("#Podcast", caption)
        self.assertIn("#Filme", caption)
        self.assertNotIn("#Destaque", caption)
        self.assertNotIn("#Democracia", caption)
        self.assertNotIn("#Ambiente", caption)
        self.assertNotIn("#Economia", caption)

    def test_caption_uses_the_specific_recommendation_category_hashtag(self):
        selected = {
            qkey: verified_item(media_type, "conteudo")
            for qkey, media_type in generate_post.REQUIRED_TYPES.items()
        }
        selected["q3"]["category"] = "Série"
        selected["q4"]["category"] = "Artigo de Opinião"

        caption = generate_post.build_caption(selected)

        self.assertIn("#Serie", caption)
        self.assertIn("#ArtigoDeOpiniao", caption)
        self.assertNotIn("#Filme", caption)
        self.assertNotIn("#Destaque", caption)

    def test_pending_items_are_never_selected(self):
        queue = []
        for media_type in generate_post.REQUIRED_TYPES.values():
            queue.append(verified_item(media_type, "pending", status="pending_sent"))
            queue.append(verified_item(media_type, "approved", status="queue"))

        colours = {
            "book": (200, 20, 20),
            "podcast": (20, 200, 20),
            "movie": (20, 20, 200),
            "highlight": (180, 120, 20),
        }

        def fake_resolve(item, force=False):
            self.assertEqual(item["status"], "queue")
            self.assertFalse(force)
            return item

        def fake_cover(item):
            return Image.new("RGB", (300, 300), colours[item["type"]])

        with (
            mock.patch.object(
                generate_post, "resolve_recommendation", side_effect=fake_resolve
            ),
            mock.patch.object(
                generate_post, "load_cover_for_item", side_effect=fake_cover
            ),
            mock.patch.object(
                generate_post, "_revalidate_reviewed_source", return_value=None
            ),
        ):
            selected, covers = generate_post.get_recommendations_with_valid_covers(
                queue
            )

        self.assertEqual(set(selected), set(generate_post.REQUIRED_TYPES))
        self.assertEqual(set(covers), set(generate_post.REQUIRED_TYPES))
        self.assertTrue(
            all(item["id"].endswith("_approved") for item in selected.values())
        )

    def test_duplicate_cover_falls_back_to_next_verified_candidate(self):
        queue = [
            verified_item("book", "approved"),
            verified_item("podcast", "duplicate"),
            verified_item("podcast", "fallback"),
            verified_item("movie", "approved"),
            verified_item("highlight", "approved"),
        ]
        queue[0]["verification"]["coverHash"] = "same-artwork"
        queue[1]["verification"]["coverHash"] = "same-artwork"

        with (
            mock.patch.object(
                generate_post,
                "resolve_recommendation",
                side_effect=lambda item, force=False: item,
            ),
            mock.patch.object(
                generate_post,
                "load_cover_for_item",
                return_value=Image.new("RGB", (300, 300), "navy"),
            ),
            mock.patch.object(
                generate_post, "_revalidate_reviewed_source", return_value=None
            ),
        ):
            selected, _ = generate_post.get_recommendations_with_valid_covers(
                queue
            )

        self.assertEqual(selected["q2"]["id"], "podcast_fallback")

    def test_latest_podcast_is_selected_and_expired_one_is_ignored(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        old = verified_item("podcast", "old")
        old["sourcePublishedAt"] = (now - datetime.timedelta(days=8)).isoformat()
        old["expiryDate"] = (now - datetime.timedelta(hours=1)).isoformat()
        latest = verified_item("podcast", "latest")
        latest["sourcePublishedAt"] = (now - datetime.timedelta(hours=6)).isoformat()
        latest["expiryDate"] = (now + datetime.timedelta(days=2)).isoformat()
        queue = [
            verified_item("book", "approved"),
            old,
            latest,
            verified_item("movie", "approved"),
            verified_item("highlight", "approved"),
        ]

        with (
            mock.patch.object(
                generate_post,
                "resolve_recommendation",
                side_effect=lambda item, force=False: item,
            ),
            mock.patch.object(
                generate_post,
                "load_cover_for_item",
                return_value=Image.new("RGB", (300, 300), "navy"),
            ),
            mock.patch.object(
                generate_post, "_revalidate_reviewed_source", return_value=None
            ),
        ):
            selected, _ = generate_post.get_recommendations_with_valid_covers(
                queue
            )

        self.assertEqual(selected["q2"]["id"], "podcast_latest")


class ApprovedDraftCommitTests(unittest.TestCase):
    def test_commit_preserves_reviewed_links_and_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            rec_path = tmp_path / "recommendations.json"
            draft_path = tmp_path / "review_draft.json"
            post_path = tmp_path / "current_post.png"
            caption_path = tmp_path / "current_caption.txt"
            receipt_path = tmp_path / "instagram_publication.json"

            post_path.write_bytes(b"reviewed-post-bytes")
            caption_path.write_text("Legenda revista", encoding="utf-8")

            quadrants = {}
            queue = []
            for qkey, media_type in generate_post.REQUIRED_TYPES.items():
                item = verified_item(media_type, qkey)
                quadrants[qkey] = item
                queue.append(copy.deepcopy(item))
            rec_path.write_text(
                json.dumps({"queue": queue, "history": []}, ensure_ascii=False),
                encoding="utf-8",
            )

            with (
                mock.patch.object(generate_post, "REC_FILE", str(rec_path)),
                mock.patch.object(generate_post, "OUTPUT_PATH", str(post_path)),
                mock.patch.object(
                    generate_post, "OUTPUT_CAPTION_PATH", str(caption_path)
                ),
                mock.patch.object(
                    generate_post,
                    "load_cover_for_item",
                    return_value=Image.new("RGB", (300, 300), "navy"),
                ),
            ):
                post_sha = generate_post._sha256_file(post_path)
                caption_sha = generate_post._sha256_file(caption_path)
                content_hash = generate_post._draft_content_hash(
                    quadrants, post_sha, caption_sha
                )
                draft_id = f"draft_{content_hash[:20]}"
                draft = {
                    "schema_version": 2,
                    "draft_id": draft_id,
                    "content_hash": content_hash,
                    "created_at": datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat(),
                    "is_test": False,
                    "post_sha256": post_sha,
                    "caption_sha256": caption_sha,
                    "approval": {
                        "approved": True,
                        "draft_id": draft_id,
                        "content_hash": content_hash,
                    },
                    **quadrants,
                }
                draft_path.write_text(
                    json.dumps(draft, ensure_ascii=False), encoding="utf-8"
                )
                receipt_path.write_text(
                    json.dumps(
                        {
                            "draft_id": draft_id,
                            "content_hash": content_hash,
                            "post_id": "instagram-post-123",
                        }
                    ),
                    encoding="utf-8",
                )

                generate_post.commit_approved_draft(
                    str(draft_path),
                    receipt_file=str(receipt_path),
                )

            committed = json.loads(rec_path.read_text(encoding="utf-8"))
            self.assertEqual(committed["queue"], [])
            self.assertEqual(len(committed["history"]), 4)
            self.assertFalse(draft_path.exists())
            reviewed_by_id = {item["id"]: item for item in quadrants.values()}
            for item in committed["history"]:
                reviewed = reviewed_by_id[item["id"]]
                self.assertEqual(item["link"], reviewed["link"])
                self.assertEqual(item["imageUrl"], reviewed["imageUrl"])
                self.assertEqual(item["status"], "published")
                self.assertEqual(item["publishedDraftId"], draft_id)
                self.assertEqual(item["instagramPostId"], "instagram-post-123")

    def test_unapproved_draft_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft_path = Path(tmp) / "review_draft.json"
            draft_path.write_text(
                json.dumps(
                    {
                        "draft_id": "draft_x",
                        "content_hash": "abc",
                        "approval": {"approved": False},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "não foi aprovado"):
                generate_post.commit_approved_draft(str(draft_path))
    def test_dry_run_rejects_item_replaced_after_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            rec_path = tmp_path / "recommendations.json"
            draft_path = tmp_path / "review_draft.json"
            post_path = tmp_path / "current_post.jpg"
            caption_path = tmp_path / "current_caption.txt"
            post_path.write_bytes(b"approved-jpeg")
            caption_path.write_text("Legenda aprovada", encoding="utf-8")

            quadrants = {
                qkey: verified_item(media_type, qkey)
                for qkey, media_type in generate_post.REQUIRED_TYPES.items()
            }
            queue = [
                copy.deepcopy(item)
                for qkey, item in quadrants.items()
                if qkey != "q2"
            ]
            queue.append(verified_item("podcast", "replacement"))
            rec_path.write_text(
                json.dumps({"queue": queue, "history": []}, ensure_ascii=False),
                encoding="utf-8",
            )

            with (
                mock.patch.object(generate_post, "REC_FILE", str(rec_path)),
                mock.patch.object(generate_post, "OUTPUT_PATH", str(post_path)),
                mock.patch.object(
                    generate_post, "OUTPUT_CAPTION_PATH", str(caption_path)
                ),
                mock.patch.object(
                    generate_post,
                    "load_cover_for_item",
                    return_value=Image.new("RGB", (300, 300), "navy"),
                ),
                mock.patch.object(
                    generate_post,
                    "_revalidate_reviewed_source",
                    return_value=None,
                ),
            ):
                post_sha = generate_post._sha256_file(post_path)
                caption_sha = generate_post._sha256_file(caption_path)
                content_hash = generate_post._draft_content_hash(
                    quadrants, post_sha, caption_sha
                )
                draft_id = f"draft_{content_hash[:20]}"
                draft_path.write_text(
                    json.dumps(
                        {
                            "draft_id": draft_id,
                            "content_hash": content_hash,
                            "created_at": datetime.datetime.now(
                                datetime.timezone.utc
                            ).isoformat(),
                            "is_test": False,
                            "post_sha256": post_sha,
                            "caption_sha256": caption_sha,
                            "approval": {
                                "approved": True,
                                "draft_id": draft_id,
                                "content_hash": content_hash,
                            },
                            **quadrants,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(RuntimeError, "itens em falta"):
                    generate_post.commit_approved_draft(
                        str(draft_path),
                        require_publication_receipt=False,
                        dry_run=True,
                    )


if __name__ == "__main__":
    unittest.main()
