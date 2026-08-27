# Promessas, notícias e votações

O pipeline `scripts/political_intelligence.py` mantém três tipos de evidência separados:

- programas eleitorais e promessas candidatas;
- excertos breves, atribuídos e permitidos de notícias de política/economia;
- iniciativas e votações dos dados abertos da Assembleia da República.

As relações entre uma promessa e uma iniciativa são sempre sugestões para revisão. Uma proposta ou uma votação favorável nunca é apresentada como prova automática de que uma promessa foi cumprida.

## Atualização

Para iniciar uma recolha completa no Windows, sem limites de dias, URLs ou páginas detalhadas, abra na raiz do projeto:

```text
executar_recolha.cmd
```

O comando inclui todas as legislaturas configuradas, guarda checkpoints e, se
`PINECONE_API_KEY` estiver definida, envia depois apenas os chunks novos ou
alterados com embeddings locais.

Para atualizar as fontes recentes e a legislatura atual:

```powershell
python scripts/political_intelligence.py all
```

Para carregar todas as legislaturas históricas, uma vez ou numa execução manual:

```powershell
python scripts/political_intelligence.py assembly --all-history --force-assembly --max-detail-pages all
```

Os resultados locais são:

- `data/political_intelligence_state.json` — estado incremental e evidência normalizada;
- `website/public/political-intelligence.json` — manifesto do quadro público;
- `website/public/political-intelligence-shards/` — partes abaixo de 45 MB carregadas pelo site;
- `scripts/extracted_chunks_political_intelligence.json` — manifesto do corpus curto;
- `scripts/extracted_chunks_political_intelligence-shards/` — partes do corpus usadas pelo upload.

O estado da recolha e os corpora dos PDFs são artefactos regeneráveis e não são
commitados no Git; no GitHub Actions, o estado é mantido na cache entre
execuções.

## Memória do bot

O upload completo e incremental é feito por:

```powershell
python scripts/upload_pinecone.py --dry-run
python scripts/upload_pinecone.py --embedding-mode local
```

O namespace padrão recebe os programas/orçamentos extraídos; o namespace
`political-intelligence` recebe notícias, promessas, iniciativas e votações.
O tracking fica em `scripts/pinecone_upload_state.json`.

## Fontes e regras

As fontes são configuradas em `data/political_intelligence_config.json` e as entidades em `data/political_entities.json`. O coletor:

- identifica-se com um `User-Agent`, respeita `robots.txt`, aplica atraso entre pedidos e falha de forma conservadora quando não consegue confirmar as regras;
- não aplica quota por fonte, profundidade fixa de sitemap ou prazo de retenção: recolhe todas as entradas que a fonte permitir e mantém o histórico;
- quando uma fonte publica um arquivo por mês, gera apenas os meses dentro do intervalo confirmado, evita o mês corrente ainda incompleto e guarda meses/anos indisponíveis para não os voltar a pedir; por exemplo, o Público expõe `https://www.publico.pt/sitemaps/articles/{ano}-{mês}.xml` (mês sem zero à esquerda);
- guarda só título, resumo/excerto limitado, data, entidades e ligação à fonte; não arquiva artigos completos;
- mantém o Jornal de Notícias desativado até existir autorização escrita, porque as regras publicadas pela fonte não permitem recolha automática;
- redescobre em cada execução os URLs temporários dos dados abertos da Assembleia, em vez de guardar links assinados que expiram.

Para resumos por governo com um nome de governo específico, preencha `governmentPeriods` na configuração com períodos verificados (`id`, `name`, `start`, `end`). Sem esse mapeamento, o quadro só agrega propostas atribuídas genericamente ao Governo, sem inventar períodos históricos.
