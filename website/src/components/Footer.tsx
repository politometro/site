"use client";

import { useEffect, useState } from "react";
import styles from "./Footer.module.css";

export default function Footer() {
  const [year, setYear] = useState<number>(2026);

  useEffect(() => {
    setYear(new Date().getFullYear());
  }, []);

  return (
    <footer className={styles.footer}>
      <div className={styles.container}>
        <span>© {year}</span>
        <span className={styles.dot}>·</span>
        <span className={styles.brand}>Politiza-te</span>
      </div>
    </footer>
  );
}
