import os
import requests

# Load webhook URL from environment variables
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

if not WEBHOOK_URL:
    print("❌ ERRO: A variável de ambiente DISCORD_WEBHOOK_URL não está definida!")
    exit(1)

# Determine paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
IMAGE_PATH = os.path.join(ROOT_DIR, "website", "public", "current_post.jpg")
CAPTION_PATH = os.path.join(ROOT_DIR, "website", "public", "current_caption.txt")

if not os.path.exists(IMAGE_PATH) or not os.path.exists(CAPTION_PATH):
    print("❌ ERRO: Ficheiros current_post.jpg ou current_caption.txt não encontrados!")
    exit(1)

# Read caption content
with open(CAPTION_PATH, "r", encoding="utf-8") as f:
    caption = f.read()

# Build payload
payload = {
    "content": f"📢 **Nova proposta de post gerada para revisão no GitHub Actions!**\n\n**Legenda:**\n```\n{caption[:1800]}\n```"
}

# Post to Discord Webhook
try:
    print("🚀 A enviar imagem e legenda para o Discord via Webhook...")
    with open(IMAGE_PATH, "rb") as img_file:
        files = {
            "file": ("current_post.jpg", img_file, "image/jpeg")
        }
        r = requests.post(WEBHOOK_URL, data=payload, files=files, timeout=30)
        
    if r.status_code in (200, 204):
        print("✅ Sucesso: Notificação enviada para o Discord!")
    else:
        print(f"❌ Falha: O webhook retornou o estado {r.status_code} - {r.text}")
except Exception as e:
    print(f"❌ Erro ao enviar para o Discord: {e}")
