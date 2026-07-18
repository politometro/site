import os
import sys
import time
import threading
import asyncio
import gradio as gr

# Try to import spaces (ZeroGPU library), fallback to dummy decorator for local runs
try:
    import spaces
except ImportError:
    class spaces:
        @staticmethod
        def GPU(func):
            return func

# Force unbuffered output so we can see logs in real-time
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# A dummy function with the literal decorator so Hugging Face detects it on startup
@spaces.GPU
def keep_zerogpu_happy():
    print("⚡ ZeroGPU function check: OK!")
    return "Happy"

# Import bot and token
from discord_reviewer import bot, TOKEN
from twitch_bot import run_twitch_bot_forever, twitch_configured

def start_bot():
    # Wait 15 seconds to let Gradio fully initialize first
    print("⏳ A aguardar 15 segundos para o Gradio inicializar...")
    time.sleep(15)
    
    # Run the dummy GPU function once to register GPU activity
    try:
        keep_zerogpu_happy()
    except Exception as e:
        print(f"Aviso ao chamar função GPU: {e}")
        
    print("🚀 A iniciar o Bot de Discord do Politómetro...")
    if not TOKEN:
        print("❌ Erro: DISCORD_BOT_TOKEN não configurado no ambiente!")
        return
    try:
        # Set new event loop for this background thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Run the bot
        bot.run(TOKEN)
    except Exception as e:
        print(f"❌ Erro crítico no Bot de Discord: {e}")

# Run the bot in a background thread
threading.Thread(target=start_bot, daemon=True).start()

if twitch_configured():
    threading.Thread(target=run_twitch_bot_forever, daemon=True).start()
else:
    print("Bot de Twitch inativo: configura TWITCH_BOT_USERNAME, TWITCH_OAUTH_TOKEN e TWITCH_CHANNELS para ativar.")

# Build a simple Gradio UI to keep Hugging Face happy
with gr.Blocks() as demo:
    gr.Markdown("# 🤖 Politómetro Discord Bot")
    gr.Markdown("O bot está online e a correr em segundo plano na nuvem de forma gratuita!")
    gr.Markdown("---")
    gr.Markdown("👉 Envia uma **Mensagem Privada (DM)** ao bot no Discord com o comando `!check` para iniciar a revisão.")
    gr.Markdown("👉 Podes também fazer perguntas sobre os programas políticos diretamente na DM dele!")
    gr.Markdown("👉 Se configurares a Twitch, o bot também responde quando for mencionado no chat.")

if __name__ == "__main__":
    # Launch the interface
    demo.launch()
