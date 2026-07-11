import { NextRequest, NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  try {
    const { title, link, type } = await req.json();
    const groqApiKey = process.env.GROQ_API_KEY;
    const groqModel = process.env.GROQ_MODEL || "llama-3.3-70b-versatile";

    if (!groqApiKey) {
      console.warn("GROQ_API_KEY is not configured. Returning fallback placeholder details.");
      return NextResponse.json({
        authorOrMeta: type === "book" ? "Autor Desconhecido" : type === "podcast" ? "Canal" : "Info",
        description: `Informação sobre "${title}" obtida através de ${link || "pesquisa"}.`,
        imageUrl: type === "book" 
          ? "https://images.unsplash.com/photo-1543002588-bfa74002ed7e?q=80&w=200" 
          : "https://images.unsplash.com/photo-1507679799987-c73779587ccf?q=80&w=200"
      });
    }

    const systemPrompt = `Tu és um assistente inteligente especializado em obter e enriquecer metadados de recomendações e sugestões.
Com base no título: "${title}", link: "${link}" e tipo de recomendação: "${type}", deves obter/gerar os seguintes detalhes em Português de Portugal (pt-PT) de forma séria e rigorosa:
- se o tipo for "book" (livro), define o Autor do livro;
- se o tipo for "podcast", define o criador, produtor ou canal do podcast/episódio;
- se o tipo for "movie" (filme ou série), indica o ano de lançamento e género;
- se o tipo for "highlight" (artigo/destaque), indica o autor, editora ou editor do artigo;
- se o tipo for "project" (sugestão para o projeto Politómetro em si), define como metadata a categoria da sugestão (ex: "Funcionalidade", "UI/UX", "Base de Dados", "Melhoria").

Gera também uma descrição curta e apelativa de exatamente 1 a 2 frases explicando o que é a recomendação ou o porquê de ser relevante, sempre em português de Portugal (pt-PT).
Se o tipo for "project", a descrição deve resumir a sugestão técnica do utilizador de forma clara.

Sugerir também um URL de imagem (capa do livro, logotipo do podcast, poster do filme, imagem técnica ou um placeholder elegante do Unsplash correspondente).

Devolve APENAS um objeto JSON puro, sem formatação markdown (sem \`\`\`json ou qualquer texto explicativo), com as chaves exatas:
{
  "authorOrMeta": "autor ou metadata do tipo correspondente",
  "description": "descrição de 1 ou 2 frases em pt-PT",
  "imageUrl": "URL de imagem recomendada"
}`;

    const response = await fetch("https://api.groq.com/openai/v1/chat/completions", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${groqApiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: groqModel,
        messages: [
          { role: "system", content: systemPrompt },
          { role: "user", content: `Enriquece: Título: "${title}", Link: "${link}", Tipo: "${type}"` }
        ],
        temperature: 0.2,
        response_format: { type: "json_object" }
      }),
    });

    if (!response.ok) {
      const errBody = await response.text();
      throw new Error(`Groq API failure: ${response.status} - ${errBody}`);
    }

    const data = await response.json();
    const content = data.choices?.[0]?.message?.content;
    const enriched = JSON.parse(content || "{}");

    return NextResponse.json({
      authorOrMeta: enriched.authorOrMeta || "Informação geral",
      description: enriched.description || `Sobre "${title}" no link disponibilizado.`,
      imageUrl: enriched.imageUrl || "https://images.unsplash.com/photo-1507679799987-c73779587ccf?q=80&w=200"
    });

  } catch (err: any) {
    console.error("Enrich API error:", err);
    return NextResponse.json({ error: err.message }, { status: 500 });
  }
}
