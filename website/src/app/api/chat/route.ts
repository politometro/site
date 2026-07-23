import { NextRequest, NextResponse } from "next/server";

// Shared memory in the Node process to track daily limit exhaustions
// (Keys are model names, value is the timestamp when it can be retried)
const modelDailyExhaustionTimes: { [model: string]: number } = {};

// Shared memory to track request counts for rate limiting (100 requests per user per day)
const requestCounts: { [key: string]: { count: number; day: string } } = {};

function retrievalPlanFor(query: string) {
  const normalized = query
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
  const years = new Set(
    normalized.match(/\b(?:19|20)\d{2}\b/g) || []
  );
  const asksForEvolution =
    /\b(evolucao|ao longo dos anos|historico|mudou|mudanca|desde|entre anos|varios anos)\b/.test(
      normalized
    );
  const asksForComparison =
    /\b(compara|comparacao|diferenca|versus|vs)\b/.test(normalized);
  const asksForBroadOverview =
    /\b(o que sabes|o que sabe|fala-me|visao geral|panorama|em geral)\b/.test(
      normalized
    );
  const asksForExhaustiveCoverage =
    /\b(todas|todos|lista completa|exaustiv)\b/.test(normalized);
  const asksSpecificQuestion =
    /\b(qual|quais|como|quando|quanto|medida|proposta|posicao|defende|preve)\b/.test(
      normalized
    );

  let mode = "standard";
  let maxSources = 8;
  if (asksForEvolution || asksForComparison || years.size >= 2) {
    mode = "comparative";
    maxSources = 15;
  } else if (years.size === 1) {
    mode = "single-year";
    maxSources = 5;
  } else if (asksForExhaustiveCoverage) {
    mode = "broad";
    maxSources = 12;
  } else if (asksForBroadOverview) {
    mode = "overview";
    maxSources = 10;
  } else if (asksSpecificQuestion) {
    mode = "specific";
    maxSources = 6;
  }

  const contextSettings: Record<
    string,
    { total: number; perSource: number }
  > = {
    comparative: { total: 7500, perSource: 500 },
    "single-year": { total: 7500, perSource: 1500 },
    broad: { total: 12000, perSource: 1000 },
    overview: { total: 10000, perSource: 1000 },
    specific: { total: 9000, perSource: 1500 },
    standard: { total: 10000, perSource: 1250 },
  };
  const context = contextSettings[mode];

  return {
    mode,
    maxSources,
    candidateCount: Math.min(30, maxSources * 2),
    maxContextCharacters: context.total,
    maxCharactersPerSource: context.perSource,
  };
}

function sourceExcerpt(text: string, maxCharacters: number) {
  if (text.length <= maxCharacters) {
    return text;
  }
  const prefix = text.slice(0, maxCharacters).trimEnd();
  const sentenceEnd = Math.max(
    prefix.lastIndexOf(". "),
    prefix.lastIndexOf("? "),
    prefix.lastIndexOf("! ")
  );
  if (sentenceEnd >= Math.floor(maxCharacters * 0.55)) {
    return prefix.slice(0, sentenceEnd + 1).trim();
  }
  const wordEnd = prefix.lastIndexOf(" ");
  return prefix.slice(0, wordEnd > 0 ? wordEnd : prefix.length).trim();
}

export async function POST(req: NextRequest) {
  try {
    const { messages } = await req.json();
    
    const groqApiKey = process.env.GROQ_API_KEY;
    const groqModel = process.env.GROQ_MODEL || "llama-3.3-70b-versatile";
    const pineconeApiKey = process.env.PINECONE_API_KEY;
    const pineconeIndexName = process.env.PINECONE_INDEX_NAME || "politometro";

    if (!groqApiKey || !pineconeApiKey || !pineconeIndexName || groqApiKey.includes("your_actual")) {
      return NextResponse.json(
        { error: "Ainda não configurou as chaves de API secretas no ficheiro .env.local do servidor." },
        { status: 500 }
      );
    }

    // Rate limiting: 100 requests per user per day
    const clientId = req.headers.get("x-client-id") || "anonymous";
    const isTwitchClient = clientId.toLowerCase().startsWith("twitch-bot:");
    const ip = req.headers.get("x-forwarded-for")?.split(",")[0].trim() || req.headers.get("x-real-ip") || "unknown";
    const todayStr = new Date().toISOString().split("T")[0]; // YYYY-MM-DD

    if (clientId && clientId !== "anonymous") {
      const clientKey = `client:${clientId}`;
      const clientData = requestCounts[clientKey];
      if (clientData && clientData.day === todayStr) {
        if (clientData.count >= 100) {
          return NextResponse.json(
            { error: "Atingiu o limite de 100 perguntas diárias por utilizador. Por favor, tente novamente amanhã!" },
            { status: 429 }
          );
        }
        clientData.count += 1;
      } else {
        requestCounts[clientKey] = { count: 1, day: todayStr };
      }
    } else if (ip && ip !== "unknown") {
      const ipKey = `ip:${ip}`;
      const ipData = requestCounts[ipKey];
      if (ipData && ipData.day === todayStr) {
        if (ipData.count >= 100) {
          return NextResponse.json(
            { error: "Atingiu o limite de 100 perguntas diárias por utilizador. Por favor, tente novamente amanhã!" },
            { status: 429 }
          );
        }
        ipData.count += 1;
      } else {
        requestCounts[ipKey] = { count: 1, day: todayStr };
      }
    }

    // Get the last user message
    const userMessages = messages.filter((m: any) => m.role === "user");
    const lastUserMessage = userMessages[userMessages.length - 1]?.content || "";
    const retrievalPlan = retrievalPlanFor(lastUserMessage);

    let contextText = "";
    let retrievedSources: any[] = [];

    if (lastUserMessage) {
      try {
        // Step 1: Get index details (specifically the host url)
        const indexRes = await fetch(
          `https://api.pinecone.io/indexes/${encodeURIComponent(pineconeIndexName)}`,
          {
            headers: {
              "Api-Key": pineconeApiKey,
              "X-Pinecone-Api-Version": "2025-10",
            },
          }
        );

        if (!indexRes.ok) {
          throw new Error(
            `PINECONE_INDEX_LOOKUP_FAILED status=${indexRes.status}`
          );
        }

        const indexData = await indexRes.json();
        const indexHost = String(indexData.host || "")
          .replace(/^https?:\/\//i, "")
          .replace(/\/+$/, "");
        if (!indexHost) {
          throw new Error("PINECONE_INDEX_HOST_MISSING");
        }

        // Step 2: Generate a query vector with the same model used to build
        // the index. Prefer Hugging Face so Pinecone's inference quota does
        // not affect document search.
        let queryVector = null;
        let pineconeEmbedStatus = "not_attempted";
        let huggingFaceEmbedStatus = "not_configured";
        const hfToken = process.env.HF_TOKEN;

        if (hfToken) {
          try {
            const hfRes = await fetch("https://router.huggingface.co/hf-inference/models/intfloat/multilingual-e5-large", {
              method: "POST",
              headers: {
                "Authorization": `Bearer ${hfToken}`,
                "Content-Type": "application/json",
              },
              body: JSON.stringify({
                inputs: `query: ${lastUserMessage}`
              }),
            });

            if (hfRes.ok) {
              const hfData = await hfRes.json();
              if (Array.isArray(hfData)) {
                // Feature extraction might return a 2D array or 1D array
                queryVector = Array.isArray(hfData[0]) ? hfData[0] : hfData;
                huggingFaceEmbedStatus = queryVector
                  ? "ok"
                  : "invalid_response";
                console.log(
                  "Successfully generated query embedding using Hugging Face."
                );
              } else {
                huggingFaceEmbedStatus = "invalid_response";
                console.error("Hugging Face API returned non-array data:", hfData);
              }
            } else {
              huggingFaceEmbedStatus = `http_${hfRes.status}`;
              console.error(
                "Hugging Face embedding fallback failed:",
                hfRes.status
              );
            }
          } catch (err) {
            huggingFaceEmbedStatus = "request_failed";
            console.error("Hugging Face embedding fallback error:", err);
          }
        }

        // Pinecone inference remains a fallback for environments without a
        // working Hugging Face token.
        if (!queryVector) {
          try {
            const embedRes = await fetch("https://api.pinecone.io/embed", {
              method: "POST",
              headers: {
                "Api-Key": pineconeApiKey,
                "Content-Type": "application/json",
                "X-Pinecone-API-Version": "2025-10",
              },
              body: JSON.stringify({
                model: "multilingual-e5-large",
                inputs: [{ text: lastUserMessage }],
                parameters: { input_type: "query" },
              }),
            });

            if (embedRes.ok) {
              const embedData = await embedRes.json();
              queryVector = embedData.data?.[0]?.values;
              pineconeEmbedStatus = queryVector
                ? "ok"
                : "invalid_response";
            } else {
              pineconeEmbedStatus = `http_${embedRes.status}`;
            }
          } catch {
            pineconeEmbedStatus = "request_failed";
          }
        }

        if (!queryVector) {
          throw new Error(
            "EMBEDDING_FAILED " +
            `pinecone=${pineconeEmbedStatus} ` +
            `huggingface=${huggingFaceEmbedStatus}`
          );
        }

        if (queryVector) {
          // Determine if query is regional
          let filter: any = undefined;
          const lowerMessage = lastUserMessage.toLowerCase();
          const isRegionalQuery = 
            lowerMessage.includes("açores") || 
            lowerMessage.includes("açor") || 
            lowerMessage.includes("madeira") || 
            lowerMessage.includes("madeir") || 
            lowerMessage.includes("regional") || 
            lowerMessage.includes("regiões") || 
            lowerMessage.includes("região");

          if (!isRegionalQuery) {
            filter = {
              category: {
                $nin: ["Açores", "Madeira"]
              }
            };
          }

          // Step 3: Query Pinecone index using the vector
          const queryRes = await fetch(`https://${indexHost}/query`, {
            method: "POST",
            headers: {
              "Api-Key": pineconeApiKey,
              "Content-Type": "application/json",
              "X-Pinecone-Api-Version": "2025-10",
            },
            body: JSON.stringify({
              vector: queryVector,
              topK: retrievalPlan.candidateCount,
              includeMetadata: true,
              ...(filter ? { filter } : {})
            }),
          });

          if (!queryRes.ok) {
            throw new Error(
              `PINECONE_QUERY_FAILED status=${queryRes.status}`
            );
          }

          const queryData = await queryRes.json();
          const matches = queryData.matches || [];
          const maxSources = retrievalPlan.maxSources;
          const maxContextCharacters =
            retrievalPlan.maxContextCharacters;
          const seenSources = new Set<string>();
          const sourcesPerYear = new Map<string, number>();

          for (const match of matches) {
            const meta = match.metadata || {};
            const sourceText = String(meta.text || "").trim();
            if (!sourceText) {
              continue;
            }
            const numericYear = Number(meta.year);
            if (
              retrievalPlan.mode === "comparative" &&
              (!Number.isFinite(numericYear) || numericYear < 1975)
            ) {
              continue;
            }
            const openingText = sourceText.slice(0, 500);
            const looksLikeIndex =
              /\b[ií]ndice\b/i.test(openingText) &&
              (openingText.match(/\d/g) || []).length >= 12;
            const hasBrokenNumbering =
              /(?:\b\d+\.){5,}/.test(openingText);
            if (looksLikeIndex || hasBrokenNumbering) {
              continue;
            }
            const sourceKey = [
              meta.filename,
              meta.page,
            ].join("|");
            if (seenSources.has(sourceKey)) {
              continue;
            }
            const sourceYear = String(meta.year || "sem-ano");
            const yearCount = sourcesPerYear.get(sourceYear) || 0;
            if (
              retrievalPlan.mode === "comparative" &&
              yearCount >= 5
            ) {
              continue;
            }

            const excerpt = sourceExcerpt(
              sourceText,
              retrievalPlan.maxCharactersPerSource
            );
            const contextBlock =
              `\n--- Programa Eleitoral: ${meta.party}, ` +
              `${meta.category} ${meta.year} (Página ${meta.page}) ---\n` +
              `${excerpt}\n`;
            if (
              retrievedSources.length > 0 &&
              contextText.length + contextBlock.length >
                maxContextCharacters
            ) {
              continue;
            }

            seenSources.add(sourceKey);
            sourcesPerYear.set(sourceYear, yearCount + 1);
            retrievedSources.push({
              party: meta.party,
              year: meta.year,
              category: meta.category,
              filename: meta.filename,
              page: meta.page,
              score: match.score,
            });
            contextText += contextBlock;

            if (retrievedSources.length >= maxSources) {
              break;
            }
          }
        }
      } catch (err: any) {
        const ragError = String(err?.message || "RAG_FAILED")
          .replace(/[^\w= -]/g, "")
          .slice(0, 120);
        console.error("Erro RAG Pinecone:", ragError);
        return NextResponse.json(
          {
            error:
              "A pesquisa documental está temporariamente indisponível. " +
              "Tenta novamente dentro de instantes.",
          },
          {
            status: 503,
            headers: {
              "X-Rag-Status": "unavailable",
              "X-Rag-Error": ragError,
            },
          }
        );
      }
    }

    // Persona & Instructions
    const systemPrompt = `És um assistente especializado na análise de programas eleitorais de todos os partidos políticos portugueses entre 1975 e 2025. A tua única base de conhecimento são os documentos dos programas eleitorais disponibilizados, bem como, quando pertinente, a Constituição da República Portuguesa e os Orçamentos do Estado.

Tens acesso a uma vasta base documental indexada na tua base de dados (através do sistema de recuperação RAG), que inclui:
- Programas eleitorais para as eleições Legislativas de todos os partidos políticos portugueses desde 1975 até 2025.
- Programas para as eleições Regionais dos Açores (de 2000 a 2024) e da Madeira (de 2000 a 2025).
- Programas para as eleições Europeias de 1999 a 2024.
- Orçamentos do Estado de 1999 a 2026.
- Declarações de princípios dos partidos políticos.
- Constituição da República Portuguesa.

Explicação sobre o acesso aos documentos:
O teu acesso a esta base documental é feito através de pesquisa semântica (RAG). Isto significa que, para cada pergunta do utilizador, a base de dados recupera apenas os trechos mais relevantes. Se o utilizador perguntar a que ficheiros ou documentos tens acesso, não deves assumir que só tens acesso aos 4 ou 5 documentos cujos trechos foram incluídos no contexto atual. Pelo contrário, deves indicar a cobertura geral acima (Legislativas, Regionais, Europeias, Orçamentos de Estado, Declarações de Princípios e Constituição) e explicar de forma clara e amigável que utilizas um sistema de recuperação inteligente para consultar os trechos mais relevantes para a pergunta dele a partir dessa vasta biblioteca. Lembra-o também de que ele pode consultar a lista completa e detalhada de todos os documentos disponíveis na aba "Documentação" no menu superior do website.

Mantém sempre um tom sério, objetivo e informativo. Responde exclusivamente com base nos conteúdos dos programas eleitorais, dos Orçamentos do Estado e da Constituição, sem adicionar opiniões ou interpretações externas. Evita erros factuais e não inventes informação.
Todas as respostas devem ser redigidas em português de Portugal (pt-PT) exemplar, livre de erros ortográficos ou gramaticais (por exemplo, escreve sempre "não tem relação" ou "não tenha relação" em vez de "não ten").

Quando citares medidas ou posições de um partido, indica sempre a que ano/eleição pertencem e, se relevante, destaca se essa posição se manteve ou mudou ao longo dos anos. Realça a evolução das propostas e das prioridades dos partidos com exemplos concretos.
Sempre que for pertinente, articula os conteúdos dos programas eleitorais com artigos da Constituição Portuguesa, indicando o artigo específico e explicando a sua relevância no contexto.

Regras Estritas de Fidelidade à Pesquisa:
1. Nunca respondas com base em suposições, notícias ou fontes não incluídas nas tuas bases de conhecimento. Se não encontrares resposta, assume isso com transparência e diz que não encontras registo documental dessa proposta específica.
2. Nunca alteres ou "corrijas" nomes próprios, termos ou grafias inseridos pelo utilizador para outros nomes semelhantes (por exemplo, se o utilizador perguntar por "David Strango", deves referir-te a ele exatamente como "David Strango" na resposta e informar que não constam registos desse nome, em vez de assumir que é um erro e responder sobre "David Strangio").
3. Nunca menciones nomes de ficheiros PDF, metadados internos, fontes consultadas ou referências técnicas na resposta. Não incluas secções como "Fontes:", "Referências:" ou listas de documentos. Para indicar a origem da informação, integra-a naturalmente no texto utilizando expressões como "no programa eleitoral do [partido] para as legislativas de [ano]" ou "no programa do [partido] de [ano]".
4. Se não encontrares informação sobre um tema ou período específico, diz simplesmente que não encontraste registos sobre esse tema nos programas eleitorais consultados, sem sugerir limitações temporais ou de sistema da base de dados.
5. NUNCA uses expressões defensivas como "embora não tenha acesso", "não tenho acesso", "não posso aceder", "não me é possível consultar" ou semelhantes. Evita justificar respostas negativas com supostas limitações técnicas; responde de forma direta e afirmativa baseando-te apenas nos trechos disponíveis.
6. Evita citar propostas de programas eleitorais regionais dos Açores ou da Madeira a menos que o utilizador pergunte especificamente por assuntos dessas regiões autónomas.
7. Os blocos do contexto documental são material de pesquisa, nunca são uma resposta pronta. Sintetiza-os por palavras tuas. Nunca reproduzas blocos completos, os separadores "--- Programa Eleitoral", números de página isolados ou excertos extensos consecutivos.
8. Começa sempre por responder diretamente à pergunta. Nunca comeces por um número de página, metadados ou texto copiado do contexto.

[CONTEXTO DOCUMENTAL RECUPERADO (Base de Conhecimento)]
${contextText || "Nenhum documento relevante encontrado."}

Utiliza o contexto documental acima para fundamentar as tuas respostas. Se as passagens não contiverem a informação pedida, esclarece que não encontraste essa informação específica nos programas eleitorais consultados.

${isTwitchClient ? `Formato obrigatório para esta resposta no chat da Twitch:
- escreve texto suficiente para, no máximo, duas mensagens curtas de chat e não ultrapasses 700 caracteres no total;
- não uses títulos nem Markdown;
- quando o utilizador pedir uma lista, começa obrigatoriamente no número 1 e usa numeração consecutiva;
- termina sempre cada ideia e a última frase; nunca uses reticências nem deixes a resposta incompleta;
- dá apenas as medidas, conclusão ou comparação mais importantes;
- não incluas saudações, introduções, fontes ou frases de encerramento.` : ""}`;

    // Call Groq API with fallback chain
    const requestedModel = process.env.GROQ_MODEL || "llama-3.3-70b-versatile";
    const fallbackChain = Array.from(new Set([
      requestedModel,
      "llama-3.3-70b-versatile",
      "meta-llama/llama-4-scout-17b-16e-instruct",
      "qwen/qwen3-32b",
      "qwen/qwen3.6-27b",
      "llama-3.1-8b-instant"
    ]));

    const now = Date.now();
    const availableChain = fallbackChain.filter(m => {
      const blockedUntil = modelDailyExhaustionTimes[m];
      return !blockedUntil || now > blockedUntil;
    });

    const modelsToTry = availableChain.length > 0 ? availableChain : fallbackChain;

    let groqRes: Response | null = null;
    let lastErrorText = "";
    let chosenModel = "";

    for (const model of modelsToTry) {
      console.log(`[API CHAT] Trying model: ${model}`);
      try {
        groqRes = await fetch("https://api.groq.com/openai/v1/chat/completions", {
          method: "POST",
          headers: {
            Authorization: `Bearer ${groqApiKey}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            model: model,
            messages: [
              { role: "system", content: systemPrompt },
              ...messages,
            ],
            temperature: 0.15,
            max_completion_tokens: isTwitchClient ? 300 : 900,
            stream: true,
          }),
        });

        if (groqRes.ok) {
          chosenModel = model;
          console.log(`[API CHAT] Successfully initiated stream using model: ${chosenModel}`);
          break; // Success! Break the loop
        }

        // It failed, inspect the error
        const errText = await groqRes.clone().text(); // Use clone() so we can read it without locking body
        lastErrorText = errText;
        console.error(`[API CHAT] Model ${model} failed: ${groqRes.status} - ${errText}`);

        // Check if this was a daily limit (token or request per day limit)
        const errStr = errText.toLowerCase();
        const isDailyLimit = 
          errStr.includes("tokens_per_day") || 
          errStr.includes("requests_per_day") || 
          errStr.includes("daily") || 
          errStr.includes("tpd") || 
          errStr.includes("rpd") ||
          groqRes.status === 403; // Quota exceeded is sometimes 403 or 429 depending on API
          
        if (isDailyLimit) {
          // Blacklist the model for 12 hours
          modelDailyExhaustionTimes[model] = Date.now() + 12 * 60 * 60 * 1000;
          console.warn(`[API CHAT] Model ${model} blacklisted due to daily limit exhaustion.`);
        }
      } catch (fetchErr: any) {
        console.error(`[API CHAT] Fetch error trying model ${model}:`, fetchErr);
        lastErrorText = fetchErr.message || String(fetchErr);
      }
    }

    if (!groqRes || !groqRes.ok) {
      return NextResponse.json(
        { error: `Erro na API do Groq (todas as tentativas falharam): ${lastErrorText}` },
        { status: groqRes ? groqRes.status : 500 }
      );
    }

    // Set headers for SSE streaming
    const responseHeaders = new Headers({
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    });

    // If there are sources, we can append them in a header or as a special message chunk
    // We will inject the sources metadata in the stream or in custom headers
    responseHeaders.set("X-Sources", encodeURIComponent(JSON.stringify(retrievedSources)));
    responseHeaders.set("X-Retrieval-Mode", retrievalPlan.mode);
    responseHeaders.set(
      "X-Retrieval-Source-Limit",
      String(retrievalPlan.maxSources)
    );
    responseHeaders.set("X-Groq-Model", chosenModel);

    return new Response(groqRes.body, {
      headers: responseHeaders,
    });
  } catch (err: any) {
    console.error("Erro na API Chat:", err);
    return NextResponse.json(
      { error: `Erro interno no servidor: ${err.message}` },
      { status: 500 }
    );
  }
}
