import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Notícias Recentes de Portugal | Politómetro",
  description:
    "Acompanha as notícias mais recentes da CNN Portugal, RTP, Expresso, Observador e Público, com títulos, resumos e ligações para as fontes originais.",
  alternates: {
    canonical: "/noticias",
  },
  openGraph: {
    title: "Notícias Recentes de Portugal | Politómetro",
    description:
      "Consulta num só lugar as notícias mais recentes dos principais órgãos de comunicação social portugueses.",
    url: "/noticias",
    siteName: "Politómetro",
    locale: "pt_PT",
    type: "website",
    images: [
      {
        url: "/banner.jpg",
        width: 1200,
        height: 630,
        alt: "Notícias recentes no Politómetro",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "Notícias Recentes de Portugal | Politómetro",
    description:
      "Notícias recentes da CNN Portugal, RTP, Expresso, Observador e Público, sempre ligadas às fontes originais.",
    images: ["/banner.jpg"],
  },
  robots: {
    index: true,
    follow: true,
  },
};

export default function NewsLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return children;
}
