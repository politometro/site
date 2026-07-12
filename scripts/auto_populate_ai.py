import os
import sys
import json
import datetime
import requests
import time

def auto_populate():
    groq_api_key = os.environ.get("GROQ_API_KEY")
    if not groq_api_key:
        print("\n[WARNING] GROQ_API_KEY is not configured in the environment variables.")
        print("To enable 100% autonomous database updates via AI, please add the GROQ_API_KEY secret to your GitHub Repository secrets.")
        print("Proceeding with the existing items in the queue...\n")
        return

    # Paths
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)
    rec_file = os.path.join(root_dir, "website", "public", "recommendations.json")
    watchlist_file = os.path.join(root_dir, "website", "public", "watchlist.json")

    if not os.path.exists(rec_file):
        print(f"Error: recommendations database not found at {rec_file}")
        return

    # Load existing recommendations
    with open(rec_file, "r", encoding="utf-8") as f:
        rec_data = json.load(f)

    queue = rec_data.get("queue", [])
    history = rec_data.get("history", [])

    # Count how many items of each type we have in the queue
    types_count = {"book": 0, "podcast": 0, "movie": 0, "highlight": 0}
    for item in queue:
        itype = item.get("type")
        if itype in types_count:
            types_count[itype] += 1

    print(f"Current queue pool: {types_count}")

    # Determine if we need to auto-populate (e.g. if any type has fewer than 4 items in the queue)
    needs_populate = any(count < 4 for count in types_count.values())
    if not needs_populate:
        print("Queue pool is sufficiently full. Skipping AI auto-population.")
        return

    print("Queue pool is low. Querying Groq API to generate fresh Portuguese political/historical recommendations...")

    # Load watchlist to provide context for podcasts
    watchlist_podcasts = []
    if os.path.exists(watchlist_file):
        try:
            with open(watchlist_file, "r", encoding="utf-8") as f:
                wl_data = json.load(f)
                watchlist_podcasts = wl_data.get("podcasts", [])
        except Exception as e:
            print(f"Warning: Failed to load watchlist: {e}")

    # Collect already used titles to prevent duplicates
    seen_titles = list(set(item["title"].strip().lower() for item in queue + history))

    # Formulate prompt
    watchlist_desc = "\n".join([f"- {p['name']} ({p['author']}): {p['link']}" for p in watchlist_podcasts])

    system_prompt = f"""Tu és um assistente editorial inteligente especializado em política, história e economia de Portugal.
O teu objetivo é gerar novas recomendações culturais e informativas relevantes para o Politómetro.

Deves gerar exatamente 8 novas recomendações no total, distribuídas da seguinte forma:
- 2 livros (type: "book")
- 3 podcasts (type: "podcast")
- 2 filmes, documentários ou séries (type: "movie")
- 1 artigo de fundo, investigação ou destaque político (type: "highlight")

Instruções específicas por tipo:
1. Livros (book): Devem ser livros publicados sobre a história de Portugal, economia portuguesa, biografias de políticos portugueses ou ensaios políticos.
2. Podcasts (podcast): Podes sugerir episódios recentes ou temas debatidos nos seguintes podcasts da nossa watchlist oficial (inclui o link oficial fornecido):
{watchlist_desc}
Ou podes sugerir outros podcasts portugueses conceituados de atualidade e política (como "O Princípio da Incerteza", "Expresso da Meia-Noite", "Fora do Baralho", "A Noite da Má Língua", etc.).
3. Filmes/Documentários (movie): Documentários ou filmes históricos sobre o 25 de Abril, o PREC, figuras históricas portuguesas ou documentários políticos/sociais.
4. Destaques (highlight): Artigos de investigação, relatórios económicos de referência (como da Fundação Francisco Manuel dos Santos) ou ensaios.

Regras estritas:
- NÃO deves sugerir nenhum dos seguintes títulos que já foram sugeridos/publicados anteriormente:
{json.dumps(seen_titles[:60], ensure_ascii=False)}
- Todo o conteúdo (títulos, metadados e descrições) deve ser em Português de Portugal (pt-PT) rigoroso e formal.
- A descrição deve ser curta, apelativa e resumir em 1 ou 2 frases o porquê de ser interessante.
- Devolve links de referência reais e funcionais (ex: link do Wook para livros, link oficial do podcast ou link do IMDB para filmes).

Devolve APENAS um array JSON puro (sem formatação markdown ```json, sem texto antes ou depois) com o seguinte formato de objeto:
[
  {{
    "type": "book | podcast | movie | highlight",
    "category": "Livro | Podcast | Filme | Destaque",
    "title": "Título exato do item",
    "authorOrMeta": "Autor do livro, criador do podcast, realizador do filme ou editor da fonte",
    "description": "Descrição sucinta de 1-2 frases em pt-PT.",
    "link": "URL de referência real",
    "imageUrl": "URL de imagem sugerida (opcional, senão usa-se placeholder)",
    "priority": 3
  }}
]"""

    try:
        models_to_try = [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "llama3-70b-8192",
            "llama3-8b-8192",
            "gemma2-9b-it"
        ]

        response = None
        last_error = ""

        for model in models_to_try:
            try:
                print(f"Trying Groq model: {model}...")
                res = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {groq_api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", content: system_prompt},
                            {"role": "user", content: "Gera 8 novas recomendações políticas e históricas portuguesas respeitando a lista de exclusões."}
                        ],
                        "temperature": 0.3,
                        "response_format": {"type": "json_object"}
                    },
                    timeout=30
                )
                if res.ok:
                    response = res
                    print(f"Successfully generated suggestions using model: {model}")
                    break
                else:
                    last_error = f"Status {res.status_code}: {res.text}"
                    print(f"Model {model} failed. Error: {last_error}")
            except Exception as e:
                last_error = str(e)
                print(f"Model {model} encountered connection error: {last_error}")

        if not response or not response.ok:
            print("\n[WARNING] All Groq models in the fallback chain failed, were rate limited, or quota was exhausted.")
            print(f"Details of last failure: {last_error}")
            print("Skipping AI auto-population for this run to prevent workflow failure.")
            print("The workflow will continue using the existing items in the recommendations pool.\n")
            return

        res_data = response.json()
        content = res_data.get("choices", [{}])[0].get("message", {}).get("content", "")
        
        # Clean markdown wrap if any
        if content.startswith("```"):
            content = content.split("```json")[-1].split("```")[0].strip()
        
        new_items = []
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                new_items = parsed
            elif isinstance(parsed, dict):
                for val in parsed.values():
                    if isinstance(val, list):
                        new_items = val
                        break
                if not new_items:
                    new_items = [parsed]
        except Exception as e:
            print(f"Failed to parse AI output: {e}\nRaw Content:\n{content}")
            return

        # Validate and format new items
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        added_count = 0

        for idx, item in enumerate(new_items):
            itype = item.get("type")
            title = item.get("title")
            if not itype or not title:
                continue

            # Double check duplicate title
            if title.strip().lower() in seen_titles:
                continue

            # Format item correctly
            formatted = {
                "id": f"ai_{itype}_{int(time.time())}_{idx}",
                "type": itype,
                "category": item.get("category", "Livro" if itype == "book" else "Podcast" if itype == "podcast" else "Filme" if itype == "movie" else "Destaque"),
                "title": title.strip(),
                "authorOrMeta": item.get("authorOrMeta", "Vários").strip(),
                "description": item.get("description", "").strip(),
                "imageUrl": item.get("imageUrl") or ("https://images.unsplash.com/photo-1543002588-bfa74002ed7e?q=80&w=200" if itype == "book" else "https://images.unsplash.com/photo-1507679799987-c73779587ccf?q=80&w=200"),
                "link": item.get("link", "").strip(),
                "priority": int(item.get("priority", 3)),
                "expiryDate": None,
                "createdAt": now_str,
                "status": "queue"
            }
            queue.append(formatted)
            added_count += 1
            print(f"  -> Added '{formatted['title']}' (type: {formatted['type']}) to queue.")

        if added_count > 0:
            rec_data["queue"] = queue
            with open(rec_file, "w", encoding="utf-8") as f:
                json.dump(rec_data, f, indent=2, ensure_ascii=False)
            print(f"[OK] Successfully added {added_count} new AI-generated recommendations to recommendations.json.")
        else:
            print("No valid new recommendations were added.")

    except Exception as err:
        print(f"Error during auto-population process: {err}")

if __name__ == "__main__":
    auto_populate()
