import type { Metadata } from "next";
import "./globals.css";
import Footer from "@/components/Footer";
import AnalyticsGate from "@/components/AnalyticsGate";

export const metadata: Metadata = {
  metadataBase: new URL("https://politometro.vercel.app"),
  title: "Politómetro - Análise de Programas Eleitorais Portugueses",
  description: "Explore, pesquise e compare propostas dos partidos políticos portugueses de forma rigorosa, imparcial e baseada exclusivamente em documentos oficiais.",
  keywords: [
    "Politómetro", "programas eleitorais", "Portugal", "partidos políticos", 
    "legislativas", "Constituição Portuguesa", "Orçamento do Estado", "eleições", 
    "escrutínio", "neutralidade", "informação política"
  ],
  authors: [{ name: "Politiza-te" }],
  creator: "Politiza-te",
  publisher: "Politiza-te",
  formatDetection: {
    email: false,
    address: false,
    telephone: false,
  },
  openGraph: {
    title: "Politómetro - Análise de Programas Eleitorais Portugueses",
    description: "Explore e compare as propostas dos partidos políticos de forma imparcial com fontes oficiais citadas.",
    url: "https://politometro.politiza-te.pt",
    siteName: "Politómetro",
    locale: "pt_PT",
    type: "website",
    images: [
      {
        url: "/banner.jpg",
        width: 1200,
        height: 630,
        alt: "Politómetro - O teu voto. A tua voz. A tua informação.",
      }
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "Politómetro - Análise de Programas Eleitorais Portugueses",
    description: "Pesquise e compare as propostas dos partidos com fontes oficiais citadas e neutralidade absoluta.",
    images: ["/banner.jpg"],
  },
  robots: {
    index: true,
    follow: true,
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="pt" suppressHydrationWarning>
      <body suppressHydrationWarning>
        <div className="ambient-glow"></div>
        <div style={{ display: "flex", flexDirection: "column", minHeight: "100dvh", width: "100%" }}>
          <div style={{ flex: "1 1 auto", width: "100%", display: "flex", flexDirection: "column" }}>
            {children}
          </div>
          <Footer />
        </div>
        <AnalyticsGate />
      </body>
    </html>
  );
}
