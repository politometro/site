"use client";

import { Analytics, type BeforeSendEvent } from "@vercel/analytics/next";
import { useEffect } from "react";

export default function AnalyticsGate() {
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("analytics") === "off") {
      window.localStorage.setItem("va-disable", "true");
    }
    if (new URLSearchParams(window.location.search).get("analytics") === "on") {
      window.localStorage.removeItem("va-disable");
    }
  }, []);

  return (
    <Analytics
      beforeSend={(event: BeforeSendEvent) => {
        try {
          const queryDisabled =
            new URLSearchParams(window.location.search).get("analytics") === "off";
          if (queryDisabled || window.localStorage.getItem("va-disable") === "true") {
            return null;
          }
        } catch {
          // Analytics must never interfere with page rendering.
        }
        return event;
      }}
    />
  );
}
