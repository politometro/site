import { NextRequest, NextResponse } from "next/server";
import fs from "fs";
import path from "path";

function parseRecommendations(contentStr: string) {
  try {
    const parsed = JSON.parse(contentStr || "{}");
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return {
        queue: parsed.queue || [],
        history: parsed.history || []
      };
    }
    // Legacy support (if it was an array)
    if (Array.isArray(parsed)) {
      return { queue: parsed, history: [] };
    }
    return { queue: [], history: [] };
  } catch (e) {
    return { queue: [], history: [] };
  }
}

export async function GET(req: NextRequest) {
  try {
    const token = process.env.GITHUB_TOKEN;
    const repo = process.env.GITHUB_REPO;
    const branch = process.env.GITHUB_BRANCH || "main";

    // If GitHub credentials exist on the server, sync from GitHub
    if (token && repo && !token.includes("your_actual")) {
      const res = await fetch(
        `https://api.github.com/repos/${repo}/contents/website/public/recommendations.json?ref=${branch}`,
        {
          headers: {
            Authorization: `token ${token}`,
            Accept: "application/vnd.github.v3+json",
            "Cache-Control": "no-cache"
          },
        }
      );

      if (res.ok) {
        const data = await res.json();
        const content = Buffer.from(data.content, "base64").toString("utf-8");
        const parsed = parseRecommendations(content);
        
        const now = new Date().getTime();
        const activeHistory = parsed.history.filter((item: any) => {
          if (item.is_test && item.expires_at) {
            const expTime = new Date(item.expires_at).getTime();
            return expTime > now;
          }
          return true;
        });

        return NextResponse.json({
          queue: parsed.queue,
          history: activeHistory,
          sha: data.sha,
          source: "github"
        });
      }
    }

    // Fallback: Read from local filesystem (localhost mode)
    const localPath = path.join(process.cwd(), "public", "recommendations.json");
    if (fs.existsSync(localPath)) {
      const content = fs.readFileSync(localPath, "utf-8");
      const parsed = parseRecommendations(content);
      
      const now = new Date().getTime();
      const activeHistory = parsed.history.filter((item: any) => {
        if (item.is_test && item.expires_at) {
          const expTime = new Date(item.expires_at).getTime();
          return expTime > now;
        }
        return true;
      });

      return NextResponse.json({
        queue: parsed.queue,
        history: activeHistory,
        sha: null,
        source: "local"
      });
    }

    return NextResponse.json({ queue: [], history: [], sha: null, source: "empty" });
  } catch (err: any) {
    console.error("Error loading suggestions:", err);
    return NextResponse.json({ error: err.message }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const { queue, history, sha } = await req.json();
    const token = process.env.GITHUB_TOKEN;
    const repo = process.env.GITHUB_REPO;
    const branch = process.env.GITHUB_BRANCH || "main";

    const payload = { queue: queue || [], history: history || [] };

    // 1. If GitHub credentials exist on the server, push to GitHub
    if (token && repo && !token.includes("your_actual")) {
      const body: any = {
        message: "Update recommendations [website sync]",
        content: Buffer.from(JSON.stringify(payload, null, 2)).toString("base64"),
        branch,
      };

      if (sha) {
        body.sha = sha;
      }

      const res = await fetch(
        `https://api.github.com/repos/${repo}/contents/website/public/recommendations.json`,
        {
          method: "PUT",
          headers: {
            Authorization: `token ${token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify(body),
        }
      );

      if (res.ok) {
        const data = await res.json();
        return NextResponse.json({
          success: true,
          sha: data.content.sha,
          source: "github"
        });
      } else {
        const errText = await res.text();
        throw new Error(`Erro ao enviar para o GitHub: ${res.status} - ${errText}`);
      }
    }

    // 2. Fallback: Write directly to local file (localhost mode)
    const localPath = path.join(process.cwd(), "public", "recommendations.json");
    fs.writeFileSync(localPath, JSON.stringify(payload, null, 2), "utf-8");
    
    return NextResponse.json({
      success: true,
      sha: null,
      source: "local"
    });
  } catch (err: any) {
    console.error("Error saving suggestions:", err);
    return NextResponse.json({ error: err.message }, { status: 500 });
  }
}
