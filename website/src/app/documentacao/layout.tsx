import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Documentação & Matriz Eleitoral",
  description:
    "Matriz completa de programas eleitorais, propostas partidárias e documentos oficiais dos partidos políticos portugueses.",
  alternates: {
    canonical: "/documentacao",
  },
  openGraph: {
    title: "Documentação & Matriz Eleitoral | Politómetro",
    description:
      "Consulte a matriz completa dos programas eleitorais e documentos oficiais dos partidos em Portugal.",
    url: "/documentacao",
    siteName: "Politómetro",
    locale: "pt_PT",
    type: "website",
  },
};

export default function DocumentacaoLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return children;
}
