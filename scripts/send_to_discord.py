import os
import sys
import json
import discord
from discord.ext import commands
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
raw_channel = os.environ.get("DISCORD_REVIEW_CHANNEL_ID", "").strip()

if not raw_channel or raw_channel == "0":
    print("⚠️ Aviso: DISCORD_REVIEW_CHANNEL_ID está vazio ou é 0. O envio para o Discord foi ignorado.")
    sys.exit(1)

if not TOKEN:
    print("Erro: DISCORD_BOT_TOKEN está vazio.")
    sys.exit(1)

try:
    CHANNEL_ID = int(raw_channel)
except ValueError:
    print(f"❌ Erro: '{raw_channel}' não é um ID de canal válido (deve ser um número).")
    sys.exit(1)

PING_EVERYONE = os.environ.get("PING_EVERYONE", "false").lower() == "true"

# Determine paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
IMAGE_PATH = os.path.join(ROOT_DIR, "website", "public", "current_post.jpg")
CAPTION_PATH = os.path.join(ROOT_DIR, "website", "public", "current_caption.txt")
NOTIFICATION_PATH = os.path.join(SCRIPT_DIR, "review_notification.json")


def _write_notification(value):
    temporary = NOTIFICATION_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temporary, NOTIFICATION_PATH)

if not os.path.exists(IMAGE_PATH) or not os.path.exists(CAPTION_PATH):
    print("❌ Erro: Ficheiros current_post.jpg ou current_caption.txt em falta!")
    sys.exit(1)

with open(CAPTION_PATH, "r", encoding="utf-8") as f:
    caption = f.read()

intents = discord.Intents.default()
# Enable message content intent to suppress the bot commands warning
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
SEND_ERROR = None

# Persistent view with matching custom_ids
class PostReviewView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Aprovar", style=discord.ButtonStyle.green, custom_id="approve_post", emoji="✅")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(label="Rejeitar", style=discord.ButtonStyle.red, custom_id="reject_post", emoji="❌")
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

@bot.event
async def on_ready():
    global SEND_ERROR
    print(f"✅ Ligado como {bot.user} para enviar a proposta semanal...")
    try:
        channel = bot.get_channel(CHANNEL_ID)
        if not channel:
            channel = await bot.fetch_channel(CHANNEL_ID)
            
        embed = discord.Embed(
            title="📅 Revisão Semanal de Recomendações - Politómetro",
            description="Verifica a imagem em anexo. A legenda sugerida segue na mensagem abaixo.",
            color=discord.Color.blue()
        )
        
        # Load links from review_draft.json
        draft_path = os.path.join(SCRIPT_DIR, "review_draft.json")
        if not os.path.exists(draft_path):
            raise FileNotFoundError("review_draft.json está em falta")
        if os.path.exists(draft_path):
            try:
                with open(draft_path, "r", encoding="utf-8") as df:
                    draft_data = json.load(df)
                draft_id = draft_data.get("draft_id")
                content_hash = draft_data.get("content_hash")
                if not draft_id or not content_hash:
                    raise ValueError("rascunho sem draft_id/content_hash")
                q1 = draft_data.get("q1", {})
                q2 = draft_data.get("q2", {})
                q3 = draft_data.get("q3", {})
                q4 = draft_data.get("q4", {})
                for qkey, item in {
                    "Q1": q1,
                    "Q2": q2,
                    "Q3": q3,
                    "Q4": q4,
                }.items():
                    if not str(item.get("link") or "").startswith(
                        ("http://", "https://")
                    ):
                        raise ValueError(f"{qkey} sem link verificável")
                
                links_text = (
                    f"📚 **Q1 ({q1.get('category', 'Livro')})**: [{q1.get('title')}]({q1.get('link')})\n"
                    f"🎙️ **Q2 ({q2.get('category', 'Podcast')})**: [{q2.get('title')}]({q2.get('link')})\n"
                    f"🎬 **Q3 ({q3.get('category', 'Filme')})**: [{q3.get('title')}]({q3.get('link')})\n"
                    f"⭐ **Q4 ({q4.get('category', 'Destaque')})**: [{q4.get('title')}]({q4.get('link')})"
                )
                embed.add_field(name="🔗 Links para Verificação", value=links_text, inline=False)
                embed.set_footer(
                    text=f"Rascunho: {draft_id} | Hash: {content_hash[:16]}"
                )
                if draft_data.get("is_test"):
                    embed.description = (
                        "TESTE — verifica imagem e ligações. Este rascunho não "
                        "pode ser publicado no Instagram."
                    )
            except Exception as e:
                print(f"❌ Rascunho inválido: {e}")
                raise
        
        file_to_send = discord.File(IMAGE_PATH, filename="post.jpg")
        embed.set_image(url="attachment://post.jpg")
        
        view = PostReviewView()
        
        # Determine the ping message prefix
        msg_prefix = "@everyone 📅 **Nova proposta semanal pronta para revisão!**" if PING_EVERYONE else "📅 **Nova proposta semanal pronta para revisão!**"

        # A recovery run may arrive after Discord succeeded but before GitHub
        # stored its receipt. Reuse the exact existing card in that case.
        expected_footer = f"Rascunho: {draft_id} | Hash: {content_hash[:16]}"
        caption_marker = f"Rascunho: `{draft_id}`"
        caption_content = (
            f"📝 **Legenda do Instagram (copiar/colar):**\n\n{caption}"
            f"\n\n— {caption_marker}"
        )
        existing_review = None
        existing_caption = None
        async for existing in channel.history(limit=100):
            if any(
                existing_embed.footer
                and existing_embed.footer.text == expected_footer
                for existing_embed in existing.embeds
            ):
                existing_review = existing
            if caption_marker in (existing.content or ""):
                existing_caption = existing
        if existing_review:
            if not existing_caption:
                existing_caption = await channel.send(content=caption_content)
            _write_notification(
                {
                    "schema_version": 1,
                    "draft_id": draft_id,
                    "content_hash": content_hash,
                    "review_message_id": str(existing_review.id),
                    "caption_message_id": str(existing_caption.id),
                    "reused": True,
                }
            )
            print("Proposta já presente no Discord; duplicado evitado.")
            return
        
        # 1. Send the review image embed card
        review_message = await channel.send(
            content=msg_prefix,
            file=file_to_send,
            embed=embed,
            view=view,
        )
        
        # 2. Send the full caption as a separate copy-pasteable message
        caption_message = await channel.send(content=caption_content)
        _write_notification(
            {
                "schema_version": 1,
                "draft_id": draft_id,
                "content_hash": content_hash,
                "review_message_id": str(review_message.id),
                "caption_message_id": str(caption_message.id),
                "reused": False,
            }
        )
        print("🎉 Proposta e legenda enviadas para o Discord!")
    except Exception as e:
        print(f"❌ Erro ao enviar proposta: {e}")
        SEND_ERROR = str(e)
    finally:
        await bot.close()

if __name__ == "__main__":
    bot.run(TOKEN)
    if SEND_ERROR:
        sys.exit(1)
