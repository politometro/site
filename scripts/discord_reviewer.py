import os
import json
import datetime
import discord
from discord import app_commands
from discord.ext import commands
import urllib.request
import urllib.parse
import tempfile
import requests
import asyncio
import base64
import threading
import time
from dotenv import load_dotenv
from publication_schedule import (
    PUBLICATION_TIMEZONE_NAME,
    scheduled_for_draft,
)

# Load environment variables if available
load_dotenv()

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("DISCORD_REVIEW_CHANNEL_ID", "0"))
WEBSITE_URL = os.environ.get(
    "WEBSITE_URL", "https://politometro.vercel.app/"
)
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")  # owner/repo
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
DISCORD_SUBMISSION_SECRET = os.environ.get(
    "DISCORD_SUBMISSION_SECRET", ""
).strip()


def _configured_ids(name):
    values = os.environ.get(name, "")
    return {
        value.strip()
        for value in values.split(",")
        if value.strip().isdigit()
    }


APPROVER_USER_IDS = _configured_ids("DISCORD_APPROVER_USER_IDS")
APPROVER_ROLE_IDS = _configured_ids("DISCORD_APPROVER_ROLE_IDS")


def _is_authorized_reviewer(user):
    """Allow configured reviewers or server managers; deny anonymous DMs."""
    if str(getattr(user, "id", "")) in APPROVER_USER_IDS:
        return True
    roles = getattr(user, "roles", []) or []
    if any(str(getattr(role, "id", "")) in APPROVER_ROLE_IDS for role in roles):
        return True
    permissions = getattr(user, "guild_permissions", None)
    return bool(
        permissions
        and (
            getattr(permissions, "administrator", False)
            or getattr(permissions, "manage_guild", False)
        )
    )


def _is_expired_recommendation(item):
    value = item.get("expiryDate")
    if not value:
        return False
    try:
        expiry = datetime.datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=datetime.timezone.utc)
    return expiry <= datetime.datetime.now(datetime.timezone.utc)

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
waiting_for_link_query = None
waiting_for_link_manual = None
waiting_for_cover_query = None

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
_application_commands_synced = False
_persistent_views_registered = False

RECOMMENDATION_TYPE_CHOICES = [
    app_commands.Choice(name="Livro", value="book"),
    app_commands.Choice(name="Podcast / Canal", value="podcast"),
    app_commands.Choice(name="Filme / Série", value="movie"),
    app_commands.Choice(name="Destaque / Artigo", value="highlight"),
    app_commands.Choice(
        name="Sugestão para o Projeto (Politómetro)",
        value="project",
    ),
]

DISCORD_RECOMMENDATION_LIMIT = max(
    1, int(os.environ.get("DISCORD_RECOMMENDATION_LIMIT", "5"))
)
DISCORD_RECOMMENDATION_WINDOW_SECONDS = max(
    60, int(os.environ.get("DISCORD_RECOMMENDATION_WINDOW_SECONDS", "1800"))
)
DISCORD_RECOMMENDATION_BLOCK_SECONDS = max(
    300, int(os.environ.get("DISCORD_RECOMMENDATION_BLOCK_SECONDS", "21600"))
)
_discord_recommendation_limits = {}
_discord_recommendation_limits_lock = threading.Lock()


def _check_discord_recommendation_rate_limit(user_id, now=None):
    """Limit submissions by Discord account; Discord does not expose user IPs."""
    current = float(time.time() if now is None else now)
    key = str(user_id)
    with _discord_recommendation_limits_lock:
        if len(_discord_recommendation_limits) > 2000:
            stale = [
                candidate
                for candidate, bucket in _discord_recommendation_limits.items()
                if max(bucket["reset_at"], bucket["blocked_until"]) <= current
            ]
            for candidate in stale:
                _discord_recommendation_limits.pop(candidate, None)

        bucket = _discord_recommendation_limits.get(key)
        if bucket and bucket["blocked_until"] > current:
            return {
                "allowed": False,
                "retry_after_seconds": max(
                    1, int(bucket["blocked_until"] - current)
                ),
            }

        if not bucket or bucket["reset_at"] <= current:
            _discord_recommendation_limits[key] = {
                "count": 1,
                "reset_at": current
                + DISCORD_RECOMMENDATION_WINDOW_SECONDS,
                "blocked_until": 0.0,
            }
            return {"allowed": True, "retry_after_seconds": 0}

        if bucket["count"] >= DISCORD_RECOMMENDATION_LIMIT:
            bucket["blocked_until"] = (
                current + DISCORD_RECOMMENDATION_BLOCK_SECONDS
            )
            return {
                "allowed": False,
                "retry_after_seconds": DISCORD_RECOMMENDATION_BLOCK_SECONDS,
            }

        bucket["count"] += 1
        return {"allowed": True, "retry_after_seconds": 0}

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


def _discord_chunks(value, limit=2000):
    """Split a response on paragraphs without exceeding Discord's limit."""
    text = str(value or "").strip()
    if not text:
        return ["Não foi possível obter uma resposta."]
    chunks = []
    current = ""
    for paragraph in text.splitlines() or [text]:
        candidate = f"{current}\n{paragraph}".strip()
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(paragraph) > limit:
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


def submit_discord_recommendation(media_type, title, link, user_id):
    """Use the website's strict resolver and durable Discord approval outbox."""
    endpoint = f"{WEBSITE_URL.rstrip('/')}/api/suggestions"
    payload = {
        "action": "append",
        "item": {
            "type": media_type,
            "title": str(title or "").strip(),
            "link": str(link or "").strip(),
        },
    }
    response = requests.post(
        endpoint,
        json=payload,
        headers={
            "Content-Type": "application/json",
            "x-client-id": f"discord-recommendation:{user_id}",
            **(
                {
                    "x-discord-submission-secret":
                        DISCORD_SUBMISSION_SECRET
                }
                if DISCORD_SUBMISSION_SECRET
                else {}
            ),
        },
        timeout=45,
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not response.ok:
        detail = body.get("error") if isinstance(body, dict) else None
        raise RuntimeError(
            detail
            or f"O servidor recusou a recomendação (HTTP {response.status_code})."
        )
    return body if isinstance(body, dict) else {}


def public_recommendation_error(error):
    """Return helpful copy without exposing service or storage internals."""
    original = str(error or "").strip()
    message = original.lower()
    if "prazo de relevância" in message or "expir" in message:
        return (
            "Este conteúdo já não é suficientemente recente para ser "
            "recomendado. Escolhe uma publicação mais atual e tenta novamente."
        )
    if "já existe" in message or "histórico" in message:
        return (
            "Esta sugestão já foi recebida anteriormente. "
            "Obrigado pela contribuição."
        )
    if "demasiad" in message or "limite" in message:
        return (
            "Recebemos várias sugestões num curto espaço de tempo. "
            "Aguarda um pouco antes de tentares novamente."
        )
    if (
        "link indicado" in message
        or "link fornecido" in message
        or "apenas pelo título" in message
    ):
        return original
    if "tipo de recomendação" in message:
        return "Seleciona um tipo de conteúdo válido e tenta novamente."
    return (
        "Não foi possível concluir a submissão neste momento. "
        "Confirma os dados e tenta novamente dentro de alguns minutos."
    )

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
class ReviewFeedbackModal(discord.ui.Modal, title="Descrever a correção"):
    correction = discord.ui.TextInput(
        label="O que deve ser corrigido?",
        style=discord.TextStyle.paragraph,
        placeholder=(
            "Ex.: O destaque é uma notícia; substitui-o por um artigo de "
            "opinião sobre o mesmo tema."
        ),
        min_length=5,
        max_length=1000,
        required=True,
    )

    def __init__(
        self,
        original_msg_id,
        expected_draft_id,
        expected_hash_prefix,
    ):
        super().__init__()
        self.original_msg_id = original_msg_id
        self.expected_draft_id = expected_draft_id
        self.expected_hash_prefix = expected_hash_prefix

    async def on_submit(self, interaction: discord.Interaction):
        result = _store_review_feedback(
            self.expected_draft_id,
            self.expected_hash_prefix,
            str(self.correction.value),
            interaction.user,
        )
        if result is not True:
            await interaction.response.send_message(
                f"❌ {result}", ephemeral=True
            )
            return
        await interaction.response.send_message(
            (
                "Observação registada no rascunho. Usa a opção automática "
                "mais adequada do menu de rejeição quando pretenderes aplicar "
                "a correção."
            ),
            ephemeral=True,
        )
        if interaction.channel:
            await interaction.channel.send(
                (
                    f"📝 **Correção solicitada por "
                    f"{interaction.user.mention}:**\n"
                    f"{str(self.correction.value).strip()}"
                )
            )


class RejectionReasonSelect(discord.ui.Select):
    def __init__(
        self,
        original_msg_id,
        expected_draft_id="",
        expected_hash_prefix="",
    ):
        options = [
            discord.SelectOption(label="Imagem mal formatada", value="bad_image", description="Alternar layout (template) e redesenhar.", emoji="🖼️"),
            discord.SelectOption(label="Capas erradas (Nova Capa)", value="wrong_covers", description="Substituir capa de um quadrante (Pesquisa ou Manual).", emoji="📚"),
            discord.SelectOption(label="Erros na legenda", value="typo_text", description="Fornecer texto de legenda corrigido.", emoji="✍️"),
            discord.SelectOption(label="Erros de escrita na imagem", value="typo_image_text", description="Corrigir erros no texto desenhado na imagem.", emoji="📝"),
            discord.SelectOption(label="Links inválidos/incorretos", value="bad_links", description="Corrigir links incorretos ou quebrados (Pesquisa ou Manual).", emoji="🔗"),
            discord.SelectOption(label="Más recomendações (Regerar)", value="bad_recs", description="Descartar estes itens e buscar novos candidatos.", emoji="👎"),
            discord.SelectOption(label="Descrever outra correção", value="custom_feedback", description="Registar por texto o que deve ser alterado.", emoji="💬"),
        ]
        super().__init__(
            placeholder="Selecione o(s) motivo(s) da rejeição...", 
            options=options,
            min_values=1,
            max_values=len(options)
        )
        self.original_msg_id = original_msg_id
        self.expected_draft_id = expected_draft_id
        self.expected_hash_prefix = expected_hash_prefix

    async def callback(self, interaction: discord.Interaction):
        global waiting_for_text, waiting_for_image_quadrant
        reasons = self.values
        channel = interaction.channel

        if "custom_feedback" in reasons:
            if len(reasons) > 1:
                await interaction.response.send_message(
                    (
                        "Seleciona «Descrever outra correção» isoladamente "
                        "para abrir o campo de texto."
                    ),
                    ephemeral=True,
                )
                return
            await interaction.response.send_modal(
                ReviewFeedbackModal(
                    self.original_msg_id,
                    self.expected_draft_id,
                    self.expected_hash_prefix,
                )
            )
            return

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
                if self.expected_draft_id and self.expected_hash_prefix:
                    rejected = _reject_current_draft(
                        self.expected_draft_id,
                        self.expected_hash_prefix,
                        interaction.user,
                    )
                    if rejected is not True:
                        await interaction.followup.send(
                            f"❌ {rejected}", ephemeral=True
                        )
                        continue
                    res_wf = trigger_github_workflow(
                        "instagram_generate.yml"
                    )
                    if res_wf is True:
                        try:
                            old_msg = await channel.fetch_message(
                                self.original_msg_id
                            )
                            await old_msg.edit(
                                content=(
                                    "❌ Proposta rejeitada. A selecionar "
                                    "novos candidatos verificados..."
                                ),
                                embed=None,
                                view=None,
                            )
                        except Exception:
                            pass
                        await interaction.followup.send(
                            (
                                "Recomendações rejeitadas. Está a ser gerada "
                                "uma proposta nova."
                            ),
                            ephemeral=True,
                        )
                    else:
                        await interaction.followup.send(
                            (
                                "As recomendações foram rejeitadas, mas não "
                                f"foi possível iniciar a substituição: {res_wf}"
                            ),
                            ephemeral=True,
                        )
                    continue

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


class RejectionReasonView(discord.ui.View):
    def __init__(
        self,
        original_msg_id,
        expected_draft_id,
        expected_hash_prefix,
    ):
        super().__init__(timeout=300)
        self.add_item(
            RejectionReasonSelect(
                original_msg_id,
                expected_draft_id,
                expected_hash_prefix,
            )
        )

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

def _review_identity_from_message(message):
    """Return the immutable draft id/hash embedded in this review card."""
    if not message or not message.embeds:
        return None, None
    footer = message.embeds[0].footer
    text = footer.text if footer else ""
    match = re.search(r"Rascunho:\s*([^|]+)\|\s*Hash:\s*([0-9a-f]+)", text)
    if not match:
        return None, None
    return match.group(1).strip(), match.group(2).strip()


def _approve_current_draft(expected_draft_id, expected_hash_prefix, user):
    """Atomically bind an approval to the exact draft represented by a card."""
    draft_content, draft_sha = get_github_file("scripts/review_draft.json")
    if not draft_content:
        return f"Erro ao obter o rascunho atual: {draft_sha}"

    try:
        draft = json.loads(draft_content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return f"Rascunho inválido: {exc}"

    draft_id = draft.get("draft_id")
    content_hash = draft.get("content_hash", "")
    if draft_id != expected_draft_id or not content_hash.startswith(expected_hash_prefix):
        return (
            "Este cartão pertence a um rascunho antigo. Usa o cartão de revisão "
            "mais recente para evitar publicar a proposta errada."
        )
    if draft.get("is_test"):
        return "Este é um rascunho de teste e não pode ser publicado."

    existing = draft.get("approval") or {}
    if existing.get("approved"):
        if (
            existing.get("draft_id") == draft_id
            and existing.get("content_hash") == content_hash
        ):
            return True
        return "O rascunho contém uma aprovação inconsistente."

    scheduled_for = scheduled_for_draft(draft)
    if not scheduled_for:
        return (
            "Não foi possível determinar o domingo desta proposta. "
            "Gera uma nova proposta e tenta novamente."
        )

    draft["approval"] = {
        "approved": True,
        "draft_id": draft_id,
        "content_hash": content_hash,
        "approved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "approved_by_id": str(user.id),
        "approved_by_name": user.display_name,
        "scheduled_for": scheduled_for,
        "scheduled_timezone": PUBLICATION_TIMEZONE_NAME,
    }
    encoded = json.dumps(draft, indent=2, ensure_ascii=False).encode("utf-8")
    return update_github_file(
        "scripts/review_draft.json",
        encoded,
        f"Approve weekly draft {draft_id} [bot]",
        sha=draft_sha,
    )


def _reject_current_draft(expected_draft_id, expected_hash_prefix, user):
    """Reject the exact card and let the automated resolver replace all four items."""
    draft_content, _ = get_github_file("scripts/review_draft.json")
    if not draft_content:
        return "Não foi possível obter o rascunho atual."
    try:
        draft = json.loads(draft_content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return f"Rascunho inválido: {exc}"

    draft_id = draft.get("draft_id")
    content_hash = str(draft.get("content_hash") or "")
    if draft_id != expected_draft_id or not content_hash.startswith(
        expected_hash_prefix
    ):
        return "Este cartão já não corresponde ao rascunho atual."

    selected_ids = {
        item.get("id")
        for qkey in ("q1", "q2", "q3", "q4")
        for item in [draft.get(qkey)]
        if isinstance(item, dict) and item.get("id")
    }
    rec_content, rec_sha = get_github_file(
        "website/public/recommendations.json"
    )
    if not rec_content:
        return f"Não foi possível obter a fila: {rec_sha}"
    try:
        rec_data = json.loads(rec_content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return f"Fila inválida: {exc}"

    rejected = 0
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for item in rec_data.get("queue", []):
        if item.get("id") in selected_ids and item.get("status") == "queue":
            item["status"] = "skip"
            item["rejectedAt"] = now
            item["rejectedBy"] = str(user.id)
            rejected += 1
    if rejected != len(selected_ids):
        return "A fila mudou; gera uma proposta nova antes de rejeitar."

    encoded = json.dumps(rec_data, indent=2, ensure_ascii=False).encode("utf-8")
    return update_github_file(
        "website/public/recommendations.json",
        encoded,
        f"Reject weekly draft {draft_id} and replace automatically [bot]",
        sha=rec_sha,
    )


def _store_review_feedback(
    expected_draft_id,
    expected_hash_prefix,
    text,
    user,
):
    """Attach a reviewer-authored correction note to the exact current draft."""

    clean_text = re.sub(r"\s+", " ", str(text or "")).strip()[:1000]
    if len(clean_text) < 5:
        return "Descreve a correção com um pouco mais de detalhe."
    draft_content, draft_sha = get_github_file("scripts/review_draft.json")
    if not draft_content:
        return "Não foi possível obter o rascunho atual."
    try:
        draft = json.loads(draft_content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "O rascunho atual não pôde ser lido."
    draft_id = draft.get("draft_id")
    content_hash = str(draft.get("content_hash") or "")
    if (
        draft_id != expected_draft_id
        or not content_hash.startswith(expected_hash_prefix)
    ):
        return "Este cartão já não corresponde ao rascunho atual."

    feedback = draft.get("reviewFeedback")
    if not isinstance(feedback, list):
        feedback = []
    feedback.append(
        {
            "text": clean_text,
            "createdAt": datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat(),
            "createdById": str(user.id),
            "createdByName": getattr(user, "display_name", str(user)),
        }
    )
    draft["reviewFeedback"] = feedback[-20:]
    encoded = json.dumps(
        draft, indent=2, ensure_ascii=False
    ).encode("utf-8")
    return update_github_file(
        "scripts/review_draft.json",
        encoded,
        f"Record review feedback for {draft_id} [bot]",
        sha=draft_sha,
    )


class PostReviewView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Aprovar", style=discord.ButtonStyle.green, custom_id="approve_post", emoji="✅")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not _is_authorized_reviewer(interaction.user):
            await interaction.followup.send(
                "Não tens permissão para aprovar publicações.",
                ephemeral=True,
            )
            return

        draft_id, hash_prefix = _review_identity_from_message(interaction.message)
        if not draft_id or not hash_prefix:
            await interaction.followup.send(
                "Este cartão é antigo e não contém a identidade segura do rascunho. "
                "Gera uma nova proposta com `!check`.",
                ephemeral=True,
            )
            return

        approval = _approve_current_draft(
            draft_id, hash_prefix, interaction.user
        )
        if approval is not True:
            await interaction.followup.send(f"❌ {approval}", ephemeral=True)
            return

        await interaction.message.edit(
            content=(
                f"✅ Post **Aprovado** por {interaction.user.mention}! "
                "Publicação agendada para domingo às 10:00 "
                "(hora de Lisboa)."
            ),
            embed=None,
            view=None,
        )
        await interaction.followup.send(
            f"Rascunho `{draft_id}` aprovado e agendado para domingo às 10:00.",
            ephemeral=True,
        )

    @discord.ui.button(label="Rejeitar", style=discord.ButtonStyle.red, custom_id="reject_post", emoji="❌")
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_authorized_reviewer(interaction.user):
            await interaction.response.send_message(
                "Não tens permissão para rejeitar publicações.",
                ephemeral=True,
            )
            return
        draft_id, hash_prefix = _review_identity_from_message(
            interaction.message
        )
        if not draft_id or not hash_prefix:
            await interaction.response.send_message(
                "Este cartão é antigo; gera uma nova proposta.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "Indica o que deve ser corrigido nesta proposta:",
            view=RejectionReasonView(
                interaction.message.id,
                draft_id,
                hash_prefix,
            ),
            ephemeral=True,
        )

def _recommendation_external_id(item):
    verification = item.get("verification") or {}
    return str(
        item.get("externalId")
        or verification.get("externalId")
        or verification.get("entityId")
        or ""
    )


def _podcast_collection_id(item):
    external_id = _recommendation_external_id(item)
    match = re.search(r"(?:apple:podcast:|apple-podcast:)(\d+)$", external_id)
    return match.group(1) if match else ""


def _is_whole_podcast(item):
    return item.get("type") == "podcast" and bool(
        _podcast_collection_id(item)
    )


def _apple_podcast_metadata(collection_id):
    if not collection_id:
        return {}
    try:
        response = requests.get(
            "https://itunes.apple.com/lookup",
            params={
                "id": collection_id,
                "media": "podcast",
                "entity": "podcast",
                "country": "PT",
            },
            timeout=20,
        )
        response.raise_for_status()
        results = response.json().get("results") or []
        return next(
            (
                result
                for result in results
                if str(result.get("collectionId") or result.get("trackId") or "")
                == str(collection_id)
            ),
            results[0] if results else {},
        )
    except (requests.RequestException, ValueError, AttributeError):
        return {}


def _watchlist_entry(item):
    collection_id = _podcast_collection_id(item)
    if not collection_id:
        raise RuntimeError(
            "A recomendação não representa um podcast completo do Apple Podcasts."
        )
    metadata = _apple_podcast_metadata(collection_id)
    title = str(
        metadata.get("collectionName")
        or metadata.get("trackName")
        or item.get("title")
        or ""
    ).strip()
    author = str(metadata.get("artistName") or "").strip()
    if not author:
        stored_author = str(item.get("authorOrMeta") or "").strip()
        parts = [part.strip() for part in stored_author.split(" / ") if part.strip()]
        author = parts[-1] if len(parts) > 1 else stored_author
    source_image = str(
        metadata.get("artworkUrl600")
        or metadata.get("artworkUrl100")
        or item.get("sourceImageUrl")
        or ""
    ).strip()
    source_link = str(
        metadata.get("collectionViewUrl")
        or metadata.get("trackViewUrl")
        or item.get("link")
        or ""
    ).strip()
    return {
        "name": title,
        "author": author,
        "description": str(item.get("description") or "").strip(),
        "imageUrl": source_image if source_image.startswith("http") else "",
        "link": source_link,
        "appleCollectionId": str(collection_id),
        "feedUrl": str(metadata.get("feedUrl") or "").strip(),
        "addedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "origin": "discord-approved-recommendation",
    }


def add_podcast_to_watchlist(item):
    """Add a verified whole podcast to the persistent watchlist, idempotently."""
    watchlist_content, watchlist_sha = get_github_file(
        "website/public/watchlist.json"
    )
    if not watchlist_content:
        return f"Erro ao obter watchlist.json: {watchlist_sha}"
    try:
        watchlist = json.loads(watchlist_content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return f"watchlist.json inválido: {exc}"
    if not isinstance(watchlist, dict):
        return "watchlist.json não contém um objeto."
    podcasts = watchlist.setdefault("podcasts", [])
    if not isinstance(podcasts, list):
        return "A lista de podcasts da watchlist é inválida."

    try:
        entry = _watchlist_entry(item)
    except RuntimeError as exc:
        return str(exc)
    collection_id = entry["appleCollectionId"]
    normalized_name = re.sub(r"\W+", "", entry["name"].casefold())
    for existing in podcasts:
        if not isinstance(existing, dict):
            continue
        same_id = str(existing.get("appleCollectionId") or "") == collection_id
        existing_name = re.sub(
            r"\W+", "", str(existing.get("name") or "").casefold()
        )
        if same_id or (normalized_name and existing_name == normalized_name):
            return {
                "status": "already_watched",
                "entry": existing,
            }

    podcasts.append(entry)
    encoded = json.dumps(
        watchlist, indent=2, ensure_ascii=False
    ).encode("utf-8")
    result = update_github_file(
        "website/public/watchlist.json",
        encoded,
        f"Watch approved podcast: {entry['name']} [bot]",
        sha=watchlist_sha,
    )
    if result is not True:
        return result
    return {"status": "added", "entry": entry}


def approve_recommendation(item_id, user, mode="queue"):
    """Approve one recommendation or convert a whole podcast into a watch."""
    if mode not in {"queue", "watch", "both"}:
        return {"ok": False, "error": "Modo de aprovação inválido."}
    rec_content, rec_sha = get_github_file(
        "website/public/recommendations.json"
    )
    if not rec_content:
        return {
            "ok": False,
            "error": f"Erro ao obter recommendations.json: {rec_sha}",
        }
    try:
        rec_data = json.loads(rec_content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"recommendations.json inválido: {exc}"}

    item = next(
        (
            candidate
            for candidate in rec_data.get("queue", [])
            if candidate.get("id") == item_id
        ),
        None,
    )
    if not item:
        return {"ok": False, "error": f"Item `{item_id}` não encontrado na fila."}
    if item.get("status") not in {"pending_approval", "pending_sent"}:
        return {
            "ok": False,
            "error": "Este cartão já foi processado ou pertence a um estado antigo.",
        }

    verification = item.get("verification") or {}
    verified = bool(
        item.get("resolutionStatus") == "verified"
        and verification.get("status") == "verified"
        and verification.get("entityId")
        and verification.get("coverHash")
        and str(item.get("link") or "").startswith(("http://", "https://"))
        and str(item.get("imageUrl") or "").startswith("/covers/")
    )
    if not verified:
        return {
            "ok": False,
            "error": (
                "Esta sugestão ainda não tem identidade, link e imagem "
                "verificados."
            ),
        }
    whole_podcast = _is_whole_podcast(item)
    if mode in {"watch", "both"} and not whole_podcast:
        return {
            "ok": False,
            "error": "A opção de observação exige um podcast completo.",
        }
    if mode in {"queue", "both"} and _is_expired_recommendation(item):
        return {
            "ok": False,
            "error": (
                "Esta recomendação já expirou. Podes acompanhar o podcast, "
                "mas não recomendar agora este conteúdo desatualizado."
            ),
        }

    watch_result = None
    if mode in {"watch", "both"}:
        watch_result = add_podcast_to_watchlist(item)
        if not isinstance(watch_result, dict):
            return {"ok": False, "error": str(watch_result)}

    item["status"] = "queue" if mode in {"queue", "both"} else "watching"
    item["approvalMode"] = mode
    item["approvedAt"] = datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat()
    item["approvedBy"] = str(getattr(user, "id", ""))
    if watch_result:
        item["watchlistStatus"] = watch_result["status"]
        item["watchlistCollectionId"] = _podcast_collection_id(item)

    title = str(item.get("title") or item_id)
    encoded = json.dumps(
        rec_data, indent=2, ensure_ascii=False
    ).encode("utf-8")
    result = update_github_file(
        "website/public/recommendations.json",
        encoded,
        f"Approve recommendation ({mode}): {title} [bot]",
        sha=rec_sha,
    )
    if result is not True:
        return {"ok": False, "error": str(result)}
    return {
        "ok": True,
        "title": title,
        "mode": mode,
        "watchlist": watch_result,
    }


class PodcastApprovalChoiceView(discord.ui.View):
    def __init__(self, item_id, review_message=None):
        super().__init__(timeout=180)
        self.item_id = item_id
        self.review_message = review_message

    async def _apply(self, interaction, mode):
        if not _is_authorized_reviewer(interaction.user):
            await interaction.response.send_message(
                "Não tens permissão para aprovar recomendações.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        result = await asyncio.to_thread(
            approve_recommendation,
            self.item_id,
            interaction.user,
            mode,
        )
        if not result.get("ok"):
            await interaction.followup.send(
                f"❌ {result.get('error')}", ephemeral=True
            )
            return
        messages = {
            "queue": "Podcast aprovado como recomendação única.",
            "watch": (
                "Podcast adicionado à observação; os episódios recentes "
                "passam a ser considerados automaticamente."
            ),
            "both": (
                "Podcast aprovado agora e também adicionado à observação "
                "para episódios futuros."
            ),
        }
        await interaction.followup.send(
            f"✅ **{result['title']}** — {messages[mode]}",
            ephemeral=True,
        )
        if self.review_message and self.review_message.embeds:
            embed = self.review_message.embeds[0]
            embed.color = 0x2ECC71
            embed.set_footer(
                text=(
                    f"APROVADO ({mode}) por "
                    f"{interaction.user.display_name} | ID: {self.item_id}"
                )
            )
            try:
                await self.review_message.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Aprovar só o podcast", style=discord.ButtonStyle.success)
    async def approve_once(self, interaction, button):
        await self._apply(interaction, "queue")

    @discord.ui.button(label="Acompanhar episódios", style=discord.ButtonStyle.primary)
    async def watch_episodes(self, interaction, button):
        await self._apply(interaction, "watch")

    @discord.ui.button(label="Aprovar e acompanhar", style=discord.ButtonStyle.secondary)
    async def approve_and_watch(self, interaction, button):
        await self._apply(interaction, "both")


# ===================== RECOMMENDATION APPROVAL VIEW =====================
class RecommendationApprovalView(discord.ui.View):
    """Persistent view for approving/rejecting individual AI-generated recommendations."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Aprovar", style=discord.ButtonStyle.success, custom_id="rec_approve", emoji="\u2705")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not _is_authorized_reviewer(interaction.user):
            await interaction.followup.send(
                "Não tens permissão para aprovar recomendações.",
                ephemeral=True,
            )
            return
        
        # Extract item_id from the message embed footer
        item_id = None
        if interaction.message and interaction.message.embeds:
            footer = interaction.message.embeds[0].footer
            if footer and footer.text:
                # Footer format: "ID: ai_book_1234_0 | Gerado por IA"
                parts = footer.text.split("|")[0].strip()
                if parts.startswith("ID:"):
                    item_id = parts[3:].strip()
        
        if not item_id:
            await interaction.followup.send("Erro: Nao foi possivel identificar a recomendacao.", ephemeral=True)
            return
        
        rec_content, rec_sha = get_github_file(
            "website/public/recommendations.json"
        )
        if not rec_content:
            await interaction.followup.send(
                f"Erro ao obter recommendations.json: {rec_sha}",
                ephemeral=True,
            )
            return
        try:
            rec_data = json.loads(rec_content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            await interaction.followup.send(
                f"recommendations.json inválido: {exc}", ephemeral=True
            )
            return
        item = next(
            (
                candidate
                for candidate in rec_data.get("queue", [])
                if candidate.get("id") == item_id
            ),
            None,
        )
        if not item:
            await interaction.followup.send(
                f"Item `{item_id}` não encontrado na fila.", ephemeral=True
            )
            return
        if _is_whole_podcast(item):
            await interaction.followup.send(
                (
                    "Este cartão representa um **podcast completo**. "
                    "Escolhe como deve ser usado:"
                ),
                view=PodcastApprovalChoiceView(
                    item_id, review_message=interaction.message
                ),
                ephemeral=True,
            )
            return

        result = await asyncio.to_thread(
            approve_recommendation,
            item_id,
            interaction.user,
            "queue",
        )
        if not result.get("ok"):
            await interaction.followup.send(
                f"❌ {result.get('error')}", ephemeral=True
            )
            return
        embed = interaction.message.embeds[0]
        embed.color = 0x2ECC71
        embed.set_footer(
            text=(
                f"APROVADO por {interaction.user.display_name} | "
                f"ID: {item_id}"
            )
        )
        await interaction.message.edit(embed=embed, view=None)
        await interaction.followup.send(
            (
                f"Recomendação **{result['title']}** aprovada e "
                "adicionada à fila."
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="Rejeitar", style=discord.ButtonStyle.danger, custom_id="rec_reject", emoji="\u274C")
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not _is_authorized_reviewer(interaction.user):
            await interaction.followup.send(
                "Não tens permissão para rejeitar recomendações.",
                ephemeral=True,
            )
            return
        
        # Extract item_id from the message embed footer
        item_id = None
        if interaction.message and interaction.message.embeds:
            footer = interaction.message.embeds[0].footer
            if footer and footer.text:
                parts = footer.text.split("|")[0].strip()
                if parts.startswith("ID:"):
                    item_id = parts[3:].strip()
        
        if not item_id:
            await interaction.followup.send("Erro: Nao foi possivel identificar a recomendacao.", ephemeral=True)
            return
        
        # Fetch recommendations.json from GitHub
        rec_content, rec_sha = get_github_file("website/public/recommendations.json")
        if not rec_content:
            await interaction.followup.send(f"Erro ao obter recommendations.json: {rec_sha}", ephemeral=True)
            return
        
        rec_data = json.loads(rec_content.decode("utf-8"))
        
        # Find and update the item
        updated = False
        item_title = ""
        for item in rec_data.get("queue", []):
            if item.get("id") == item_id:
                if item.get("status") not in {
                    "pending_approval",
                    "pending_sent",
                }:
                    await interaction.followup.send(
                        "Este cartão já foi processado ou pertence a um estado antigo.",
                        ephemeral=True,
                    )
                    return
                item["status"] = "skip"
                item_title = item.get("title", item_id)
                updated = True
                break
        
        if not updated:
            await interaction.followup.send(f"Item `{item_id}` nao encontrado na fila.", ephemeral=True)
            return
        
        # Save back to GitHub
        new_rec_bytes = json.dumps(rec_data, indent=2, ensure_ascii=False).encode("utf-8")
        res = update_github_file("website/public/recommendations.json", new_rec_bytes, f"Reject recommendation: {item_title} [bot]", sha=rec_sha)
        
        if res is True:
            # Update the embed to show rejection
            embed = interaction.message.embeds[0]
            embed.color = 0xE74C3C  # red
            embed.set_footer(text=f"REJEITADO por {interaction.user.display_name} | ID: {item_id}")
            await interaction.message.edit(embed=embed, view=None)
            await interaction.followup.send(f"Recomendacao **{item_title}** rejeitada e ignorada.", ephemeral=True)
        else:
            await interaction.followup.send(f"Erro ao guardar no GitHub: {res}", ephemeral=True)

# ===================== DISCORD APPLICATION COMMANDS =====================
@bot.tree.command(
    name="perguntar",
    description="Faz uma pergunta ao Politómetro sobre política e programas eleitorais.",
)
@app_commands.describe(pergunta="A pergunta a enviar ao Politómetro")
async def ask_application_command(
    interaction: discord.Interaction,
    pergunta: str,
):
    query = pergunta.strip()
    if not query:
        await interaction.response.send_message(
            "Escreve uma pergunta.", ephemeral=True
        )
        return
    await interaction.response.defer(thinking=True)
    response_text = await asyncio.to_thread(
        query_politometro_chat,
        query,
        str(interaction.user.id),
    )
    for chunk in _discord_chunks(response_text):
        await interaction.followup.send(chunk)


@bot.tree.command(
    name="recomendar",
    description="Submete uma recomendação para validação e aprovação.",
)
@app_commands.describe(
    tipo="Tipo de conteúdo",
    titulo="Título exato do conteúdo",
    link="Link direto para o conteúdo; recomendado sempre que exista",
)
@app_commands.choices(tipo=RECOMMENDATION_TYPE_CHOICES)
async def recommend_application_command(
    interaction: discord.Interaction,
    tipo: app_commands.Choice[str],
    titulo: str,
    link: str = "",
):
    clean_title = titulo.strip()
    clean_link = link.strip()
    if len(clean_title) < 3:
        await interaction.response.send_message(
            "O título deve ter pelo menos três caracteres.",
            ephemeral=True,
        )
        return
    if clean_link and not clean_link.startswith(("http://", "https://")):
        await interaction.response.send_message(
            "O link deve começar por `https://` ou `http://`.",
            ephemeral=True,
        )
        return
    rate_limit = _check_discord_recommendation_rate_limit(
        str(interaction.user.id)
    )
    if not rate_limit["allowed"]:
        retry_minutes = max(
            1,
            (rate_limit["retry_after_seconds"] + 59) // 60,
        )
        await interaction.response.send_message(
            (
                "Foram submetidas demasiadas recomendações por esta conta. "
                f"Tenta novamente dentro de cerca de {retry_minutes} minutos."
            ),
            ephemeral=True,
        )
        return
    await interaction.response.defer(thinking=True, ephemeral=True)
    try:
        result = await asyncio.to_thread(
            submit_discord_recommendation,
            tipo.value,
            clean_title,
            clean_link,
            str(interaction.user.id),
        )
    except (RuntimeError, requests.RequestException) as exc:
        await interaction.followup.send(
            f"❌ {public_recommendation_error(exc)}",
            ephemeral=True,
        )
        return
    item = result.get("item") if isinstance(result, dict) else None
    resolved_title = (
        str(item.get("title") or clean_title)
        if isinstance(item, dict)
        else clean_title
    )
    await interaction.followup.send(
        (
            f"✅ Obrigado. A sugestão **{resolved_title}** foi recebida "
            "com sucesso e será analisada."
        ),
        ephemeral=True,
    )


# ===================== BOT EVENTS & COMMANDS =====================
@bot.event
async def on_ready():
    global _application_commands_synced, _persistent_views_registered
    if not _persistent_views_registered:
        bot.add_view(PostReviewView())
        bot.add_view(RecommendationApprovalView())
        _persistent_views_registered = True
    if not _application_commands_synced:
        try:
            synced = await bot.tree.sync()
            _application_commands_synced = True
            print(
                f"Comandos de aplicação sincronizados: {len(synced)}."
            )
        except discord.HTTPException as exc:
            print(f"Falha ao sincronizar comandos de aplicação: {exc}")
    print(f"Bot de Revisao Politometro ligado como {bot.user}!")

@bot.command(name="check")
async def check_queue(ctx):
    if not _is_authorized_reviewer(ctx.author):
        await ctx.reply("Não tens permissão para iniciar esta geração.")
        return
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
