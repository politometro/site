import os
import json
import discord
from discord.ext import commands
import urllib.request
import urllib.parse
import tempfile
import requests
import asyncio
import base64
from dotenv import load_dotenv

# Load environment variables if available
load_dotenv()

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("DISCORD_REVIEW_CHANNEL_ID", "0"))
WEBSITE_URL = os.environ.get("WEBSITE_URL", "http://localhost:3000")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")  # owner/repo
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

# Import _cache_key helper
import hashlib
import re
def _cache_key(title, media_type):
    raw = f"{media_type}_{title}".lower()
    safe = re.sub(r'[^a-z0-9]', '_', raw)[:60]
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"{safe}_{h}"

# Global states to track user inputs
waiting_for_text = False
waiting_for_image_quadrant = None  # None or dict

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ===================== GITHUB API HELPERS =====================
def get_github_file(file_path):
    """Fetches a file content and its SHA from the GitHub repository"""
    if not GITHUB_REPO or not GITHUB_TOKEN:
        return None, "Erro: GITHUB_REPO ou GITHUB_TOKEN não configurados nos Secrets do Hugging Face."
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            data = r.json()
            content = base64.b64decode(data["content"])
            return content, data["sha"]
        return None, f"Status {r.status_code} - {r.text}"
    except Exception as e:
        return None, str(e)

def update_github_file(file_path, content_bytes, commit_message, sha=None):
    """Creates or updates a file in the GitHub repository"""
    if not GITHUB_REPO or not GITHUB_TOKEN:
        return "Erro: GITHUB_REPO ou GITHUB_TOKEN não configurados nos Secrets do Hugging Face."
    
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    # Fetch SHA if not provided
    if not sha:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            sha = r.json().get("sha")
            
    content_b64 = base64.b64encode(content_bytes).decode("utf-8")
    payload = {
        "message": commit_message,
        "content": content_b64,
        "branch": "main"
    }
    if sha:
        payload["sha"] = sha
        
    try:
        r_put = requests.put(url, json=payload, headers=headers, timeout=20)
        if r_put.status_code in (200, 201):
            return True
        return f"Status {r_put.status_code} - {r_put.text}"
    except Exception as e:
        return str(e)

def trigger_github_workflow(workflow_name):
    """Triggers a GitHub Actions workflow dispatch"""
    if not GITHUB_REPO or not GITHUB_TOKEN:
        return "Erro: GITHUB_REPO ou GITHUB_TOKEN não configurados nos Secrets do Hugging Face."
    
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{workflow_name}/dispatches"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    payload = {
        "ref": "main"
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        if r.status_code == 204:
            return True
        return f"Status {r.status_code} - {r.text}"
    except Exception as e:
        return str(e)

# ===================== CHATBOT Q&A API =====================
def query_politometro_chat(query, user_id="unknown"):
    """Queries the main Next.js website chat API"""
    base_url = WEBSITE_URL.rstrip("/")
    url = f"{base_url}/api/chat"
    payload = {
        "messages": [
            {"role": "user", "content": query}
        ]
    }
    headers = {
        "Content-Type": "application/json",
        "x-client-id": f"discord-bot:{user_id}"
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, stream=True, timeout=35)
        
        if response.status_code != 200:
            try:
                err_json = response.json()
                return f"❌ Erro do Servidor ({response.status_code}): {err_json.get('error', 'Erro desconhecido')}"
            except:
                return f"❌ Erro do Servidor ({response.status_code}): Não foi possível obter resposta."
                
        full_text = ""
        for line in response.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                if decoded_line.startswith("data: "):
                    data_str = decoded_line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data_json = json.loads(data_str)
                        content = data_json["choices"][0]["delta"].get("content", "")
                        full_text += content
                    except:
                        pass
                        
        # We do not append the sources at the bottom as a list (to match the site and prevent raw .pdf file output).
        # The AI model already incorporates sources in-text if needed.
        if not full_text:
            return "Não foi possível obter uma resposta para a tua pergunta."
            
        return full_text
    except Exception as e:
        return f"❌ Ocorreu um erro ao ligar à API do Politómetro: {e}"

# ===================== DUCKDUCKGO SEARCH HELPER =====================
def search_duckduckgo_link(query):
    """Search DuckDuckGo HTML for a query and return the first result URL."""
    url = "https://html.duckduckgo.com/html/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    }
    try:
        r = requests.post(url, data={"q": query}, headers=headers, timeout=10)
        redirects = re.findall(r'href="([^"]+uddg=[^"]+)"', r.text)
        for rl in redirects:
            match = re.search(r'uddg=([^&"]+)', rl)
            if match:
                decoded = urllib.parse.unquote(match.group(1))
                if "duckduckgo.com" not in decoded:
                    return decoded
        direct = re.findall(r'class="result__url"\s+href="([^"]+)"', r.text)
        if direct:
            return direct[0]
    except Exception as e:
        print(f"Error searching DDG: {e}")
    return None

async def update_recommendation_field(original_msg_id, quadrant, field_name, new_value):
    """Helper to update a recommendation field in both draft and main DB, and trigger regeneration."""
    draft_content, draft_sha = get_github_file("scripts/review_draft.json")
    if not draft_content:
        return f"Erro ao obter review_draft.json: {draft_sha}"
        
    draft_data = json.loads(draft_content.decode("utf-8"))
    item = draft_data.get(quadrant)
    if not item:
        return f"Quadrante {quadrant} não encontrado no rascunho."
        
    item_id = item.get("id")
    item[field_name] = new_value
    
    rec_content, rec_sha = get_github_file("website/public/recommendations.json")
    if not rec_content:
        return f"Erro ao obter recommendations.json: {rec_sha}"
        
    rec_data = json.loads(rec_content.decode("utf-8"))
    
    updated = False
    for section in ["queue", "history"]:
        for r_item in rec_data.get(section, []):
            if r_item.get("id") == item_id:
                r_item[field_name] = new_value
                updated = True
                break
        if updated:
            break
            
    new_rec_bytes = json.dumps(rec_data, indent=2, ensure_ascii=False).encode("utf-8")
    res_db = update_github_file("website/public/recommendations.json", new_rec_bytes, f"Update {field_name} of {item_id} [bot]", sha=rec_sha)
    if res_db is not True:
        return f"Erro ao salvar recommendations.json: {res_db}"
        
    new_draft_bytes = json.dumps(draft_data, indent=2, ensure_ascii=False).encode("utf-8")
    res_draft = update_github_file("scripts/review_draft.json", new_draft_bytes, f"Update {field_name} in draft [bot]", sha=draft_sha)
    if res_draft is not True:
        return f"Erro ao salvar review_draft.json: {res_draft}"
        
    res_wf = trigger_github_workflow("instagram_generate.yml")
    if res_wf is not True:
        return f"Erro ao acionar workflow: {res_wf}"
        
    return True

# ===================== INTERACTIVE VIEWS =====================
class RejectionReasonSelect(discord.ui.Select):
    def __init__(self, original_msg_id):
        options = [
            discord.SelectOption(label="Imagem mal formatada", value="bad_image", description="Alternar layout (template) e redesenhar.", emoji="🖼️"),
            discord.SelectOption(label="Capas erradas (Nova Capa)", value="wrong_covers", description="Substituir capa de um quadrante (Pesquisa ou Manual).", emoji="📚"),
            discord.SelectOption(label="Erros na legenda", value="typo_text", description="Fornecer texto de legenda corrigido.", emoji="✍️"),
            discord.SelectOption(label="Erros de escrita na imagem", value="typo_image_text", description="Corrigir erros no texto desenhado na imagem.", emoji="📝"),
            discord.SelectOption(label="Links inválidos/incorretos", value="bad_links", description="Corrigir links incorretos ou quebrados (Pesquisa ou Manual).", emoji="🔗"),
            discord.SelectOption(label="Más recomendações (Regerar)", value="bad_recs", description="Descartar estes itens e buscar novos candidatos.", emoji="👎")
        ]
        super().__init__(
            placeholder="Selecione o(s) motivo(s) da rejeição...", 
            options=options,
            min_values=1,
            max_values=len(options)
        )
        self.original_msg_id = original_msg_id

    async def callback(self, interaction: discord.Interaction):
        global waiting_for_text, waiting_for_image_quadrant
        reasons = self.values
        channel = interaction.channel

        await interaction.response.defer(ephemeral=True)

        for reason in reasons:
            if reason == "bad_image":
                draft_content, draft_sha = get_github_file("scripts/review_draft.json")
                if not draft_content:
                    await interaction.followup.send(f"❌ Não foi possível aceder ao rascunho do post no GitHub: {draft_sha}", ephemeral=True)
                    continue
                
                draft_data = json.loads(draft_content.decode("utf-8"))
                quadrants = {k: v for k, v in draft_data.items() if k in ["q1", "q2", "q3", "q4"]}
                for qkey, item in quadrants.items():
                    if isinstance(item, dict):
                        curr_layout = item.get("layout_preference", "template_1")
                        new_layout = "template_2" if curr_layout == "template_1" else ("template_3" if curr_layout == "template_2" else "template_1")
                        item["layout_preference"] = new_layout

                new_draft_bytes = json.dumps(draft_data, indent=2, ensure_ascii=False).encode("utf-8")
                res_draft = update_github_file("scripts/review_draft.json", new_draft_bytes, "Cycle layout preference [bot]", sha=draft_sha)
                if res_draft is not True:
                    await interaction.followup.send(f"❌ Erro ao atualizar layout no GitHub: {res_draft}", ephemeral=True)
                    continue

                res_wf = trigger_github_workflow("instagram_generate.yml")
                if res_wf is True:
                    try:
                        old_msg = await channel.fetch_message(self.original_msg_id)
                        await old_msg.edit(content="❌ Post rejeitado. A alterar layout e a regerar imagem no GitHub Actions...", embed=None, view=None)
                    except: pass
                    await interaction.followup.send("Layout alterado! Imagem está a ser regerada no GitHub.", ephemeral=True)
                else:
                    await interaction.followup.send(f"❌ Erro ao acionar workflow de regeneração: {res_wf}", ephemeral=True)

            elif reason == "wrong_covers":
                quadrant_view = discord.ui.View()
                quadrant_view.add_item(QuadrantSelect(self.original_msg_id))
                await interaction.followup.send(
                    "📚 Qual é o quadrante da capa incorreta?",
                    view=quadrant_view,
                    ephemeral=True
                )

            elif reason == "typo_text":
                waiting_for_text = True
                await interaction.followup.send(
                    "✍️ Por favor, **responda a esta mensagem enviando o texto da legenda corrigido**.", 
                    ephemeral=True
                )

            elif reason == "typo_image_text":
                await interaction.followup.send(
                    "📝 **Para corrigir erros de escrita na imagem:**\n"
                    "1. Acede ao teu GitHub e abre o ficheiro `website/public/recommendations.json`.\n"
                    "2. Edita o `title`, `authorOrMeta` ou `description` da recomendação correspondente.\n"
                    "3. Efetua o commit das alterações no GitHub.\n"
                    "4. Volta aqui ao Discord e corre `!check` novamente para regenerar a imagem com as correções!",
                    ephemeral=True
                )

            elif reason == "bad_links":
                link_quad_view = discord.ui.View()
                link_quad_view.add_item(LinkQuadrantSelect(self.original_msg_id))
                await interaction.followup.send(
                    "🔗 Qual é o quadrante do link incorreto?",
                    view=link_quad_view,
                    ephemeral=True
                )

            elif reason == "bad_recs":
                draft_content, draft_sha = get_github_file("scripts/review_draft.json")
                if not draft_content:
                    await interaction.followup.send(f"❌ Erro ao ler rascunho no GitHub: {draft_sha}", ephemeral=True)
                    continue
                
                draft_data = json.loads(draft_content.decode("utf-8"))
                quadrants = {k: v for k, v in draft_data.items() if k in ["q1", "q2", "q3", "q4"]}
                selected_ids = [item["id"] for item in quadrants.values() if item and isinstance(item, dict) and "id" in item]

                rec_content, rec_sha = get_github_file("website/public/recommendations.json")
                if not rec_content:
                    await interaction.followup.send(f"❌ Erro ao ler recommendations.json no GitHub: {rec_sha}", ephemeral=True)
                    continue
                
                rec_data = json.loads(rec_content.decode("utf-8"))
                
                updated = False
                for item in rec_data.get("queue", []):
                    if item["id"] in selected_ids:
                        item["status"] = "skip"
                        updated = True

                if not updated:
                    await interaction.followup.send("Itens do rascunho não encontrados na fila para saltar.", ephemeral=True)
                    continue

                new_rec_bytes = json.dumps(rec_data, indent=2, ensure_ascii=False).encode("utf-8")
                res_db = update_github_file("website/public/recommendations.json", new_rec_bytes, "Reject items and skip [bot]", sha=rec_sha)
                if res_db is not True:
                    await interaction.followup.send(f"❌ Erro ao atualizar recommendations.json: {res_db}", ephemeral=True)
                    continue

                res_wf = trigger_github_workflow("instagram_generate.yml")
                if res_wf is True:
                    try:
                        old_msg = await channel.fetch_message(self.original_msg_id)
                        await old_msg.edit(content="❌ Post rejeitado. A selecionar novos candidatos e a regerar imagem no GitHub Actions...", embed=None, view=None)
                    except: pass
                    await interaction.followup.send("Itens rejeitados! Novas recomendações estão a ser geradas no GitHub.", ephemeral=True)
                else:
                    await interaction.followup.send(f"❌ Erro ao acionar workflow de regeneração: {res_wf}", ephemeral=True)

class LinkQuadrantSelect(discord.ui.Select):
    def __init__(self, original_msg_id):
        options = [
            discord.SelectOption(label="Quadrante 1 (Superior Esquerdo)", value="q1"),
            discord.SelectOption(label="Quadrante 2 (Superior Direito)", value="q2"),
            discord.SelectOption(label="Quadrante 3 (Inferior Esquerdo)", value="q3"),
            discord.SelectOption(label="Quadrante 4 (Destaque da Semana)", value="q4")
        ]
        super().__init__(placeholder="Selecione o quadrante do link incorreto...", options=options)
        self.original_msg_id = original_msg_id

    async def callback(self, interaction: discord.Interaction):
        quad = self.values[0]
        view = LinkCorrectionView(self.original_msg_id, quad)
        await interaction.response.send_message(
            f"🔗 Como pretendes corrigir o link do quadrante **{quad}**?",
            view=view,
            ephemeral=True
        )

class LinkCorrectionView(discord.ui.View):
    def __init__(self, original_msg_id, quadrant):
        super().__init__(timeout=120)
        self.original_msg_id = original_msg_id
        self.quadrant = quadrant

    @discord.ui.button(label="🔍 Pesquisa Automática", style=discord.ButtonStyle.success, custom_id="link_auto_search_item")
    async def auto_search_item(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        # 1. Fetch draft
        draft_content, _ = get_github_file("scripts/review_draft.json")
        if not draft_content:
            await interaction.followup.send("❌ Erro ao ler rascunho do post no GitHub.", ephemeral=True)
            return
        draft_data = json.loads(draft_content.decode("utf-8"))
        item = draft_data.get(self.quadrant)
        if not item:
            await interaction.followup.send(f"❌ Item do quadrante {self.quadrant} não encontrado.", ephemeral=True)
            return
            
        # 2. Build query
        query = item["title"]
        if item.get("type") == "podcast":
            query = f"episódio {query}"
        if item.get("authorOrMeta"):
            # Clean author type indicators
            clean_author = re.sub(r'^(Filme|S[eé]rie|Document[aá]rio|Podcast)\s*/\s*', '', item["authorOrMeta"]).strip()
            query += f" {clean_author}"
            
        await interaction.followup.send(f"🔍 A pesquisar link para '{query}' no DuckDuckGo...", ephemeral=True)
        
        loop = asyncio.get_event_loop()
        found_url = await loop.run_in_executor(None, search_duckduckgo_link, query)
        
        if not found_url:
            await interaction.followup.send("❌ Não foi possível encontrar nenhum link automaticamente. Por favor, usa o botão de pesquisa manual ou introduz o link direto.", ephemeral=True)
            return
            
        await interaction.followup.send(f"✅ Link encontrado: <{found_url}>\nA atualizar no GitHub...", ephemeral=True)
        res = await update_recommendation_field(self.original_msg_id, self.quadrant, "link", found_url)
        if res is True:
            await interaction.followup.send(f"🔗 Link do quadrante **{self.quadrant}** atualizado! A regerar proposta...", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ Erro ao atualizar no GitHub: {res}", ephemeral=True)

    @discord.ui.button(label="✍️ Pesquisa Manual (Digitar)", style=discord.ButtonStyle.primary, custom_id="link_auto_search_query")
    async def auto_search_query(self, interaction: discord.Interaction, button: discord.ui.Button):
        global waiting_for_link_query
        waiting_for_link_query = {
            "quadrant": self.quadrant,
            "original_msg_id": self.original_msg_id
        }
        await interaction.response.send_message(
            "✍️ Escreve o **termo de pesquisa** para eu tentar encontrar o link correto no DuckDuckGo.",
            ephemeral=True
        )

    @discord.ui.button(label="🔗 Inserir Link Direto", style=discord.ButtonStyle.secondary, custom_id="link_manual")
    async def manual_input(self, interaction: discord.Interaction, button: discord.ui.Button):
        global waiting_for_link_manual
        waiting_for_link_manual = {
            "quadrant": self.quadrant,
            "original_msg_id": self.original_msg_id
        }
        await interaction.response.send_message(
            "✍️ Envia o **link direto** (começando com `http` ou `https`) que queres associar.",
            ephemeral=True
        )

class QuadrantSelect(discord.ui.Select):
    def __init__(self, original_msg_id):
        options = [
            discord.SelectOption(label="Quadrante 1 (Superior Esquerdo)", value="q1"),
            discord.SelectOption(label="Quadrante 2 (Superior Direito)", value="q2"),
            discord.SelectOption(label="Quadrante 3 (Inferior Esquerdo)", value="q3"),
            discord.SelectOption(label="Quadrante 4 (Destaque da Semana)", value="q4")
        ]
        super().__init__(placeholder="Selecione o quadrante da capa incorreta...", options=options)
        self.original_msg_id = original_msg_id

    async def callback(self, interaction: discord.Interaction):
        quad = self.values[0]
        view = CoverCorrectionView(self.original_msg_id, quad)
        await interaction.response.send_message(
            f"📚 Como pretendes corrigir a capa do quadrante **{quad}**?",
            view=view,
            ephemeral=True
        )

class CoverCorrectionView(discord.ui.View):
    def __init__(self, original_msg_id, quadrant):
        super().__init__(timeout=120)
        self.original_msg_id = original_msg_id
        self.quadrant = quadrant

    @discord.ui.button(label="🔍 Pesquisa Automática", style=discord.ButtonStyle.success, custom_id="cover_auto_search_item")
    async def auto_search_item(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        # 1. Fetch draft
        draft_content, _ = get_github_file("scripts/review_draft.json")
        if not draft_content:
            await interaction.followup.send("❌ Erro ao ler rascunho do post no GitHub.", ephemeral=True)
            return
        draft_data = json.loads(draft_content.decode("utf-8"))
        item = draft_data.get(self.quadrant)
        if not item:
            await interaction.followup.send(f"❌ Item do quadrante {self.quadrant} não encontrado.", ephemeral=True)
            return
            
        # 2. Build search query
        query = item["title"]
        author = item.get("authorOrMeta")
        clean_author = None
        if author:
            clean_author = re.sub(r'^(Filme|S[eé]rie|Document[aá]rio|Podcast)\s*/\s*', '', author).strip()
            
        await interaction.followup.send(f"🔍 A pesquisar capa para '{query}' automaticamente com a IA...", ephemeral=True)
        
        from cover_fetcher import fetch_cover, _cache_key
        
        loop = asyncio.get_event_loop()
        try:
            cover_img = await loop.run_in_executor(
                None,
                fetch_cover,
                query,
                item["type"],
                clean_author,
                None,  # Do a fresh search instead of using hint URL
                item.get("category")
            )
            if not cover_img:
                await interaction.followup.send("❌ Não foi possível encontrar nenhuma capa automaticamente. Por favor, usa o botão de pesquisa manual (digitar) ou carrega o ficheiro diretamente.", ephemeral=True)
                return
                
            from io import BytesIO
            bio = BytesIO()
            cover_img.convert("RGB").save(bio, format="JPEG", quality=90)
            image_bytes = bio.getvalue()
            
            key = _cache_key(item["title"], item["type"])
            res_upload = update_github_file(f"website/public/covers/{key}.jpg", image_bytes, "Update cover cache image [bot]")
            if res_upload is True:
                res_db = await update_recommendation_field(self.original_msg_id, self.quadrant, "imageUrl", f"/covers/{key}.jpg")
                if res_db is True:
                    await interaction.followup.send(f"🖼️ Capa do quadrante **{self.quadrant}** atualizada! A regerar proposta...", ephemeral=True)
                else:
                    await interaction.followup.send(f"❌ Erro ao atualizar base de dados: {res_db}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Erro ao guardar capa no GitHub: {res_upload}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Ocorreu um erro na pesquisa da capa: {e}", ephemeral=True)

    @discord.ui.button(label="✍️ Pesquisa Manual (Digitar)", style=discord.ButtonStyle.primary, custom_id="cover_auto_search_query")
    async def auto_search_query(self, interaction: discord.Interaction, button: discord.ui.Button):
        global waiting_for_cover_query
        waiting_for_cover_query = {
            "quadrant": self.quadrant,
            "original_msg_id": self.original_msg_id
        }
        await interaction.response.send_message(
            f"🔍 Escreve o **termo de pesquisa** para eu tentar encontrar a capa do quadrante **{self.quadrant}** automaticamente.",
            ephemeral=True
        )

    @discord.ui.button(label="📥 Carregar Capa Manualmente", style=discord.ButtonStyle.secondary, custom_id="cover_manual")
    async def manual_input(self, interaction: discord.Interaction, button: discord.ui.Button):
        global waiting_for_image_quadrant
        waiting_for_image_quadrant = {
            "quadrant": self.quadrant,
            "original_msg_id": self.original_msg_id
        }
        await interaction.response.send_message(
            f"📥 Envia a nova imagem de capa em anexo para substituir a capa do quadrante **{self.quadrant}**.",
            ephemeral=True
        )

class PostReviewView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Aprovar", style=discord.ButtonStyle.green, custom_id="approve_post", emoji="✅")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        res = trigger_github_workflow("instagram_publish.yml")
        if res is True:
            await interaction.followup.edit_message(
                message_id=interaction.message.id,
                content=f"✅ Post **Aprovado** por {interaction.user.mention}! A iniciar publicação no Instagram e gravação na base de dados via GitHub Actions...",
                embed=None,
                view=None
            )
        else:
            await interaction.followup.send(f"❌ Erro ao acionar workflow de publicação: {res}", ephemeral=True)

    @discord.ui.button(label="Rejeitar", style=discord.ButtonStyle.red, custom_id="reject_post", emoji="❌")
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        reject_view = discord.ui.View()
        reject_view.add_item(RejectionReasonSelect(interaction.message.id))
        await interaction.response.send_message(
            content="Qual é o problema com este post?",
            view=reject_view,
            ephemeral=True
        )

# ===================== BOT EVENTS & COMMANDS =====================
@bot.event
async def on_ready():
    bot.add_view(PostReviewView())
    print(f"✅ Bot de Revisão Politómetro ligado como {bot.user}!")

@bot.command(name="check")
async def check_queue(ctx):
    is_dm = isinstance(ctx.channel, discord.DMChannel)
    if not is_dm and ctx.channel.id != CHANNEL_ID:
        return
        
    await ctx.send("⏳ A acionar a geração da proposta de post no GitHub Actions...")
    res = trigger_github_workflow("instagram_generate.yml")
    if res is True:
        await ctx.send("🚀 Workflow iniciado com sucesso! A imagem e legenda serão enviadas para este canal assim que geradas.")
    else:
        await ctx.send(f"❌ Erro ao iniciar geração de post no GitHub: {res}")

@bot.event
async def on_message(message):
    global waiting_for_text, waiting_for_image_quadrant, waiting_for_link_query, waiting_for_link_manual, waiting_for_cover_query
    if message.author.bot:
        return

    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mention = bot.user in message.mentions
    is_allowed = (message.channel.id == CHANNEL_ID) or is_dm

    # Check command prefix
    ctx = await bot.get_context(message)
    if ctx.valid:
        await bot.process_commands(message)
        return

    if is_allowed:
        # 1. Caption text correction
        if waiting_for_text and message.reference:
            await message.channel.typing()
            content_bytes = message.content.encode("utf-8")
            res = update_github_file("website/public/current_caption.txt", content_bytes, "Update caption text [bot]")
            
            waiting_for_text = False
            if res is True:
                await message.reply("✍️ Legenda do post atualizada no GitHub! Podes prosseguir com a aprovação.")
            else:
                await message.reply(f"❌ Erro ao guardar legenda no GitHub: {res}")
            return

        # 2. Manual link correction
        elif waiting_for_link_manual:
            url_input = message.content.strip()
            quad_info = waiting_for_link_manual
            quad = quad_info["quadrant"]
            waiting_for_link_manual = None
            
            if not url_input.startswith("http"):
                await message.reply("❌ Link inválido. Deve começar com http:// ou https://.")
                return
                
            await message.reply(f"⏳ A atualizar o link do quadrante **{quad}** no GitHub...")
            res = await update_recommendation_field(quad_info["original_msg_id"], quad, "link", url_input)
            if res is True:
                await message.reply(f"🔗 Link do quadrante **{quad}** atualizado! A regerar proposta de post no GitHub Actions...")
            else:
                await message.reply(f"❌ Erro ao atualizar no GitHub: {res}")
            return

        # 3. Auto link search correction
        elif waiting_for_link_query:
            query = message.content.strip()
            quad_info = waiting_for_link_query
            quad = quad_info["quadrant"]
            waiting_for_link_query = None
            
            await message.reply(f"🔍 A pesquisar link para '{query}' no DuckDuckGo...")
            found_url = search_duckduckgo_link(query)
            if not found_url:
                await message.reply("❌ Não foi possível encontrar nenhum link para a tua pesquisa. Por favor, envia o link direto manualmente.")
                return
                
            await message.reply(f"✅ Link encontrado: <{found_url}>\nA atualizar no GitHub...")
            res = await update_recommendation_field(quad_info["original_msg_id"], quad, "link", found_url)
            if res is True:
                await message.reply(f"🔗 Link do quadrante **{quad}** atualizado! A regerar proposta de post no GitHub Actions...")
            else:
                await message.reply(f"❌ Erro ao atualizar no GitHub: {res}")
            return

        # 4. Auto cover search correction
        elif waiting_for_cover_query:
            query = message.content.strip()
            quad_info = waiting_for_cover_query
            quad = quad_info["quadrant"]
            waiting_for_cover_query = None
            
            await message.reply(f"🔍 A pesquisar capa para '{query}' com a IA de busca...")
            
            # Fetch draft details to get the media type
            draft_content, _ = get_github_file("scripts/review_draft.json")
            if not draft_content:
                await message.reply("❌ Não foi possível aceder ao rascunho do post no GitHub.")
                return
                
            draft_data = json.loads(draft_content.decode("utf-8"))
            item = draft_data.get(quad)
            if not item:
                await message.reply(f"❌ Item do quadrante {quad} não encontrado.")
                return
                
            from cover_fetcher import fetch_cover, _cache_key
            
            loop = asyncio.get_event_loop()
            try:
                cover_img = await loop.run_in_executor(
                    None,
                    fetch_cover,
                    query,
                    item["type"],
                    None,
                    None,
                    item.get("category")
                )
                if not cover_img:
                    await message.reply("❌ Não foi possível encontrar uma capa. Por favor, carrega a imagem manualmente.")
                    return
                    
                from io import BytesIO
                bio = BytesIO()
                cover_img.convert("RGB").save(bio, format="JPEG", quality=90)
                image_bytes = bio.getvalue()
                
                key = _cache_key(item["title"], item["type"])
                res_upload = update_github_file(f"website/public/covers/{key}.jpg", image_bytes, "Update cover cache image [bot]")
                if res_upload is True:
                    res_db = await update_recommendation_field(quad_info["original_msg_id"], quad, "imageUrl", f"/covers/{key}.jpg")
                    if res_db is True:
                        await message.reply(f"🖼️ Nova capa para o quadrante **{quad}** pesquisada e atualizada! A regerar proposta...")
                    else:
                        await message.reply(f"❌ Erro ao atualizar imageUrl na base de dados: {res_db}")
                else:
                    await message.reply(f"❌ Erro ao guardar capa no GitHub: {res_upload}")
            except Exception as e:
                await message.reply(f"❌ Ocorreu um erro na pesquisa da capa: {e}")
            return

        # 5. Manual cover file correction
        elif waiting_for_image_quadrant and (message.attachments or message.content.startswith("http")):
            attachment_url = message.attachments[0].url if message.attachments else message.content.strip()
            quad_info = waiting_for_image_quadrant
            quad = quad_info["quadrant"]
            waiting_for_image_quadrant = None
            
            await message.reply(f"⏳ A processar e a enviar a nova imagem para o quadrante **{quad}** no GitHub...")
            
            draft_content, _ = get_github_file("scripts/review_draft.json")
            if draft_content:
                selected = json.loads(draft_content.decode("utf-8"))
                item = selected.get(quad)
                if item:
                    from cover_fetcher import _cache_key
                    key = _cache_key(item["title"], item["type"])
                    
                    try:
                        req = urllib.request.Request(
                            attachment_url, 
                            headers={'User-Agent': 'Mozilla/5.0'}
                        )
                        with urllib.request.urlopen(req) as response:
                            image_bytes = response.read()
                        
                        res_upload = update_github_file(f"website/public/covers/{key}.jpg", image_bytes, "Update cover cache image [bot]")
                        
                        if res_upload is True:
                            res_db = await update_recommendation_field(quad_info["original_msg_id"], quad, "imageUrl", f"/covers/{key}.jpg")
                            if res_db is True:
                                await message.reply("🖼️ Nova capa gravada com sucesso! A regerar proposta de post no GitHub Actions...")
                            else:
                                await message.reply(f"❌ Erro ao atualizar base de dados: {res_db}")
                        else:
                            await message.reply(f"❌ Erro ao guardar capa no GitHub: {res_upload}")
                    except Exception as e:
                        await message.reply(f"❌ Erro ao descarregar imagem: {e}")
            return

    # Q&A logic (mentions or DMs)
    if is_dm or is_mention:
        query = message.content
        if is_mention:
            query = query.replace(f"<@{bot.user.id}>", "").strip()
            query = query.replace(f"<@!{bot.user.id}>", "").strip()
            
        if not query:
            await message.reply("Olá! Em que posso ajudar hoje sobre os programas eleitorais?")
            return
            
        async with message.channel.typing():
            loop = asyncio.get_event_loop()
            response_text = await loop.run_in_executor(None, query_politometro_chat, query, message.author.id)
            
            if len(response_text) <= 2000:
                await message.reply(response_text)
            else:
                chunks = []
                current_chunk = ""
                paragraphs = response_text.split("\n")
                for p in paragraphs:
                    if len(current_chunk) + len(p) + 2 > 2000:
                        if current_chunk:
                            chunks.append(current_chunk.strip())
                        current_chunk = p + "\n"
                    else:
                        current_chunk += p + "\n"
                if current_chunk:
                    chunks.append(current_chunk.strip())
                
                if chunks:
                    await message.reply(chunks[0])
                    for chunk in chunks[1:]:
                        await message.channel.send(chunk)
        return

    await bot.process_commands(message)

if __name__ == "__main__":
    if not TOKEN:
        print("❌ Erro: DISCORD_BOT_TOKEN não configurado no ambiente!")
    else:
        bot.run(TOKEN)
