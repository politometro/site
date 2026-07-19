"use client";

import { Analytics, type BeforeSendEvent } from "@vercel/analytics/next";

export default function AnalyticsGate() {
  return (
    <Analytics
      beforeSend={(event: BeforeSendEvent) => {
        try {
          if (window.localStorage.getItem("va-disable") === "true") {
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
