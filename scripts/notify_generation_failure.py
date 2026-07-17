"""Send an operational Discord alert without exposing a defective post."""

import os
import sys

import requests


def main():
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel_id = os.environ.get("DISCORD_REVIEW_CHANNEL_ID", "").strip()
    run_url = os.environ.get("GITHUB_RUN_URL", "").strip()
    failure_kind = os.environ.get("FAILURE_KIND", "generation").strip().lower()
    if not token or not channel_id:
        print("[ERROR] Discord failure alert skipped: credentials are missing.")
        sys.exit(1)

    details = f"\nDiagnóstico: {run_url}" if run_url else ""
    if failure_kind == "publish":
        content = (
            "⚠️ **A publicação no Instagram não foi concluída.** O rascunho "
            "aprovado não será marcado como publicado sem confirmação da Meta. "
            "O recibo impede duplicações quando o workflow for repetido."
            f"{details}"
        )
    elif failure_kind == "late_generation":
        content = (
            "⏱️ **A execução semanal arrancou fora da janela segura.** "
            "Para evitar uma proposta enviada com horas de atraso, esta execução "
            "terminou antes de gerar conteúdo. A manutenção diária continuará a "
            "renovar a reserva, mas esta semana não haverá publicação automática "
            "tardia."
            f"{details}"
        )
    else:
        content = (
            "⚠️ **A proposta semanal não foi enviada.** O controlo de qualidade "
            "não encontrou quatro recomendações totalmente verificadas ou uma "
            "fonte temporária falhou. Nenhum post incorreto será publicado. A "
            "reserva será atualizada e só haverá duas recuperações automáticas "
            "na janela desta noite; depois disso o processo para, sem atrasos "
            "indefinidos."
            f"{details}"
        )

    response = requests.post(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
        },
        json={"content": content},
        timeout=20,
    )
    if not response.ok:
        print(
            f"[ERROR] Discord failure alert returned {response.status_code}: "
            f"{response.text}"
        )
        sys.exit(1)
    print(f"[OK] Discord {failure_kind}-failure alert sent.")


if __name__ == "__main__":
    main()
