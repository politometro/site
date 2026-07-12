const fs = require('fs');
const path = require('path');

function parseEnvLocal() {
  const filepath = path.join(__dirname, '.env.local');
  const vars = {};
  if (!fs.existsSync(filepath)) {
    console.log("No .env.local file found in website folder!");
    return vars;
  }
  const content = fs.readFileSync(filepath, 'utf8');
  content.split('\n').forEach(line => {
    line = line.trim();
    if (!line || line.startsWith('#')) return;
    const parts = line.split('=');
    if (parts.length >= 2) {
      const key = parts[0].trim();
      const val = parts.slice(1).join('=').trim();
      vars[key] = val;
    }
  });
  return vars;
}

async function testConnection() {
  const vars = parseEnvLocal();
  const pineconeKey = vars.PINECONE_API_KEY;
  const pineconeIndex = vars.PINECONE_INDEX_NAME || "politometro";
  const hfToken = vars.HF_TOKEN;
  const groqKey = vars.GROQ_API_KEY;

  console.log("\n=== DIAGNÓSTICO DE CONEXÃO NODE.JS ===");
  console.log("Pinecone Index Name:", pineconeIndex);
  console.log("Pinecone Key (truncada):", pineconeKey ? pineconeKey.slice(0, 10) + "..." : "Não configurada");
  console.log("Hugging Face Token (truncado):", hfToken ? hfToken.slice(0, 10) + "..." : "Não configurado");
  console.log("Groq Key (truncada):", groqKey ? groqKey.slice(0, 10) + "..." : "Não configurada");

  if (!pineconeKey) {
    console.error("[ERRO] PINECONE_API_KEY em falta no .env.local!");
  }
  if (!hfToken) {
    console.error("[ERRO] HF_TOKEN em falta no .env.local!");
  }

  // 1. Test Pinecone API index check
  console.log("\n1. A testar ligação ao API do Pinecone...");
  try {
    const res = await fetch(`https://api.pinecone.io/indexes/${pineconeIndex}`, {
      headers: { "Api-Key": pineconeKey }
    });
    console.log("  Status Code:", res.status);
    const body = await res.text();
    if (res.ok) {
      const data = JSON.parse(body);
      console.log("  [SUCESSO] Conectado ao Pinecone!");
      console.log("  Host do Índice:", data.host);
      console.log("  Status do Índice:", data.status?.state);
    } else {
      console.error("  [ERRO] Detalhes do Pinecone:", body);
    }
  } catch (err) {
    console.error("  [FALHA] Não foi possível contactar o Pinecone:", err.message || err);
  }

  // 2. Test Hugging Face Inference API
  console.log("\n2. A testar ligação ao Hugging Face (Feature Extraction)...");
  try {
    const res = await fetch("https://router.huggingface.co/hf-inference/models/intfloat/multilingual-e5-large", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${hfToken}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        inputs: "query: teste de conexao"
      })
    });
    console.log("  Status Code:", res.status);
    const body = await res.text();
    if (res.ok) {
      const data = JSON.parse(body);
      if (Array.isArray(data)) {
        console.log("  [SUCESSO] Hugging Face respondeu corretamente!");
        console.log("  Dimensão do embedding retornado:", Array.isArray(data[0]) ? data[0].length : data.length);
      } else {
        console.log("  [AVISO] HF respondeu com formato inesperado:", body);
      }
    } else {
      console.error("  [ERRO] Detalhes do Hugging Face:", body);
    }
  } catch (err) {
    console.error("  [FALHA] Não foi possível contactar o Hugging Face:", err.message || err);
    if (err.cause) {
      console.error("  Causa detalhada:", err.cause.message || err.cause);
    }
  }

  // 3. Test Groq
  console.log("\n3. A testar ligação à Groq...");
  try {
    const res = await fetch("https://api.groq.com/openai/v1/chat/completions", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${groqKey}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        model: "llama-3.3-70b-versatile",
        messages: [{ role: "user", content: "Olá" }]
      })
    });
    console.log("  Status Code:", res.status);
    const body = await res.text();
    if (res.ok) {
      console.log("  [SUCESSO] Conectado à Groq!");
    } else {
      console.error("  [ERRO] Detalhes do Groq:", body);
    }
  } catch (err) {
    console.error("  [FALHA] Não foi possível contactar a Groq:", err.message || err);
  }
}

testConnection();
