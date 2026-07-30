import type { MetadataRoute } from "next";

export default function sitemap(): MetadataRoute.Sitemap {
  const baseUrl = "https://politometro.vercel.app";

  return [
    {
      url: baseUrl,
      changeFrequency: "weekly",
      priority: 1.0,
    },
    {
      url: `${baseUrl}/documentacao`,
      changeFrequency: "weekly",
      priority: 0.9,
    },
    {
      url: `${baseUrl}/noticias`,
      changeFrequency: "daily",
      priority: 0.85,
    },
    {
      url: `${baseUrl}/recomendacoes`,
      changeFrequency: "weekly",
      priority: 0.8,
    },
    {
      url: `${baseUrl}/sugestoes`,
      changeFrequency: "monthly",
      priority: 0.6,
    },
  ];
}
