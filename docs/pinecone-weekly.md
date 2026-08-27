# Recolha semanal e Pinecone

O workflow `.github/workflows/sync_political_intelligence.yml` executa aos
sábados às 11:00 no fuso `Europe/Lisbon`. A recolha gera temporariamente os
corpora dos programas/orçamentos e depois atualiza notícias, promessas,
iniciativas e votações. O estado grande é restaurado/guardado na cache do
GitHub Actions; não é commitado.

## Configuração no GitHub

Em `Settings > Secrets and variables > Actions` configurar:

- Secret `PINECONE_API_KEY`;
- Variable `PINECONE_INDEX_NAME` com `politometro` (ou o nome real do índice).

O índice deve ser denso, com 1024 dimensões, métrica `cosine`, compatível com
`multilingual-e5-large`. O corpus base vai para o namespace predefinido; os
factos atuais vão para `political-intelligence`, que é o namespace consultado
pela API do chat.

## Incrementalidade

`scripts/upload_pinecone.py` guarda em `scripts/pinecone_upload_state.json` um
SHA-256 por `namespace/id`. Um chunk igual é ignorado; um chunk novo ou alterado
é novamente embebido e feito `upsert` com o mesmo ID. O tracking é pequeno e é
versionado para sobreviver às execuções.

O upload inclui:

- `extracted_chunks.json` e `extracted_chunks_ocr.json` — programas, votos e
  documentos portugueses extraídos;
- `extracted_chunks_eu_budget.json` — orçamentos e documentação europeia;
- `extracted_chunks_political_intelligence.json` (ou os seus shards) — notícias,
  promessas, iniciativas e votações políticas/europeias.

## Limite do plano Free/Starter

O workflow semanal força `PINECONE_EMBEDDINGS=local` e usa
`intfloat/multilingual-e5-large` no runner. Portanto, os novos chunks não
consomem a quota de embeddings hospedados pelo Pinecone. A primeira carga de
todo o histórico deve ser feita no workflow manual, também com `local`, podendo
usar `limit` para repartir o trabalho.

Só escolher `pinecone` no workflow manual se quiser consumir a quota mensal.
Nesse modo o script trava antes de ultrapassar o orçamento local de 4,5 milhões
de tokens estimados, deixando margem para a quota oficial.

Para inicializar um estado GitHub vazio com o histórico que está apenas no
computador, execute primeiro o workflow de sincronização manualmente com
`all_history=true` e `upload_pinecone=false`. Isto cria a cache de estado. Depois
use o workflow `Reindexar corpus no Pinecone (manual)` com embeddings `local` e,
se necessário, `limit` em várias execuções. A partir daí, o workflow semanal
envia apenas os novos/alterados.

## Execução local

Com `PINECONE_API_KEY` definida e `sentence-transformers` instalado:

```text
executar_recolha.cmd
```

O comando extrai os PDFs, atualiza o estado e faz apenas o upload incremental
com embeddings locais. Sem a variável da API, termina a recolha e avisa que o
upload foi omitido.

Para o primeiro backfill sem voltar a executar toda a recolha, use:

```text
executar_upload_pinecone_local.cmd
```

Pode repartir o carregamento por lotes, por exemplo com
`executar_upload_pinecone_local.cmd 5000`. Depois de cada lote, mantenha
`scripts/pinecone_upload_state.json`: é o único ficheiro do upload que deve ser
versionado. Os JSON de chunks continuam locais e ignorados pelo Git.
