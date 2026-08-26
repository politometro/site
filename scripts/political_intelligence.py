#!/usr/bin/env python3
"""Incremental political-news, promises and parliamentary-votes pipeline.

The pipeline deliberately keeps three kinds of evidence separate:

* electoral-programme excerpts and promises;
* short, attributed excerpts from permitted news sources;
* official data published by the Assembleia da República.

It is safe to run repeatedly.  It respects robots.txt, stores only limited news
excerpts, rediscovers signed open-data URLs from the Assembleia resource pages,
and marks every promise/proposal correspondence as an automatic suggestion until
someone verifies it.

Examples
--------
    python scripts/political_intelligence.py all --since-days 5
    python scripts/political_intelligence.py assembly --all-history --max-detail-pages 250
    python scripts/political_intelligence.py export
"""

from __future__ import annotations

import argparse
import atexit
import datetime as dt
import hashlib
import html
import itertools
import json
import logging
import mmap
import os
import re
import sys
import tempfile
import threading
import signal
import time
import unicodedata
import urllib.parse
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

import requests

try:
    import orjson
except ImportError:  # pragma: no cover - fallback for minimal installations
    orjson = None


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "data" / "political_intelligence_config.json"
DEFAULT_ENTITIES = ROOT / "data" / "political_entities.json"
DEFAULT_STATE = ROOT / "data" / "political_intelligence_state.json"
DEFAULT_PUBLIC_OUTPUT = ROOT / "website" / "public" / "political-intelligence.json"
DEFAULT_MEMORY_OUTPUT = ROOT / "scripts" / "extracted_chunks_political_intelligence.json"
PROGRAM_CHUNK_FILES = (
    ROOT / "scripts" / "extracted_chunks.json",
    ROOT / "scripts" / "extracted_chunks_ocr.json",
)

LOGGER = logging.getLogger("political_intelligence")

# Paragem cooperativa: Ctrl+C assinala o evento; as fontes terminam a operação
# em curso (sub-segundo) e o pipeline salta para guardar estado + resumo.
# Um segundo Ctrl+C força a saída imediata via KeyboardInterrupt real.
_STOP_REQUESTED = threading.Event()
UTC = dt.timezone.utc
ALL_LEGISLATURES = (
    "XVII", "XVI", "XV", "XIV", "XIII", "XII", "XI", "X", "IX", "VIII",
    "VII", "VI", "V", "IV", "III", "II",
)

STOP_WORDS = {
    "a", "ao", "aos", "as", "com", "da", "das", "de", "do", "dos", "e",
    "em", "na", "nas", "no", "nos", "o", "os", "ou", "para", "por", "que",
    "se", "um", "uma", "uns", "umas", "sobre", "entre", "como", "mais", "menos",
    "sua", "seu", "suas", "seus", "pela", "pelo", "pelas", "pelos", "ser", "sao",
    "foi", "ter", "tem", "nosso", "nossa", "este", "esta", "estes", "estas",
    "portugal", "portugues", "portuguesa", "medida", "medidas", "proposta",
    "propostas", "programa", "nacional", "governo", "partido", "politica",
    "politicas", "pessoas", "novo", "nova", "novos", "novas", "promete",
    "prometem", "prometeu", "prometer", "compromete", "comprometem",
    "comprometeu", "propoe", "propoem", "propor", "pretende", "pretendem",
}

TOPIC_TERMS = {
    "politica": {
        "politica", "politico", "politicos", "politica", "politicas", "partido", "partidos", "partidario",
        "governo", "governacao", "governante", "governantes", "conselho de ministros",
        "primeiro-ministro", "primeira-ministra", "secretario de estado", "secretaria de estado",
        "parlamento", "parlamentar", "parlamentares", "assembleia", "assembleia da republica",
        "deputado", "deputada", "deputados", "deputadas", "hemiciclo", "bancada",
        "ministerio", "ministerios", "ministro", "ministra", "ministros", "ministras",
        "eleicao", "eleicoes", "eleitoral", "eleitorais", "legislatura", "legislativas",
        "autarquicas", "presidenciais", "europeias", "sufragio", "voto", "votos", "votacao", "votacoes",
        "iniciativa legislativa", "projeto de lei", "proposta de lei", "decreto", "decreto-lei",
        "legislacao", "lei", "leis", "diploma", "diplomas", "constituicao", "constitucional",
        "tribunal constitucional", "presidencia", "presidente da republica", "chefe de estado", "belem", "sao bento",
        "oposicao", "maioria", "maioria absoluta", "coligacao", "coligacoes", "mocao de censura", "mocao de confianca",
        "autarquia", "autarquias", "autarquico", "autarquica", "autarquicos", "autarquicas",
        "camara municipal", "camaras municipais", "camara", "presidente da camara", "presidentes de camara",
        "vereador", "vereadores", "vereacao", "junta de freguesia", "juntas de freguesia", "assembleia municipal",
        "municipio", "municipios", "poder local", "anmp", "anafre",
        "estado", "falhanco do estado", "politicas publicas", "administracao publica", "funcao publica", "servicos publicos",
        "ministerio publico", "procuradoria", "procurador-geral", "procuradora-geral", "pgr",
        "tribunal", "tribunais", "juiz", "juizes", "magistrado", "magistrados",
        "policia judiciaria", "pj", "psp", "gnr", "forcas de seguranca", "seguranca interna", "protecao civil",
        "corrupcao", "fraude fiscal", "branqueamento", "prevaricacao", "trafico de influencias",
        "uniao europeia", "comissao europeia", "parlamento europeu", "conselho europeu", "conselho da ue", "bruxelas",
        "eurodeputado", "eurodeputados", "eurodeputada", "eurodeputadas",
        "tratado", "diplomacia", "diplomatico", "geopolitica", "relacoes internacionais", "politica externa", "negociacoes",
        "onu", "nacoes unidas", "embaixada", "embaixador", "imigracao", "imigrante", "imigrantes", "migrantes", "asilo", "fronteiras", "sef", "aima",
        "sns", "servico nacional de saude", "saude", "hospital", "hospitais", "urgencias", "centros de saude",
        "educacao", "escola", "escolas", "professores", "docentes", "ensino superior", "universidades",
        "seguranca social", "habitacao social", "casas sociais",
        "guerra", "missil", "misseis", "sancoes", "mne", "negocios estrangeiros",
        "prisioneiros de guerra", "cessar fogo", "cessar fogo",
    },
    "economia": {
        "economia", "economico", "economica", "economicos", "economicas",
        "orcamento", "orcamento do estado", "orcamentario", "financas", "financas publicas",
        "fiscal", "fiscalidade", "imposto", "impostos", "irs", "irc", "iva", "imi", "imposto do selo",
        "taxa", "taxas", "tarifas", "deficit", "defice", "superavit", "divida publica", "divida",
        "inflacao", "precos", "custo de vida", "poder de compra",
        "combustivel", "combustiveis", "gasolina", "gasoleo", "preco dos combustiveis", "preco eficiente",
        "energia", "eletricidade", "gas", "erse", "tarifa da luz", "tarifa de gas",
        "salario", "salarios", "salario minimo", "salario medio", "emprego", "desemprego",
        "trabalho", "trabalhadores", "pensao", "pensoes", "pensionistas", "reforma", "reformas",
        "subsidio", "subsidios", "subsidio de desemprego", "rendimento social de insercao", "rsi", "apoios sociais", "concertacao social",
        "habitacao", "habitacao acessivel", "rendas", "renda acessivel", "arrendamento", "senhorios", "inquilinos", "imobiliario", "casas", "alojamento local", "casas sociais",
        "pib", "crescimento economico", "recessao", "banco", "bancos", "banca", "banco de portugal", "bce", "banco central europeu",
        "juros", "taxa de juro", "taxas de juro", "euribor", "credito", "credito a habitacao", "credito pessoal",
        "mercado", "mercados", "bolsa", "empresas", "exportacoes", "importacoes", "balanca comercial",
        "investimento", "investimento publico", "investimento direto estrangeiro", "prr", "plano de recuperacao e resiliencia",
        "fundos europeus", "portugal 2030", "subsidios europeus", "cmvm", "concorrencia",
    },
}

TOPIC_PATTERNS = {
    label: re.compile(
        rf"(?<!\w)(?:{'|'.join(re.escape(term) for term in sorted(terms, key=len, reverse=True))})(?!\w)"
    )
    for label, terms in TOPIC_TERMS.items()
}

NEWS_PROMISE_PATTERNS = (
    r"\bpromet(?:e|em|eu|emos|era|eram)\b",
    r"\bcompromet(?:e|em|eu|emo-nos|emos|era|eram)\b",
    r"\b(?:vamos|iremos)\s+(?:criar|aumentar|reduzir|reforcar|garantir|"
    r"implementar|aprovar|revogar|eliminar|investir|assegurar|promover|"
    r"construir|resolver|combater|defender|apoiar|continuar|fazer)\b",
    r"\bpropomos\b", r"\bpropoe\b",
    r"\b(?:vai|irao|pretende|pretendem|quer|querem)\s+(?:criar|aumentar|reduzir|reforcar|garantir|implementar|aprovar|revogar|eliminar|investir)\b",
    r"\b(?:garantiremos|criaremos|aumentaremos|reduziremos|reforcaremos|implementaremos|aprovaremos|revogaremos|eliminaremos|investiremos|asseguraremos)\b",
)

NEWS_PROMISE_TRIGGER_RE = re.compile(
    r"\b(?:promet(?:e|em|eu|emos|ia|iam|era|eram)|"
    r"compromet(?:e|em|eu|emos|ia|iam|era|eram)|vamos|iremos|propomos|prop[oõ]e|"
    r"vai|ir[aã]o|pretende|pretendem|quer|querem|garantiremos|criaremos|"
    r"aumentaremos|reduziremos|refor[çc]aremos|implementaremos|aprovaremos|"
    r"revogaremos|eliminaremos|investiremos|asseguraremos)\b",
    re.IGNORECASE,
)

PROGRAMME_LEADING_ACTION_PATTERN = re.compile(
    r"\b(?:criar|reforcar|alargar|garantir|assegurar|promover|implementar|"
    r"reduzir|aumentar|eliminar|revogar|aprovar|investir|construir|lancar|"
    r"estabelecer|introduzir|apoiar|melhorar|modernizar|recuperar|baixar|"
    r"isentar|simplificar|proteger|valorizar|universalizar|defender|rever|"
    r"renegociar|aumento|reducao|criacao|reforco|alargamento|garantia|"
    r"promocao|implementacao|eliminacao|revogacao|investimento|construcao|"
    r"lancamento|estabelecimento|introducao|apoio|melhoria|modernizacao|"
    r"recuperacao|isencao|simplificacao|protecao|valorizacao|revisao|"
    r"renegociacao|publicacao|retirada|extincao|novo\s+modelo|novo\s+programa|"
    r"novo\s+plano)\b"
)
PROGRAMME_EXPLICIT_COMMITMENT_PATTERN = re.compile(
    r"\b(?:propomos|defendemos|pretendemos|comprometemo-nos|comprometemos|"
    r"criaremos|reforcaremos|alargaremos|garantiremos|asseguraremos|"
    r"promoveremos|implementaremos|reduziremos|aumentaremos|eliminaremos|"
    r"revogaremos|aprovaremos|investiremos|construiremos|lancaremos|"
    r"estabeleceremos|introduziremos|apoiaremos|melhoraremos|modernizaremos|"
    r"recuperaremos|baixaremos|isentaremos|simplificaremos|protegeremos|"
    r"valorizaremos|universalizaremos|iremos\s+(?:criar|reforcar|alargar|"
    r"garantir|assegurar|promover|implementar|reduzir|aumentar|eliminar|"
    r"revogar|aprovar|investir|construir|lancar|estabelecer|introduzir|apoiar)|"
    r"vamos\s+(?:criar|reforcar|alargar|garantir|assegurar|promover|"
    r"implementar|reduzir|aumentar|eliminar|revogar|aprovar|investir|construir|"
    r"lancar|estabelecer|introduzir|apoiar))\b"
)
PROGRAMME_PASSIVE_COMMITMENT_PATTERN = re.compile(
    r"\b(?:devera|deverao|tera\s+de|terao\s+de|sera|serao)\s+"
    r"(?:\w+\s+){0,3}(?:criad|reforcad|alargad|garantid|assegurad|promovid|"
    r"implementad|reduzid|aumentad|eliminad|revogad|aprova[dr]|investid|"
    r"construid|lancad|estabelecid|introduzid|apoiad|melhorad|modernizad|"
    r"recuperad|isentad|simplificad|protegid|valorizad|instituid)\w*\b"
)
PROGRAMME_NORMATIVE_COMMITMENT_PATTERN = re.compile(
    r"\b(?:urge\s+a\s+(?:criacao|extincao|eliminacao|revogacao|revisao|"
    r"implementacao|reducao|aumento)|(?:o\s+estado\s+)?tem\s+o\s+dever\s+de\s+"
    r"(?:criar|reforcar|garantir|assegurar|proteger|implementar|reduzir|aumentar)|"
    r"importa\s+(?:criar|reforcar|garantir|assegurar|proteger|implementar|"
    r"reduzir|aumentar|eliminar|revogar|aprovar))\b"
)
PROGRAMME_PROMISE_EXTRACTOR_VERSION = "5"
NEWS_FILTER_VERSION = "10"
# Review markers let repeat runs skip deterministic re-classification of
# unchanged articles.  They are derived from the filter version so any filter
# bump automatically invalidates every stored review.
ARTICLE_REVIEW_VERSION = f"rv-{NEWS_FILTER_VERSION}"
PROMISE_REVIEW_VERSION = f"pv-{NEWS_FILTER_VERSION}"

RELEVANT_SECTION_MARKERS = {
    "politica", "politico", "economia", "economico", "nacional", "pais",
    "portugal", "autarquias", "eleicoes", "parlamento", "governo", "justica",
    "sociedade", "mundo", "internacional", "regioes", "local",
}
FOREIGN_SECTION_MARKERS = {
    "desporto", "futebol", "modalidades", "lifestyle", "fama", "gente", "famosos",
    "motores", "auto", "tech", "tecnologia", "gastronomia", "receitas", "horoscopo",
    "saude-e-bem-estar", "cultura",
}
SPORTS_AND_BETTING_SECTION_MARKERS = {
    "desporto", "futebol", "modalidades", "apostas", "apostas-desportivas",
    "jogos", "jogos-de-azar", "casino", "casinos", "odds", "prognosticos",
    "prognostico", "lifestyle", "fama", "gente", "famosos", "motores",
    "auto", "tech", "tecnologia", "gastronomia", "receitas", "horoscopo",
}

BLOCKED_PATH_MARKERS = (
    "sitemap", "/tag/", "/autor/", "/search", "/pesquisa", "/rss", "/feed",
    "/apostas", "/apostas-desportivas", "/desporto", "/futebol", "/modalidades",
    "/casino", "/casinos", "/jogos-de-azar", "/prognostico", "/odds",
    "/lifestyle", "/fama", "/gente", "/famosos", "/motores", "/auto",
    "/tech", "/tecnologia", "/gastronomia", "/receitas", "/horoscopo",
    "/bwin", "/betclic", "/betano", "/lebull", "/solverdept", "/solverde.pt",
)

BETTING_BRANDS_RE = re.compile(
    r"\b(?:bwin|betclic|betano|lebull|solverdept|placard\.pt|bacana\s*play|"
    r"pokerstars|luckia|golden\s*park|esc\s*online|casino\s+portugal|"
    r"casinos?\s+portugal|estoril\s+sol\s+casinos?)\b",
    re.IGNORECASE,
)

BETTING_TERMS_RE = re.compile(
    r"\b(?:odds?|progn[oó]stico(?:s)?|apostas?(?:\s+desportivas?|\s+online)?|"
    r"casas?\s+de\s+apostas|c[oó]digo(?:s)?\s+promociona(?:l|is)|"
    r"c[oó]digo\s+de\s+b[oó]nus|b[oó]nus\s+(?:de\s+)?(?:boas[- ]vindas|registo|apostas|natal)|"
    r"freebets?|jogos?\s+de\s+(?:fortuna\s+ou\s+)?azar|roleta\s+online|"
    r"slots?\s+online|slot\s+machines?|blackjack\s+online)\b",
    re.IGNORECASE,
)

SPORTS_COMPETITIONS_RE = re.compile(
    r"\b(?:premier\s+league|champions\s+league|liga\s+dos\s+campe[oõ]es|"
    r"liga\s+europa|europa\s+league|conference\s+league|primeira\s+liga|"
    r"liga\s+portugal|liga\s+betclic|ta[çc]a\s+de\s+portugal|"
    r"ta[çc]a\s+da\s+liga|superta[çc]a|qualifica[çc][aã]o\s+mundial)\b",
    re.IGNORECASE,
)

MATCH_VS_RE = re.compile(
    r"\b[A-Za-zÀ-ÿ0-9\s-]+\s+(?:vs|v\.|contra)\s+[A-Za-zÀ-ÿ0-9\s-]+\b",
    re.IGNORECASE,
)

# Indicadores de que a notícia diz respeito a Portugal ou à UE enquanto espaço
# político/económico. Usada para validar tópicos sem entidade nomeada.
AMBITO_PT_UE_RE = re.compile(
    r"\b(?:"
    r"portugal|portugu[eê]s(?:a)?s?|lisboa|\bporto\b|"
    r"governo\s+portugu[eê]s|assembleia(?:\s+da\s+rep[uú]blica)?|"
    r"s[aã]o bento|bel[eé]m|santo bento|"
    r"uni[aã]o europeia|europeu(?:a)?s?|bruxelas|comiss[aã]o europeia|"
    r"parlamento europeu|banco central europeu|bce\b|eurosistema|"
    r"\beuro\b|euros?[\s]|minist[eé]rio p[uú]blico|tribunal constitucional|"
    r"presid[eê]ncia(?:\s+do\s+conselho)?|primeiro[- ]ministro|"
    r"oposi[cç][aã]o|conselho de ministros|deputados?|"
    r"segurana[cç]a social|servi[cç]o nacional de sa[uú]de|sns\b|"
    r"iref\b|dgt\b|ine\b|banco de portugal"
    r")\b",
    re.IGNORECASE,
)

CELEBRITY_LIFESTYLE_RE = re.compile(
    r"\b(?:"
    # Celebridades internacionais e nacionais sem ligação política
    r"dolly parton|kim kardashian|kanye west|taylor swift|brad pitt|"
    r"angelina jolie|leonardo dicaprio|miley cyrus|beyonc[eé]|rihanna|"
    r"cristiano ronaldo|messi|neymar|mbapp[eé]|haaland|"
    r"shakira|jennifer lopez|lady gaga|madonna|justin bieber|"
    # Moda/beleza/corpo
    r"cabelo|seios|bikini|biqu[ií]ni|ver[aã]o|f[eé]rias?\s+(?:na|em)\s+"
    r"|roteiro\s+de\s+viagem|guia\s+de\s+viagem|melhores\s+praias"
    r")\b",
    re.IGNORECASE,
)

CRIME_ACCIDENT_RE = re.compile(
    r"\b(?:"
    r"assaltant[eo]s?|assalto|roubo|furto|atropelament[oa]s?|atropelad[oa]s?|"
    r"homic[ií]dio| assassinado|esfaquead[oa]s?|balead[oa]s?|tiroteio|"
    r"acidente\s+(?:de\s+)?(?:via[cç][aã]o|carro|moto)|desaparecid[oa]s?|"
    r"inc[eê]ndio\s+florestal|preso\s+preventivo|detid[oa]s?\s+pela?\s+pj\b|"
    r"morto[s]?\s+(?:em|no|pelo?)|ferido[s]?\s+(?:graves?|leves)"
    r")\b",
    re.IGNORECASE,
)

SPORTS_TRANSFER_RE = re.compile(
    r"\b(?:"
    r"refor[cç]o\s+do\s+\w+|contrato\s+at[eé]\s+\d{4}|"
    r"ficha\s+(?:do|da)\s+jogador|empr[eé]stimo\s+com\s+op[cç][aã]o|"
    r"mercado\s+de\s+(?:transfer[eê]ncias|contrata[cç][oõ]es)|"
    r"renova\s+(?:com|at[eé])\s+(?:o|a)\s+\w+\s+at[eé]"
    r")\b",
    re.IGNORECASE,
)

# Léxico de coberturas desportivas puras (futebol sobretudo). Só rejeita quando
# NÃO há âncora política forte nem tema político/económico genuíno: "Montenegro
# candidato ao Mundial 2030" mantém-se, "Campeão FC Porto bate Arouca" sai.
_FOOTBALL_LEXICON = (
    r"gol[oa]s?\b|gol[oa]\s+(?:de|na|ao)|marc(?:ou|a|aram)\s+(?:o\s+)?(?:golo|golos)|"
    r"derby|d[eé]rbi\b|jogo\s+(?:amig[aá]vel|da\s+(?:jornada|ta[çc]a|liga))|"
    r"refor[çc]o\b|contrata[cç][aã]o\b|contratad[oa]\s+pelo|transfer[eê]ncia\b|"
    r"treinador\b|plantel\b|"
    r"liga\s+(?:dos\s+campe[oõ]es|portugal|espanhola)|ta[çc]a\s+de\s+portugal|"
    r"campe[aã]o\s+nacional\b|t[ií]tulo\s+(?:nacional|continental|europeu)|"
    r"est[aá]dio\b|cl[uú]be\s+(?:de\s+)?futebol|"
    r"eliminat[oó]ria\b|goleada\b"
)
_FOOTBALL_CLUBS = (
    r"benfica|sporting(?:\s+clube)?|\bscp\b|fc\s+porto|\bfc p\b|sc\s+braga|"
    r"boavista|arouca|farense|estoril(?:\s+praia)?|gil\s+vicente|rio\s+ave|"
    r"moreirense|famalic[ãa]o|vit[óo]ria\s+(?:de\s+)?guimar[ãa]es|"
    r"portimonense|mar[ií]timo|nacional\b|tondela|leix[õo]es|acad[eé]mica|"
    r"casa\s+pia|estrela\s+amadora|pa[çc]os\s+de\s+ferreira|feirense|"
    r"sele[çc][ãa]o\s+(?:portuguesa|nacional)"
)
_FOOTBALL_ACTIONS = (
    r"bater\b|bateu\b|venceu\b|vence\b|vit[óo]ria\b|derrot(?:a|ou)\b|empat(?:e|ou)\b|"
    r"isola-se\b|lideran[çc]a\b|campe[ãa]o\b|t[ií]tulo\b|refor[çc]o\b|"
    r"contrata(?:r|do|da|[çc][ãa]o)?\b|transfer[eê]ncia\b|jogador\b|treinador\b|"
    r"contrato\b|milh[õo]es\b|golo\b|golos\b|marcou\b|convocad[oa]\b"
)
FOOTBALL_COVERAGE_RE = re.compile(
    rf"\b(?:{_FOOTBALL_LEXICON}"
    rf"|(?:{_FOOTBALL_CLUBS}).{{0,80}}(?:{_FOOTBALL_ACTIONS})"
    rf"|(?:{_FOOTBALL_ACTIONS}).{{0,80}}(?:{_FOOTBALL_CLUBS}))",
    re.IGNORECASE,
)

# Marcas institucionais/municipais que, presentes, mantêm a peça mesmo com
# vocabulário de futebol (freguesia de Benfica, habitação municipal, etc.).
CIVIC_CONTEXT_RE = re.compile(
    r"\b(?:"
    r"freguesia\b|junta\s+de\s+freguesia|c[aâ]mara\s+municipal|assembleia\s+municipal|"
    r"habita[çc][aã]o\b|casas\s+sociais|renda\s+acess[ií]vel|concurso\s+p[aú]blico|"
    r"or[çc]amento\b|verba\b|munic[ií]pio\b|vereador\b|metropolitana\b"
    r")\b",
    re.IGNORECASE,
)

# Menção a policymaking real: uma peça sobre o Governo a legislar estádios ou
# segurança em jogos continua dentro do âmbito político.
POLICY_CONTEXT_RE = re.compile(
    r"\b(?:"
    r"governo|governante|ministr[oa]s?\b|primeir[oa]-ministr[oa]|"
    r"parlamento|assembleia\s+da\s+rep[uú]blica|decreto|legislatura|"
    r"lei\s+(?:n[ºo.]|\b)|leis\b|or[çc]amento\s+(?:do\s+estado|de)"
    r")\b",
    re.IGNORECASE,
)

MEDICAL_PROGNOSIS_RE = re.compile(
    r"\bprogn[oó]stico\s+(?:muito\s+)?reservado\b",
    re.IGNORECASE,
)

_FOREIGN_TERMS_ALT = (
    # Foreign countries and nationalities
    r"col[oó]mbia|colombiano(?:a)?|colombianos|colombianas|"
    r"brasil|brasileiro(?:a)?|brasileiros|brasileiras|"
    r"argentina|argentino(?:a)?|argentinos|argentinas|"
    r"venezuela|venezuelano(?:a)?|venezuelanos|venezuelanas|"
    r"m[eé]xico|mexicano(?:a)?|mexicanos|mexicanas|"
    r"chile|chileno(?:a)?|chilenos|chilenas|"
    r"peru|peruano(?:a)?|peruanos|peruanas|"
    r"equador|equatoriano(?:a)?|bol[ií]via|boliviano(?:a)?|paraguai|paraguaio(?:a)?|uruguai|uruguaio(?:a)?|cuba|cubano(?:a)?|"
    r"estados\s+unidos|eua|e\.u\.a\.|norte-americano(?:a)?|norte-americanos|norte-americanas|americano(?:a)?|americanos|americanas|"
    r"canad[aá]|canadiano(?:a)?|canadianos|canadianas|canadense|"
    r"espanha|espanhol(?:a)?|espanh[oó]is|espanholas|"
    r"fran[cç]a|franc[eê]s(?:a)?|franceses|francesas|"
    r"reino\s+unido|gr[aã]-bretanha|inglaterra|brit[aâ]nico(?:a)?|brit[aâ]nicos|brit[aâ]nicas|ingl[eê]s(?:a)?|ingleses|inglesas|"
    r"alemanha|alem[aã]o|alem[aã]|alem[aã]es|alem[aã]s|"
    r"it[aá]lia|italiano(?:a)?|italianos|italianas|"
    r"r[uú]ssia|russo(?:a)?|russos|russas|"
    r"ucr[aâ]nia|ucraniano(?:a)?|ucranianos|ucranianas|"
    r"pol[oó]nia|polaco(?:a)?|polacos|polacas|polon[eê]s(?:a)?|"
    r"su[ií][cç]a|su[ií][cç]o(?:a)?|su[eé]cia|sueco(?:a)?|noruega|noruegu[eê]s(?:a)?|finl[aâ]ndia|finland[eê]s(?:a)?|dinamarca|dinamarqu[eê]s(?:a)?|"
    r"holanda|pa[ií]ses\s+baixos|holand[eê]s(?:a)?|gr[eé]cia|grego(?:a)?|turquia|turco(?:a)?|irlanda|irland[eê]s(?:a)?|[aá]ustria|austr[ií]aco(?:a)?|b[eé]lgica|belga|belgas|"
    r"angola|angolano(?:a)?|angolanos|angolanas|mo[cç]ambique|mo[cç]ambicano(?:a)?|cabo\s+verde|cabo-verdiano(?:a)?|guin[eé]-bissau|guineense|timor-leste|timorense|"
    r"china|chin[eê]s(?:a)?|chineses|chinesas|jap[aã]o|japon[eê]s(?:a)?|[ií]ndia|indiano(?:a)?|"
    r"israel|israelita|israelitas|israelense|palestina|palestiniano(?:a)?|palestinianos|palestinianas|faixa\s+de\s+gaza|gaza|"
    r"ir[aã]o|iraniano(?:a)?|iraque|iraquiano(?:a)?|s[ií]ria|s[ií]rio(?:a)?|l[ií]bano|liban[eê]s(?:a)?|ar[aá]bia\s+saudita|saudita|sauditas|egito|eg[ií]pcio(?:a)?|marrocos|marroquino(?:a)?|arg[eé]lia|argelino(?:a)?|tun[ií]sia|tunisino(?:a)?|[aá]frica\s+do\s+sul|sul-africano(?:a)?|"
    r"coreia\s+do\s+norte|coreia\s+do\s+sul|norte-coreano(?:a)?|sul-coreano(?:a)?|"
    r"i[eé]men|iemenita|iemenitas|afeganist[aã]o|afeg[aã]o(?:s)?|paquist[aã]o|paquistan[êe]s|bangladesh|nepal|sri\s+lanka|"
    r"mianmar|birmania|tail[aâ]ndia|tailand[êe]s|vietname|vietnamita|camboja|cambojano|indon[eé]sia|indon[eé]sio|mal[aá]sia|mal[aá]sio|filipinas|filipino|"
    r"austr[aá]lia|australiano(?:a)?|nova\s+zel[aâ]ndia|neozeland[êe]s|"
    r"hamas|hezbollah|talib[aã]|estado isl[aâ]mico|al-qaeda|"
    # Foreign cities & capitals
    r"bogot[aá]|medell[ií]n|\bcali\b|bras[ií]lia|rio(?:\s+de\s+janeiro)?|s[aã]o\s+paulo|belo\s+horizonte|salvador\s+da\s+bahia|curitiba|recife|fortaleza|porto\s+alegre|buenos\s+aires|santiago|lima|caracas|cidade\s+do\s+m[eé]xico|havana|montevideu|assun[cç][aã]o|la\s+paz|quito|"
    r"madrid|barcelona|val[eê]ncia|sevilha|bilbau|catalunha|andaluzia|galiza|pa[ií]s\s+basco|paris|londres|roma|mil[aã]o|berlim|munique|frankfurt|moscovo|kiev|kyiv|vars[oó]via|genebra|zurique|estocolmo|oslo|hels[ií]nquia|copenhaga|amesterd[aã]o|atenas|ancara|istambul|dublin|viena|"
    r"nova\s+iorque|new\s+york|washington|fl[oó]rida|calif[oó]rnia|texas|miami|chicago|los\s+angeles|ottawa|toronto|"
    r"luanda|maputo|pequim|beijing|t[oó]quio|nova\s+deli|jerusal[eé]m|telavive|tel\s+aviv|teer[aã]o|bagdad[e]?|damasco|beirute|riade|cairo|rabat|seul|pyongyang|sanaa|"
    # Foreign municipal / government terminology
    r"prefeitura(?:\s+municipal)?|prefeito(?:\s+municipal)?|prefeita(?:\s+municipal)?|prefeitos|prefeitas|subprefeito|subprefeitura|"
    r"c[aâ]mara\s+dos\s+representantes|c[aâ]mara\s+dos\s+comuns|c[aâ]mara\s+dos\s+lordes|c[aâ]mara\s+dos\s+deputados\s+(?:do\s+brasil|brasileira)?|"
    r"casa\s+branca|white\s+house|capit[oó]lio|pent[aá]gono|pal[aá]cio\s+do\s+planalto|pal[aá]cio\s+da\s+alvorada|pal[aá]cio\s+do\s+eliseu|pal[aá]cio\s+da\s+moncloa|downing\s+street|kremlin|duma|bundestag|knesset|"
    r"senado\s+dos\s+eua|congresso\s+dos\s+eua|supremo\s+tribunal\s+dos\s+eua|suprema\s+corte\s+(?:dos\s+eua|americana)|stf|tse|supremo\s+tribunal\s+federal|"
    # Foreign national political leaders
    r"gustavo\s+petro|iv[aá]n\s+duque|[aá]lvaro\s+uribe|"
    r"lula(?:\s+da\s+silva)?|jair\s+bolsonaro|tarc[ií]sio\s+de\s+freitas|cl[aá]udio\s+castro|donald\s+trump|joe\s+biden|kamala\s+harris|jd\s+vance|emmanuel\s+macron|marine\s+le\s+pen|pedro\s+s[aá]nchez|alberto\s+n[uú][nñ]ez\s+feij[oó]o|keir\s+starmer|rishi\s+sunak|olaf\s+scholz|giorgia\s+meloni|javier\s+milei|nicol[aá]s\s+maduro|mar[ií]a\s+corina\s+machado|vladimir\s+putin|volodymyr\s+zelensky|benjamin\s+netanyahu|xi\s+jinping|narendra\s+modi|recep\s+tayyip\s+erdogan|ali\s+khamenei|masoud\s+pezeshkian"
)

FOREIGN_JURISDICTION_RE = re.compile(rf"\b(?:{_FOREIGN_TERMS_ALT})\b", re.IGNORECASE)

# "Governo espanhol", "ministério francês", etc. não são âncora portuguesa:
# remover antes da extração de entidades para o GOVERNO genérico não casar.
GOVERNO_ESTRANGEIRO_RE = re.compile(
    rf"\b(?:governo|ministra[cç][aã]o?|presid[eê]ncia|parlamento|assembleia|tribunal)\s+(?:{_FOREIGN_TERMS_ALT})\b",
    re.IGNORECASE,
)

PORTUGAL_OR_EU_OVERRIDE_RE = re.compile(
    r"\b(?:"
    r"portugal|portugu[eê]s(?:a)?|portugueses|portuguesas|luso|lusa|lusos|lusas|"
    r"a[cç]ores|madeira|lisboa|porto|coimbra|braga|faro|set[uú]bal|aveiro|leiria|santar[eé]m|[eé]vora|beja|portalegre|castelo\s+branco|guarda|viseu|vila\s+real|bragan[cç]a|funchal|ponta\s+delgada|"
    r"cascais|sintra|oeiras|loures|almada|guimar[aã]es|matosinhos|gaia|vila\s+nova\s+de\s+gaia|albufeira|cerveira|vila\s+nova\s+de\s+cerveira|"
    r"s[aã]o\s+bento|bel[eé]m|assembleia\s+da\s+rep[uú]blica|governo\s+portugu[eê]s|rep[uú]blica\s+portuguesa|embaixada\s+de\s+portugal|consulado\s+de\s+portugal|mne|minist[eé]rio\s+dos\s+neg[oó]cios\s+estrangeiros|"
    r"ue|uni[aã]o\s+europeia|comiss[aã]o\s+europeia|parlamento\s+europeu|conselho\s+europeu|conselho\s+da\s+ue|bruxelas|bce|banco\s+central\s+europeu|eurodeputad[oas]+|zona\s+euro|prr|copernicus|frontex|"
    r"onu|organiza[cç][aã]o\s+das\s+na[cç][oõ]es\s+unidas|guterres|ant[oó]nio\s+guterres|ant[oó]nio\s+costa|maria\s+lu[ií]s\s+albuquerque"
    r")\b",
    re.IGNORECASE,
)


def is_purely_foreign_news(evidence: str, entities: Sequence[Mapping[str, Any]]) -> bool:
    if not FOREIGN_JURISDICTION_RE.search(evidence):
        return False
    # Check if there is a Portuguese or EU anchor that makes this foreign news relevant to Portugal / EU
    has_specific_pt_entity = any(
        item.get("kind") in {"party", "coalition", "youth_wing", "person"}
        or item.get("id") in {"UNIAO-EUROPEIA", "ONU"}
        for item in entities
    )
    if has_specific_pt_entity:
        return False
    if PORTUGAL_OR_EU_OVERRIDE_RE.search(evidence):
        return False
    return True


def is_blocked_non_political(candidate: Mapping[str, Any] | str) -> bool:
    if isinstance(candidate, str):
        title = candidate
        url = candidate
        summary = ""
        section = ""
    else:
        title = str(candidate.get("title") or "")
        url = str(candidate.get("url") or "")
        summary = str(candidate.get("summary") or "")
        section = str(candidate.get("section") or "")

    path = urllib.parse.urlsplit(url).path.casefold()
    if any(marker in path for marker in BLOCKED_PATH_MARKERS):
        return True

    text = f"{title} {summary} {section} {path}"
    titulo_casefold = f"{title} {summary}".casefold()

    if MEDICAL_PROGNOSIS_RE.search(text) and not (
        BETTING_BRANDS_RE.search(text)
        or SPORTS_COMPETITIONS_RE.search(text)
        or "apostas" in text.casefold()
    ):
        return False

    if BETTING_BRANDS_RE.search(text):
        if any(sec in path for sec in ("/politica/", "/economia/", "/nacional/", "/pais/", "/justica/")):
            if not any(
                w in text.casefold()
                for w in (
                    "login", "app", "levantamentos", "bonus", "bónus",
                    "odds", "prognostico", "aposta", "freebet", "promocional", "contacto",
                )
            ):
                return False
        return True

    if BETTING_TERMS_RE.search(text):
        return True

    if SPORTS_COMPETITIONS_RE.search(text):
        return True

    if MATCH_VS_RE.search(title) and not topic_labels(f"{title} {summary}"):
        if any(
            kw in text.casefold()
            for kw in (
                "futebol", "liga", "jogo", "odd", "golo", "amigavel",
                "amigável", "prognostico", "aposta",
            )
        ):
            return True

    return False


ANTIBOT_MARKERS_RE = re.compile(
    r"awswaf|challenge-container|captcha|cf-challenge"
    r"|just a moment|attention required|error from cloudfront"
    r"|verif(?:y|icar)[^\n<]{0,40}rob[oô]|not a robot",
    re.IGNORECASE,
)


def pagina_eh_antibot(raw_html: str) -> bool:
    """Detecta paredes anti-bot que respondem 200 com uma casca vazia."""

    if len(raw_html) > 400_000:
        return False
    return bool(ANTIBOT_MARKERS_RE.search(raw_html)) and "<p" not in raw_html.casefold()


class PipelineError(RuntimeError):
    """An expected pipeline failure that should be rendered to the operator."""


def utc_now() -> dt.datetime:
    return dt.datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def configure_console_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
    for name in ("urllib3", "requests", "charset_normalizer", "chardet"):
        logging.getLogger(name).setLevel(logging.WARNING)


def safe_print(text: Any = "", flush: bool = True) -> None:
    message = str(text)
    try:
        print(message, flush=flush)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        encoded = message.encode(encoding, errors="replace").decode(encoding)
        print(encoded, flush=flush)


def normalise_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def compact_text(value: Any, limit: int = 1200) -> str:
    text = html.unescape(str(value or ""))
    # Strip HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    prefix = text[:limit].rstrip()
    sentence_end = max(prefix.rfind(". "), prefix.rfind("! "), prefix.rfind("? "))
    if sentence_end >= int(limit * 0.55):
        return prefix[: sentence_end + 1].strip()
    word_end = prefix.rfind(" ")
    return (prefix[:word_end] if word_end > 0 else prefix).strip() + "…"


def clean_article_title(title: str) -> str:
    if not title:
        return ""
    title = str(title)
    # CDATA strip
    title = re.sub(r"^\s*<!\[CDATA\[\s*|\s*\]\]>\s*$", "", title).strip()
    # Unescape HTML entities
    title = html.unescape(title)

    # 1. Remove HTML elements that are clearly badges/tags by class or badge keywords inside the tag
    title = re.sub(
        r"<[a-zA-Z0-9]+[^>]*\bclass=['\"][^'\"]*(?:tag|badge|premium|exclusiv|fechad|audio|rubrica)[^'\"]*['\"][^>]*>.*?</[a-zA-Z0-9]+>",
        "",
        title,
        flags=re.IGNORECASE | re.DOTALL,
    )
    title = re.sub(
        r"<[a-zA-Z0-9]+[^>]*>\s*(?:premium|exclusivo|fechado|artigo fechado|áudio|audio|assinante|exclusivos|opinião)\s*</[a-zA-Z0-9]+>",
        "",
        title,
        flags=re.IGNORECASE | re.DOTALL,
    )
    # 2. Strip any remaining HTML tags
    title = re.sub(r"<[^>]+>", " ", title)

    # 3. Strip bracketed/delimited badges at the end: e.g. [Exclusivo], (Áudio), | Exclusivo, - Premium
    title = re.sub(
        r"\s*(?:\[|\(|\s[-|–—:]\s*)\s*(?:premium|exclusivo|fechado|artigo fechado|áudio|audio|assinantes?)\s*(?:\]|\)|\s*)$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()

    # 4. Strip leading ISO or standard date prefixes: "2026-08-22 - Title", "2026_08_22 - Title", "2026/08/22: Title"
    title = re.sub(r"^\d{4}[-_/.]\d{1,2}[-_/.]\d{1,2}\s*[-|:]\s*", "", title).strip()

    # 5. Strip leading hour bullets: "8h. ", "7h. ", "23h30. "
    title = re.sub(r"^\d{1,2}h(?:\d{2})?\.\s*", "", title).strip()

    # 6. Strip trailing site branding
    title = re.sub(
        r"\s*[-|–—]\s*(?:SIC Notícias|Expresso|Notícias ao Minuto|Jornal de Notícias|CMTV|Correio da Manhã|CNN Portugal|RTP Notícias|RTP|Observador|ECO|Jornal Económico|TSF|Revista Sábado|Sábado|SAPO 24|SAPO|Now Canal|NOW|Público|PÚBLICO)$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()

    # 7. Normalise spaces
    title = re.sub(r"\s+", " ", title).strip()
    return title


def tokenise(value: Any) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]{3,}", normalise_text(value))
        if token not in STOP_WORDS
    }


def stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def content_hash(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def json_load(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    is_state = "state" in str(path)
    if is_state:
        try:
            print(f"A carregar ficheiro de estado ({path.stat().st_size // (1024*1024)} MB)...", flush=True)
        except OSError:
            pass
    for attempt in range(4):
        try:
            if orjson is not None:
                # Memory-map large files so parsing never needs a second full
                # copy of the payload in RAM (matters for the ~1 GB state).
                try:
                    with path.open("rb") as handle, mmap.mmap(
                        handle.fileno(), 0, access=mmap.ACCESS_READ
                    ) as mapped:
                        return orjson.loads(memoryview(mapped))
                except (ValueError, OSError, TypeError):
                    pass
            raw = path.read_bytes()
            return orjson.loads(raw) if orjson is not None else json.loads(raw)
        except (OSError, ValueError) as exc:
            if attempt < 3:
                time.sleep(2.0)
                continue
            raise PipelineError(f"Não foi possível ler JSON em {path}: {exc}") from exc


def json_save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(4):
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        try:
            # Compact output keeps the multi-hundred-MB state and public
            # exports dramatically cheaper to serialise, write and reload.
            if orjson is not None:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(orjson.dumps(payload, default=str))
                    handle.write(b"\n")
                    handle.flush()
            else:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                    handle.write("\n")
                    handle.flush()
            os.replace(temporary, path)
            return
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            if attempt < 3:
                time.sleep(2.0)
                continue
            raise


class CheckpointManager:
    """Throttles periodic state saves to disk (e.g. every 60s) with instant forced saves."""

    def __init__(
        self,
        state_path: Path,
        state: dict[str, Any],
        interval_seconds: float = 60.0,
        dry_run: bool = False,
    ):
        self.state_path = state_path
        self.state = state
        self.interval_seconds = interval_seconds
        self.dry_run = dry_run
        self._last_saved = time.monotonic()
        self._dirty = False

    def mark_dirty(self) -> None:
        self._dirty = True

    def __call__(self, force: bool = False, quiet: bool = True) -> None:
        if self.dry_run:
            return
        now = time.monotonic()
        if force or (self._dirty and (now - self._last_saved) >= self.interval_seconds):
            self.state["updatedAt"] = iso_now()
            json_save(self.state_path, self.state)
            self._last_saved = now
            self._dirty = False


def print_collection_banner(config: Mapping[str, Any]) -> None:
    sources = [
        source for source in config.get("sources", []) if isinstance(source, Mapping)
    ]
    safe_print("==============================================================================")
    safe_print("  🏛️  POLITÓMETRO — RECOLHA INTEGRAL DE INTELIGÊNCIA POLÍTICA")
    safe_print("==============================================================================")
    safe_print()
    safe_print(" Esta execução irá sincronizar e atualizar:")
    safe_print("   • Notícias de todos os órgãos de comunicação social ativos;")
    safe_print("   • Dados abertos da Assembleia da República (iniciativas e votações);")
    safe_print("   • Promessas de programas eleitorais e declarações na imprensa;")
    safe_print("   • Base de dados local, painel web e ficheiros de memória do chatbot.")
    safe_print()
    safe_print(" Fontes jornalísticas configuradas nesta execução:")
    for source in sources:
        name = str(source.get("name") or source.get("id") or "Fonte sem nome")
        suffix = " (desativada)" if not source.get("enabled", True) else ""
        safe_print(f"   • {name}{suffix}")
    safe_print()
    safe_print(" (O progresso é guardado em tempo real por checkpoints em disco)")
    safe_print("------------------------------------------------------------------------------")
    safe_print()


def parse_datetime(value: Any) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
        return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def iso_datetime(value: Any) -> str | None:
    parsed = parse_datetime(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed else None


def safe_url(value: Any, allowed_hosts: Iterable[str] | None = None) -> str:
    try:
        parsed = urllib.parse.urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    host = parsed.hostname.lower() if parsed.hostname else ""
    if allowed_hosts:
        normalized_hosts = {str(item).lower() for item in allowed_hosts}
        if not any(host == item or host.endswith(f".{item}") for item in normalized_hosts):
            return ""
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def source_hosts(source: Mapping[str, Any]) -> set[str]:
    hosts: set[str] = set()
    for host in as_list(source.get("allowedHosts")):
        if str(host).strip():
            hosts.add(str(host).strip().lower())
    archive_sitemap = source.get("archiveSitemap")
    archive_template = (
        archive_sitemap.get("urlTemplate")
        if isinstance(archive_sitemap, Mapping)
        else ""
    )
    for value in (
        source.get("homepage"),
        source.get("robotsUrl"),
        *source.get("sitemapSeeds", []),
        *source.get("rssFeeds", []),
        archive_template,
    ):
        parsed = urllib.parse.urlsplit(str(value or ""))
        if parsed.hostname:
            hosts.add(parsed.hostname.lower())
    return hosts


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def coerce_string(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "; ".join(filter(None, (coerce_string(item) for item in value)))
    if isinstance(value, Mapping):
        for key in ("#text", "nome", "name", "sigla", "valor", "value", "descricao"):
            if value.get(key):
                return coerce_string(value[key])
        return "; ".join(
            filter(None, (coerce_string(item) for item in value.values()))
        )
    return compact_text(value, 1000)


def sentence_candidates(value: Any) -> Iterator[str]:
    text = html.unescape(str(value or ""))
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"(?m)^\s*(?:[•▪◦‣*-]|\d+[.)])\s+", "\n", text)
    text = re.sub(r"[•▪◦‣]", "\n", text)
    for candidate in re.split(r"\n+|(?<=[.!?;])\s+", text):
        candidate = compact_text(candidate, 620)
        if 35 <= len(candidate) <= 620:
            yield candidate


@dataclass(frozen=True)
class Entity:
    id: str
    kind: str
    name: str
    aliases: tuple[str, ...]
    affiliations: tuple[str, ...] = ()


class EntityMatcher:
    """Fast compiled matcher which treats terse acronyms conservatively."""

    def __init__(self, payload: Mapping[str, Any]):
        raw_entities = list(payload.get("entities", [])) + [
            {**item, "kind": item.get("kind", "person")}
            for item in payload.get("people", [])
        ]
        self.entities: list[Entity] = []
        self.party_aliases: dict[str, str] = {}
        exact_map: dict[str, list[Entity]] = {}
        ci_map: dict[str, list[Entity]] = {}
        norm_map: dict[str, list[Entity]] = {}

        for raw in raw_entities:
            entity = Entity(
                id=str(raw.get("id") or "").strip(),
                kind=str(raw.get("kind") or "other").strip(),
                name=str(raw.get("name") or raw.get("id") or "").strip(),
                aliases=tuple(
                    alias for alias in (str(item).strip() for item in raw.get("aliases", [])) if alias
                ),
                affiliations=tuple(
                    affiliation
                    for affiliation in (
                        str(item).strip()
                        for item in as_list(raw.get("party") or raw.get("parties"))
                    )
                    if affiliation
                ),
            )
            if not entity.id or not entity.aliases:
                continue
            self.entities.append(entity)
            for alias in entity.aliases:
                # 2-letter all-caps acronyms (PS, IL, BE, CH, AD) remain uppercase
                # to prevent false matches with common Portuguese words.
                # 3+ letter aliases (PSD, CDS, PCP, CHEGA, PAN, etc.) are matched case-insensitively.
                if len(alias) <= 2 and alias.isupper():
                    exact_map.setdefault(alias, []).append(entity)
                else:
                    ci_map.setdefault(alias.casefold(), []).append(entity)
                    norm_alias = normalise_text(alias)
                    if norm_alias and norm_alias != alias.casefold() and len(norm_alias) > 2:
                        norm_map.setdefault(norm_alias, []).append(entity)

                if entity.kind in {"party", "coalition"}:
                    self.party_aliases[normalise_text(alias)] = entity.id

        self._exact_map = exact_map
        self._ci_map = ci_map
        self._norm_map = norm_map

        exact_keys = sorted(exact_map.keys(), key=len, reverse=True)
        self._exact_re = (
            re.compile(rf"(?<![\w])(?:{'|'.join(re.escape(k) for k in exact_keys)})(?![\w])")
            if exact_keys
            else None
        )

        ci_keys = sorted(ci_map.keys(), key=len, reverse=True)
        self._ci_re = (
            re.compile(rf"(?<![\w])(?:{'|'.join(re.escape(k) for k in ci_keys)})(?![\w])", re.IGNORECASE)
            if ci_keys
            else None
        )

        norm_keys = sorted(norm_map.keys(), key=len, reverse=True)
        self._norm_re = (
            re.compile(rf"(?<![\w])(?:{'|'.join(re.escape(k) for k in norm_keys)})(?![\w])", re.IGNORECASE)
            if norm_keys
            else None
        )

    def match(self, text: Any) -> list[dict[str, Any]]:
        value = str(text or "")
        if not value:
            return []
        found: dict[str, Entity] = {}

        if self._exact_re:
            for match_obj in self._exact_re.finditer(value):
                for entity in self._exact_map.get(match_obj.group(0), []):
                    found[entity.id] = entity

        if self._ci_re:
            for match_obj in self._ci_re.finditer(value):
                for entity in self._ci_map.get(match_obj.group(0).casefold(), []):
                    found[entity.id] = entity

        if self._norm_re:
            norm_value = normalise_text(value)
            for match_obj in self._norm_re.finditer(norm_value):
                for entity in self._norm_map.get(match_obj.group(0).casefold(), []):
                    found[entity.id] = entity

        result: list[dict[str, Any]] = []
        for entity in sorted(found.values(), key=lambda item: (item.kind, item.name)):
            item: dict[str, Any] = {
                "id": entity.id,
                "kind": entity.kind,
                "name": entity.name,
            }
            if entity.affiliations:
                item["affiliations"] = list(entity.affiliations)
            result.append(item)
        return result

    def parties_in(self, text: Any) -> list[str]:
        return [
            item["id"]
            for item in self.match(text)
            if item["kind"] in {"party", "coalition"}
        ]

    def canonical_party(self, value: Any) -> str | None:
        normalized = normalise_text(value)
        if not normalized:
            return None
        exact = self.party_aliases.get(normalized)
        if exact:
            return exact
        matches = self.parties_in(value)
        return matches[0] if len(matches) == 1 else None


def decode_response_text(response: requests.Response) -> str:
    """Decode publisher payloads without trusting incorrect charset headers.

    Several Portuguese feeds and older Assembly exports declare UTF-8 while
    serving Windows-1252/Latin-1 bytes (and the reverse also occurs).  Trying a
    short deterministic set and penalising replacement/mojibake characters is
    both faster and more reliable than silently accepting ``response.text``.
    """

    payload = response.content
    if not payload:
        return ""
    encodings: list[str] = []
    for encoding in (
        response.encoding,
        "utf-8-sig",
        "utf-8",
        "cp1252",
        "iso-8859-1",
        getattr(response, "apparent_encoding", None),
    ):
        normalized = str(encoding or "").strip()
        if normalized and normalized.casefold() not in {item.casefold() for item in encodings}:
            encodings.append(normalized)

    candidates: list[tuple[int, int, str]] = []
    for order, encoding in enumerate(encodings):
        try:
            decoded = payload.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
        mojibake = sum(decoded.count(marker) for marker in ("Ã", "Â", "â€", "ï¿½", "�"))
        controls = sum(1 for char in decoded if 0x7F <= ord(char) <= 0x9F)
        replacements = decoded.count("\ufffd")
        score = (replacements * 100) + (mojibake * 12) + (controls * 8)
        candidates.append((score, order, decoded))
    if candidates:
        return min(candidates, key=lambda item: (item[0], item[1]))[2]
    return payload.decode("utf-8", errors="replace")


NAVEGADOR_ESTADO: dict[str, Any] = {"pw": None, "browser": None, "contexto": None}
NAVEGADOR_LOCK = threading.Lock()


def obter_html_navegador(url: str, timeout_ms: int = 35000) -> str:
    """Resolve challenges WAF abrindo a página num Chromium headless.

    Mesmo padrão do pipeline 'Notícias de Ontem': o challenge JavaScript é
    processado pelo browser real; os cookies resultantes (aws-waf-token) são
    depois copiados para a sessão de pedidos normais.
    """

    global NAVEGADOR_ESTADO
    if not NAVEGADOR_LOCK.acquire(timeout=90):
        raise TimeoutError("navegador ocupado há >90s; a saltar para não bloquear")
    try:
        estado = NAVEGADOR_ESTADO
        if estado["pw"] is None:
            from playwright.sync_api import sync_playwright

            pw = sync_playwright().start()
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            contexto = browser.new_context(
                user_agent=str(NAVEGADOR_ESTADO.get("user_agent") or ""),
                viewport={"width": 1280, "height": 800},
                locale="pt-PT",
            )
            contexto.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                "window.chrome=window.chrome||{runtime:{}};"
                "Object.defineProperty(navigator,'languages',{get:()=>['pt-PT','pt','en']});"
                "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
            )
            estado.update(pw=pw, browser=browser, contexto=contexto)
        page = estado["contexto"].new_page()
        try:
            page.goto(url, timeout=timeout_ms, wait_until="commit")
            page.wait_for_timeout(3000)
            html = page.content()
            return html
        except Exception:
            return ""
        finally:
            try:
                page.close()
            except Exception:
                pass
    finally:
        NAVEGADOR_LOCK.release()


def cookies_do_navegador(url: str) -> list[dict[str, str]]:
    with NAVEGADOR_LOCK:
        contexto = NAVEGADOR_ESTADO.get("contexto")
        if contexto is None:
            return []
        return [
            {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c["path"]}
            for c in contexto.cookies(urls=[url])
        ]


def fechar_navegador() -> None:
    global NAVEGADOR_ESTADO
    with NAVEGADOR_LOCK:
        estado = NAVEGADOR_ESTADO
        for closer in (
            estado.get("contexto") and estado["contexto"].close,
            estado.get("browser") and estado["browser"].close,
            estado.get("pw") and estado["pw"].stop,
        ):
            try:
                if closer:
                    closer()
            except Exception:
                pass
        NAVEGADOR_ESTADO = {"pw": None, "browser": None, "contexto": None}


class HttpClient:
    """Polite HTTP client with a shared per-host delay and safe concurrency.

    Requests from worker threads stay rate-limited per destination host while
    different hosts are fetched in parallel.
    """

    def __init__(self, config: Mapping[str, Any]):
        self.timeout = int(config.get("requestTimeoutSeconds", 20))
        self.delay = max(0.0, float(config.get("delaySeconds", 1.0)))
        self.retries = max(0, int(config.get("requestRetries", 2)))
        self.playwright_fallback = bool(config.get("playwrightFallback", True))
        self.max_concurrent = max(1, int(config.get("maxConcurrentRequests", 12)))
        self.user_agent = str(config.get("userAgent") or "PolitometroResearchBot/1.0")
        NAVEGADOR_ESTADO["user_agent"] = self.user_agent
        self.session = requests.Session()
        pool_size = max(20, self.max_concurrent * 2)
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=pool_size,
            pool_maxsize=pool_size,
            max_retries=0,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept": "application/xml,text/xml,application/json,text/html;q=0.9,*/*;q=0.5",
                "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.4",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
            }
        )
        self._host_gates: dict[str, tuple[threading.Lock, float]] = {}
        self._gates_lock = threading.Lock()

    def _host_gate(self, host: str) -> None:
        """Serialise requests per host keeping at least ``delay`` between them."""

        with self._gates_lock:
            gate = self._host_gates.get(host)
            if gate is None:
                gate = (threading.Lock(), 0.0)
                self._host_gates[host] = gate
        lock, _ = gate
        with lock:
            _lock, last_request = self._host_gates[host]
            sleep_for = self.delay - (time.monotonic() - last_request)
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._host_gates[host] = (lock, time.monotonic())

    def get(self, url: str, extra_headers: Mapping[str, str] | None = None) -> requests.Response:
        safe = safe_url(url)
        if not safe:
            raise PipelineError(f"URL inseguro ou inválido: {url!r}")
        host = urllib.parse.urlsplit(safe).netloc
        merged_headers = dict(extra_headers) if extra_headers else None
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                self._host_gate(host)
                response = self.session.get(
                    safe, timeout=self.timeout, allow_redirects=True, headers=merged_headers
                )
            except requests.RequestException as exc:
                with self._gates_lock:
                    lock, _ = self._host_gates.get(host, (threading.Lock(), 0.0))
                    self._host_gates[host] = (lock, time.monotonic())
                last_error = exc
                if attempt >= self.retries:
                    break
                time.sleep(min(8.0, 1.5 * (attempt + 1)))
                continue
            if response.status_code < 400:
                return response
            if response.status_code not in {408, 425, 429, 500, 502, 503, 504} or attempt >= self.retries:
                raise PipelineError(f"{safe} respondeu com HTTP {response.status_code}")
            retry_after = response.headers.get("Retry-After", "")
            try:
                wait_seconds = float(retry_after)
            except (TypeError, ValueError):
                wait_seconds = 1.5 * (attempt + 1)
            time.sleep(min(30.0, max(self.delay, wait_seconds)))
        raise PipelineError(f"Pedido falhou para {safe}: {last_error}") from last_error

    def text(self, url: str) -> tuple[str, Mapping[str, str], str]:
        response = self.get(url)
        # Páginas enormes (alguns arquivos servem HTML de dezenas de MB)
        # multiplicam o pico de memória em paralelo; o extrato útil cabe em
        # folga nos primeiros 2 MB.
        limite = 2 * 1024 * 1024
        try:
            if len(response.content) > limite:
                response._content = response.content[:limite]
        except (AttributeError, TypeError):
            pass
        return decode_response_text(response), response.headers, response.url


class ArticleParser(HTMLParser):
    """Extract metadata and short paragraph excerpts without retaining full HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.canonical = ""
        self._in_script = False
        self._script_type = ""
        self._script_parts: list[str] = []
        self.json_ld: list[str] = []
        self._article_depth = 0
        self._paragraph_depth = 0
        self._paragraph_parts: list[str] = []
        self.article_paragraphs: list[str] = []
        self.fallback_paragraphs: list[str] = []
        self._in_title = False
        self.title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.casefold()
        values = {key.casefold(): (value or "") for key, value in attrs}
        if name == "meta":
            key = values.get("property") or values.get("name") or values.get("itemprop")
            content = values.get("content", "")
            if key and content:
                self.meta[key.casefold()] = content
        elif name == "link" and values.get("rel", "").casefold() == "canonical":
            self.canonical = values.get("href", "")
        elif name == "script":
            self._in_script = True
            self._script_type = values.get("type", "").casefold()
            self._script_parts = []
        elif name == "article":
            self._article_depth += 1
        elif name == "p":
            self._paragraph_depth += 1
            if self._paragraph_depth == 1:
                self._paragraph_parts = []
        elif name == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        name = tag.casefold()
        if name == "script" and self._in_script:
            if "ld+json" in self._script_type and self._script_parts:
                self.json_ld.append("".join(self._script_parts))
            self._in_script = False
            self._script_type = ""
            self._script_parts = []
        elif name == "article" and self._article_depth:
            self._article_depth -= 1
        elif name == "p" and self._paragraph_depth:
            self._paragraph_depth -= 1
            if not self._paragraph_depth:
                text = compact_text("".join(self._paragraph_parts), 900)
                if len(text) >= 35:
                    target = self.article_paragraphs if self._article_depth else self.fallback_paragraphs
                    target.append(text)
                self._paragraph_parts = []
        elif name == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._script_parts.append(data)
        elif self._paragraph_depth:
            self._paragraph_parts.append(data)
        elif self._in_title:
            self.title += data


def iter_json_objects(value: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from iter_json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_json_objects(child)


def first_jsonld_article(raw_blocks: Sequence[str]) -> Mapping[str, Any]:
    for raw in raw_blocks:
        raw = raw.strip().removeprefix("<!--").removesuffix("-->").strip()
        try:
            if orjson is not None:
                parsed = orjson.loads(raw)
            else:
                parsed = json.loads(raw)
        except Exception:
            continue
        for item in iter_json_objects(parsed):
            type_value = item.get("@type", "")
            types = {normalise_text(value) for value in as_list(type_value)}
            if {"newsarticle", "article", "reportagenewsarticle"} & types:
                return item
    return {}


def parse_article_html(raw_html: str, url: str, excerpt_limit: int) -> dict[str, str]:
    parser = ArticleParser()
    try:
        parser.feed(raw_html)
        parser.close()
    except Exception:
        # Metadata can still be useful even when malformed publisher HTML stops
        # the lightweight parser early.
        pass
    jsonld = first_jsonld_article(parser.json_ld)
    headline = (
        mapping_value(jsonld, "headline")
        or mapping_value(jsonld, "name")
        or parser.meta.get("og:title")
        or parser.meta.get("twitter:title")
        or parser.title
    )
    description = (
        mapping_value(jsonld, "description")
        or parser.meta.get("og:description")
        or parser.meta.get("twitter:description")
        or parser.meta.get("description")
    )
    published = (
        mapping_value(jsonld, "datePublished")
        or mapping_value(jsonld, "dateCreated")
        or parser.meta.get("article:published_time")
        or parser.meta.get("og:article:published_time")
        or parser.meta.get("pubdate")
        or parser.meta.get("publish-date")
    )
    section = (
        mapping_value(jsonld, "articleSection")
        or parser.meta.get("article:section")
        or parser.meta.get("section")
    )
    canonical = safe_url(parser.canonical or mapping_value(jsonld, "url") or url) or url
    paragraphs = parser.article_paragraphs or parser.fallback_paragraphs
    excerpt = compact_text(" ".join(paragraphs), excerpt_limit)
    return {
        "title": clean_article_title(compact_text(headline, 300)),
        "summary": compact_text(description, 600),
        "publishedAt": iso_datetime(published) or "",
        "section": compact_text(section, 120),
        "canonicalUrl": canonical,
        "excerpt": excerpt,
    }


def topic_labels(value: Any) -> list[str]:
    normal = normalise_text(value)
    if not normal:
        return []
    return [label for label, pattern in TOPIC_PATTERNS.items() if pattern.search(normal)]


def source_state(state: dict[str, Any], source_id: str) -> dict[str, Any]:
    return state.setdefault("sources", {}).setdefault(
        source_id,
        {"seen": {}, "lastRunAt": None, "lastStatus": "never"},
    )


def initial_state() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "updatedAt": None,
        "sources": {},
        "articles": {},
        "initiatives": {},
        "votes": {},
        "promises": {},
        "assembly": {"lastSyncedAt": None, "resourceSnapshots": {}},
        "programCorpusFingerprint": None,
    }


class LinkParser(HTMLParser):
    """Extract links and labels from the simple resource pages of the AR."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        values = {name.casefold(): (value or "") for name, value in attrs}
        self._href = values.get("href", "")
        self._parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a" and self._href:
            self.links.append((self._href, compact_text("".join(self._parts), 300)))
            self._href = ""

    def handle_data(self, data: str) -> None:
        if self._href:
            self._parts.append(data)


def extract_links(raw_html: str, base_url: str) -> list[tuple[str, str]]:
    parser = LinkParser()
    parser.feed(raw_html)
    parser.close()
    result: list[tuple[str, str]] = []
    for href, label in parser.links:
        resolved = safe_url(urllib.parse.urljoin(base_url, href))
        if resolved:
            result.append((resolved, label))
    return result


def robots_policy(
    source: Mapping[str, Any], client: HttpClient, crawl_config: Mapping[str, Any]
) -> tuple[urllib.robotparser.RobotFileParser | None, list[str], str | None]:
    """Read robots once per run and return a conservative policy decision."""

    robots_url = safe_url(source.get("robotsUrl") or urllib.parse.urljoin(source["homepage"], "/robots.txt"))
    if not robots_url:
        return None, [], "robots.txt sem URL válido"
    try:
        raw, _headers, resolved = client.text(robots_url)
    except PipelineError as exc:
        err_msg = str(exc)
        # RFC 9309: HTTP 404 / 410 indicates no robots restrictions (allow all)
        if "404" in err_msg or "410" in err_msg:
            parser = urllib.robotparser.RobotFileParser()
            parser.parse([])
            return parser, [], None
        if crawl_config.get("failClosedOnRobotsError", True):
            return None, [], f"Não foi possível confirmar robots.txt: {exc}"
        return None, [], None
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(resolved)
    parser.parse(raw.splitlines())
    sitemap_urls = [
        safe_url(match.group(1).strip())
        for match in re.finditer(r"(?im)^\s*sitemap\s*:\s*(\S+)\s*$", raw)
    ]
    return parser, [url for url in sitemap_urls if url], None


def can_fetch(
    policy: urllib.robotparser.RobotFileParser | None, client: HttpClient, url: str
) -> bool:
    return not policy or policy.can_fetch(client.user_agent, url)


def child_text(node: ET.Element, wanted: str) -> str:
    wanted = wanted.casefold()
    for child in node.iter():
        text = (child.text or "").strip()
        if local_name(child.tag) == wanted and text:
            return text
    return ""


def parse_xml_root(raw_xml: str, source_url: str, label: str) -> ET.Element:
    """Parse XML with a conservative repair pass for broken publisher feeds."""

    first_tag = raw_xml.find("<")
    text = raw_xml[first_tag:] if first_tag != -1 else raw_xml
    try:
        return ET.fromstring(text)
    except ET.ParseError as first_error:
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", text)
        parts = re.split(r"(<!\[CDATA\[.*?\]\]>)", cleaned, flags=re.DOTALL)
        for index in range(0, len(parts), 2):
            parts[index] = re.sub(
                r"&(?!#\d+;|#x[0-9a-fA-F]+;|[A-Za-z][A-Za-z0-9]+;)",
                "&amp;",
                parts[index],
            )
        cleaned = "".join(parts)
        if cleaned != text:
            try:
                return ET.fromstring(cleaned)
            except ET.ParseError:
                pass
        raise PipelineError(f"{label} XML inválido em {source_url}: {first_error}") from first_error


def parse_sitemap(raw_xml: str, source_url: str) -> tuple[list[str], list[dict[str, str]]]:
    """Return nested sitemap URLs and URL records from normal or Google-News XML."""

    root = parse_xml_root(raw_xml, source_url, "Sitemap")
    root_name = local_name(root.tag)
    if root_name == "sitemapindex":
        return (
            [
                child_text(node, "loc")
                for node in root
                if local_name(node.tag) == "sitemap" and child_text(node, "loc")
            ],
            [],
        )
    if root_name != "urlset":
        raise PipelineError(f"Formato de sitemap não suportado em {source_url}: {root_name}")
    records: list[dict[str, str]] = []
    for node in root:
        if local_name(node.tag) != "url":
            continue
        tag_map = {local_name(child.tag): (child.text or "").strip() for child in node.iter()}
        loc = tag_map.get("loc")
        if not loc:
            continue
        records.append(
            {
                "url": loc,
                "lastmod": tag_map.get("lastmod") or tag_map.get("publication_date") or "",
                "title": tag_map.get("title") or "",
                "section": tag_map.get("genres") or "",
            }
        )
    return [], records


def parse_feed(raw_xml: str, source_url: str) -> list[dict[str, str]]:
    """Parse RSS 2.0 and Atom enough to use their title/date as cheap metadata."""

    root = parse_xml_root(raw_xml, source_url, "Feed")
    entries: list[dict[str, str]] = []
    for node in root.iter():
        if local_name(node.tag) not in {"item", "entry"}:
            continue
        link = ""
        for child in node:
            if local_name(child.tag) != "link":
                continue
            link = child.attrib.get("href", "") or (child.text or "")
            if link:
                break
        link = safe_url(link or child_text(node, "guid"))
        if not link:
            continue
        section_values = [
            compact_text(child.text or "", 120)
            for child in node
            if local_name(child.tag) in {"category", "section"} and (child.text or "").strip()
        ]
        entries.append(
            {
                "url": link,
                "lastmod": child_text(node, "pubdate") or child_text(node, "published") or child_text(node, "updated"),
                "title": child_text(node, "title"),
                "summary": child_text(node, "description") or child_text(node, "summary") or child_text(node, "content"),
                "section": "; ".join(section_values),
            }
        )
    return entries


def recent_enough(value: Any, cutoff: dt.datetime | None) -> bool:
    if cutoff is None:
        return True
    parsed = parse_datetime(value)
    return parsed is None or parsed >= cutoff


def looks_like_article(url: str) -> bool:
    lower_path = urllib.parse.urlsplit(url).path.casefold()
    if not lower_path or lower_path in {"/", "/ultimas", "/ultimas/"}:
        return False
    if is_blocked_non_political(url):
        return False
    return not any(marker in lower_path for marker in BLOCKED_PATH_MARKERS)


def parse_month(value: Any) -> dt.date | None:
    """Parse a YYYY-MM month value without accepting an imprecise date."""
    match = re.fullmatch(r"(\d{4})-(\d{2})", str(value or "").strip())
    if not match:
        return None
    try:
        return dt.date(int(match.group(1)), int(match.group(2)), 1)
    except ValueError:
        return None


def previous_month(value: dt.date) -> dt.date:
    if value.month == 1:
        return dt.date(value.year - 1, 12, 1)
    return dt.date(value.year, value.month - 1, 1)


def next_month(value: dt.date) -> dt.date:
    if value.month == 12:
        return dt.date(value.year + 1, 1, 1)
    return dt.date(value.year, value.month + 1, 1)


def archive_sitemap_urls(
    source: Mapping[str, Any],
    source_runtime_state: dict[str, Any] | None,
    cutoff: dt.datetime | None,
) -> list[tuple[str, str]]:
    """Build only known-range monthly sitemap URLs for archive-style sources.

    Some publishers expose individual monthly sitemaps rather than a sitemap
    index.  The configured earliest available month and the completed-month
    boundary prevent synthetic requests for years before the archive exists or
    for a still-being-published current month.  A missing month is remembered
    after its first confirmed failure, so it is not repeatedly requested.
    """
    archive_config = source.get("archiveSitemap")
    if not isinstance(archive_config, Mapping):
        return []
    url_template = str(archive_config.get("urlTemplate") or "").strip()
    first_month = parse_month(archive_config.get("firstAvailableMonth"))
    if not url_template or not first_month:
        raise PipelineError(
            f"Arquivo de sitemaps inválido na fonte {source.get('id') or source.get('name')}."
        )
    source_floor = parse_datetime(source.get("sitemapDateFloor"))
    if source_floor:
        first_month = max(
            first_month,
            dt.date(source_floor.year, source_floor.month, 1),
        )

    now = utc_now()
    current_month = dt.date(now.year, now.month, 1)
    last_month = (
        previous_month(current_month)
        if archive_config.get("completedMonthsOnly", True)
        else current_month
    )
    if cutoff:
        cutoff_month = dt.date(cutoff.year, cutoff.month, 1)
        first_month = max(first_month, cutoff_month)
    if first_month > last_month:
        return []

    archive_state: dict[str, Any] = {}
    if source_runtime_state is not None:
        archive_state = source_runtime_state.setdefault("archiveSitemap", {})
    month_state = archive_state.setdefault("months", {})
    year_state = archive_state.setdefault("years", {})
    refresh_recent_months = max(
        0, int(archive_config.get("refreshRecentMonths", 2))
    )
    refresh_from: dt.date | None = last_month if refresh_recent_months else None
    if refresh_from:
        for _ in range(max(0, refresh_recent_months - 1)):
            refresh_from = previous_month(refresh_from)

    allowed_hosts = source_hosts(source)
    results: list[tuple[str, str]] = []
    month = first_month
    while month <= last_month:
        month_key = month.strftime("%Y-%m")
        stored_month = month_state.get(month_key, {})
        stored_year = year_state.get(str(month.year), {})
        status = str(stored_month.get("status") or "")
        if (
            str(stored_year.get("status") or "") != "unavailable"
            and status != "unavailable"
            and not (
                status == "complete"
                and (refresh_from is None or month < refresh_from)
            )
        ):
            try:
                rendered = url_template.format(
                    year=month.year,
                    month=month.month,
                    monthPadded=f"{month.month:02d}",
                    monthKey=month_key,
                )
            except (KeyError, ValueError) as exc:
                raise PipelineError(
                    f"Template de sitemap inválido na fonte {source.get('id') or source.get('name')}: {exc}"
                ) from exc
            safe = safe_url(rendered, allowed_hosts)
            if not safe:
                raise PipelineError(
                    f"Template de sitemap fora do domínio autorizado na fonte {source.get('id') or source.get('name')}."
                )
            results.append((safe, month_key))
        month = next_month(month)
    # Newest first gives an interrupted first backfill useful current data while
    # the persistent sitemap checkpoints continue through the older archive.
    return list(reversed(results))


def mark_archive_month_unavailable(
    source_runtime_state: dict[str, Any], month_key: str, note: str
) -> None:
    archive_state = source_runtime_state.setdefault("archiveSitemap", {})
    month_state = archive_state.setdefault("months", {})
    year_state = archive_state.setdefault("years", {})
    month_state[month_key] = {
        "status": "unavailable",
        "checkedAt": iso_now(),
        "note": compact_text(note, 240),
    }
    year = month_key.split("-", 1)[0]
    if all(
        str(month_state.get(f"{year}-{month:02d}", {}).get("status") or "")
        == "unavailable"
        for month in range(1, 13)
    ):
        year_state[year] = {
            "status": "unavailable",
            "checkedAt": iso_now(),
        }


def mark_archive_month_complete(
    source_runtime_state: dict[str, Any], month_key: str
) -> None:
    archive_state = source_runtime_state.setdefault("archiveSitemap", {})
    month_state = archive_state.setdefault("months", {})
    month_state[month_key] = {
        "status": "complete",
        "checkedAt": iso_now(),
    }


def permanent_archive_sitemap_error(error: PipelineError) -> bool:
    text = normalise_text(str(error))
    return "http 404" in text or "formato de sitemap nao suportado" in text


def sitemap_date_from_url(value: Any) -> tuple[dt.date | None, str]:
    """Read an explicit day/month from known sitemap URL formats."""

    parsed = urllib.parse.urlsplit(str(value or ""))
    query = {key.casefold(): values[-1] for key, values in urllib.parse.parse_qs(parsed.query).items() if values}
    if {"yyyy", "mm", "dd"}.issubset(query):
        try:
            return dt.date(int(query["yyyy"]), int(query["mm"]), int(query["dd"])), "day"
        except ValueError:
            return None, ""
    basename = parsed.path.rsplit("/", 1)[-1]
    match = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?\.xml", basename, re.IGNORECASE)
    if match:
        try:
            day = int(match.group(3)) if match.group(3) else 1
            return dt.date(int(match.group(1)), int(match.group(2)), day), "day" if match.group(3) else "month"
        except ValueError:
            return None, ""
    return None, ""


def sitemap_date_allowed(source: Mapping[str, Any], url: str) -> bool:
    """Reject impossible archive dates before any sitemap HTTP request."""

    sitemap_date, _precision = sitemap_date_from_url(url)
    if not sitemap_date:
        return True
    if sitemap_date > utc_now().date():
        return False
    floor = parse_datetime(source.get("sitemapDateFloor"))
    return not floor or sitemap_date >= floor.date()


def sitemap_child_allowed(source: Mapping[str, Any], url: str) -> bool:
    """Apply source archive boundaries before any child-sitemap HTTP call."""

    include_patterns = [str(item) for item in source.get("sitemapChildIncludePatterns", []) if str(item)]
    exclude_patterns = [str(item) for item in source.get("sitemapChildExcludePatterns", []) if str(item)]
    try:
        if include_patterns and not any(re.search(pattern, url, re.IGNORECASE) for pattern in include_patterns):
            return False
        if any(re.search(pattern, url, re.IGNORECASE) for pattern in exclude_patterns):
            return False
    except re.error as exc:
        raise PipelineError(f"Filtro de sitemap inválido em {source.get('id')}: {exc}") from exc

    return sitemap_date_allowed(source, url)


def sitemap_needs_refresh(url: str, cached: Mapping[str, Any] | None, seed: bool) -> bool:
    if not cached or seed:
        return True
    sitemap_date, precision = sitemap_date_from_url(url)
    today = utc_now().date()
    if precision == "month" and sitemap_date:
        return (sitemap_date.year, sitemap_date.month) == (today.year, today.month)
    if precision == "day" and sitemap_date:
        return sitemap_date >= today - dt.timedelta(days=3)
    # News sitemaps are rolling even when their URL is stable.  Other undated
    # leaves are archive pages and are rediscovered from their refreshed index.
    return bool(re.search(r"(?:^|[/_-])news(?:[._/-]|$)", url, re.IGNORECASE))


def iter_sitemap_records(
    source: Mapping[str, Any],
    client: HttpClient,
    policy: urllib.robotparser.RobotFileParser | None,
    robots_sitemaps: Sequence[str],
    crawl_config: Mapping[str, Any],
    cutoff: dt.datetime | None,
    source_runtime_state: dict[str, Any] | None,
    errors: list[str],
    checkpoint: Callable[[], None] | None = None,
) -> Iterator[dict[str, str]]:
    """Stream sitemap entries and checkpoint after every completed sitemap.

    A leaf is only marked complete after the consumer has handled all yielded
    entries.  If the process stops mid-leaf, the leaf is fetched again and the
    per-URL ``seen`` state skips work already completed.
    """

    configured_depth = crawl_config.get("maxSitemapDepth", 3)
    configured_sitemap_limit = crawl_config.get("maxSitemapsPerSource", 30)
    max_depth = None if configured_depth in (None, "") else max(0, int(configured_depth))
    max_sitemaps = None if configured_sitemap_limit in (None, "") else max(1, int(configured_sitemap_limit))
    runtime_state = source_runtime_state if source_runtime_state is not None else {}
    progress = runtime_state.setdefault("sitemapProgress", {})
    completed = progress.setdefault("completed", {})
    allowed_hosts = source_hosts(source)
    queue: list[tuple[str, int, str, bool]] = []
    queued_urls: set[str] = set()

    def enqueue(candidate: Any, depth: int, archive_month: str = "", seed: bool = False) -> None:
        safe = safe_url(candidate, allowed_hosts)
        if not safe or safe in queued_urls:
            return
        # Date floors apply to every dated URL, including configured/generated
        # seeds. Child type filters only apply to links discovered inside an
        # index, so rolling seeds such as news.xml remain usable.
        if not sitemap_date_allowed(source, safe):
            return
        if not seed and not sitemap_child_allowed(source, safe):
            return
        queued_urls.add(safe)
        queue.append((safe, depth, archive_month, seed))

    robots_seeds = [] if source.get("ignoreRobotsSitemaps") else robots_sitemaps
    for candidate in [*source.get("sitemapSeeds", []), *robots_seeds]:
        enqueue(candidate, 0, seed=True)
    for candidate, archive_month in archive_sitemap_urls(source, runtime_state, cutoff):
        enqueue(candidate, 0, archive_month, seed=True)

    visited: set[str] = set()
    max_concurrent = int(getattr(client, "max_concurrent", 4) or 4)
    wave_size = max(2, min(max_concurrent, 4))
    download_pool = ThreadPoolExecutor(max_workers=wave_size)
    transferidos = 0
    total_conhecidos = 0
    progresso_ativo = False

    def load_sitemap_document(
        item: tuple[str, int, str, bool]
    ) -> tuple[tuple[str, int, str, bool], str, tuple[list[str], list[dict[str, str]]] | None, str | None]:
        sitemap_url, _depth, _archive_month, _seed = item
        try:
            raw, _headers, resolved = client.text(sitemap_url)
            return item, resolved, parse_sitemap(raw, resolved), None
        except PipelineError as exc:
            return item, sitemap_url, None, str(exc)

    try:
        while (
            queue
            and not _STOP_REQUESTED.is_set()
            and (max_sitemaps is None or len(visited) < max_sitemaps)
        ):
            batch: list[tuple[str, int, str, bool]] = []
            while queue and len(batch) < wave_size:
                if max_sitemaps is not None and len(visited) >= max_sitemaps:
                    break
                sitemap_url, depth, archive_month, seed = queue.pop(0)
                if sitemap_url in visited:
                    continue
                visited.add(sitemap_url)
                if not can_fetch(policy, client, sitemap_url):
                    errors.append(f"robots.txt não permite o sitemap {sitemap_url}")
                    continue

                cached = completed.get(sitemap_url)
                if isinstance(cached, Mapping) and not sitemap_needs_refresh(sitemap_url, cached, seed):
                    if max_depth is None or depth < max_depth:
                        for child in cached.get("children", []):
                            enqueue(child, depth + 1, archive_month)
                    continue
                batch.append((sitemap_url, depth, archive_month, seed))

            if not batch:
                continue

            try:
                for (sitemap_url, depth, archive_month, seed), _resolved, parsed, error in download_pool.map(
                    load_sitemap_document, batch
                ):
                    transferidos += 1
                    etiqueta = f"{transferidos}/{total_conhecidos}" if total_conhecidos else str(transferidos)
                    print(f"\r    📥 transferidos {etiqueta} sitemaps", end="", flush=True)
                    progresso_ativo = True
                    if parsed is None:
                        exc_message = error or "erro desconhecido"
                        errors.append(exc_message)
                        if archive_month and permanent_archive_sitemap_error(PipelineError(exc_message)):
                            mark_archive_month_unavailable(runtime_state, archive_month, exc_message)
                            if checkpoint:
                                checkpoint()
                        continue

                    children, entries = parsed
                    accepted_children: list[str] = []
                    if max_depth is None or depth < max_depth:
                        for child in children:
                            safe_child = safe_url(child, allowed_hosts)
                            if safe_child and sitemap_child_allowed(source, safe_child):
                                accepted_children.append(safe_child)
                                enqueue(safe_child, depth + 1, archive_month)

                    if not total_conhecidos and len(children) > 1:
                        total_conhecidos = len(visited) + len(queue) + len(batch)
                        if total_conhecidos > transferidos:
                            print(
                                f"\r    📥 {total_conhecidos} sitemaps detetados no índice",
                                end="",
                                flush=True,
                            )

                    yielded = 0
                    seen_map = runtime_state.get("seen", {}) if isinstance(runtime_state, Mapping) else {}
                    for entry in entries:
                        safe = safe_url(entry.get("url"), allowed_hosts)
                        if not safe or not looks_like_article(safe) or not recent_enough(entry.get("lastmod"), cutoff):
                            continue
                        article_id = stable_id("news", safe)
                        entry_date = iso_datetime(entry.get("lastmod"))
                        seen_entry = seen_map.get(article_id)
                        if seen_entry:
                            seen_date = seen_entry.get("lastmod")
                            if (not entry_date or seen_date == entry_date) and seen_entry.get("filterVersion") == NEWS_FILTER_VERSION:
                                continue
                        record = {**entry, "url": safe}
                        if archive_month:
                            record["_archiveMonth"] = archive_month
                        yielded += 1
                        yield record

                    completed[sitemap_url] = {
                        "checkedAt": iso_now(),
                        "children": accepted_children,
                        "entryCount": yielded,
                    }
                    if archive_month and not accepted_children and cutoff is None:
                        mark_archive_month_complete(runtime_state, archive_month)
                    if checkpoint:
                        checkpoint()
            except KeyboardInterrupt:
                if progresso_ativo:
                    print()
                raise
            finally:
                if progresso_ativo:
                    print()
                progresso_ativo = False
        # fim while queue
    except KeyboardInterrupt:
        if progresso_ativo:
            print()
        raise
    finally:
        download_pool.shutdown(wait=False)


def discover_sitemap_records(
    source: Mapping[str, Any],
    client: HttpClient,
    policy: urllib.robotparser.RobotFileParser | None,
    robots_sitemaps: Sequence[str],
    crawl_config: Mapping[str, Any],
    cutoff: dt.datetime | None,
    source_runtime_state: dict[str, Any] | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> tuple[list[dict[str, str]], list[str]]:
    """Compatibility wrapper used by tests and small callers."""

    errors: list[str] = []
    records = list(
        iter_sitemap_records(
            source, client, policy, robots_sitemaps, crawl_config, cutoff,
            source_runtime_state, errors, checkpoint,
        )
    )
    return records, errors


def article_evidence(article: Mapping[str, Any]) -> str:
    url = urllib.parse.unquote(str(article.get("url") or ""))
    readable_url = re.sub(r"[-_/]+", " ", urllib.parse.urlsplit(url).path)
    evidence = " ".join(
        str(article.get(key) or "")
        for key in ("title", "summary", "section", "excerpt")
    ) + " " + readable_url
    # "Governo iemenita/espanhol/…" não é âncora portuguesa — remover a
    # qualificação estrangeira antes de extrair entidades e tópicos.
    return GOVERNO_ESTRANGEIRO_RE.sub(" ", evidence)


def is_foreign_or_non_news_section(path_tokens: set[str]) -> tuple[bool, bool]:
    is_foreign = bool({"mundo", "internacional"} & path_tokens)
    is_non_news = bool(SPORTS_AND_BETTING_SECTION_MARKERS & path_tokens)
    return is_foreign, is_non_news


def has_relevant_political_anchor(entities: Sequence[Mapping[str, Any]]) -> bool:
    for item in entities:
        kind = str(item.get("kind") or "")
        entity_id = str(item.get("id") or "")
        if kind in {"party", "coalition", "youth_wing", "person"}:
            return True
        if entity_id in {
            "UNIAO-EUROPEIA", "ONU", "AUTARQUIAS", "JUSTICA", "PRESIDENCIA",
            "PARLAMENTO", "REGULADORES", "BANCO-DE-PORTUGAL", "ESTADO", "SNS",
            "FORCAS-SEGURANCA", "SEGURANCA-SOCIAL", "EDUCACAO",
        }:
            return True
    return False


LOW_VALUE_TITLE_RE = re.compile(
    r"^(?:artigo|pagina|documento|imagem|foto|video|galeria|\d{1,4}|titulo[\s\d]*)$"
)


def title_is_low_value(title: str) -> bool:
    """Detect slug-derived placeholders that carry no usable signal."""

    normalized = normalise_text(str(title or "")).strip().casefold()
    return bool(normalized) and bool(LOW_VALUE_TITLE_RE.match(normalized))


def candidate_has_topic_signal(candidate: Mapping[str, Any]) -> bool:
    """True when title/summary alone name política/economia themes."""

    evidence = ". ".join(
        str(candidate.get(key) or "").strip()
        for key in ("title", "summary")
        if str(candidate.get(key) or "").strip()
    )
    return bool(topic_labels(evidence))


def candidate_may_be_relevant(
    candidate: Mapping[str, Any], matcher: EntityMatcher, allow_topic_signal: bool = False
) -> bool:
    """Cheap pre-download gate.

    Two checks only, both safe against false rejections:
    1. Hard blocks (sports, betting, junk paths);
    2. Editorial scope: purely foreign news without a Portuguese/EU anchor is
       out of scope for the project.

    Everything else is downloaded — relevance is decided by the classifier on
    the full article text, never on sparse sitemap metadata.
    """

    if is_blocked_non_political(candidate):
        return False
    evidence = article_evidence(candidate)
    entities = matcher.match(evidence)
    return not is_purely_foreign_news(evidence, entities)

def classify_article(
    article: Mapping[str, Any], matcher: EntityMatcher
) -> tuple[list[str], list[dict[str, Any]], bool]:
    if is_blocked_non_political(article):
        return [], [], False
    evidence = article_evidence(article)
    entities = matcher.match(evidence)

    # Celebridade/estilo de vida, crime e desporto só rejeitam quando não há
    # âncora política nem tópico claro — evita perder peças como "Governo apoia
    # férias de verão dos jovens" ou "Montenegro lamenta morte de…".
    titulo_casefold = f"{article.get('title') or ''} {article.get('summary') or ''}".casefold()
    if (
        not has_relevant_political_anchor(entities)
        and not topic_labels(evidence)
        and any(
            padrao.search(titulo_casefold)
            for padrao in (CELEBRITY_LIFESTYLE_RE, CRIME_ACCIDENT_RE, SPORTS_TRANSFER_RE)
        )
    ):
        return [], entities, False

    # Âmbito do projeto: notícias estrangeiras sem âncora portuguesa/UE ficam
    # de fora (decisão editorial original).
    if is_purely_foreign_news(evidence, entities):
        return [], entities, False
    topics = set(topic_labels(evidence))
    normal_path = normalise_text(re.sub(r"[-_/]+", " ", urllib.parse.urlsplit(str(article.get("url") or "")).path))
    path_tokens = set(re.findall(r"[a-z0-9]+", normal_path))
    strong_section = bool({"politica", "economia"} & path_tokens)
    broad_section = bool(RELEVANT_SECTION_MARKERS & path_tokens)
    is_foreign, is_non_news = is_foreign_or_non_news_section(path_tokens)
    specific = any(item.get("kind") in {"party", "coalition", "youth_wing", "person"} for item in entities)
    has_anchor = has_relevant_political_anchor(entities)
    has_foreign_anchor = any(
        item.get("kind") in {"party", "coalition", "youth_wing", "person"}
        or item.get("id") in {"UNIAO-EUROPEIA", "ONU"}
        for item in entities
    )

    if any(item.get("id") in {"REGULADORES", "BANCO-DE-PORTUGAL"} for item in entities):
        topics.add("economia")
    if has_anchor or specific or strong_section:
        if not topics:
            topics.add("politica" if not strong_section or "politica" in path_tokens else "economia")

    sorted_topics = sorted(topics)

    # Cobertura futebolística sem âncora política forte (partido/coligação/
    # pessoa política), sem contexto municipal e sem policymaking real: fora
    # do âmbito mesmo que uma instituição fraca (autarquias, forças de
    # segurança) apareça no excerto ou o título ganhe um tópico espúrio.
    if (
        not specific
        and not strong_section
        and not CIVIC_CONTEXT_RE.search(evidence)
        and not POLICY_CONTEXT_RE.search(evidence)
        and FOOTBALL_COVERAGE_RE.search(evidence)
    ):
        return sorted_topics, entities, False

    if is_non_news and not strong_section:
        return sorted_topics, entities, False
    if is_foreign and not strong_section and not has_foreign_anchor:
        return sorted_topics, entities, False

    relevant = bool(
        (has_foreign_anchor if is_foreign else has_anchor)
        or (sorted_topics and entities)
        # Tópico sem entidade nomeada só basta quando o texto ancora claramente
        # em Portugal/UE ("Governo e oposição querem…" ✓; "Governo iemenita
        # ordena…" ✗). Evita tanto perder notícias nacionais genéricas como
        # aceitar governos estrangeiros que escapam à lista de jurisdições.
        or (sorted_topics and AMBITO_PT_UE_RE.search(evidence))
        or (strong_section and (sorted_topics or entities))
        or (specific and (sorted_topics or broad_section))
    )
    return sorted_topics, entities, relevant


def prune_irrelevant_articles(
    state: dict[str, Any], matcher: EntityMatcher
) -> int:
    """Reclassify stored articles after filter/entity improvements.

    This removes false positives already persisted by an older extractor while
    retaining and refreshing the evidence fields of records that still pass.

    Articles already reviewed by this exact logic version on identical content
    are skipped outright — classification is deterministic, so re-running it
    over hundreds of thousands of unchanged records would dominate every run.
    """

    removed = 0
    removed_ids: set[str] = set()
    reviewed_now = 0
    for article_id, article in list(state.get("articles", {}).items()):
        if (
            article.get("reviewVersion") == ARTICLE_REVIEW_VERSION
            and article.get("reviewedContentHash")
            and article.get("reviewedContentHash") == article.get("contentHash")
        ):
            continue
        if is_blocked_non_political(article):
            state["articles"].pop(article_id, None)
            removed_ids.add(article_id)
            if article.get("id"):
                removed_ids.add(str(article["id"]))
            if article.get("url"):
                removed_ids.add(str(article["url"]))
                removed_ids.add(stable_id("news", article["url"]))
            removed += 1
            continue
        topics, entities, relevant = classify_article(article, matcher)
        if not relevant:
            state["articles"].pop(article_id, None)
            removed_ids.add(article_id)
            if article.get("id"):
                removed_ids.add(str(article["id"]))
            if article.get("url"):
                removed_ids.add(str(article["url"]))
                removed_ids.add(stable_id("news", article["url"]))
            removed += 1
            continue
        article["topics"] = topics
        article["entities"] = entities
        article["reviewVersion"] = ARTICLE_REVIEW_VERSION
        article["reviewedContentHash"] = article.get("contentHash") or content_hash(
            "|".join(str(article.get(key) or "") for key in ("title", "summary", "excerpt", "publishedAt"))
        )
        reviewed_now += 1

    if "promises" in state:
        for promise_id, promise in list(state.get("promises", {}).items()):
            if promise.get("origin") == "noticia":
                source_url = str(promise.get("source", {}).get("url") or "")
                source_article_id = str(promise.get("source", {}).get("articleId") or "")
                article_id = stable_id("news", source_url)
                if (
                    article_id in removed_ids
                    or source_url in removed_ids
                    or source_article_id in removed_ids
                    or is_blocked_non_political(source_url)
                    or is_blocked_non_political(str(promise.get("statement") or ""))
                ):
                    state["promises"].pop(promise_id, None)

    if "sources" in state:
        for source_id, source_data in state["sources"].items():
            if isinstance(source_data, Mapping) and "seen" in source_data:
                seen = source_data["seen"]
                for rem_id in removed_ids:
                    if rem_id in seen:
                        seen[rem_id]["decision"] = "metadata_irrelevant"

    return removed


def fetch_article(
    candidate: Mapping[str, Any],
    source: Mapping[str, Any],
    client: HttpClient,
    policy: urllib.robotparser.RobotFileParser | None,
    crawl_config: Mapping[str, Any],
    matcher: EntityMatcher,
) -> dict[str, Any] | None:
    url = safe_url(candidate.get("url"), source_hosts(source))
    if not url or not can_fetch(policy, client, url):
        return None
    metadata = {
        "title": compact_text(candidate.get("title"), 300),
        "summary": compact_text(candidate.get("summary"), 600),
        "publishedAt": iso_datetime(candidate.get("lastmod")) or "",
        "section": compact_text(candidate.get("section"), 120),
        "canonicalUrl": url,
        "excerpt": "",
    }
    validator = candidate.get("_validator") if isinstance(candidate, Mapping) else None
    cached_article = candidate.get("_cachedArticle")
    extra_headers: dict[str, str] = {}
    if isinstance(validator, Mapping):
        etag = str(validator.get("etag") or "")
        last_modified = str(validator.get("lastModified") or "")
        if etag:
            extra_headers["If-None-Match"] = etag
        if last_modified:
            extra_headers["If-Modified-Since"] = last_modified
    response: requests.Response | None = None
    raw_headers: Mapping[str, str] | None = None
    try:
        if extra_headers and cached_article is None:
            # Nothing worth validating against; a full download would be needed
            # anyway, so skip the conditional round-trip entirely.
            extra_headers = {}
        if extra_headers:
            response = client.get(url, extra_headers=extra_headers)
            if response.status_code == 304:
                if cached_article is None:
                    return None
                reused = dict(cached_article)
                reused["fetchedAt"] = iso_now()
                if isinstance(candidate, dict):
                    candidate["_reusedUnchanged"] = True
                return reused
            raw = decode_response_text(response)
            resolved = response.url
            raw_headers = response.headers
        else:
            raw, raw_headers, resolved = client.text(url)
        if pagina_eh_antibot(raw):
            # Paredes WAF/captcha respondem 200 com cascas vazias: marcar como
            # rejeitada (não relevante), não pendente para retry.
            return None
        metadata = parse_article_html(raw, resolved, int(crawl_config.get("maxArticleExcerptCharacters", 1200)))
        if isinstance(raw_headers, Mapping):
            stored_validator = {
                "etag": str(raw_headers.get("ETag") or raw_headers.get("etag") or ""),
                "lastModified": str(
                    raw_headers.get("Last-Modified") or raw_headers.get("last-modified") or ""
                ),
            }
            if isinstance(candidate, dict) and (stored_validator["etag"] or stored_validator["lastModified"]):
                candidate["_validator"] = stored_validator
        if not metadata["title"]:
            metadata["title"] = compact_text(candidate.get("title"), 300)
        if not metadata["summary"]:
            metadata["summary"] = compact_text(candidate.get("summary"), 600)
        if not metadata["publishedAt"]:
            metadata["publishedAt"] = iso_datetime(candidate.get("lastmod")) or ""
        if not metadata["section"]:
            metadata["section"] = compact_text(candidate.get("section"), 120)
    except PipelineError as exc:
        # Keep RSS/Google News metadata when the article itself is unavailable;
        # it still gets filtered and is never represented as a full-text copy.
        LOGGER.info("Artigo indisponível (%s): %s", url, exc)
        if isinstance(candidate, dict) and cached_article is None:
            # Fontes com RSS rico (título+resumo oficiais): classificar pelos
            # metadados do feed com critério estrito em vez de descartar.
            resumo_feed = compact_text(candidate.get("summary"), 600)
            if resumo_feed and candidate.get("title"):
                t_, e_, rel_ = classify_article(
                    {
                        "title": candidate.get("title"),
                        "summary": resumo_feed,
                        "excerpt": "",
                        "url": url,
                    },
                    matcher,
                )
                if rel_ and e_:
                    resultado_feed = {
                        "id": stable_id("news", url),
                        "sourceId": source["id"],
                        "source": source["name"],
                        "url": url,
                        "title": compact_text(candidate.get("title"), 300),
                        "summary": resumo_feed,
                        "excerpt": "",
                        "section": compact_text(candidate.get("section"), 120),
                        "publishedAt": iso_datetime(candidate.get("lastmod")) or iso_now(),
                        "topics": t_,
                        "entities": e_,
                        "sourceType": "news",
                        "fetchedAt": iso_now(),
                        "viaFeed": True,
                    }
                    resultado_feed["contentHash"] = content_hash(
                        "|".join(str(resultado_feed[k]) for k in ("title", "summary", "excerpt", "publishedAt"))
                    )
                    return resultado_feed
                # Metadados do feed insuficientes: é uma rejeição correta
                # (vídeo, página sem texto, fora do âmbito), não indisponível.
        if cached_article is not None:
            # A transient fetch failure must never demote an already-collected
            # article: reuse the stored version instead of judging it on the
            # sparse sitemap metadata alone.
            reused = dict(cached_article)
            reused["fetchedAt"] = iso_now()
            if isinstance(candidate, dict):
                candidate["_reusedUnchanged"] = True
            return reused
    metadata["url"] = safe_url(metadata["canonicalUrl"] or url, source_hosts(source)) or url
    topics, entities, relevant = classify_article(metadata, matcher)
    if not relevant or not metadata["title"]:
        return None
    article_id = stable_id("news", metadata["url"])
    summary = metadata["summary"] or metadata["excerpt"] or "Sem resumo disponibilizado pela fonte."
    result = {
        "id": article_id,
        "sourceId": source["id"],
        "source": source["name"],
        "url": metadata["url"],
        "title": metadata["title"],
        "summary": compact_text(summary, 600),
        "excerpt": compact_text(metadata["excerpt"], int(crawl_config.get("maxArticleExcerptCharacters", 1200))),
        "section": metadata["section"],
        "publishedAt": metadata["publishedAt"] or iso_now(),
        "topics": topics,
        "entities": entities,
        "sourceType": "news",
        "fetchedAt": iso_now(),
    }
    result["contentHash"] = content_hash(
        "|".join(str(result[key]) for key in ("title", "summary", "excerpt", "publishedAt"))
    )
    return result


def clean_slug_title(slug: str) -> str:
    slug = re.sub(r"\.[a-zA-Z0-9]+$", "", slug)
    # Strip leading date stamps: e.g. "2026-08-22-", "2026_08_22_", "20260822-123456-", "2026-8-2-"
    slug = re.sub(r"^\d{4}[-_/]\d{1,2}[-_/]\d{1,2}(?:[-_]\d{4,6})?[-_]?", "", slug)
    slug = re.sub(r"^\d{8}[-_]\d{4,6}[-_]?", "", slug)
    # Strip trailing hex/alphanumeric hash IDs (SIC/Expresso use 8-char hex: e.g. -c874c6cb, -565c610e)
    slug = re.sub(r"[-_][a-f0-9]{6,16}$", "", slug, flags=re.IGNORECASE)
    # Strip trailing numeric IDs
    slug = re.sub(r"[-_]\d+$", "", slug)
    slug = re.sub(r"[-_]+", " ", slug).strip()
    return slug.capitalize() if slug else ""


def candidate_title_for_display(candidate: Mapping[str, Any]) -> str:
    title = compact_text(candidate.get("title"), 140)
    if title:
        clean = clean_article_title(title)
        return clean if clean else title
    url = str(candidate.get("url") or "")
    path = urllib.parse.unquote(urllib.parse.urlsplit(url).path).strip("/")
    parts = [p for p in path.split("/") if p]
    if parts:
        last_part = parts[-1]
        if len(parts) >= 2 and ("-" not in last_part and len(last_part) > 10):
            slug = parts[-2]
        elif len(parts) >= 2 and last_part.isdigit():
            slug = parts[-2]
        else:
            slug = last_part
    else:
        slug = ""
    clean = clean_slug_title(slug)
    return clean if clean else url


def candidate_date_for_display(candidate: Mapping[str, Any]) -> str:
    date_val = iso_datetime(candidate.get("lastmod") or candidate.get("publishedAt"))
    if date_val:
        return date_val[:10]
    url = str(candidate.get("url") or "")
    match = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", url)
    if match:
        y, m, d = match.groups()
        return f"{y}-{int(m):02d}-{int(d):02d}"
    return "----/--/--"


def date_for_progress(value: Any) -> str:
    date_val = iso_datetime(value)
    if date_val:
        return date_val[:10]
    match = re.search(r"\b(\d{4})(?:[-/]?(\d{1,2})(?:[-/]?(\d{1,2}))?)?\b", str(value or ""))
    if match:
        year, month, day = match.groups()
        return f"{year}-{int(month or 1):02d}-{int(day or 1):02d}"
    return "----/--/--"


def format_duration(seconds: float) -> str:
    secs = int(max(0, seconds))
    if secs <= 0:
        return "--"
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60:02d}s"
    hours = secs // 3600
    mins = (secs % 3600) // 60
    return f"{hours}h {mins:02d}m"


class CrawlProgressTracker:
    """Calculates live global and individual completion % and estimated time remaining (ETA)."""

    def __init__(self, total_sources: int):
        self.total_sources = max(1, total_sources)
        self.global_start_time = time.monotonic()
        self.source_start_time = time.monotonic()
        self.source_index = 1
        self.source_name = ""
        self.target_count: int | None = None
        self.completed_source_times: list[float] = []

    def start_source(self, index: int, name: str, target_count: int | None = None) -> None:
        self.source_index = index
        self.source_name = name
        self.target_count = target_count
        self.source_start_time = time.monotonic()

    def finish_source(self) -> None:
        duration = time.monotonic() - self.source_start_time
        self.completed_source_times.append(duration)

    def stats(self, processed_in_source: int, queue_estimate: int = 0) -> tuple[float, float, float, float]:
        now = time.monotonic()
        indiv_elapsed = max(0.001, now - self.source_start_time)
        global_elapsed = max(0.001, now - self.global_start_time)

        # Individual percentage & remaining items
        if self.target_count and self.target_count > 0:
            indiv_pct = min(100.0, (processed_in_source / self.target_count) * 100.0)
            remaining_items = max(0, self.target_count - processed_in_source)
        elif (processed_in_source + queue_estimate) > 0:
            total_est = processed_in_source + queue_estimate
            indiv_pct = min(99.9, (processed_in_source / total_est) * 100.0)
            remaining_items = max(0, queue_estimate)
        else:
            indiv_pct = 0.0
            remaining_items = 0

        # Individual ETA
        indiv_speed = processed_in_source / indiv_elapsed if indiv_elapsed > 0 else 0.0
        indiv_eta = (remaining_items / indiv_speed) if (indiv_speed > 0 and remaining_items > 0) else 0.0

        # Global percentage
        frac_current = (indiv_pct / 100.0) if indiv_pct > 0 else 0.0
        global_frac = (self.source_index - 1 + frac_current) / self.total_sources
        global_pct = min(99.9, max(0.0, global_frac * 100.0))

        # Global ETA
        if self.completed_source_times and (self.total_sources - self.source_index) > 0:
            avg_source_time = sum(self.completed_source_times) / len(self.completed_source_times)
            global_eta = (avg_source_time * (self.total_sources - self.source_index)) + indiv_eta
        elif global_frac > 0.005:
            est_total_time = global_elapsed / global_frac
            global_eta = max(0.0, est_total_time - global_elapsed)
        else:
            global_eta = 0.0

        return indiv_pct, indiv_eta, global_pct, global_eta

    def badge(self, processed_in_source: int, queue_estimate: int = 0) -> str:
        indiv_pct, indiv_eta, global_pct, global_eta = self.stats(processed_in_source, queue_estimate)
        indiv_eta_str = format_duration(indiv_eta) if indiv_eta > 0 else "--"
        global_eta_str = format_duration(global_eta) if global_eta > 0 else "--"
        return f"[{indiv_pct:4.1f}% • restam {indiv_eta_str:>6} | Global: {global_pct:4.1f}% • restam {global_eta_str:>6}]"

    def global_summary(self) -> str:
        now = time.monotonic()
        global_elapsed = max(0.001, now - self.global_start_time)
        global_frac = (self.source_index - 1) / self.total_sources
        global_pct = min(100.0, max(0.0, global_frac * 100.0))
        if self.completed_source_times and (self.total_sources - self.source_index + 1) > 0:
            avg_source_time = sum(self.completed_source_times) / len(self.completed_source_times)
            global_eta = avg_source_time * (self.total_sources - self.source_index + 1)
        elif global_frac > 0.005:
            est_total_time = global_elapsed / global_frac
            global_eta = max(0.0, est_total_time - global_elapsed)
        else:
            global_eta = 0.0
        global_eta_str = format_duration(global_eta) if global_eta > 0 else "--"
        return f"Global: {global_pct:4.1f}% (tempo restante: {global_eta_str})"


def print_progress_record(
    date_value: Any,
    source_name: str,
    title: Any,
    *,
    saved: bool = True,
    already_saved: bool = False,
) -> None:
    if saved:
        status = "Já guardada" if already_saved else "Guardada"
        icon = "✅"
    else:
        status = "Não guardada"
        icon = "❌"
    safe_print(
        f"[{date_for_progress(date_value)}] [{source_name}] {icon} {status}: {compact_text(title, 180)}",
        flush=True,
    )


def prune_stale_seen(state: dict[str, Any]) -> int:
    """Drop rejected ``seen`` entries recorded under older filter versions.

    Rejections from superseded filter versions carry no useful skip signal any
    more (the classifier changed since), yet they dominate the state file size
    and therefore the load/checkpoint time of every run.  ``collected`` entries
    are always kept so saved articles keep their cheap "unchanged" shortcut;
    a pruned rejection that resurfaces is simply re-classified from metadata,
    which never triggers a download on its own.
    """

    removed = 0
    for source_data in state.get("sources", {}).values():
        if not isinstance(source_data, Mapping):
            continue
        seen = source_data.get("seen")
        if not isinstance(seen, dict):
            continue
        stale_ids = [
            article_id
            for article_id, entry in seen.items()
            if isinstance(entry, Mapping)
            and entry.get("decision") != "collected"
            and str(entry.get("filterVersion") or "") != NEWS_FILTER_VERSION
        ]
        for article_id in stale_ids:
            seen.pop(article_id, None)
        removed += len(stale_ids)
    return removed


def cleanup_stale_tmp_files(*paths: Path, max_age_hours: float = 24.0) -> int:
    """Remove leftover atomic-save temporaries from previously killed runs."""

    removed = 0
    cutoff = time.time() - max_age_hours * 3600.0
    seen_dirs: dict[tuple[Path, str], None] = {}
    for path in paths:
        try:
            key = (path.parent, path.name)
        except OSError:
            continue
        if key in seen_dirs:
            continue
        seen_dirs[key] = None
        try:
            candidates = list(path.parent.glob(f".{path.name}.*.tmp"))
        except OSError:
            continue
        for candidate in candidates:
            try:
                if candidate.is_file() and candidate.stat().st_mtime < cutoff:
                    candidate.unlink()
                    removed += 1
            except OSError:
                continue
    return removed


class _Pool:
    """ThreadPoolExecutor que não bloqueia o encerramento após Ctrl+C.

    Em interrupção, cancela os futuros pendentes e sai de imediato; os
    trabalhadores ainda terminam o pedido HTTP em curso, mas o processo
    deixa de esperar por filas inteiras antes de mostrar o resumo.
    """

    def __init__(self, max_workers: int) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max(1, int(max_workers)))

    def __enter__(self) -> ThreadPoolExecutor:
        return self._pool

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if exc_type is KeyboardInterrupt:
            self._pool.shutdown(wait=False, cancel_futures=True)
        else:
            self._pool.shutdown(wait=True)
        return False


def fetch_robots_policies(
    sources: Sequence[Mapping[str, Any]], client: HttpClient, crawl_config: Mapping[str, Any]
) -> dict[str, tuple[urllib.robotparser.RobotFileParser | None, list[str], str | None]]:
    """Prefetch every enabled source's robots.txt concurrently."""

    enabled = [
        source
        for source in sources
        if str(source.get("id") or "") and bool(source.get("enabled", True))
    ]
    results: dict[
        str, tuple[urllib.robotparser.RobotFileParser | None, list[str], str | None]
    ] = {}
    if not enabled:
        return results
    max_concurrent = int(getattr(client, "max_concurrent", 4) or 4)
    with _Pool(min(len(enabled), max(1, max_concurrent))) as pool:
        futures = {
            pool.submit(robots_policy, source, client, crawl_config): str(source.get("id"))
            for source in enabled
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return results


def sync_news(
    state: dict[str, Any],
    config: Mapping[str, Any],
    matcher: EntityMatcher,
    client: HttpClient,
    since_days: int,
    max_urls_override: int | None = None,
    checkpoint: Callable[[], None] | None = None,
    source_filter: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Synchronise permitted sources and return per-source operational status."""

    crawl_config = config.get("crawl", {})
    cutoff = utc_now() - dt.timedelta(days=since_days) if since_days > 0 else None
    configured_url_limit = crawl_config.get("maxUrlsPerSource", 180)
    max_urls = (
        max_urls_override
        if max_urls_override is not None
        else (None if configured_url_limit in (None, "") else max(1, int(configured_url_limit)))
    )
    statuses_by_index: dict[int, dict[str, Any]] = {}
    sources = list(config.get("sources", []))
    if source_filter:
        wanted = {str(item).strip().casefold() for item in source_filter if str(item).strip()}
        selected = [item for item in sources if str(item.get("id") or "").casefold() in wanted]
        missing = sorted(
            wanted - {str(item.get("id") or "").casefold() for item in selected}
        )
        if missing:
            safe_print(
                "⚠️  Fontes desconhecidas ignoradas em --sources: " + ", ".join(missing)
            )
        sources = selected
    source_total = len(sources)
    robots_policies = fetch_robots_policies(sources, client, crawl_config)

    def register_disabled(source_index: int, source: Mapping[str, Any]) -> None:
        source_id = str(source.get("id") or "")
        status = {
            "id": source_id,
            "name": source.get("name", source_id),
            "enabled": bool(source.get("enabled", True)),
            "collected": 0,
            "candidates": 0,
            "status": "pending",
            "note": "",
            "updatedAt": iso_now(),
        }
        status.update(
            status="disabled",
            note=str(source.get("disabledReason") or "Fonte desativada na configuração."),
        )
        statuses_by_index[source_index] = status
        safe_print(f"\n📰 [{source_index}/{source_total}] {status['name']} (desativada)")

    enabled_sources: list[tuple[int, Mapping[str, Any]]] = []
    for source_index, source in enumerate(sources, start=1):
        source_id = str(source.get("id") or "")
        if not source_id or not bool(source.get("enabled", True)):
            register_disabled(source_index, source)
            continue
        enabled_sources.append((source_index, source))

    max_concurrent_sources = max(1, int(crawl_config.get("maxConcurrentSources", 4)))
    safe_print(
        f"   (robots.txt de {source_total} fontes em paralelo; até "
        f"{max_concurrent_sources} fontes sincronizadas em simultâneo...)"
    )

    flush_event = threading.Event()
    stop_ticker = threading.Event()

    def checkpoint_ticker() -> None:
        """Perform all state saves on this thread so writes never race."""

        while not stop_ticker.wait(5.0):
            if checkpoint is None:
                continue
            try:
                if flush_event.is_set():
                    flush_event.clear()
                    checkpoint(force=True)
                else:
                    checkpoint()
            except Exception:  # never let the ticker die mid-run
                LOGGER.exception("Checkpoint periódico falhou")

    def note_dirty(force: bool = False) -> None:
        if checkpoint is not None and hasattr(checkpoint, "mark_dirty"):
            checkpoint.mark_dirty()
        if force:
            flush_event.set()

    def process_source(source_index: int, source: Mapping[str, Any]) -> dict[str, Any]:
        source_id = str(source.get("id") or "")
        source_status = {
            "id": source_id,
            "name": source.get("name", source_id),
            "enabled": True,
            "collected": 0,
            "candidates": 0,
            "rejectedMetadata": 0,
            "rejectedArticle": 0,
            "indisponiveis": 0,
            "status": "pending",
            "note": "",
            "updatedAt": iso_now(),
        }
        safe_print(f"\n📰 [{source_index}/{source_total}] {source.get('name', source_id)}")
        local_state = source_state(state, source_id)
        local_state.setdefault("seen", {})
        # Referência partilhada: os checkpoints persistem o progresso ao vivo
        # e o resumo pós-interrupção consegue listar o que cada fonte fez.
        local_state["lastRun"] = source_status
        policy, robots_sitemaps, robots_error = robots_policies.get(source_id, (None, [], None))
        if robots_error:
            source_status.update(status="skipped", note=robots_error)
            local_state["lastRunAt"] = iso_now()
            local_state["lastStatus"] = "skipped"
            safe_print(f"[{source_id}] ⚠️  Fonte não processada ({robots_error})")
            note_dirty(force=True)
            return source_status
        errors: list[str] = []
        handled_urls: set[str] = set()
        processed_count = 0
        verbose_rejections = bool(crawl_config.get("verboseRejections", False))
        pending_fetches: list[tuple[str, dict[str, Any]]] = []

        def consider_candidate(candidate: Mapping[str, Any]) -> bool:
            """Queue candidates without network I/O; false when URL cap reached."""

            nonlocal processed_count
            url = safe_url(candidate.get("url"), source_hosts(source))
            if not url or url in handled_urls:
                return True
            if _STOP_REQUESTED.is_set():
                return False
            article_id = stable_id("news", url)
            seen = local_state["seen"].get(article_id, {})
            candidate_date = iso_datetime(candidate.get("lastmod"))
            unchanged = seen and (not candidate_date or seen.get("lastmod") == candidate_date)

            # 1. Já guardado e inalterado: saltar sempre. A re-revisão de
            #    filtros faz-se offline (prune_irrelevant_articles) sobre a
            #    cópia guardada — nunca re-descarregar por causa da versão.
            cached_article = state.get("articles", {}).get(article_id)
            if cached_article and unchanged:
                return True

            # 2. Previously rejected under current filter version.
            #    Skip regardless of lastmod changes: the classification verdict
            #    doesn't expire just because the sitemap regenerated.
            if (
                seen
                and seen.get("filterVersion") == NEWS_FILTER_VERSION
                and seen.get("decision") in {"metadata_irrelevant", "article_irrelevant"}
            ):
                return True

            if max_urls is not None and processed_count >= max_urls:
                return False

            handled_urls.add(url)
            processed_count += 1
            source_status["candidates"] += 1
            normalized_candidate = dict(candidate)
            normalized_candidate["url"] = url

            date_str = candidate_date_for_display(normalized_candidate)
            display_title = candidate_title_for_display(normalized_candidate)
            if title_is_low_value(display_title) and not str(
                normalized_candidate.get("title") or ""
            ).strip():
                # Placeholder slugs ("Artigo", "07", image names) carry no
                # classification signal.
                if verbose_rejections:
                    safe_print(f"[{date_str}] [{source['name']}] ❌ Não guardada: {display_title}")
                local_state["seen"][article_id] = {
                    "lastmod": candidate_date,
                    "checkedAt": iso_now(),
                    "filterVersion": NEWS_FILTER_VERSION,
                    "decision": "metadata_irrelevant",
                }
                source_status["rejectedMetadata"] += 1
                note_dirty()
                return True
            if cached_article and not unchanged:
                # O aviso de atualização é impresso junto do resultado, na fase
                # de descargas, para manter a consola coerente.
                pass

            # 3. Cheap metadata-only rejection happens inline; real downloads are
            # queued so they can run on the shared worker pool.  Topic-bearing
            # titles always reach the full-text classifier.
            if not candidate_may_be_relevant(
                normalized_candidate, matcher, allow_topic_signal=True
            ):
                if verbose_rejections:
                    safe_print(f"[{date_str}] [{source['name']}] ❌ Não guardada: {display_title}")
                source_status["rejectedMetadata"] += 1
                local_state["seen"][article_id] = {
                    "lastmod": candidate_date,
                    "checkedAt": iso_now(),
                    "filterVersion": NEWS_FILTER_VERSION,
                    "decision": "metadata_irrelevant",
                }
                note_dirty()
                return True
            if crawl_config.get("useConditionalGet", True):
                seen_entry_full = local_state["seen"].get(article_id) or {}
                normalized_candidate["_validator"] = {
                    "etag": str(seen_entry_full.get("etag") or ""),
                    "lastModified": str(seen_entry_full.get("lastModified") or ""),
                }
                normalized_candidate["_cachedArticle"] = state.get("articles", {}).get(article_id)
            pending_fetches.append((article_id, normalized_candidate))
            return True

        def record_fetch_result(article_id: str, candidate: Mapping[str, Any], article: dict[str, Any] | None) -> None:
            """Persist one completed download back into the shared state."""

            date_str = candidate_date_for_display(candidate)
            display_title = candidate_title_for_display(candidate)
            indisponivel = bool(candidate.get("_indisponivel")) if isinstance(candidate, dict) else False
            entry = {
                "lastmod": iso_datetime(candidate.get("lastmod")),
                "checkedAt": iso_now(),
                "filterVersion": NEWS_FILTER_VERSION,
                "decision": (
                    "indisponivel" if indisponivel else
                    ("collected" if article else "article_irrelevant")
                ),
            }
            validator = candidate.pop("_validator", None) if isinstance(candidate, dict) else None
            if isinstance(validator, Mapping) and (validator.get("etag") or validator.get("lastModified")):
                entry["etag"] = str(validator.get("etag") or "")
                entry["lastModified"] = str(validator.get("lastModified") or "")
            local_state["seen"][article_id] = entry
            if candidate.get("_reusedUnchanged"):
                # Artigo verificado, inalterado no servidor: atualizar apenas
                # o seen (checkedAt/validators). Silencioso e sem contar como
                # guardada — não é uma nova recolha.
                return
            if article:
                saved_date = (article.get("publishedAt") or "")[:10] or date_str
                if candidate.get("_cachedArticle"):
                    note = "🔄 Atualizada: "
                elif article.get("viaFeed"):
                    note = "📡 Guardada (via feed): "
                else:
                    note = "✅ Guardada: "
                safe_print(f"[{saved_date}] [{source['name']}] {note}{article.get('title') or display_title}")
                state["articles"][article["id"]] = article
                source_status["collected"] += 1
            elif indisponivel:
                # Fonte bloqueou o acesso (WAF/403): pendente para retry,
                # nunca contabilizado como rejeição.
                source_status["indisponiveis"] += 1
                safe_print(f"[{date_str}] [{source['name']}] ⏳ Indisponível: {display_title}")
            else:
                if verbose_rejections:
                    safe_print(f"[{date_str}] [{source['name']}] ❌ Não guardada: {display_title}")
                source_status["rejectedArticle"] += 1

        feed_candidates: dict[str, dict[str, Any]] = {}

        def load_feed(feed_url: str) -> list[dict[str, str]]:
            safe_feed = safe_url(feed_url, source_hosts(source))
            if not safe_feed or not can_fetch(policy, client, safe_feed):
                return []
            try:
                raw, _headers, resolved = client.text(safe_feed)
                return parse_feed(raw, resolved)
            except PipelineError as exc:
                errors.append(str(exc))
                return []

        feed_urls = [str(feed_url) for feed_url in source.get("rssFeeds", [])]
        if feed_urls:
            max_concurrent_requests = int(getattr(client, "max_concurrent", 4) or 4)
            with _Pool(
                min(len(feed_urls), max(1, max_concurrent_requests))
            ) as feed_pool:
                for records in feed_pool.map(load_feed, feed_urls):
                    for record in records:
                        if looks_like_article(record["url"]) and recent_enough(record.get("lastmod"), cutoff):
                            feed_candidates.setdefault(record["url"], record)
        keep_going = True
        seen_map = local_state.get("seen", {})
        for candidate in sorted(
            feed_candidates.values(),
            key=lambda item: parse_datetime(item.get("lastmod")) or dt.datetime.min.replace(tzinfo=UTC),
            reverse=True,
        ):
            cand_url = safe_url(candidate.get("url"), source_hosts(source))
            if not cand_url:
                continue
            cand_id = stable_id("news", cand_url)
            cand_date = iso_datetime(candidate.get("lastmod"))
            seen_cand = seen_map.get(cand_id)
            if (
                seen_cand
                and (not cand_date or seen_cand.get("lastmod") == cand_date)
                and seen_cand.get("filterVersion") == NEWS_FILTER_VERSION
            ):
                continue
            if not consider_candidate(candidate):
                keep_going = False
                break

        if keep_going:
            try:
                for candidate in iter_sitemap_records(
                    source,
                    client,
                    policy,
                    robots_sitemaps,
                    crawl_config,
                    cutoff,
                    local_state,
                    errors,
                    lambda: note_dirty(),
                ):
                    if not consider_candidate(candidate):
                        keep_going = False
                        break
            except PipelineError as exc:
                errors.append(str(exc))

        if pending_fetches:
            max_concurrent_requests = int(getattr(client, "max_concurrent", 4) or 4)
            per_source_workers = max(2, min(max_concurrent_requests // 2, max_concurrent_sources * 2))
            workers = min(per_source_workers, len(pending_fetches))
            completed_since_flush = 0
            with _Pool(workers) as fetch_pool:
                future_map = {
                    fetch_pool.submit(
                        fetch_article, candidate, source, client, policy, crawl_config, matcher
                    ): (article_id, candidate)
                    for article_id, candidate in pending_fetches
                }
                for future in as_completed(future_map):
                    article_id, candidate = future_map[future]
                    try:
                        article = future.result()
                    except PipelineError as exc:
                        errors.append(str(exc))
                        article = None
                    except Exception as exc:  # defensive: never lose the bookkeeping
                        LOGGER.warning("Falha a obter %s: %s", candidate.get("url"), exc)
                        article = None
                    record_fetch_result(article_id, candidate, article)
                    completed_since_flush += 1
                    if completed_since_flush >= 200:
                        completed_since_flush = 0
                        note_dirty(force=True)
                    else:
                        note_dirty()
                    if _STOP_REQUESTED.is_set():
                        fetch_pool.shutdown(wait=False, cancel_futures=True)
                        break

        local_state["lastRunAt"] = iso_now()
        local_state["lastStatus"] = "ok" if not errors else "partial"
        source_status["status"] = local_state["lastStatus"]
        source_status["note"] = " | ".join(errors[:2])
        safe_print(
            f"[{source_id}] ↳ Concluído: {source_status['collected']} notícias guardadas "
            f"({source_status['candidates']} analisadas; "
            f"{source_status['rejectedMetadata']} fora do tema + "
            f"{source_status['rejectedArticle']} sem âncora política no texto"
            f"; {source_status['indisponiveis']} indisponíveis para retry)."
        )
        note_dirty(force=True)
        return source_status

    ticker_thread: threading.Thread | None = None
    if enabled_sources and checkpoint is not None:
        ticker_thread = threading.Thread(target=checkpoint_ticker, name="checkpoint-ticker", daemon=True)
        ticker_thread.start()
    try:
        if enabled_sources:
            with _Pool(min(len(enabled_sources), max_concurrent_sources)) as source_pool:
                future_map = {
                    source_pool.submit(process_source, index, source): index
                    for index, source in enabled_sources
                }
                for future in as_completed(future_map):
                    index = future_map[future]
                    try:
                        statuses_by_index[index] = future.result()
                    except Exception as exc:
                        LOGGER.exception("Fonte no índice %s falhou", index)
                        failed_source = dict(sources[index - 1])
                        statuses_by_index[index] = {
                            "id": str(failed_source.get("id") or ""),
                            "name": failed_source.get("name", failed_source.get("id")),
                            "enabled": True,
                            "collected": 0,
                            "candidates": 0,
                            "status": "error",
                            "note": str(exc)[:300],
                            "updatedAt": iso_now(),
                        }
    finally:
        stop_ticker.set()
        if ticker_thread is not None:
            ticker_thread.join(timeout=30.0)
        if checkpoint is not None:
            try:
                if hasattr(checkpoint, "mark_dirty"):
                    checkpoint.mark_dirty()
                checkpoint(force=True)
            except Exception:
                LOGGER.exception("Checkpoint final das notícias falhou")
    configured_retention = crawl_config.get("newsRetentionDays", 1095)
    if configured_retention not in (None, ""):
        retention = utc_now() - dt.timedelta(days=max(1, int(configured_retention)))
        stale_ids = [
            article_id
            for article_id, article in state["articles"].items()
            if (parse_datetime(article.get("publishedAt")) or utc_now()) < retention
        ]
        for article_id in stale_ids:
            state["articles"].pop(article_id, None)
    return [statuses_by_index[index] for index in sorted(statuses_by_index)]


# ---------------------------------------------------------------------------
# Assembleia da República open-data synchronisation


def normalise_key(value: Any) -> str:
    """Make heterogeneous open-data field names comparable."""

    return re.sub(r"[^a-z0-9]", "", normalise_text(value))


def mapping_value(record: Mapping[str, Any], *names: str) -> str:
    """Return the first non-empty field whose normalised name matches ``names``."""

    wanted = {normalise_key(name) for name in names}
    for key, value in record.items():
        if normalise_key(key) in wanted and value not in (None, "", [], {}):
            return coerce_string(value)
    return ""


def mapping_values_with_key(record: Mapping[str, Any], *fragments: str) -> list[Any]:
    """Return values whose key contains one of the supplied normalised fragments."""

    wanted = tuple(normalise_key(fragment) for fragment in fragments)
    return [
        value
        for key, value in record.items()
        if any(fragment in normalise_key(key) for fragment in wanted)
        and value not in (None, "", [], {})
    ]


def flatten_strings(value: Any) -> Iterator[str]:
    if isinstance(value, Mapping):
        for child in value.values():
            yield from flatten_strings(child)
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            yield from flatten_strings(child)
    else:
        text = compact_text(value, 1600)
        if text:
            yield text


def party_values(value: Any, matcher: EntityMatcher) -> list[str]:
    found: set[str] = set()
    for text in flatten_strings(value):
        found.update(matcher.parties_in(text))
    return sorted(found)


def first_nested_record_list(payload: Any, hints: Sequence[str]) -> list[Mapping[str, Any]]:
    """Find the largest record list below a key that actually matches hints."""

    wanted = tuple(normalise_key(hint) for hint in hints)
    candidates: list[list[Mapping[str, Any]]] = []

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if isinstance(child, list) and any(hint in normalise_key(key) for hint in wanted):
                    records = [item for item in child if isinstance(item, Mapping)]
                    if records:
                        candidates.append(records)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    if not candidates and isinstance(payload, list):
        records = [item for item in payload if isinstance(item, Mapping)]
        if records:
            candidates.append(records)
    if not candidates:
        return []
    return max(candidates, key=len)


def canonical_vote_position(value: Any) -> str | None:
    normal = normalise_text(value)
    if not normal:
        return None
    if re.fullmatch(r"(?:votos?\s+de\s+)?abstenc(?:ao|oes)", normal):
        return "abstencao"
    if re.fullmatch(
        r"(?:votos?\s+de\s+)?ausenc(?:ia|ias)|nao\s+vot(?:ou|aram)", normal
    ):
        return "ausencia"
    if re.fullmatch(r"(?:votos?\s+)?contra", normal):
        return "contra"
    if re.fullmatch(r"(?:votos?\s+)?a\s+favor|favoravel(?:eis)?", normal):
        return "favor"
    return None


def labelled_vote_positions(value: Any, matcher: EntityMatcher) -> dict[str, str]:
    """Parse only explicitly labelled sections such as ``A Favor: PS, PSD``."""

    raw = html.unescape(str(value or ""))
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    label_pattern = re.compile(
        r"(?i)(a\s+favor|contra|absten(?:ç|c)[aã]o|aus[eê]ncias?)\s*:"
    )
    matches = list(label_pattern.finditer(raw))
    positions: dict[str, str] = {}
    conflicts: set[str] = set()
    for index, match in enumerate(matches):
        label = canonical_vote_position(match.group(1))
        if not label:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        for party in matcher.parties_in(raw[match.end():end]):
            if party in positions and positions[party] != label:
                conflicts.add(party)
                positions.pop(party, None)
            elif party not in conflicts:
                positions[party] = label
    return positions


def position_records(value: Any, matcher: EntityMatcher, context: str = "") -> list[dict[str, str]]:
    """Extract group/party positions from nested JSON or labelled text.

    The Assembly publishes several schemas over time.  This parser only emits a
    position when both a recognisable party and a clear label are present; an
    omitted group is intentionally never converted into an abstention.
    """

    positions: dict[str, str] = labelled_vote_positions(value, matcher) if not isinstance(value, (Mapping, list, tuple, set)) else {}
    conflicts: set[str] = set()

    def assign(party: str, label: str) -> None:
        if party in positions and positions[party] != label:
            conflicts.add(party)
            positions.pop(party, None)
        elif party not in conflicts:
            positions[party] = label

    def assign_from(value_to_scan: Any, label: str) -> None:
        for party in party_values(value_to_scan, matcher):
            assign(party, label)

    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            row_position = mapping_value(item, "sentido", "posicao", "posição", "resultadoVoto")
            row_label = canonical_vote_position(row_position)
            if row_label:
                party_fields = [
                    child for key, child in item.items()
                    if normalise_key(key) in {"grupo", "grupoparlamentar", "gp", "partido", "sigla"}
                ]
                assign_from(party_fields, row_label)
            for key, child in item.items():
                key_label = canonical_vote_position(key)
                if key_label:
                    assign_from(child, key_label)
                elif normalise_key(key) in {"detalhe", "posicoes", "posicaogrupos", "sentidosvoto"}:
                    for party, label in labelled_vote_positions(child, matcher).items():
                        assign(party, label)
                    walk(child)
        elif isinstance(item, (list, tuple, set)):
            for child in item:
                walk(child)
        elif isinstance(item, str):
            for party, label in labelled_vote_positions(item, matcher).items():
                assign(party, label)

    walk(value)
    return [
        {"party": party, "position": position}
        for party, position in sorted(positions.items())
        if position
    ]


PRESIDENT_ACTION_PATTERNS = (
    ("promulgada", re.compile(r"(?i)promulga")),
    ("veto", re.compile(r"(?i)\bveto\b|vetad[oa]")),
    ("apreciacao_parlamentar", re.compile(r"(?i)aprecia[çc][aã]o\s+parlamentar|devolu[çc][aã]o\s+[àa]\s+assembleia")),
    ("constitucionalidade", re.compile(r"(?i)aprecia[çc][aã]o\s+preventiva|tribunal\s+constitucional")),
)


def presidential_action_from_events(
    events: Sequence[Mapping[str, Any]]
) -> dict[str, Any] | None:
    """Derive the President's decision from Assembly tramitação events.

    A iniciativa aprovada no Parlamento ainda passa pelo PR (promulgação ou
    veto) e pode ter ido a apreciação parlamentar. O evento mais recente com
    fase presidencial conhecida é o estado actual dessa apreciação.
    """

    best: tuple[str, str, dict[str, Any]] | None = None
    for event in events:
        phase = normalise_text(mapping_value(event, "Fase", "fase", "descTipo"))
        if not phase:
            continue
        date = iso_datetime(mapping_value(event, "DataFase", "data")) or ""
        for kind, pattern in PRESIDENT_ACTION_PATTERNS:
            if not pattern.search(phase):
                continue
            candidate = (date or "", kind, {"kind": kind, "date": date or None, "phaseLabel": compact_text(phase, 160)})
            if best is None or candidate[0] > best[0]:
                best = candidate
            break
    return best[2] if best else None


def nested_vote_records(record: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    """Yield official vote objects from nested ``Votacao*`` containers."""

    def walk(value: Any) -> Iterator[Mapping[str, Any]]:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if "votacao" in normalise_key(key) and child not in (None, "", [], {}):
                    for candidate in as_list(child):
                        if isinstance(candidate, Mapping):
                            yield candidate
                else:
                    yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)

    yield from walk(record)


def records_with_vote_containers(payload: Any) -> list[Mapping[str, Any]]:
    """Return activity parent records containing one or more explicit votes."""

    result: list[Mapping[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            has_votes = any(
                "votacao" in normalise_key(key) and child not in (None, "", [], {})
                for key, child in value.items()
            )
            if has_votes:
                result.append(value)
            for key, child in value.items():
                if "votacao" not in normalise_key(key):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return result


def normalise_vote(
    raw: Mapping[str, Any],
    matcher: EntityMatcher,
    legislature: str,
    initiative: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Turn an Assembly vote/activity record into a stable small evidence item."""

    context = context or {}
    subject = mapping_value(
        raw,
        "assunto", "descricao", "designacao", "titulo", "votacaoAssunto", "actDescricao",
        "iniTitulo",
    ) or mapping_value(context, "assunto", "descricao", "designacao", "titulo", "iniTitulo")
    result = mapping_value(raw, "resultado", "resultadoVotacao", "votacaoResultado", "actResultado")
    date = iso_datetime(
        mapping_value(raw, "data", "dataVotacao", "votacaoData", "actData", "date")
    )
    phase = mapping_value(raw, "fase", "faseVotacao", "actFase", "tipo", "actTipo") or mapping_value(
        context, "fase", "descTipo", "tipo", "actTipo"
    )
    vote_number = mapping_value(raw, "id", "bid", "votacaoId", "actId", "numero", "sequencia")
    positions = position_records(raw, matcher)
    by_party = {item["party"]: item["position"] for item in positions}
    for absence_value in mapping_values_with_key(raw, "ausencias", "ausência", "ausencia"):
        for party in party_values(absence_value, matcher):
            by_party.setdefault(party, "ausencia")
    positions = [
        {"party": party, "position": position}
        for party, position in sorted(by_party.items())
    ]
    if not (subject or result or positions):
        return None
    initiative_id = str((initiative or {}).get("id") or "")
    initiative_bid = str((initiative or {}).get("bid") or "")
    vote_id = (
        stable_id("vote", legislature, vote_number)
        if vote_number
        else stable_id("vote", legislature, initiative_id, date, phase, subject)
    )
    source_url = str((initiative or {}).get("sourceUrl") or "")
    if not source_url:
        for item in iter_json_objects(raw):
            for key, value in item.items():
                if normalise_key(key) in {"urldiario", "url", "link"}:
                    candidate_url = safe_url(value, official_data_hosts())
                    if candidate_url:
                        source_url = candidate_url
                        break
            if source_url:
                break
    return {
        "id": vote_id,
        "officialId": vote_number or None,
        "initiativeId": initiative_id or None,
        "initiativeBid": initiative_bid or None,
        "legislature": legislature,
        "date": date or None,
        "subject": compact_text(subject or (initiative or {}).get("title"), 420),
        "phase": compact_text(phase, 160),
        "result": compact_text(result, 160),
        "positions": positions,
        "sourceType": "assembly_vote",
        "sourceUrl": source_url,
        "fetchedAt": iso_now(),
    }


def normalise_initiative(
    raw: Mapping[str, Any],
    matcher: EntityMatcher,
    legislature: str,
    assembly_config: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Normalise a publication from the initiatives open-data dataset."""

    bid = mapping_value(raw, "IniId", "bid", "iniBid", "iniciativaBid", "id")
    number = mapping_value(raw, "iniNr", "numero", "numeroIniciativa", "iniNumero")
    title = mapping_value(raw, "iniTitulo", "titulo", "designacao", "assunto", "descricao")
    if not (bid or number or title):
        return None
    record_legislature = mapping_value(raw, "iniLeg", "legislatura", "leg") or legislature
    type_name = mapping_value(raw, "IniDescTipo", "iniTipo", "tipo", "tipoIniciativa")
    events = [item for item in as_list(raw.get("IniEventos")) if isinstance(item, Mapping)]
    dated_events = [
        (parse_datetime(mapping_value(event, "DataFase", "data")), index, event)
        for index, event in enumerate(events)
    ]
    dated_events = [item for item in dated_events if item[0]]
    latest_event = max(dated_events, key=lambda item: (item[0], item[1]))[2] if dated_events else {}
    status = mapping_value(raw, "iniFase", "estado", "fase", "situacao") or mapping_value(
        latest_event, "Fase", "estado", "situacao"
    )
    entry_dates = [
        parse_datetime(mapping_value(event, "DataFase", "data"))
        for event in events
        if normalise_text(mapping_value(event, "Fase")) == "entrada"
    ]
    resolved_dates = [value for value in entry_dates if isinstance(value, dt.datetime)]
    if not resolved_dates:
        resolved_dates = [item[0] for item in dated_events if isinstance(item[0], dt.datetime)]
    submitted_at = iso_datetime(min(resolved_dates)) if resolved_dates else iso_datetime(
        mapping_value(raw, "iniDataEntrada", "dataEntrada", "data", "date")
    )
    author_values = mapping_values_with_key(raw, "autor", "proponent", "subscritor", "grupo")
    authors = party_values(author_values, matcher)
    author_text = normalise_text(" ".join(flatten_strings(author_values)))
    if re.search(r"\bgoverno\b", author_text):
        authors.append("GOVERNO")
    authors = sorted(set(authors))
    session = mapping_value(raw, "IniSel", "sessao", "sessaoLegislativa")
    display_number = number
    if number and "/" not in number and record_legislature:
        display_number = f"{number}/{record_legislature}/{session or '1'}"
    initiative_id = stable_id("ini", record_legislature, bid or display_number)
    template = str(assembly_config.get("detailUrlTemplate") or "")
    source_url = safe_url(template.format(bid=urllib.parse.quote(bid, safe=""))) if bid and template else ""
    result = {
        "id": initiative_id,
        "bid": bid or None,
        "legislature": record_legislature,
        "number": compact_text(display_number, 80),
        "type": compact_text(type_name, 100),
        "title": compact_text(title, 500),
        "status": compact_text(status, 160),
        "submittedAt": submitted_at,
        "authors": authors,
        "sourceType": "assembly_initiative",
        "sourceUrl": source_url,
        "fetchedAt": iso_now(),
    }
    president_action = presidential_action_from_events(events)
    if president_action:
        result["presidentAction"] = president_action
    result["contentHash"] = content_hash(
        "|".join(str(result.get(key) or "") for key in ("bid", "number", "type", "title", "status", "submittedAt", "authors"))
    )
    return result


def official_data_hosts() -> set[str]:
    return {"parlamento.pt", "app.parlamento.pt"}


def discover_open_data_json(
    client: HttpClient,
    assembly_config: Mapping[str, Any],
    resource_page: str,
    legislature: str,
) -> str:
    """Rediscover a current JSON download URL from an Assembly landing page.

    The official pages frequently point to temporary/tokenised URLs.  Persisting
    them would make later runs brittle, so each sync begins here instead.
    """

    base = str(assembly_config.get("openDataBaseUrl") or "")
    page_url = safe_url(urllib.parse.urljoin(base, resource_page), official_data_hosts())
    if not page_url:
        raise PipelineError(f"Página de dados abertos inválida: {resource_page}")
    raw, _headers, resolved = client.text(page_url)
    direct_links = extract_links(raw, resolved)
    resource_key = normalise_key(resource_page)
    prefix = "iniciativas" if "iniciativa" in resource_key else "atividades" if "atividade" in resource_key else ""

    def is_json_link(url: str, label: str) -> bool:
        filename = urllib.parse.unquote(urllib.parse.urlsplit(url).query + " " + label)
        key = normalise_key(filename)
        return bool(prefix and f"{prefix}{legislature.casefold()}json" in key)

    for url, label in direct_links:
        safe = safe_url(url, official_data_hosts())
        if safe and is_json_link(safe, label):
            return safe

    # Follow only the exact resource-folder link.  A substring search for a
    # Roman numeral (especially V or X) also matches navigation/committee URLs.
    wanted_label = normalise_text(f"{legislature} Legislatura")
    resource_basename = urllib.parse.urlsplit(page_url).path.rsplit("/", 1)[-1].casefold()
    legislature_links = [
        safe_url(url, official_data_hosts())
        for url, label in direct_links
        if normalise_text(label) == wanted_label
        and resource_basename in urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1].casefold()
    ]
    for candidate in dict.fromkeys(url for url in legislature_links if url):
        linked_raw, _linked_headers, linked_resolved = client.text(candidate)
        for url, label in extract_links(linked_raw, linked_resolved):
            safe = safe_url(url, official_data_hosts())
            if safe and is_json_link(safe, label):
                return safe
    raise PipelineError(
        f"Não foi encontrado o recurso JSON da {legislature} Legislatura em {page_url}."
    )


def fetch_open_data_records(
    client: HttpClient,
    assembly_config: Mapping[str, Any],
    resource_page: str,
    legislature: str,
    hints: Sequence[str],
) -> tuple[list[Mapping[str, Any]], str, str]:
    resource_url = discover_open_data_json(client, assembly_config, resource_page, legislature)
    response = client.get(resource_url)
    raw = decode_response_text(response)
    resolved = response.url
    try:
        # orjson is several times faster than the stdlib on the large
        # legislature exports (tens of MB of JSON per request).
        payload = (orjson.loads(raw.lstrip("\ufeff")) if orjson is not None else json.loads(raw.lstrip("\ufeff")))
    except ValueError as exc:
        raise PipelineError(f"JSON inválido nos dados abertos da Assembleia ({resolved}): {exc}") from exc
    resource_key = normalise_key(resource_page)
    if "iniciativa" in resource_key and isinstance(payload, list):
        records = [item for item in payload if isinstance(item, Mapping)]
    elif "atividade" in resource_key:
        records = records_with_vote_containers(payload)
    else:
        records = first_nested_record_list(payload, hints)
    return records, resolved, hashlib.sha256(response.content).hexdigest()


def merge_evidence(
    existing: Mapping[str, Any] | None, incoming: Mapping[str, Any]
) -> dict[str, Any]:
    """Preserve useful old fields when a partial later source omits them."""

    if not existing:
        return dict(incoming)
    merged = dict(existing)
    for key, value in incoming.items():
        if value not in (None, "", [], {}):
            merged[key] = value
    merged["fetchedAt"] = incoming.get("fetchedAt") or iso_now()
    return merged


def assembly_due(state: Mapping[str, Any], assembly_config: Mapping[str, Any], force: bool) -> bool:
    if force:
        return True
    last_sync = parse_datetime((state.get("assembly") or {}).get("lastSyncedAt"))
    if not last_sync:
        return True
    interval = max(1, int(assembly_config.get("syncIntervalHours", 24)))
    return utc_now() - last_sync >= dt.timedelta(hours=interval)


def sync_assembly(
    state: dict[str, Any],
    config: Mapping[str, Any],
    matcher: EntityMatcher,
    client: HttpClient,
    legislatures: Sequence[str],
    force: bool = False,
    max_detail_pages: int | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    """Synchronise initiatives and voting evidence from official open data."""

    assembly_config = config.get("assembly", {})
    if not assembly_config.get("enabled", True):
        LOGGER.info("A recolha da Assembleia está desativada.")
        return [{"status": "disabled", "note": "Sincronização da Assembleia desativada."}]
    if not assembly_due(state, assembly_config, force):
        LOGGER.info("A recolha da Assembleia não está prevista nesta execução.")
        return [{"status": "not_due", "note": "A sincronização oficial ainda está dentro do intervalo configurado."}]

    statuses: list[dict[str, Any]] = []
    assembly_state = state.setdefault("assembly", {"resourceSnapshots": {}})
    snapshots = assembly_state.setdefault("resourceSnapshots", {})
    legislature_total = len(legislatures)
    assembly_start_time = time.monotonic()
    safe_print(
        f"\n🏛️  A iniciar recolha da Assembleia da República: {legislature_total} legislaturas ({', '.join(legislatures)})..."
    )
    for legislature_index, legislature in enumerate(legislatures, start=1):
        safe_print(
            f"\n🏛️  [{legislature_index}/{legislature_total}] Assembleia da República — Legislatura {legislature}"
        )
        initiative_records: list[Mapping[str, Any]] = []
        initiative_url = ""
        try:
            initiative_records, initiative_url, snapshot_hash = fetch_open_data_records(
                client,
                assembly_config,
                str(assembly_config.get("initiativesPage") or "DAIniciativas.aspx"),
                legislature,
                ("iniciativa", "ini"),
            )
            if not initiative_records:
                raise PipelineError(
                    f"O recurso oficial de iniciativas da {legislature} Legislatura veio vazio."
                )
            snapshots[f"initiatives:{legislature}"] = {
                "url": initiative_url,
                "contentHash": snapshot_hash,
                "fetchedAt": iso_now(),
            }
        except PipelineError as exc:
            statuses.append({"legislature": legislature, "resource": "initiatives", "status": "error", "note": str(exc)})
            safe_print(
                f"[----/--/--] [Assembleia {legislature}] ❌ Não guardada: iniciativas ({exc})"
            )
            continue

        imported_initiatives = 0
        imported_initiative_ids: set[str] = set()
        imported_vote_ids: set[str] = set()
        existing_by_bid = {
            str(item.get("bid")): initiative_id
            for initiative_id, item in state["initiatives"].items()
            if item.get("legislature") == legislature and item.get("bid")
        }
        existing_votes_by_official_id = {
            str(item.get("officialId")): vote_id
            for vote_id, item in state["votes"].items()
            if item.get("legislature") == legislature and item.get("officialId")
        }
        ini_total = len(initiative_records)
        leg_start = time.monotonic()

        def save_vote(vote: Mapping[str, Any]) -> None:
            official_id = str(vote.get("officialId") or "")
            old_vote_id = existing_votes_by_official_id.get(official_id) if official_id else None
            if old_vote_id and old_vote_id != vote["id"]:
                state["votes"].pop(old_vote_id, None)
            already_saved = str(vote["id"]) in state["votes"]
            state["votes"][str(vote["id"])] = merge_evidence(
                state["votes"].get(str(vote["id"])), vote
            )
            imported_vote_ids.add(str(vote["id"]))
            print_progress_record(
                vote.get("date"),
                f"Assembleia {legislature}",
                vote.get("subject") or "Votação sem assunto",
                already_saved=already_saved,
            )

        for initiative_index, raw in enumerate(initiative_records, start=1):
            initiative = normalise_initiative(raw, matcher, legislature, assembly_config)
            if not initiative:
                print_progress_record(
                    None,
                    f"Assembleia {legislature}",
                    f"Registo de iniciativa {initiative_index}",
                    saved=False,
                )
                continue
            initiative_id = initiative["id"]
            old_natural_id = existing_by_bid.get(str(initiative.get("bid") or ""))
            if old_natural_id and old_natural_id != initiative_id:
                state["initiatives"].pop(old_natural_id, None)
            old = state["initiatives"].get(initiative_id)
            state["initiatives"][initiative_id] = merge_evidence(old, initiative)
            imported_initiatives += 1
            imported_initiative_ids.add(str(initiative_id))
            print_progress_record(
                initiative.get("submittedAt"),
                f"Assembleia {legislature}",
                " - ".join(
                    part
                    for part in (
                        str(initiative.get("number") or "").strip(),
                        str(initiative.get("title") or "Iniciativa sem título").strip(),
                    )
                    if part
                ),
                already_saved=old is not None,
            )
            for raw_vote in nested_vote_records(raw):
                vote = normalise_vote(raw_vote, matcher, initiative["legislature"], initiative)
                if vote:
                    save_vote(vote)
            if checkpoint and initiative_index % 100 == 0:
                checkpoint()

        # Activities carries votes that do not necessarily appear nested below an
        # initiative.  It is optional: initiatives remain usable if unavailable.
        activity_error = ""
        try:
            activity_records, activity_url, activity_hash = fetch_open_data_records(
                client,
                assembly_config,
                str(assembly_config.get("activitiesPage") or "DAatividades.aspx"),
                legislature,
                ("atividade", "actividade", "act"),
            )
            if not activity_records:
                raise PipelineError(
                    f"O recurso oficial de atividades da {legislature} Legislatura não contém votações."
                )
            snapshots[f"activities:{legislature}"] = {
                "url": activity_url,
                "contentHash": activity_hash,
                "fetchedAt": iso_now(),
            }
            act_total = len(activity_records)
            for activity_index, raw_activity in enumerate(activity_records, start=1):
                for raw_vote in nested_vote_records(raw_activity):
                    vote = normalise_vote(
                        raw_vote, matcher, legislature, context=raw_activity
                    )
                    if vote:
                        save_vote(vote)
                if checkpoint and activity_index % 100 == 0:
                    checkpoint()
        except PipelineError as exc:
            activity_error = str(exc)
            LOGGER.warning("Atividades da %s indisponíveis: %s", legislature, exc)
            safe_print(
                f"[----/--/--] [Assembleia {legislature}] ⚠️  Votações complementares indisponíveis ({exc})"
            )

        # Votes are already present in the official JSON resources.  Scraping a
        # whole detail page merged several independent ballots and produced
        # incorrect positions, so the compatibility CLI option is intentionally
        # not used as a second, lower-quality source.

        # A fully consumed official snapshot is authoritative for its
        # legislature. Remove records left behind by older parser versions or
        # withdrawn upstream entries only after the corresponding imports have
        # completed, so an interruption can never erase the previous snapshot.
        for initiative_id, stored in list(state["initiatives"].items()):
            if (
                stored.get("legislature") == legislature
                and initiative_id not in imported_initiative_ids
            ):
                state["initiatives"].pop(initiative_id, None)
        if not activity_error:
            for vote_id, stored in list(state["votes"].items()):
                if (
                    stored.get("legislature") == legislature
                    and vote_id not in imported_vote_ids
                ):
                    state["votes"].pop(vote_id, None)

        statuses.append(
            {
                "legislature": legislature,
                "resource": "assembly",
                "status": "partial" if activity_error else "ok",
                "initiatives": imported_initiatives,
                "votes": len(imported_vote_ids),
                "detailPages": 0,
                "sourceUrl": initiative_url,
                "note": activity_error,
            }
        )
        safe_print(
            f"   ↳ Concluído: {imported_initiatives} iniciativas e {len(imported_vote_ids)} votações guardadas."
        )
        if checkpoint:
            checkpoint()
    if any(status.get("status") in {"ok", "partial"} for status in statuses):
        assembly_state["lastSyncedAt"] = iso_now()
    return statuses


# ---------------------------------------------------------------------------
# Promise extraction and transparent proposal matching


def programme_corpus_fingerprint(paths: Sequence[Path]) -> str:
    parts: list[str] = [f"extractor:{PROGRAMME_PROMISE_EXTRACTOR_VERSION}"]
    for path in paths:
        if path.exists():
            stat = path.stat()
            parts.append(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}")
    return content_hash("|".join(parts))


def entity_ids(matcher: EntityMatcher, kinds: set[str] | None = None) -> set[str]:
    return {
        entity.id
        for entity in matcher.entities
        if kinds is None or entity.kind in kinds
    }


def canonical_promise_party(value: Any, matcher: EntityMatcher) -> str | None:
    raw = str(value or "").strip()
    if raw in entity_ids(matcher, {"party", "coalition"}):
        return raw
    return matcher.canonical_party(raw)


# Pastas canónicas do corpus electoral. Cada entrada define o rótulo do
# contexto eleitoral (surfaced no site e na memória do bot) para que as
# promessas digam sempre "onde foram feitas".
PROGRAMME_CONTEST_PATTERNS = (
    {
        "regex": re.compile(r"(?i)(?:^|[\\/])legislativas[\\/]legislativas\s+(\d{4})(?:[\\/]|$)"),
        "contest": "Legislativas {year}",
    },
    {
        "regex": re.compile(r"(?i)(?:^|[\\/])europeias[\\/]europeias\s+(\d{4})(?:[\\/]|$)"),
        "contest": "Europeias {year}",
    },
    {
        "regex": re.compile(r"(?i)(?:^|[\\/])[aâ][çc]ores[\\/][aâ][çc]ores\s+(\d{4})(?:[\\/]|$)"),
        "contest": "Regionais Açores {year}",
    },
    {
        "regex": re.compile(r"(?i)(?:^|[\\/])madeira[\\/]madeira\s+(\d{4})(?:[\\/]|$)"),
        "contest": "Regionais Madeira {year}",
    },
)
ELECTION_CORPUS_PATH_RE = re.compile(
    r"(?i)(?:^|[\\/])(?:legislativas|europeias|[aâ][çc]ores|madeira)[\\/]"
)
DECLARATION_PATH_RE = re.compile(
    r"(?i)(?:^|[\\/])declara[çc][aã]o\s+de\s+princ[ií]pios[\\/]"
)
IL_LEADERSHIP_PATH_RE = re.compile(r"(?i)(?:^|[\\/])il[\\/]")
PROGRAMME_ORIGINS = ("programa_eleitoral", "declaracao_principios")


def programme_chunk_scope(chunk: Mapping[str, Any]) -> str:
    """Classify a corpus chunk: election programme, declaration, leadership or other."""

    rel_path = str(chunk.get("rel_path") or chunk.get("relPath") or "")
    if IL_LEADERSHIP_PATH_RE.search(rel_path):
        # Candidaturas internas (congressos/lideranças) não são programas para
        # uma eleição real — excluídas por defeito.
        return "leadership"
    category = normalise_text(chunk.get("category"))
    if DECLARATION_PATH_RE.search(rel_path) or "declarac" in category:
        return "declaration"
    if (
        "legislativ" in category
        or "elei" in category
        or ELECTION_CORPUS_PATH_RE.search(rel_path)
    ):
        return "election"
    return "other"


def programme_chunk_metadata(
    chunk: Mapping[str, Any], matcher: EntityMatcher
) -> tuple[str | None, int | str | None, str]:
    """Recover party, year and the contest label from the canonical chunk path."""

    rel_path = str(chunk.get("rel_path") or chunk.get("relPath") or "")
    path_match = None
    for entry in PROGRAMME_CONTEST_PATTERNS:
        found = entry["regex"].search(rel_path)
        if found:
            path_match = (entry, int(found.group(1)))
            break
    if not path_match:
        # Root-level files in the current corpus are uncatalogued duplicates
        # with an invented year. They are not a trustworthy electoral source.
        return None, None, ""
    entry, programme_year = path_match
    probe = f"{chunk.get('filename') or ''} {rel_path}"
    party = canonical_promise_party(chunk.get("party"), matcher) or canonical_promise_party(probe, matcher)
    return party, programme_year, entry["contest"].format(year=programme_year)


def clean_programme_statement(value: Any) -> str:
    """Repair common PDF line-wrap artefacts without rewriting the evidence."""

    statement = compact_text(value, 620)
    statement = re.sub(r"(?<=\w)-\s+(?=\w)", "", statement)
    return compact_text(statement, 620)


def programme_promise_candidate(statement: str) -> bool:
    normal = normalise_text(statement)
    meaningful = tokenise(normal)
    if len(meaningful) < 4 or statement.rstrip().endswith("?"):
        return False
    first_alpha = re.search(r"[^\W\d_]", statement, re.UNICODE)
    if first_alpha and first_alpha.group(0).islower():
        return False
    if normal.startswith("indice ") or re.match(r"^introducao\s+(?!d[aeo]s?\b)", normal):
        return False
    last_token = normal.rsplit(" ", 1)[-1] if normal else ""
    if last_token in {
        "a", "ao", "aos", "as", "com", "da", "das", "de", "do", "dos",
        "e", "em", "na", "nas", "no", "nos", "o", "os", "ou", "para",
        "pela", "pelas", "pelo", "pelos", "por", "que", "um", "uma",
    } or len(last_token) == 1:
        return False
    if PROGRAMME_EXPLICIT_COMMITMENT_PATTERN.search(normal):
        return True
    if PROGRAMME_PASSIVE_COMMITMENT_PATTERN.search(normal):
        return True
    if PROGRAMME_NORMATIVE_COMMITMENT_PATTERN.search(normal):
        return True
    if re.search(
        r"\b(?:teria|teriam|tivesse|tivessem)\s+(?:sido|de)|"
        r"\b(?:tolerou-se|assinale-se|foi|foram)\b",
        normal,
    ):
        return False
    leading_match = PROGRAMME_LEADING_ACTION_PATTERN.search(normal)
    if not leading_match:
        return False
    # Headings/page numbers can precede a measure, but an action word buried in
    # a long narrative paragraph is evidence about policy, not itself a promise.
    prefix_words = normal[: leading_match.start()].split()
    if len(prefix_words) <= 1:
        return True
    return len(prefix_words) <= 3 and any(word.isdigit() for word in prefix_words)


def promise_candidate(statement: str, programme: bool = False) -> bool:
    normal = normalise_text(statement)
    if len(tokenise(normal)) < 4:
        return False
    if programme:
        return programme_promise_candidate(statement)
    if any(re.search(pattern, normal) for pattern in NEWS_PROMISE_PATTERNS):
        return True
    return False


def promise_record(
    party: str,
    statement: str,
    origin: str,
    source: Mapping[str, Any],
    entities: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    source_key = source.get("url") or source.get("relPath") or source.get("filename") or ""
    promise_id = stable_id("promise", party, origin, source_key, normalise_text(statement))
    return {
        "id": promise_id,
        "party": party,
        "statement": compact_text(statement, 620),
        "origin": origin,
        "source": dict(source),
        "entities": list(entities or []),
        "status": "por_verificar",
        "reviewRequired": True,
        "proposalMatches": [],
        "createdAt": iso_now(),
        "updatedAt": iso_now(),
    }


def preserve_promise_review(
    existing: Mapping[str, Any] | None, incoming: Mapping[str, Any]
) -> dict[str, Any]:
    if not existing:
        return dict(incoming)
    result = dict(incoming)
    for key in ("status", "reviewRequired", "createdAt", "reviewedAt", "reviewedBy", "reviewNotes"):
        if key in existing:
            result[key] = existing[key]
    return result


def rebuild_programme_promises(state: dict[str, Any], matcher: EntityMatcher) -> int:
    """Extract conservative promise candidates from the existing programme corpus."""

    entity_signature = [
        {
            "id": entity.id,
            "kind": entity.kind,
            "aliases": entity.aliases,
            "affiliations": entity.affiliations,
        }
        for entity in matcher.entities
        if entity.kind in {"party", "coalition"}
    ]
    fingerprint = content_hash(
        programme_corpus_fingerprint(PROGRAM_CHUNK_FILES)
        + json.dumps(entity_signature, ensure_ascii=False, sort_keys=True)
    )
    existing_programmes = {
        promise_id: promise
        for promise_id, promise in state["promises"].items()
        if promise.get("origin") in PROGRAMME_ORIGINS
    }
    if state.get("programCorpusFingerprint") == fingerprint and existing_programmes:
        LOGGER.info(
            "Promessas dos programas eleitorais já estavam atualizadas (%s guardadas).",
            len(existing_programmes),
        )
        return 0

    rebuilt: dict[str, dict[str, Any]] = {}
    for path in PROGRAM_CHUNK_FILES:
        chunks = json_load(path, [])
        if not isinstance(chunks, list):
            LOGGER.warning("Corpus de programas com formato inesperado: %s", path)
            continue
        for chunk in chunks:
            if not isinstance(chunk, Mapping):
                continue
            scope = programme_chunk_scope(chunk)
            if scope not in {"election", "declaration"}:
                continue
            party, programme_year, contest = programme_chunk_metadata(chunk, matcher)
            if not party:
                continue
            origin = "declaracao_principios" if scope == "declaration" else "programa_eleitoral"
            for raw_statement in sentence_candidates(chunk.get("text")):
                statement = clean_programme_statement(raw_statement)
                if not promise_candidate(statement, programme=True):
                    continue
                source = {
                    "type": origin,
                    "title": str(chunk.get("filename") or "Programa eleitoral"),
                    "filename": str(chunk.get("filename") or ""),
                    "relPath": str(chunk.get("rel_path") or ""),
                    "page": chunk.get("page"),
                    "year": programme_year,
                    "contest": contest,
                }
                promise = promise_record(party, statement, origin, source)
                rebuilt[promise["id"]] = preserve_promise_review(
                    existing_programmes.get(promise["id"]), promise
                )
    for promise_id in existing_programmes:
        state["promises"].pop(promise_id, None)
    state["promises"].update(rebuilt)
    state["programCorpusFingerprint"] = fingerprint
    promise_total = len(rebuilt)
    for promise_index, promise in enumerate(
        sorted(rebuilt.values(), key=lambda item: str(item.get("statement") or "")),
        start=1,
    ):
        source = promise.get("source", {})
        party_label = f" ({promise.get('party')})" if promise.get("party") else ""
        print_progress_record(
            source.get("year"),
            f"{source.get('contest') or 'Programa Eleitoral'}{party_label}",
            promise.get("statement") or "Promessa sem descrição",
            already_saved=str(promise.get("id")) in existing_programmes,
        )
    return len(rebuilt)


def attributable_parties(entities: Sequence[Mapping[str, Any]]) -> set[str]:
    parties: set[str] = set()
    for entity in entities:
        kind = str(entity.get("kind") or "")
        if kind in {"party", "coalition"}:
            parties.add(str(entity.get("id") or ""))
        elif kind in {"person", "youth_wing"}:
            parties.update(str(item) for item in entity.get("affiliations", []) if item)
        elif entity.get("id") == "GOVERNO":
            parties.add("GOVERNO")
    return {party for party in parties if party}


def sentence_promise_parties(
    statement: str,
    article_entities: Sequence[Mapping[str, Any]],
    matcher: EntityMatcher,
) -> set[str]:
    sentence_entities = matcher.match(statement)
    parties = attributable_parties(sentence_entities)
    if len(parties) <= 1:
        if parties:
            return parties
    else:
        trigger = NEWS_PROMISE_TRIGGER_RE.search(statement)
        if trigger:
            # Attribute a sentence naming several parties only when exactly one
            # actor appears before the commitment verb (for example, "PS promete
            # apoiar proposta do PSD").  Otherwise the sentence is ambiguous.
            prefix_entities = matcher.match(statement[: trigger.start()])
            prefix_parties = attributable_parties(prefix_entities)
            if len(prefix_parties) == 1:
                return prefix_parties
        return set()
    article_parties = attributable_parties(article_entities)
    return article_parties if len(article_parties) == 1 else set()


def rebuild_news_promises(state: dict[str, Any], matcher: EntityMatcher) -> int:
    """Extract clearly attributed campaign/policy commitments from news evidence."""

    existing_news = {
        promise_id: promise
        for promise_id, promise in state["promises"].items()
        if promise.get("origin") == "noticia"
    }
    existing_by_article: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for promise in existing_news.values():
        source = promise.get("source")
        if isinstance(source, Mapping):
            existing_by_article[str(source.get("articleId") or "")].append(promise)
    rebuilt: dict[str, dict[str, Any]] = {}
    for article in state["articles"].values():
        evidence = ". ".join(
            str(article.get(key) or "").strip().rstrip(".")
            for key in ("title", "summary", "excerpt")
            if str(article.get(key) or "").strip()
        )
        article_id = str(article.get("id") or "")
        if (
            article.get("promiseReviewVersion") == PROMISE_REVIEW_VERSION
            and article.get("promisedContentHash") == content_hash(evidence)
        ):
            # Deterministic extraction over unchanged text: reuse the promises
            # already stored for this exact article instead of re-running every
            # regex over hundreds of thousands of records.
            reused = 0
            for promise in existing_by_article.get(article_id, []):
                rebuilt[str(promise["id"])] = promise
                reused += 1
            if reused:
                continue
            article["promiseReviewVersion"] = PROMISE_REVIEW_VERSION
            continue
        for statement in sentence_candidates(evidence):
            if not promise_candidate(statement):
                continue
            parties = sentence_promise_parties(
                statement, article.get("entities", []), matcher
            )
            for party in parties:
                source = {
                    "type": "noticia",
                    "title": str(article.get("title") or "Notícia"),
                    "url": str(article.get("url") or ""),
                    "publisher": str(article.get("source") or ""),
                    "publishedAt": article.get("publishedAt"),
                    "articleId": article_id,
                }
                promise = promise_record(party, statement, "noticia", source, article.get("entities", []))
                rebuilt[promise["id"]] = preserve_promise_review(
                    existing_news.get(promise["id"]), promise
                )
        article["promiseReviewVersion"] = PROMISE_REVIEW_VERSION
        article["promisedContentHash"] = content_hash(evidence)
    for promise_id in existing_news:
        state["promises"].pop(promise_id, None)
    state["promises"].update(rebuilt)
    promise_total = len(rebuilt)
    for promise_index, promise in enumerate(
        sorted(rebuilt.values(), key=lambda item: str(item.get("statement") or "")),
        start=1,
    ):
        source = promise.get("source", {})
        party_label = f" ({promise.get('party')})" if promise.get("party") else ""
        print_progress_record(
            source.get("publishedAt"),
            f"Imprensa{party_label}",
            promise.get("statement") or "Promessa sem descrição",
            already_saved=str(promise.get("id")) in existing_news,
        )
    return len(rebuilt)


def promise_similarity(promise: Mapping[str, Any], initiative: Mapping[str, Any]) -> tuple[float, list[str]]:
    promise_tokens = tokenise(promise.get("statement"))
    proposal_tokens = tokenise(
        f"{initiative.get('title', '')} {initiative.get('type', '')}"
    )
    shared = sorted(promise_tokens & proposal_tokens)
    if not shared:
        return 0.0, []
    union = promise_tokens | proposal_tokens
    jaccard = len(shared) / len(union) if union else 0.0
    containment = len(shared) / min(len(promise_tokens), len(proposal_tokens))
    score = (jaccard * 0.45) + (containment * 0.55)
    return round(score, 4), shared


def promise_source_date(promise: Mapping[str, Any]) -> dt.datetime | None:
    source = promise.get("source", {})
    if not isinstance(source, Mapping):
        return None
    published = parse_datetime(source.get("publishedAt"))
    if published:
        return published
    year_match = re.fullmatch(r"\d{4}", str(source.get("year") or "").strip())
    if year_match:
        return dt.datetime(int(year_match.group(0)), 1, 1, tzinfo=UTC)
    return None


def initiative_author_relation(
    promise_party: str, initiative: Mapping[str, Any]
) -> tuple[str, str]:
    authors = {str(item) for item in initiative.get("authors", []) if item}
    if promise_party and promise_party in authors:
        if promise_party == "GOVERNO":
            return "governo", "Apresentada pelo Governo"
        return "mesmo_partido", "Apresentada pelo mesmo partido"
    if "GOVERNO" in authors:
        return "governo", "Apresentada pelo Governo"
    if authors:
        return "outro_partido", "Apresentada por outro partido"
    return "nao_publicada", "Autoria não publicada"


def rebuild_promise_matches(state: dict[str, Any]) -> int:
    """Link promise candidates to initiatives without claiming equivalence.

    Every returned relation is labelled as an automatic, review-required match.
    Similar but non-identical proposals intentionally receive the explicit
    ``aproximada`` label required by the public UI.
    """

    LOGGER.info(
        "A iniciar correspondência entre %s promessas e %s iniciativas.",
        len(state["promises"]),
        len(state["initiatives"]),
    )
    initiatives = list(state["initiatives"].values())
    inverted: dict[str, set[str]] = defaultdict(set)
    by_id: dict[str, Mapping[str, Any]] = {}
    votes_by_initiative: dict[str, list[str]] = defaultdict(list)
    for vote in state["votes"].values():
        if vote.get("initiativeId"):
            votes_by_initiative[str(vote["initiativeId"])].append(str(vote["id"]))
    for initiative in initiatives:
        initiative_id = str(initiative.get("id") or "")
        if not initiative_id:
            continue
        by_id[initiative_id] = initiative
        for token in tokenise(f"{initiative.get('title', '')} {initiative.get('type', '')}"):
            inverted[token].add(initiative_id)

    matched = 0
    for promise in state["promises"].values():
        earliest_date = promise_source_date(promise)
        candidate_overlap: Counter[str] = Counter()
        for token in tokenise(promise.get("statement")):
            candidate_overlap.update(inverted.get(token, set()))
        # The acceptance rule below always requires two shared terms. Counting
        # postings here avoids scoring the often-thousands of initiatives that
        # share only a generic verb, without discarding any possible match.
        candidate_ids = {
            initiative_id
            for initiative_id, overlap in candidate_overlap.items()
            if overlap >= 2
        }
        suggestions: list[dict[str, Any]] = []
        for initiative_id in sorted(candidate_ids):
            initiative = by_id[initiative_id]
            submitted_at = parse_datetime(initiative.get("submittedAt"))
            if earliest_date and submitted_at and submitted_at.date() < earliest_date.date():
                continue
            score, shared = promise_similarity(promise, initiative)
            # Two terms only suffice when the overlap is unusually strong. Most
            # relations need at least three meaningful terms, which prevents
            # generic words such as "apoio" or "criar" from linking proposals.
            if len(shared) < 2:
                continue
            if len(shared) == 2 and score < 0.42:
                continue
            if len(shared) >= 3 and score < 0.22:
                continue
            author_relation, author_relation_label = initiative_author_relation(
                str(promise.get("party") or ""), initiative
            )
            if author_relation == "outro_partido" and (len(shared) < 3 or score < 0.30):
                continue
            approximate = not (
                author_relation == "mesmo_partido"
                and normalise_text(promise.get("statement"))
                == normalise_text(initiative.get("title"))
            )
            suggestions.append(
                {
                    "initiativeId": initiative_id,
                    "bid": initiative.get("bid"),
                    "number": initiative.get("number"),
                    "title": initiative.get("title"),
                    "authors": initiative.get("authors", []),
                    "submittedAt": initiative.get("submittedAt"),
                    "sourceUrl": initiative.get("sourceUrl"),
                    "score": score,
                    "sharedTerms": shared,
                    "matchKind": "aproximada" if approximate else "direta",
                    "approximate": approximate,
                    "authorRelation": author_relation,
                    "authorRelationLabel": author_relation_label,
                    "reviewRequired": True,
                    "voteIds": sorted(votes_by_initiative.get(initiative_id, [])),
                }
            )
        suggestions.sort(key=lambda item: (-item["score"], str(item.get("title") or "")))
        promise["proposalMatches"] = suggestions
        promise["updatedAt"] = iso_now()
        matched += len(promise["proposalMatches"])
    safe_print(f"   ↳ {matched} correspondências sugeridas entre promessas e iniciativas.")
    return matched


# ---------------------------------------------------------------------------
# Fonte presidencial (presidenciarepublica.pt) e iniciativas europeias.


PRESIDENCY_HOSTS = {"presidenciarepublica.pt", "www.presidenciarepublica.pt"}
EUROPEAN_PARLIAMENT_HOSTS = {"data.europarl.europa.eu"}
LAW_REFERENCE_RE = re.compile(r"(?is)leis?\s+n[.\sºo°]*\s*(\d{1,5})\s*[/\\]\s*(?:de\s+)?(\d{4})")


def _ld_text(value: Any, depth: int = 0) -> str:
    """Extract a plain string from JSON-LD values (plain, list or language map)."""

    if depth > 3:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in ("@value", "content", "name", "title", "label"):
            text = _ld_text(value.get(key), depth + 1)
            if text:
                return text
        return ""
    if isinstance(value, (list, tuple)):
        for item in value:
            text = _ld_text(item, depth + 1)
            if text:
                return text
    return ""


def sync_presidential_actions(
    state: dict[str, Any],
    config: Mapping[str, Any],
    client: HttpClient,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Collect promulgations and vetoes published by the President's site.

    Opt-in por configuração: a secção ``presidential`` declara os URLs das
    listas oficiais e só corre quando ``enabled`` estiver a true, respeitando
    robots.txt do domínio antes de qualquer pedido.
    """

    section = config.get("presidential") if isinstance(config, Mapping) else {}
    section = section if isinstance(section, Mapping) else {}
    result = {"enabled": bool(section.get("enabled")), "promulgadas": 0, "vetos": 0, "novas": 0, "note": ""}
    if not result["enabled"]:
        result["note"] = "secção 'presidential' desativada na configuração"
        return result

    policy = None
    robots_url = safe_url(str(section.get("robotsUrl") or ""), PRESIDENCY_HOSTS)
    if robots_url:
        try:
            raw_robots, _headers, _resolved = client.text(robots_url)
            policy = urllib.robotparser.RobotFileParser()
            policy.parse(raw_robots.splitlines())
        except PipelineError:
            crawl_config = config.get("crawl", {}) if isinstance(config, Mapping) else {}
            if crawl_config.get("failClosedOnRobotsError", True):
                result["note"] = "regras de robots.txt indisponíveis — fonte ignorada por segurança"
                return result

    store: dict[str, Any] = state.setdefault("presidentActions", {})
    laws_by_ref: dict[tuple[str, str], dict[str, Any]] = {}
    for kind, key in (("promulgada", "promulgationsUrl"), ("veto", "vetoesUrl")):
        url = safe_url(str(section.get(key) or ""), PRESIDENCY_HOSTS)
        if not url:
            continue
        if policy is not None and not can_fetch(policy, client, url):
            result["note"] = f"{key}: bloqueado por robots.txt"
            continue
        try:
            raw, _headers, resolved = client.text(url)
        except PipelineError as exc:
            result["note"] = f"{key}: {exc}"
            continue
        for match in LAW_REFERENCE_RE.finditer(raw):
            number, year = match.group(1), match.group(2)
            date_value = ""
            window = raw[max(0, match.start() - 300) : match.start()]
            for date_match in re.finditer(r"(\d{1,2})/(\d{1,2})/(\d{4})", window):
                day, month, year_full = date_match.groups()
                if int(month) <= 12 and int(day) <= 31:
                    date_value = f"{year_full}-{int(month):02d}-{int(day):02d}"
            action_id = stable_id("pr", kind, number, year)
            record = {
                "id": action_id,
                "kind": kind,
                "lawNumber": compact_text(number, 40),
                "lawYear": int(year),
                "date": date_value,
                "sourceType": "presidencia_pt",
                "sourceUrl": resolved,
                "fetchedAt": iso_now(),
            }
            existing = store.get(action_id)
            if existing and content_hash(json.dumps(existing, sort_keys=True, default=str)) == content_hash(
                json.dumps(record, sort_keys=True, default=str)
            ):
                continue
            store[action_id] = record
            result["novas"] += 1
            if checkpoint:
                checkpoint.mark_dirty()
        if checkpoint:
            checkpoint()

    result["promulgadas"] = sum(1 for item in store.values() if item.get("kind") == "promulgada")
    result["vetos"] = sum(1 for item in store.values() if item.get("kind") == "veto")

    # Liga registos presidenciais a iniciativas cujo título refira a mesma lei.
    for initiative in state.get("initiatives", {}).values():
        if not isinstance(initiative, Mapping) or initiative.get("presidentAction"):
            continue
        title = normalise_text(initiative.get("title"))
        match = re.search(r"(?i)leis?\s+n[.\sºo°]*\s*(\d{1,5})\s*[/\\]\s*(\d{4})", title)
        if not match:
            continue
        action = store.get(stable_id("pr", "promulgada", match.group(1), match.group(2))) or store.get(
            stable_id("pr", "veto", match.group(1), match.group(2))
        )
        if action:
            initiative["presidentAction"] = {
                "kind": action["kind"],
                "date": action.get("date") or None,
                "phaseLabel": f"Lei n.º {action['lawNumber']}/{action['lawYear']}",
                "sourceUrl": action.get("sourceUrl"),
            }
    return result


def sync_european_initiatives(
    state: dict[str, Any],
    config: Mapping[str, Any],
    client: HttpClient,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Ingest legislative procedures from the European Parliament open data API.

    Endpoint oficial ``data.europarl.europa.eu/api/v2/procedures``.  Votações
    nominais do PE ficam para uma segunda fase; o dossiê e o seu estado já
    permitem as sugestões promessa↔UE com revisão humana.
    """

    section = config.get("europeanUnion")
    section = section if isinstance(section, Mapping) else {}
    result = {
        "enabled": bool(section.get("enabled")),
        "known": 0,
        "novos": 0,
        "atualizados": 0,
        "pedidos": 0,
        "note": "",
    }
    if not result["enabled"]:
        result["note"] = "secção 'europeanUnion' desativada na configuração"
        return result

    api_base = str(section.get("apiBase") or "https://data.europarl.europa.eu/api/v2").rstrip("/")
    term = max(9, int(section.get("parliamentaryTerm") or 10))
    page_size = max(10, int(section.get("pageSize") or 100))
    max_requests = max(1, int(section.get("maxRequests") or 20))

    store: dict[str, Any] = state.setdefault("euInitiatives", {})
    for request_index in range(max_requests):
        offset = request_index * page_size
        url = (
            f"{api_base}/procedures?parliamentary-term={term}"
            f"&format=application%2Fld%2Bjson&limit={page_size}&offset={offset}"
        )
        safe = safe_url(url, EUROPEAN_PARLIAMENT_HOSTS)
        if not safe:
            result["note"] = f"URL fora dos hosts autorizados: {url}"
            break
        try:
            raw, _headers, _resolved = client.text(safe)
        except PipelineError as exc:
            result["note"] = f"procedures offset={offset}: {compact_text(exc, 200)}"
            break
        result["pedidos"] += 1
        try:
            payload = json.loads(raw.lstrip("\ufeff"))
        except ValueError as exc:
            result["note"] = f"JSON inválido do PE ({compact_text(exc, 120)})"
            break
        records: Any = None
        if isinstance(payload, Mapping):
            records = payload.get("data")
            if records is None:
                records = payload.get("@graph")
        if not isinstance(records, list):
            result["note"] = "resposta inesperada da API do PE"
            break
        for item in records:
            if not isinstance(item, Mapping):
                continue
            identifier = str(
                item.get("identifier")
                or _ld_text(item.get("@id"))
                or stable_id("eu_raw", json.dumps(item, sort_keys=True, default=str)[:400])
            ).rsplit("/", 1)[-1]
            if not identifier:
                continue
            title = compact_text(_ld_text(item.get("title")) or _ld_text(item.get("procedureTitle")), 500)
            status = compact_text(
                _ld_text(item.get("statusLabel"))
                or _ld_text(item.get("procedureStage"))
                or _ld_text(item.get("status")),
                160,
            )
            type_label = compact_text(_ld_text(item.get("type")) or _ld_text(item.get("@type")), 100)
            date_value = iso_datetime(
                _ld_text(item.get("modificationDate"))
                or _ld_text(item.get("lastModified"))
                or _ld_text(item.get("dateOfAddition"))
                or _ld_text(item.get("startDate"))
            )
            eu_id = stable_id("eui", identifier)
            record = {
                "id": eu_id,
                "identifier": identifier,
                "title": title,
                "type": type_label,
                "status": status,
                "date": date_value,
                "parliamentaryTerm": term,
                "sourceType": "ep_procedure",
                "sourceUrl": safe_url(_ld_text(item.get("@id")), EUROPEAN_PARLIAMENT_HOSTS) or safe,
                "fetchedAt": iso_now(),
            }
            existing = store.get(eu_id)
            if existing:
                comparable_new = {key: value for key, value in record.items() if key != "fetchedAt"}
                comparable_old = {key: value for key, value in existing.items() if key != "fetchedAt"}
                if content_hash(json.dumps(comparable_new, sort_keys=True, default=str)) == content_hash(
                    json.dumps(comparable_old, sort_keys=True, default=str)
                ):
                    continue
                result["atualizados"] += 1
            else:
                result["novos"] += 1
            store[eu_id] = record
        if checkpoint:
            checkpoint.mark_dirty()
            checkpoint()
        if len(records) < page_size:
            break
    result["known"] = len(store)
    return result


def rebuild_promise_european_matches(state: dict[str, Any]) -> int:
    """Suggest promise ↔ EU dossier relations with the same conservative rules."""

    eu_initiatives = state.get("euInitiatives") or {}
    matched = 0
    inverted: dict[str, set[str]] = defaultdict(set)
    for eu_id, dossier in eu_initiatives.items():
        title_tokens = tokenise(str(dossier.get("title") or ""))
        for token in title_tokens:
            inverted[token].add(eu_id)

    for promise in state["promises"].values():
        if not eu_initiatives:
            promise.pop("proposalEuropeanMatches", None)
            continue
        overlap: Counter[str] = Counter()
        for token in tokenise(promise.get("statement")):
            overlap.update(inverted.get(token, set()))
        candidate_ids = [eu_id for eu_id, hits in overlap.items() if hits >= 2]
        suggestions: list[dict[str, Any]] = []
        for eu_id in sorted(candidate_ids)[:24]:
            dossier = eu_initiatives[eu_id]
            score, shared = promise_similarity(promise, {"title": dossier.get("title"), "type": dossier.get("type")})
            if len(shared) < 2:
                continue
            if len(shared) == 2 and score < 0.42:
                continue
            if len(shared) >= 3 and score < 0.22:
                continue
            suggestions.append(
                {
                    "initiativeId": eu_id,
                    "identifier": dossier.get("identifier"),
                    "title": dossier.get("title"),
                    "status": dossier.get("status"),
                    "date": dossier.get("date"),
                    "sourceUrl": dossier.get("sourceUrl"),
                    "score": score,
                    "sharedTerms": shared[:12],
                    "reviewRequired": True,
                }
            )
        suggestions.sort(key=lambda item: (-item["score"], str(item.get("title") or "")))
        promise["proposalEuropeanMatches"] = suggestions[:12]
        matched += len(suggestions[:12])
    safe_print(f"   ↳ {matched} correspondências sugeridas entre promessas e iniciativas europeias.")
    return matched


# ---------------------------------------------------------------------------
# Vote statistics.  Absence and lack of an observed position are distinct.


def initiative_outcome(initiative: Mapping[str, Any], votes: Sequence[Mapping[str, Any]]) -> str:
    def canonical(value: Any) -> str | None:
        normal = normalise_text(value)
        if re.search(r"\brejeitad", normal):
            return "rejeitada"
        if re.search(r"\baprovad", normal):
            return "aprovada"
        return None

    # The initiative's current official phase is authoritative when terminal.
    status_outcome = canonical(initiative.get("status"))
    if status_outcome:
        return status_outcome

    decisive: list[tuple[dt.datetime, int, str]] = []
    minimum = dt.datetime.min.replace(tzinfo=UTC)
    for index, vote in enumerate(votes):
        outcome = canonical(vote.get("result"))
        if outcome:
            decisive.append((parse_datetime(vote.get("date")) or minimum, index, outcome))
    if decisive:
        return max(decisive, key=lambda item: (item[0], item[1]))[2]
    return "sem_resultado"


def deduplicated_votes(votes: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Prefer the richest observation when an activity and detail page overlap."""

    best: dict[str, Mapping[str, Any]] = {}
    for vote in votes:
        official_id = str(vote.get("officialId") or "").strip()
        if official_id:
            key = f"official|{normalise_text(vote.get('legislature'))}|{official_id}"
        else:
            key = "|".join(
                normalise_text(vote.get(field))
                for field in ("legislature", "initiativeId", "date", "phase", "subject")
            )
        if not key.strip("|"):
            key = str(vote.get("id") or "")
        old = best.get(key)
        if not old or len(vote.get("positions", [])) > len(old.get("positions", [])):
            best[key] = vote
    return list(best.values())


def government_label_for(
    value: Mapping[str, Any], government_periods: Sequence[Mapping[str, Any]]
) -> str | None:
    authors = {str(item) for item in value.get("authors", []) if item}
    if "GOVERNO" not in authors:
        return None
    relevant_date = parse_datetime(value.get("submittedAt") or value.get("date"))
    for period in government_periods:
        start = parse_datetime(period.get("start"))
        end = parse_datetime(period.get("end"))
        if relevant_date and start and relevant_date >= start and (not end or relevant_date < end):
            return str(period.get("name") or period.get("id") or "Governo")
    return "Governo (período por classificar)"


def empty_count_row(identifier: str, name: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "name": name,
        "proposalsPresented": 0,
        "proposalsApproved": 0,
        "proposalsRejected": 0,
        "proposalsWithoutResult": 0,
        "votesFor": 0,
        "votesAgainst": 0,
        "abstentions": 0,
        "absences": 0,
        "observedVotes": 0,
    }


def vote_statistics(
    state: Mapping[str, Any],
    matcher: EntityMatcher,
    legislature: str | None = None,
    government_periods: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Summarise proposals and observed vote positions in a transparent scope."""

    party_names = {entity.id: entity.name for entity in matcher.entities if entity.kind in {"party", "coalition"}}
    parties: dict[str, dict[str, Any]] = {
        party_id: empty_count_row(party_id, name) for party_id, name in party_names.items()
    }
    governments: dict[str, dict[str, Any]] = {}
    initiatives = [
        item for item in state["initiatives"].values()
        if not legislature or item.get("legislature") == legislature
    ]
    initiative_ids = {str(item["id"]) for item in initiatives}
    all_votes = deduplicated_votes(
        vote
        for vote in state["votes"].values()
        if (not legislature or vote.get("legislature") == legislature)
        and (not vote.get("initiativeId") or str(vote.get("initiativeId")) in initiative_ids)
    )
    votes_by_initiative: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for vote in all_votes:
        if vote.get("initiativeId"):
            votes_by_initiative[str(vote["initiativeId"])].append(vote)

    for initiative in initiatives:
        outcome = initiative_outcome(initiative, votes_by_initiative.get(str(initiative["id"]), []))
        government_name = government_label_for(initiative, government_periods)
        for party in initiative.get("authors", []):
            if str(party) == "GOVERNO":
                continue
            row = parties.setdefault(str(party), empty_count_row(str(party), party_names.get(str(party), str(party))))
            row["proposalsPresented"] += 1
            if outcome == "aprovada":
                row["proposalsApproved"] += 1
            elif outcome == "rejeitada":
                row["proposalsRejected"] += 1
            else:
                row["proposalsWithoutResult"] += 1
        if government_name:
            row = governments.setdefault(government_name, empty_count_row(government_name, government_name))
            row["proposalsPresented"] += 1
            if outcome == "aprovada":
                row["proposalsApproved"] += 1
            elif outcome == "rejeitada":
                row["proposalsRejected"] += 1
            else:
                row["proposalsWithoutResult"] += 1

    observed_by_pair: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"bothObserved": 0, "same": 0, "different": 0})
    for vote in all_votes:
        positions = {
            str(item.get("party")): str(item.get("position"))
            for item in vote.get("positions", [])
            if item.get("party") and item.get("position")
        }
        for party, position in positions.items():
            row = parties.setdefault(party, empty_count_row(party, party_names.get(party, party)))
            row["observedVotes"] += 1
            if position == "favor":
                row["votesFor"] += 1
            elif position == "contra":
                row["votesAgainst"] += 1
            elif position == "abstencao":
                row["abstentions"] += 1
            elif position == "ausencia":
                row["absences"] += 1
        comparable_positions = {
            party: position
            for party, position in positions.items()
            if position != "ausencia"
        }
        for left, right in itertools.combinations(sorted(comparable_positions), 2):
            pair = observed_by_pair[(left, right)]
            pair["bothObserved"] += 1
            if comparable_positions[left] == comparable_positions[right]:
                pair["same"] += 1
            else:
                pair["different"] += 1

    pairs = []
    for (left, right), values in observed_by_pair.items():
        total = values["bothObserved"]
        pairs.append(
            {
                "left": left,
                "right": right,
                **values,
                "agreementRate": round((values["same"] / total) * 100, 1) if total else None,
            }
        )
    return {
        "scope": legislature or "sempre",
        "initiativeCount": len(initiatives),
        "voteCount": len(all_votes),
        "parties": sorted(parties.values(), key=lambda item: item["name"]),
        "governments": sorted(governments.values(), key=lambda item: item["name"]),
        "pairs": sorted(pairs, key=lambda item: (item["left"], item["right"])),
        "methodology": {
            "positions": "Só conta posições explicitamente observadas; ausência não é abstenção e posição ausente não é inferida.",
            "proposalOutcome": "Aprovação/rejeição é apurada a partir do resultado oficial associado à iniciativa quando disponível.",
            "comparisons": "A concordância compara votos a favor, contra e abstenções; ausências ficam fora da taxa.",
        },
    }


# ---------------------------------------------------------------------------
# Public dataset and retrieval-memory export


def public_source(source: Mapping[str, Any]) -> dict[str, Any]:
    """Keep source attribution while omitting any crawler-only bookkeeping."""

    allowed = (
        "type", "title", "url", "publisher", "publishedAt",
        "filename", "relPath", "page", "year", "contest",
    )
    return {key: source[key] for key in allowed if source.get(key) not in (None, "")}


def public_promise(
    promise: Mapping[str, Any],
    vote_outcomes: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    matches = []
    for match in promise.get("proposalMatches", []):
        matches.append(
            {
                key: match.get(key)
                for key in (
                    "initiativeId", "bid", "number", "title", "authors", "submittedAt",
                    "sourceUrl", "matchKind", "approximate", "authorRelation",
                    "authorRelationLabel", "voteIds",
                )
            }
        )
    return {
        "id": promise.get("id"),
        "party": promise.get("party"),
        "statement": promise.get("statement"),
        "origin": promise.get("origin"),
        "source": public_source(promise.get("source", {})),
        "status": promise.get("status", "por_verificar"),
        "reviewRequired": bool(promise.get("reviewRequired", True)),
        "proposalMatches": matches,
        "voteOutcomes": list(vote_outcomes or []),
        "updatedAt": promise.get("updatedAt"),
    }


def public_article(article: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: article.get(key)
        for key in (
            "id", "sourceId", "source", "url", "title", "summary", "excerpt", "section",
            "publishedAt", "topics", "entities", "fetchedAt",
        )
    }


def public_initiative(initiative: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: initiative.get(key)
        for key in (
            "id", "bid", "legislature", "number", "type", "title", "status", "submittedAt",
            "authors", "sourceUrl", "fetchedAt", "presidentAction",
        )
    }


def promise_vote_outcomes(
    promise: Mapping[str, Any],
    initiatives_by_id: Mapping[str, Mapping[str, Any]],
    votes_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Attach vote results (and the President's decision) to promise matches."""

    outcomes: list[dict[str, Any]] = []
    for match in promise.get("proposalMatches", []):
        initiative = initiatives_by_id.get(str(match.get("initiativeId") or ""))
        if not initiative:
            continue
        votes = [
            votes_by_id[str(vote_id)]
            for vote_id in match.get("voteIds", [])
            if str(vote_id) in votes_by_id
        ]
        votes.sort(key=lambda item: str(item.get("date") or ""))
        outcomes.append(
            {
                "initiativeId": match.get("initiativeId"),
                "bid": match.get("bid"),
                "number": match.get("number"),
                "title": match.get("title"),
                "matchKind": match.get("matchKind"),
                "sourceUrl": match.get("sourceUrl"),
                "outcome": initiative_outcome(initiative, votes),
                "status": initiative.get("status"),
                "presidentAction": initiative.get("presidentAction"),
                "votes": [
                    {
                        "id": vote.get("id"),
                        "date": vote.get("date"),
                        "result": vote.get("result"),
                        "phase": vote.get("phase"),
                    }
                    for vote in votes
                ],
            }
        )
    return outcomes


def public_vote(vote: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: vote.get(key)
        for key in (
            "id", "initiativeId", "initiativeBid", "legislature", "date", "subject", "phase",
            "result", "positions", "sourceUrl", "fetchedAt",
        )
    }


def build_public_dataset(state: Mapping[str, Any], config: Mapping[str, Any], matcher: EntityMatcher) -> dict[str, Any]:
    assembly_config = config.get("assembly", {})
    current_legislature = str(assembly_config.get("currentLegislature") or "")
    government_periods = config.get("governmentPeriods", [])
    parties = [
        {"id": entity.id, "name": entity.name, "kind": entity.kind}
        for entity in matcher.entities
        if entity.kind in {"party", "coalition"}
    ]
    articles = sorted(
        (public_article(item) for item in state["articles"].values()),
        key=lambda item: item.get("publishedAt") or "",
        reverse=True,
    )
    initiatives_by_id = {str(item.get("id") or ""): item for item in state["initiatives"].values()}
    votes_by_id = {str(item.get("id") or ""): item for item in state["votes"].values()}
    promises = sorted(
        (
            public_promise(
                item,
                promise_vote_outcomes(item, initiatives_by_id, votes_by_id),
            )
            for item in state["promises"].values()
        ),
        key=lambda item: (str(item.get("party") or ""), str(item.get("statement") or "")),
    )
    initiatives = sorted(
        (public_initiative(item) for item in state["initiatives"].values()),
        key=lambda item: (str(item.get("legislature") or ""), str(item.get("submittedAt") or "")),
        reverse=True,
    )
    votes = sorted(
        (public_vote(item) for item in deduplicated_votes(state["votes"].values())),
        key=lambda item: (str(item.get("date") or ""), str(item.get("id") or "")),
        reverse=True,
    )
    legislatures = sorted(
        {str(item.get("legislature")) for item in initiatives if item.get("legislature")},
        key=lambda value: ALL_LEGISLATURES.index(value) if value in ALL_LEGISLATURES else len(ALL_LEGISLATURES),
    )
    return {
        "schemaVersion": 1,
        "generatedAt": iso_now(),
        "currentLegislature": current_legislature,
        "parties": sorted(parties, key=lambda item: item["name"]),
        "legislatures": legislatures,
        "notices": [
            "As correspondências entre promessas e iniciativas são sugestões automáticas e exigem revisão humana.",
            "As notícias são guardadas como excertos breves e atribuídos; consulte sempre a fonte original.",
            "Em votações, uma ausência não é uma abstenção e a falta de posição publicada não é inferida.",
            "Os períodos de governo só são desagregados quando estiverem configurados com datas verificadas.",
        ],
        "sources": {
            "assembly": assembly_config.get("sourceAttribution", "Dados abertos da Assembleia da República"),
            "news": "Fontes jornalísticas autorizadas pela respetiva configuração e robots.txt.",
        },
        "articles": articles,
        "promises": promises,
        "initiatives": initiatives,
        "votes": votes,
        "statistics": {
            "allTime": vote_statistics(state, matcher, government_periods=government_periods),
            "byLegislature": {
                legislature: vote_statistics(
                    state, matcher, legislature=legislature, government_periods=government_periods
                )
                for legislature in legislatures
            },
        },
        "lastRun": state.get("lastRun", {}),
    }


def memory_chunk(
    identifier: str,
    text_value: str,
    category: str,
    party: str,
    year: int | str | None,
    source_type: str,
    source_url: str = "",
) -> dict[str, Any]:
    return {
        "id": f"pi_{identifier}",
        "text": compact_text(text_value, 2_800),
        "page": 0,
        "party": party or "ASSEMBLEIA",
        "year": year or "",
        "category": category,
        "filename": f"political-intelligence/{identifier}.json",
        "rel_path": f"political-intelligence/{identifier}.json",
        "source_type": source_type,
        "source_url": source_url,
    }


def build_memory_chunks(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Create RAG chunks from concise, attributed facts rather than raw pages."""

    chunks: list[dict[str, Any]] = []
    for article in state["articles"].values():
        entities = ", ".join(entity.get("name", "") for entity in article.get("entities", []))
        party = next(
            (
                entity.get("id", "")
                for entity in article.get("entities", [])
                if entity.get("kind") in {"party", "coalition"}
            ),
            "GOVERNO" if any(entity.get("id") == "GOVERNO" for entity in article.get("entities", [])) else "",
        )
        chunks.append(
            memory_chunk(
                str(article["id"]),
                " ".join(
                    part
                    for part in (
                        f"[Notícia — {article.get('source', 'fonte jornalística')}] {article.get('title', '')}.",
                        article.get("summary") or article.get("excerpt") or "",
                        f"Entidades mencionadas: {entities}." if entities else "",
                        f"Publicada em {article.get('publishedAt', '')}." if article.get("publishedAt") else "",
                        f"Fonte: {article.get('url', '')}.",
                    )
                    if part
                ),
                "Notícias de política e economia",
                str(party),
                str(article.get("publishedAt") or "")[:4],
                "news",
                str(article.get("url") or ""),
            )
        )
    for promise in state["promises"].values():
        matches = promise.get("proposalMatches", [])
        proposals = "; ".join(
            f"{match.get('number') or 'iniciativa'}: {match.get('title') or ''} ({match.get('matchKind')}, revisão necessária)"
            for match in matches
        )
        source = promise.get("source", {})
        origin = str(promise.get("origin") or "")
        contest = str(source.get("contest") or "")
        if origin == "declaracao_principios":
            category = "Promessas — declarações de princípios"
            chunk_source_type = "promise_declaration"
            header = f"[Promessa — declaração de princípios{' ' + contest if contest else ''}] {promise.get('party')}:"
        elif origin == "noticia":
            category = "Promessas em notícias"
            chunk_source_type = "promise_news"
            header = f"[Promessa em notícia] {promise.get('party')}:"
        else:
            category = "Promessas — programas eleitorais"
            chunk_source_type = "promise_programme"
            where = f" {contest}" if contest else ""
            header = f"[Promessa — programa eleitoral{where}] {promise.get('party')}:"
        chunks.append(
            memory_chunk(
                str(promise["id"]),
                " ".join(
                    part
                    for part in (
                        f"{header} {promise.get('statement')}.",
                        f"Propostas relacionadas automaticamente: {proposals}." if proposals else "",
                        f"Fonte: {source.get('title') or source.get('filename') or ''} {source.get('url') or ''}".strip(),
                    )
                    if part
                ),
                category,
                str(promise.get("party") or ""),
                source.get("year") or str(source.get("publishedAt") or "")[:4],
                chunk_source_type,
                str(source.get("url") or ""),
            )
        )
    for initiative in state["initiatives"].values():
        chunks.append(
            memory_chunk(
                str(initiative["id"]),
                " ".join(
                    part
                    for part in (
                        f"[Iniciativa da Assembleia — {initiative.get('legislature', '')}] {initiative.get('number') or ''} {initiative.get('type') or ''}: {initiative.get('title') or ''}.",
                        f"Autores: {', '.join(initiative.get('authors', []))}." if initiative.get("authors") else "",
                        f"Estado: {initiative.get('status')}" if initiative.get("status") else "",
                        f"Fonte oficial: {initiative.get('sourceUrl')}" if initiative.get("sourceUrl") else "",
                    )
                    if part
                ),
                "Iniciativas parlamentares",
                ",".join(initiative.get("authors", [])) or "ASSEMBLEIA",
                initiative.get("legislature"),
                "assembly_initiative",
                str(initiative.get("sourceUrl") or ""),
            )
        )
    for vote in deduplicated_votes(state["votes"].values()):
        positions = "; ".join(
            f"{position.get('party')}: {position.get('position')}"
            for position in vote.get("positions", [])
        )
        chunks.append(
            memory_chunk(
                str(vote["id"]),
                " ".join(
                    part
                    for part in (
                        f"[Votação da Assembleia — {vote.get('legislature', '')}] {vote.get('subject') or ''}.",
                        f"Resultado: {vote.get('result')}" if vote.get("result") else "",
                        f"Posições observadas: {positions}." if positions else "",
                        f"Fonte oficial: {vote.get('sourceUrl')}" if vote.get("sourceUrl") else "",
                    )
                    if part
                ),
                "Votações parlamentares",
                "ASSEMBLEIA",
                str(vote.get("date") or "")[:4] or vote.get("legislature"),
                "assembly_vote",
                str(vote.get("sourceUrl") or ""),
            )
        )
    return sorted(chunks, key=lambda item: item["id"])


def ensure_state(payload: Any) -> dict[str, Any]:
    base = initial_state()
    if not isinstance(payload, Mapping):
        return base
    state = dict(payload)
    for key, fallback in base.items():
        if fallback is None:
            state.setdefault(key, None)
        elif key not in state or not isinstance(state[key], type(fallback)):
            state[key] = fallback
    state["schemaVersion"] = 1
    state.setdefault("assembly", {}).setdefault("resourceSnapshots", {})
    return state


def export_outputs(state: Mapping[str, Any], config: Mapping[str, Any], matcher: EntityMatcher, public_path: Path, memory_path: Path) -> dict[str, int]:
    dataset = build_public_dataset(state, config, matcher)
    chunks = build_memory_chunks(state)
    json_save(public_path, dataset)
    json_save(memory_path, chunks)
    return {
        "articles": len(dataset["articles"]),
        "promises": len(dataset["promises"]),
        "initiatives": len(dataset["initiatives"]),
        "votes": len(dataset["votes"]),
        "memoryChunks": len(chunks),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Atualiza notícias políticas/económicas, promessas e votações do Politómetro."
    )
    parser.add_argument(
        "command",
        type=str.lower,
        choices=("news", "assembly", "eu", "all", "export"),
        help="Comando a executar (news, assembly, all, export).",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--entities", type=Path, default=DEFAULT_ENTITIES)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--public-output", type=Path, default=DEFAULT_PUBLIC_OUTPUT)
    parser.add_argument("--memory-output", type=Path, default=DEFAULT_MEMORY_OUTPUT)
    parser.add_argument(
        "--since-days",
        type=int,
        default=0,
        help="Dias a recolher; 0 (predefinição) recolhe todo o histórico permitido.",
    )
    parser.add_argument("--max-urls-per-source", type=int, default=None)
    parser.add_argument(
        "--sources",
        type=str,
        default=None,
        help="Recolhe apenas as fontes indicadas, separadas por vírgulas (ex.: publico,tsf).",
    )
    def parse_detail_limit(value: str) -> int | None:
        if value.strip().casefold() in {"all", "unlimited", "sem-limite"}:
            return None
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("use um número não negativo ou 'all'") from exc
        if parsed < 0:
            raise argparse.ArgumentTypeError("use um número não negativo ou 'all'")
        return parsed

    parser.add_argument(
        "--max-detail-pages",
        type=parse_detail_limit,
        default=None,
        help="Opção de compatibilidade; os votos são lidos diretamente dos dados oficiais em JSON.",
    )
    parser.add_argument(
        "--legislatures",
        nargs="+",
        default=None,
        help="Legislaturas a atualizar, por exemplo XVII XVI.",
    )
    parser.add_argument("--all-history", action="store_true", help="Inclui as legislaturas II a XVII.")
    parser.add_argument("--force-assembly", action="store_true", help="Ignora o intervalo de atualização da Assembleia.")
    parser.add_argument("--no-programme-promises", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Executa recolha e resumo sem escrever ficheiros.")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def chosen_legislatures(args: argparse.Namespace, config: Mapping[str, Any]) -> list[str]:
    if args.all_history:
        return list(ALL_LEGISLATURES)
    values = args.legislatures or [config.get("assembly", {}).get("currentLegislature", "XVII")]
    result: list[str] = []
    for value in values:
        legislature = str(value or "").upper().strip()
        if legislature and legislature not in result:
            result.append(legislature)
    return result or ["XVII"]


def print_summary_table(
    state: Mapping[str, Any],
    args: argparse.Namespace,
    *,
    interrupted: bool = False,
    dry_run: bool = False,
) -> None:
    art_count = f"{len(state.get('articles', {})):,}".replace(",", ".")
    prom_count = f"{len(state.get('promises', {})):,}".replace(",", ".")
    ini_count = f"{len(state.get('initiatives', {})):,}".replace(",", ".")
    vote_count = f"{len(state.get('votes', {})):,}".replace(",", ".")

    if interrupted:
        header_text = "⚠️  POLITÓMETRO — EXECUÇÃO INTERROMPIDA (ESTADO GUARDADO)"
    elif dry_run:
        header_text = "✨  POLITÓMETRO — RESUMO DA EXECUÇÃO (MODO DRY-RUN)"
    else:
        header_text = "✨  POLITÓMETRO — RESUMO GERAL DOS DADOS GUARDADOS"

    safe_print()
    safe_print("==============================================================================")
    safe_print(f"  {header_text}")
    safe_print("==============================================================================")
    safe_print()
    safe_print(f"  📰 Notícias políticas na base de dados: {art_count:>10} artigos")
    safe_print(f"  📋 Promessas eleitorais acumuladas:     {prom_count:>10} promessas")
    safe_print(f"  🏛️  Iniciativas parlamentares:           {ini_count:>10} iniciativas")
    safe_print(f"  🗳️  Votações registadas:                {vote_count:>10} votações")
    if interrupted:
        progressos = []
        for sid, sd in (state.get("sources") or {}).items():
            if not isinstance(sd, Mapping):
                continue
            run = sd.get("lastRun")
            if not isinstance(run, Mapping):
                continue
            cand = int(run.get("candidates") or 0)
            coll = int(run.get("collected") or 0)
            if cand or coll:
                progressos.append((str(run.get("name") or sid), coll, cand, str(run.get("status") or "")))
        if progressos:
            safe_print()
            safe_print("  📊 Progresso desta execução por fonte:")
            for nome, coll, cand, status in progressos:
                marca = {"ok": "✅", "partial": "⚠️ "}.get(status, "⏳ ")
                safe_print(f"    {marca} {nome}: {coll} guardadas / {cand} analisadas")
    if not interrupted:
        chunks = len(build_memory_chunks(state))
        chunk_count = f"{chunks:,}".replace(",", ".")
        safe_print(f"  🧠 Fragmentos de memória (chatbot):     {chunk_count:>10} chunks")
    safe_print()
    if not dry_run and not interrupted:
        safe_print("  📁 Ficheiros de saída gerados:")
        safe_print(f"     • Base de dados local:  {args.state}")
        safe_print(f"     • Painel web público:   {args.public_output}")
        safe_print(f"     • Memória do chatbot:   {args.memory_output}")
        safe_print()
    safe_print("==============================================================================")
    safe_print()


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    config = json_load(args.config, {})
    entities = json_load(args.entities, {})
    if not isinstance(config, Mapping) or not isinstance(entities, Mapping):
        raise PipelineError("A configuração ou a lista de entidades não contém um objeto JSON válido.")
    print_collection_banner(config)
    state = ensure_state(json_load(args.state, initial_state()))
    matcher = EntityMatcher(entities)
    client = HttpClient(config.get("crawl", {}))
    removed_seen_entries = prune_stale_seen(state) if config.get("pruneStaleSeenEntries", True) else 0
    if removed_seen_entries:
        safe_print(
            f"🧹 {removed_seen_entries} entradas obsoletas de URLs rejeitados por versões antigas do filtro foram removidas."
        )
    cleanup_stale_tmp_files(args.state, args.public_output, args.memory_output)
    result: dict[str, Any] = {
        "command": args.command,
        "news": [],
        "assembly": [],
        "promises": {},
        "articlesRemovedAsIrrelevant": 0,
    }

    crawl_settings = config.get("crawl", {})
    try:
        checkpoint_interval = max(
            5.0, float(crawl_settings.get("checkpointIntervalSeconds", 60) or 60)
        )
    except (TypeError, ValueError):
        checkpoint_interval = 60.0
    checkpoint = CheckpointManager(
        args.state, state, interval_seconds=checkpoint_interval, dry_run=args.dry_run
    )

    try:
        if args.command in {"news", "all"}:
            safe_print("\n📰 A iniciar recolha de notícias dos órgãos de comunicação social...")
            result["news"] = sync_news(
                state,
                config,
                matcher,
                client,
                since_days=max(0, args.since_days),
                max_urls_override=args.max_urls_per_source,
                checkpoint=checkpoint,
                source_filter=(
                    [item for item in str(args.sources or "").replace(";", ",").split(",") if item.strip()]
                    if args.sources
                    else None
                ),
            )
            total_collected = sum(int(item.get("collected") or 0) for item in result["news"])
            safe_print(
                f"\n📰 Recolha de notícias concluída: {total_collected} artigos guardados em {len(result['news'])} fontes."
            )
        if _STOP_REQUESTED.is_set():
            raise KeyboardInterrupt
        if args.command in {"assembly", "all"}:
            safe_print("\n🏛️  A iniciar recolha dos dados da Assembleia da República...")
            result["assembly"] = sync_assembly(
                state,
                config,
                matcher,
                client,
                chosen_legislatures(args, config),
                force=args.force_assembly,
                max_detail_pages=args.max_detail_pages,
                checkpoint=checkpoint,
            )
            safe_print("\n🏛️  Recolha da Assembleia concluída.")

        if _STOP_REQUESTED.is_set():
            raise KeyboardInterrupt
        if args.command in {"assembly", "all"}:
            safe_print("\n🇵🇹 A verificar promulgações e vetos da Presidência da República...")
            result["presidential"] = sync_presidential_actions(state, config, client, checkpoint)
            safe_print(
                f"   ↳ {result['presidential']['promulgadas']} promulgações e "
                f"{result['presidential']['vetos']} vetos conhecidos"
                + (f" ({result['presidential']['note']})" if result["presidential"].get("note") else "")
            )
        if _STOP_REQUESTED.is_set():
            raise KeyboardInterrupt
        if args.command in {"assembly", "all", "eu"}:
            safe_print("\n🇪🇺 A recolher iniciativas do Parlamento Europeu (procedimentos)...")
            result["europeanUnion"] = sync_european_initiatives(state, config, client, checkpoint)
            eu_note = result["europeanUnion"].get("note")
            safe_print(
                f"   ↳ {result['europeanUnion']['known']} dossiês UE conhecidos "
                f"({result['europeanUnion']['novos']} novos, {result['europeanUnion']['atualizados']} atualizados)"
                + (f" — {eu_note}" if eu_note else "")
            )

        safe_print("\n🧹 A validar relevância e a filtrar notícias...")
        result["articlesRemovedAsIrrelevant"] = prune_irrelevant_articles(state, matcher)
        safe_print(
            f"   ↳ {result['articlesRemovedAsIrrelevant']} artigos irrelevantes ou não políticos removidos."
        )
        if args.command != "export":
            if not args.no_programme_promises:
                safe_print("\n📋 A extrair promessas dos programas eleitorais...")
                result["promises"]["fromProgrammes"] = rebuild_programme_promises(state, matcher)
                safe_print(
                    f"   ↳ {result['promises']['fromProgrammes']} promessas dos programas eleitorais extraídas."
                )
        safe_print("\n📋 A extrair promessas a partir das notícias...")
        result["promises"]["fromNews"] = rebuild_news_promises(state, matcher)
        safe_print(
            f"   ↳ {result['promises']['fromNews']} promessas extraídas das notícias."
        )
        result["promises"]["proposalMatches"] = rebuild_promise_matches(state)
        if state.get("euInitiatives"):
            result["promises"]["europeanMatches"] = rebuild_promise_european_matches(state)
        state["updatedAt"] = iso_now()
        state["lastRun"] = {
            "at": iso_now(),
            "command": args.command,
            "news": result["news"],
            "assembly": result["assembly"],
            "presidential": result.get("presidential"),
            "europeanUnion": result.get("europeanUnion"),
        }

        if args.dry_run:
            result["export"] = {
                "articles": len(state["articles"]),
                "promises": len(state["promises"]),
                "initiatives": len(state["initiatives"]),
                "votes": len(state["votes"]),
                "memoryChunks": len(build_memory_chunks(state)),
                "dryRun": True,
            }
            print_summary_table(state, args, dry_run=True)
            return result

        safe_print("\n💾 A guardar o estado atualizado e a exportar dados...")
        checkpoint.mark_dirty()
        checkpoint(force=True)
        result["export"] = export_outputs(
            state, config, matcher, args.public_output, args.memory_output
        )
        print_summary_table(state, args)
        return result

    except KeyboardInterrupt:
        try:
            if not args.dry_run:
                checkpoint.mark_dirty()
                checkpoint(force=True, quiet=True)
        except BaseException:
            pass
        try:
            print_summary_table(state, args, interrupted=True)
        except BaseException:
            pass
        raise


def main(argv: Sequence[str] | None = None) -> int:
    configure_console_output()
    args = parse_args(argv)
    for name in ("urllib3", "requests", "charset_normalizer", "chardet"):
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    for name in ("urllib3", "requests", "charset_normalizer", "chardet"):
        logging.getLogger(name).setLevel(logging.WARNING)

    # Ctrl+C cooperativo: 1.º sinal pede paragem limpa (guarda estado + resumo);
    # 2.º sinal força a saída imediata com KeyboardInterrupt real.
    def _pedir_paragem(signum: Any, frame: Any) -> None:
        if _STOP_REQUESTED.is_set():
            raise KeyboardInterrupt
        _STOP_REQUESTED.set()
        safe_print("\n⚠️  Interrupção pedida — a terminar a fonte atual (Ctrl+C de novo força já)…")

    for sig_name in ("SIGINT", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, _pedir_paragem)
            except (ValueError, OSError):
                pass

    try:
        result = run_pipeline(args)
    except PipelineError as exc:
        safe_print(f"\n❌ Erro no pipeline: {exc}")
        return 2
    except KeyboardInterrupt:
        return 130
    if args.verbose:
        safe_print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


atexit.register(fechar_navegador)

if __name__ == "__main__":
    raise SystemExit(main())
