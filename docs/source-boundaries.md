# Limites verificados dos sitemaps

`sitemapDateFloor` indica o início verificado do arquivo datado que a fonte
publica no sitemap. Não indica a fundação do jornal, nem pretende afirmar que
a fonte não publicou notícias antes dessa data.

Os pisos configurados são:

- CMTV: `2024-03-01`.
- SIC Notícias: `2022-08-01`.
- Expresso: `2022-08-01`.

Nos índices mensais da SIC Notícias e do Expresso, apenas são aceites filhos
com o formato `/sitemap/YYYY-M.xml` ou `/sitemap/YYYY-MM.xml`. Os seeds
`/sitemap/news.xml` continuam ativos para conservar notícias recentes.

O Público usa o seu arquivo mensal explícito, com
`archiveSitemap.firstAvailableMonth` em `1998-01` e o mesmo valor em
`sitemapDateFloor`. Esse limite é aplicado antes de qualquer pedido HTTP e tem
o significado de início verificado do arquivo, não de antiguidade da publicação.

Qualquer endereço datado anterior ao piso da fonte — mesmo que apareça por
engano num índice ou seja fornecido como seed — é descartado antes de chegar ao
cliente HTTP. Datas futuras são igualmente descartadas. As outras fontes não
geram tentativas dia a dia: só são seguidos os endereços que os próprios índices
publicam, depois dos filtros de tipo abaixo. Assim, não são inventados dias ou
anos para essas fontes.

Os filtros de filhos do Observador aceitam apenas os tipos `post`,
`obs_longform`, `obs_liveblog` e `obs_factcheck`. Os filtros do ECO aceitam
`posttype-post`, `explainer`, `news_report`, `special_article` e `interview`.
Como são listas de inclusão, taxonomias, utilizadores, páginas e os restantes
tipos não são pedidos.

Não são inventados pisos para fontes que não publicam arquivos datados
gerados. Os limites globais (`max*` e retenção) permanecem sem limite
(`null`). O JN continua desativado e a RTP usa apenas o sitemap de notícias e
o feed configurados.
