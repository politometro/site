import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Recomendações Culturais & Políticas",
  description:
    "Descubra livros, podcasts, filmes e ensaios recomendados sobre a política, história e economia de Portugal.",
  alternates: {
    canonical: "/recomendacoes",
  },
  openGraph: {
    title: "Recomendações Culturais & Políticas | Politómetro",
    description:
      "Livros, podcasts, filmes e artigos recomendados sobre a política e história de Portugal.",
    url: "/recomendacoes",
    siteName: "Politómetro",
    locale: "pt_PT",
    type: "website",
  },
};

export default function RecomendacoesLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return children;
}
