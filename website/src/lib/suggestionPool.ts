// Sugestões de arranque: antes de existir qualquer resposta do bot, as
// sugestões são servidas deste conjunto curado (sem gastar quota de API nem
// latência no carregamento). Depois de cada resposta, a API
// /api/chat/suggestions gera sugestões ligadas à resposta.

export const STARTER_SUGGESTIONS: string[] = [
  "O que propõem os partidos para a saúde?",
  "Como evoluiu a proposta de descida de impostos do PS?",
  "O que diz o programa do CHEGA sobre imigração?",
  "Quais são as prioridades da IL para a economia?",
  "O que promete a AD para a habitação?",
  "Como votaram os partidos na mobilidade?",
  "O que propõe o Bloco de Esquerda para o trabalho?",
  "O que diz a Constituição sobre o direito à habitação?",
  "Que partidos propõem descida do IVA?",
  "O que muda no Orçamento do Estado para a saúde?",
  "Compara as propostas de educação de PS e PSD.",
  "O que prometem os partidos para as pensões?",
  "Que medidas existem para a transição energética?",
  "O que dizem os programas sobre o interior do país?",
  "Como é que os partidos propõem financiar o SNS?",
];

/** Escolhe 3 sugestões distintas, variando entre visitas (sem repetir a
 * combinação anterior na mesma sessão). */
export function pickStarterSuggestions(count = 3): string[] {
  if (typeof window === "undefined") {
    return STARTER_SUGGESTIONS.slice(0, count);
  }
  const last = Number(sessionStorage.getItem("politometro_starter_seed") ?? "-1");
  let seed = Math.floor(Math.random() * STARTER_SUGGESTIONS.length);
  if (seed === last) seed = (seed + 1) % STARTER_SUGGESTIONS.length;
  sessionStorage.setItem("politometro_starter_seed", String(seed));
  const rotated = [
    ...STARTER_SUGGESTIONS.slice(seed),
    ...STARTER_SUGGESTIONS.slice(0, seed),
  ];
  return rotated.slice(0, count);
}

/** Reserva de recurso: escolhe sugestões do conjunto por palavras-chave da
 * resposta, usado quando a geração por API não está disponível. */
export function fallbackSuggestions(lastAnswer: string, count = 3): string[] {
  const normalized = (lastAnswer || "").toLowerCase();
  const topicMap: Array<[RegExp, string[]]> = [
    [/saúde|sns|hospital|médic/, ["Que partidos propõem aumentar o financiamento do SNS?", "O que dizem os programas sobre farmácia hospitalar?", "Como variou a proposta de saúde do PS desde 2019?"]],
    [/imposto|irs|iva|irc|fiscal/, ["Que partidos propõem descida do IRS?", "Como funciona a proposta de redução do IVA de cada partido?", "Que medidas fiscais são comuns a mais partidos?"]],
    [/habitação|arrendamento|renda/, ["O que propõem os partidos para o arrendamento?", "Que medidas de habitação existem no último programa da AD?", "O que diz a Constituição sobre a habitação?"]],
    [/educação|escola|professor/, ["Que partidos propõem avaliação de professores?", "O que dizem os programas sobre creches?", "Como evoluiu o financiamento da educação nos orçamentos?"]],
    [/segurança|polícia|crime/, ["Que propostas existem para a segurança urbana?", "O que dizem os programas sobre as forças de segurança?", "Como votaram os partidos na última lei da segurança?"]],
    [/pensão|segurança social|idoso/, ["Que partidos propõem aumentar as pensões mínimas?", "O que dizem os programas sobre a sustentabilidade da segurança social?", "Que medidas existem para os idosos?"]],
    [/energia|ambiente|clima|carbono/, ["Que partidos propõem acelerar as renováveis?", "O que dizem os programas sobre a descarbonização?", "Que medidas existem para as florestas?"]],
    [/europeia|europa|bruxelas/, ["O que defende cada partido sobre o Quadro Financeiro da UE?", "Que propostas existem sobre a Europa na eleição europeia?", "Como se posicionam os partidos sobre a defesa europeia?"]],
  ];
  const matched: string[] = [];
  for (const [pattern, suggestions] of topicMap) {
    if (pattern.test(normalized)) matched.push(...suggestions);
    if (matched.length >= count) break;
  }
  if (matched.length < count) {
    for (const suggestion of STARTER_SUGGESTIONS) {
      if (matched.includes(suggestion)) continue;
      matched.push(suggestion);
      if (matched.length >= count) break;
    }
  }
  return matched.slice(0, count);
}
