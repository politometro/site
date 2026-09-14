// Classificador temático leve para propostas de programas eleitorais.
// A pontuação conta ocorrências de palavras-chave por categoria; a categoria
// com maior pontuação ganha. Sem correspondências, a proposta fica em
// "Outras medidas". É heurístico por natureza — o objetivo é organizar a
// leitura lado a lado, não classificação académica.

export interface ProgramCategory {
  id: string;
  label: string;
  keywords: string[];
}

export const PROGRAM_CATEGORIES: ProgramCategory[] = [
  {
    id: "saude",
    label: "Saúde",
    keywords: [
      "saúde", "saude", "sns", "hospital", "hospitais", "saúde mental",
      "medicamento", "medicamentos", "médico", "médicos", "enfermagem",
      "enfermeiros", "utente", "utentes", "consultas", "urgência",
      "urgências", "cuidados de saúde", "cuidados primários", "vigilância",
    ],
  },
  {
    id: "educacao",
    label: "Educação",
    keywords: [
      "educação", "educacao", "escola", "escolas", "professor", "professores",
      "universidade", "universidades", "ensino", "alunos", "estudantes",
      "formação", "formacao", "currículo", "manuais escolares", "creche",
      "creches", "pré-escolar", "ensino superior", "ensino secundário",
      "bolseiro", "bolsas de estudo",
    ],
  },
  {
    id: "economia",
    label: "Economia e Emprego",
    keywords: [
      "economia", "emprego", "empregos", "empresa", "empresas", "trabalho",
      "trabalhadores", "salário", "salários", "salário mínimo", "investimento",
      "indústria", "comércio", "turismo", "pme", "empreendedorismo",
      "competitividade", "exportações", "desemprego", "contratação",
      "produtividade", "crescimento económico",
    ],
  },
  {
    id: "habitacao",
    label: "Habitação",
    keywords: [
      "habitação", "habitacao", "arrendamento", "arrendar", "casa", "casas",
      "renda", "rendas", "solos", "construção", "construções", "moradia",
      "moradias", "fogos", "casa própria", "apoio à habitação", "crise da habitação",
    ],
  },
  {
    id: "seguranca",
    label: "Segurança e Justiça",
    keywords: [
      "segurança", "segurança interna", "polícia", "polícias", "policia",
      "criminalidade", "crime", "crimes", "justiça", "justica", "tribunal",
      "tribunais", "prisão", "prisões", "delito", "segurança pública",
      "forças de segurança", "prevenção criminal",
    ],
  },
  {
    id: "ambiente",
    label: "Ambiente e Energia",
    keywords: [
      "ambiente", "clima", "climático", "energia", "energias", "renováveis",
      "carbono", "neutralidade", "floresta", "florestas", "resíduos",
      "reciclagem", "água", "recursos hídricos", "biodiversidade",
      "transição energética", "descarbonização", "poluição",
    ],
  },
  {
    id: "transportes",
    label: "Transportes e Mobilidade",
    keywords: [
      "transportes", "transporte", "mobilidade", "ferroviária", "comboio",
      "comboios", "ferrovia", "estradas", "rodovias", "metro", "metropolitanos",
      "autocarros", "autocarro", "bilhete", "passe ferroviário", "aeroporto",
      "aeroportos", "portos", "logística", "mobilidade sustentável",
    ],
  },
  {
    id: "impostos",
    label: "Impostos e Finanças Públicas",
    keywords: [
      "imposto", "impostos", "irs", "iva", "irc", "fiscal", "fiscalidade",
      "dívida", "divida", "défice", "defice", "despesa pública", "orçamento",
      "orçamentos do estado", "tributação", "escalões", "coimas",
      "finanças públicas", "disciplina orçamental",
    ],
  },
  {
    id: "estado",
    label: "Estado e Democracia",
    keywords: [
      "estado", "democracia", "democrático", "transparência", "corrupção",
      "governo", "governação", "administração pública", "descentralização",
      "poder local", "regionalização", "municípios", "parlamento",
      "eleições", "referendos", "serviço público", "serviços públicos",
      "simplificação administrativa", "estado social", "constituição",
    ],
  },
  {
    id: "social",
    label: "Proteção Social e Família",
    keywords: [
      "pensões", "pensoes", "segurança social", "pobreza", "pobres",
      "apoio social", "família", "famílias", "crianças", "idosos", "terceira idade",
      "inclusão", "deficiência", "incapacidade", "abono de família",
      "rendimento social", "complemento", "proteção social", "cuidados informais",
    ],
  },
  {
    id: "agricultura",
    label: "Agricultura, Mar e Pescas",
    keywords: [
      "agricultura", "agricultores", "agricultor", "pescas", "pescadores",
      "rural", "mundo rural", "terra", "agroalimentar", "pastorícia",
      "vinho", "vinhos", "olival", "pecuária", "florestal", "interior",
      "produtos alimentares", "soberania alimentar",
    ],
  },
  {
    id: "cultura",
    label: "Cultura e Desporto",
    keywords: [
      "cultura", "cultural", "desporto", "desportiva", "desportivo", "cinema",
      "música", "teatro", "património", "museus", "bibliotecas", "livro",
      "livros", "leitura", "festival", "festivais", "atletas", "clube",
      "clubes", "prática desportiva",
    ],
  },
  {
    id: "imigracao",
    label: "Imigração e Nacionalidade",
    keywords: [
      "imigração", "imigracao", "imigrantes", "imigrante", "migração",
      "migrações", "nacionalidade", "acolhimento", "refugiados", "asilo",
      "fronteira", "fronteiras", "integração de imigrantes", "entrada de estrangeiros",
      "retorno", "repatriamento",
    ],
  },
  {
    id: "europa",
    label: "Europa e Relações Externas",
    keywords: [
      "europa", "europeia", "europeias", "europeu", "união europeia",
      "diplomacia", "política externa", "defesa", "nato", "otan",
      "cooperação internacional", "lusofonia", "países lusófonos",
      "tratados", "consulado", "embaixadas", "relações internacionais",
    ],
  },
  {
    id: "ciencia",
    label: "Ciência, Inovação e Digital",
    keywords: [
      "digital", "ciência", "ciencia", "tecnologia", "inovação", "inovacao",
      "investigação científica", "i+d", "inteligência artificial",
      "cibersegurança", "conectividade", "redes 5g", "fibra ótica",
      "transformação digital", "startups", "competências digitais",
      "transição digital", "dados abertos",
    ],
  },
];

const FALLBACK_CATEGORY: ProgramCategory = {
  id: "outras",
  label: "Outras medidas",
  keywords: [],
};

const COMPILED = PROGRAM_CATEGORIES.map((category) => ({
  category,
  patterns: category.keywords.map((keyword) => ({
    text: keyword,
    regex: new RegExp(
      `(^|[^\\p{L}])${keyword.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}([^\\p{L}]|$)`,
      "iu",
    ),
  })),
}));

export function classifyStatement(statement: string): ProgramCategory {
  const normalized = (statement || "").toLowerCase();
  let best: { category: ProgramCategory; score: number } | null = null;
  for (const { category, patterns } of COMPILED) {
    let score = 0;
    for (const { text, regex } of patterns) {
      if (regex.test(normalized)) {
        // Pondera por tamanho da palavra-chave: termos específicos pesam mais.
        score += 1 + Math.min(1, text.length / 24);
      }
    }
    if (score > 0 && (!best || score > best.score)) {
      best = { category, score };
    }
  }
  return best?.category ?? FALLBACK_CATEGORY;
}

export const OTHER_CATEGORY_LABEL = FALLBACK_CATEGORY.label;
