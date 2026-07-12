"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useEffect, useRef } from "react";
import styles from "./Header.module.css";

export default function Header() {
  const pathname = usePathname();
  const [isOpen, setIsOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (event: MouseEvent) => {
      if (
        menuRef.current &&
        !menuRef.current.contains(event.target as Node) &&
        buttonRef.current &&
        !buttonRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isOpen]);

  return (
    <>
      <header className={`${styles.header} glass`}>
        <Link href="/" className={styles.logoContainer} onClick={() => setIsOpen(false)}>
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

        <button 
          ref={buttonRef}
          className={styles.hamburger} 
          onClick={() => setIsOpen(!isOpen)}
          aria-label="Toggle navigation menu"
        >
          {isOpen ? (
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          ) : (
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="3" y1="12" x2="21" y2="12"></line>
              <line x1="3" y1="6" x2="21" y2="6"></line>
              <line x1="3" y1="18" x2="21" y2="18"></line>
            </svg>
          )}
        </button>

        <div className={styles.actions}>
          {/* Actions empty - credentials are fully secure on the server side */}
        </div>
      </header>

      {isOpen && (
        <div ref={menuRef} className={`${styles.mobileMenu} glass`}>
          <Link 
            href="/" 
            className={`${styles.mobileNavLink} ${pathname === "/" ? styles.mobileActive : ""}`}
            onClick={() => setIsOpen(false)}
          >
            Escrutínio IA
          </Link>
          <Link 
            href="/sugestoes" 
            className={`${styles.mobileNavLink} ${pathname === "/sugestoes" ? styles.mobileActive : ""}`}
            onClick={() => setIsOpen(false)}
          >
            Sugerir Conteúdo
          </Link>
          <Link 
            href="/recomendacoes" 
            className={`${styles.mobileNavLink} ${pathname === "/recomendacoes" ? styles.mobileActive : ""}`}
            onClick={() => setIsOpen(false)}
          >
            Recomendações
          </Link>
          <Link 
            href="/documentacao" 
            className={`${styles.mobileNavLink} ${pathname === "/documentacao" ? styles.mobileActive : ""}`}
            onClick={() => setIsOpen(false)}
          >
            Documentação
          </Link>
        </div>
      )}
    </>
  );
}
