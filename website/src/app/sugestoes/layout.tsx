import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Sugerir Recomendações",
  description:
    "Submeta e sugira conteúdos de relevância política ou histórica para a comunidade do Politómetro.",
  alternates: {
    canonical: "/sugestoes",
  },
  openGraph: {
    title: "Sugerir Recomendações | Politómetro",
    description:
      "Envie a sua sugestão de livro, podcast, filme ou artigo político para o Politómetro.",
    url: "/sugestoes",
    siteName: "Politómetro",
    locale: "pt_PT",
    type: "website",
  },
};

export default function SugestoesLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return children;
}
