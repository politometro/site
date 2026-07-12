"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import styles from "./Header.module.css";

export default function Header() {
  const pathname = usePathname();

  return (
    <header className={`${styles.header} glass`}>
      <Link href="/" className={styles.logoContainer}>
        <img src="/logo.png?v=5" alt="Politómetro" className={styles.logoImage} />
      </Link>

      <nav className={styles.nav}>
        <Link 
          href="/" 
          className={`${styles.navLink} ${pathname === "/" ? styles.active : ""}`}
        >
          Escrutínio IA
        </Link>
        <Link 
          href="/sugestoes" 
          className={`${styles.navLink} ${pathname === "/sugestoes" ? styles.active : ""}`}
        >
          Sugerir Conteúdo
        </Link>
        <Link 
          href="/recomendacoes" 
          className={`${styles.navLink} ${pathname === "/recomendacoes" ? styles.active : ""}`}
        >
          Recomendações
        </Link>
        <Link 
          href="/documentacao" 
          className={`${styles.navLink} ${pathname === "/documentacao" ? styles.active : ""}`}
        >
          Documentação
        </Link>
      </nav>

      <div className={styles.actions}>
        {/* Actions empty - credentials are fully secure on the server side */}
      </div>
    </header>
  );
}
