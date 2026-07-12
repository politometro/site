import { NextRequest, NextResponse } from "next/server";


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

    // Get the last user message
    const userMessages = messages.filter((m: any) => m.role === "user");
    const lastUserMessage = userMessages[userMessages.length - 1]?.content || "";

    let contextText = "";
    let retrievedSources: any[] = [];

    if (lastUserMessage) {
      try {
        // Step 1: Get index details (specifically the host url)
        const indexRes = await fetch(
          `https://api.pinecone.io/indexes/${pineconeIndexName}`,
          {
            headers: {
              "Api-Key": pineconeApiKey,
            },
          }
        );

        if (!indexRes.ok) {
          throw new Error(`Falha ao obter índice Pinecone. Status: ${indexRes.status}`);
        }

        const indexData = await indexRes.json();
        const indexHost = indexData.host; // e.g. "https://politometro-123.svc.us-east1-gcp.pinecone.io"

        // Step 2: Embed the user query using Pinecone's inference service, with Hugging Face fallback
        let queryVector = null;
        let embedErrorMsg = "";
        const hfToken = process.env.HF_TOKEN;

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
          } else {
            const errBody = await embedRes.text();
            embedErrorMsg = `Pinecone status ${embedRes.status}: ${errBody}`;
          }
        } catch (err: any) {
          embedErrorMsg = err.message || String(err);
        }

        // Fallback to Hugging Face Inference API if Pinecone failed and HF_TOKEN is configured
        if (!queryVector && hfToken) {
          console.log("Pinecone embedding quota reached or failed. Trying Hugging Face fallback...");
          try {
            const hfRes = await fetch("https://api-inference.huggingface.co/models/intfloat/multilingual-e5-large", {
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
                console.log("Successfully generated query embedding using Hugging Face fallback!");
              } else {
                console.error("Hugging Face API returned non-array data:", hfData);
              }
            } else {
              const errText = await hfRes.text();
              console.error("Hugging Face embedding fallback failed:", hfRes.status, errText);
            }
          } catch (err) {
            console.error("Hugging Face embedding fallback error:", err);
          }
        }

        if (!queryVector) {
          throw new Error(`Falha ao gerar embeddings no Pinecone e Hugging Face. Detalhes: ${embedErrorMsg}`);
        }

        if (queryVector) {
          // Step 3: Query Pinecone index using the vector
          const queryRes = await fetch(`https://${indexHost}/query`, {
            method: "POST",
            headers: {
              "Api-Key": pineconeApiKey,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              vector: queryVector,
              topK: 6,
              includeMetadata: true,
            }),
          });

          if (queryRes.ok) {
            const queryData = await queryRes.json();
            const matches = queryData.matches || [];

            matches.forEach((match: any, idx: number) => {
              const meta = match.metadata || {};
              retrievedSources.push({
                party: meta.party,
                year: meta.year,
                category: meta.category,
                filename: meta.filename,
                page: meta.page,
                score: match.score,
              });

              contextText += `\n--- Programa Eleitoral: ${meta.party}, ${meta.category} ${meta.year} (Página ${meta.page}) ---\n${meta.text}\n`;
            });
          }
        }
      } catch (err: any) {
        console.error("Erro RAG Pinecone:", err);
        // We will fallback to calling LLM without context, but notify the LLM about it in a system prompt
        contextText = "[AVISO DE ERRO DE LIGAÇÃO: A base de dados documental de programas eleitorais está temporariamente inacessível. Por favor, inicia a resposta dizendo exatamente: 'Infelizmente, devido a um erro de ligação à base de dados de programas políticos, não me é possível consultar os documentos oficiais neste momento. Contudo, com base no conhecimento geral...' e depois responde com o teu conhecimento interno da melhor forma possível.]";
      }
    }

    // Persona & Instructions
    const systemPrompt = `És um assistente especializado na análise de programas eleitorais de todos os partidos políticos portugueses entre 1973 e 2025. A tua única base de conhecimento são os documentos dos programas eleitorais disponibilizados, bem como, quando pertinente, a Constituição da República Portuguesa.
Mantém sempre um tom sério, objetivo e informativo. Responde exclusivamente com base nos conteúdos dos programas eleitorais e da Constituição, sem adicionar opiniões ou interpretações externas. Evita erros factuais e não inventes informação.
Todas as respostas devem ser redigidas em português de Portugal (pt-PT) exemplar, livre de erros ortográficos ou gramaticais (por exemplo, escreve sempre "não tem relação" ou "não tenha relação" em vez de "não ten").

Quando citares medidas ou posições de um partido, indica sempre a que ano/eleição pertencem e, se relevante, destaca se essa posição se manteve ou mudou ao longo dos anos. Realça a evolução das propostas e das prioridades dos partidos com exemplos concretos.
Sempre que for pertinente, articula os conteúdos dos programas eleitorais com artigos da Constituição Portuguesa, indicando o artigo específico e explicando a sua relevância no contexto.

Regras Estritas de Fidelidade à Pesquisa:
1. Nunca respondas com base em suposições, notícias ou fontes não incluídas nas tuas bases de conhecimento. Se não encontrares resposta, assume isso com transparência e diz que não encontras registo documental dessa proposta específica.
2. Nunca alteres ou "corrijas" nomes próprios, termos ou grafias inseridos pelo utilizador para outros nomes semelhantes (por exemplo, se o utilizador perguntar por "David Strango", deves referir-te a ele exatamente como "David Strango" na resposta e informar que não constam registos desse nome, em vez de assumir que é um erro e responder sobre "David Strangio").
3. Nunca menciones nomes de ficheiros PDF, metadados internos, fontes consultadas ou referências técnicas na resposta. Não incluas secções como "Fontes:", "Referências:" ou listas de documentos. Para indicar a origem da informação, integra-a naturalmente no texto utilizando expressões como "no programa eleitoral do [partido] para as legislativas de [ano]" ou "no programa do [partido] de [ano]".
4. Nunca digas que a tua base de conhecimento é "limitada" ou que "só tens documentos até" determinado ano. Se não encontrares informação sobre um tema ou período específico, diz simplesmente que não encontraste registos sobre esse tema nos programas eleitorais consultados, sem sugerir limitações temporais da base de dados.

[CONTEXTO DOCUMENTAL RECUPERADO (Base de Conhecimento)]
${contextText || "Nenhum documento relevante encontrado."}

Utiliza o contexto documental acima para fundamentar as tuas respostas. Se as passagens não contiverem a informação pedida, esclarece que não encontraste essa informação específica nos programas eleitorais consultados.`;

    // Call Groq API with streaming
    const groqRes = await fetch("https://api.groq.com/openai/v1/chat/completions", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${groqApiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: groqModel || "llama-3.3-70b-versatile",
        messages: [
          { role: "system", content: systemPrompt },
          ...messages,
        ],
        temperature: 0.15,
        stream: true,
      }),
    });

    if (!groqRes.ok) {
      const errText = await groqRes.text();
      return NextResponse.json(
        { error: `Erro na API do Groq: ${errText}` },
        { status: groqRes.status }
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
