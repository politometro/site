import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Comparar Programas Eleitorais | Politómetro",
  description:
    "Compare de 2 a 4 programas eleitorais portugueses lado a lado, por categoria temática — saúde, economia, habitação, educação e mais — com as propostas apresentadas no próprio site.",
  alternates: {
    canonical: "/programas",
  },
  openGraph: {
    title: "Comparar Programas Eleitorais | Politómetro",
    description:
      "Compare de 2 a 4 programas eleitorais portugueses lado a lado, organizados por categorias temáticas.",
    url: "/programas",
    siteName: "Politómetro",
    locale: "pt_PT",
    type: "website",
    images: [
      {
        url: "/banner.jpg",
        width: 1200,
        height: 630,
        alt: "Comparação de programas eleitorais no Politómetro",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "Comparar Programas Eleitorais | Politómetro",
    description:
      "Compare de 2 a 4 programas eleitorais portugueses lado a lado, organizados por categorias temáticas.",
    images: ["/banner.jpg"],
  },
  robots: {
    index: true,
    follow: true,
  },
};

export default function ProgramasLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return children;
}
