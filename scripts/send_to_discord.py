import os
import sys
import discord
from discord.ext import commands
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
raw_channel = os.environ.get("DISCORD_REVIEW_CHANNEL_ID", "").strip()

if not raw_channel or raw_channel == "0":
    print("⚠️ Aviso: DISCORD_REVIEW_CHANNEL_ID está vazio ou é 0. O envio para o Discord foi ignorado.")
    sys.exit(0)

try:
    CHANNEL_ID = int(raw_channel)
except ValueError:
    print(f"❌ Erro: '{raw_channel}' não é um ID de canal válido (deve ser um número).")
    sys.exit(1)

PING_EVERYONE = os.environ.get("PING_EVERYONE", "false").lower() == "true"

# Determine paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
IMAGE_PATH = os.path.join(ROOT_DIR, "website", "public", "current_post.png")
CAPTION_PATH = os.path.join(ROOT_DIR, "website", "public", "current_caption.txt")

if not os.path.exists(IMAGE_PATH) or not os.path.exists(CAPTION_PATH):
    print("❌ Erro: Ficheiros current_post.png ou current_caption.txt em falta!")
    sys.exit(1)

with open(CAPTION_PATH, "r", encoding="utf-8") as f:
    caption = f.read()

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

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
        
        file_to_send = discord.File(IMAGE_PATH, filename="post.png")
        embed.set_image(url="attachment://post.png")
        
        view = PostReviewView()
        
        # Determine the ping message prefix
        msg_prefix = "@everyone 📅 **Nova proposta semanal pronta para revisão!**" if PING_EVERYONE else "📅 **Nova proposta semanal pronta para revisão!**"
        
        # 1. Send the review image embed card
        await channel.send(content=msg_prefix, file=file_to_send, embed=embed, view=view)
        
        # 2. Send the full caption as a separate copy-pasteable message
        await channel.send(content=f"📝 **Legenda do Instagram (copiar/colar):**\n\n{caption}")
        print("🎉 Proposta e legenda enviadas para o Discord!")
    except Exception as e:
        print(f"❌ Erro ao enviar proposta: {e}")
    finally:
        await bot.close()

if __name__ == "__main__":
    bot.run(TOKEN)
