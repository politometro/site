-- Esquema Turso / libSQL para o armazenamento do texto dos chunks do Politómetro.
-- O Pinecone guarda apenas os vetores (dentro do limite de 2 GB do escalão free);
-- o texto integral e os metadados estruturados ficam aqui e são unidos pelo `id`
-- do chunk na aplicação de chat.

CREATE TABLE IF NOT EXISTS chunks (
  id           TEXT    PRIMARY KEY,
  namespace    TEXT    NOT NULL,
  text         TEXT    NOT NULL,
  page         INTEGER,
  party        TEXT,
  year         TEXT,
  category     TEXT,
  filename     TEXT,
  source_url   TEXT,
  source_type  TEXT,
  embedding_model TEXT
);

CREATE INDEX IF NOT EXISTS idx_chunks_namespace ON chunks (namespace);
