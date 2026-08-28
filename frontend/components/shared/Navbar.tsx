"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu, X } from "lucide-react";
import { cn } from "@/lib/utils";

const links = [
  { href: "/", label: "Home" },
  { href: "/dashboard", label: "Dashboard" },
  { href: "/docs", label: "Documentation" },
  { href: "/contact", label: "Contact" },
];

export default function Navbar() {
  const pathname = usePathname();
  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const handler = () => setScrolled(window.scrollY > 16);
    handler();
    window.addEventListener("scroll", handler);
    return () => window.removeEventListener("scroll", handler);
  }, []);

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(href + "/");

  return (
    <header
      className={cn(
        "fixed top-0 inset-x-0 z-50 transition-all duration-200",
        scrolled
          ? "glass-strong border-b border-[var(--line)]"
          : "bg-transparent border-b border-transparent"
      )}
    >
      <nav className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
        <Link href="/" className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-md bg-gradient-to-br from-violet-600 to-violet-800 flex items-center justify-center">
            <span className="text-[var(--text)] font-bold text-sm">A</span>
          </div>
          <span className="text-[var(--text)] font-semibold text-base tracking-tight">
            Agent<span className="text-violet-400">Forge</span>
          </span>
        </Link>

        <div className="hidden md:flex items-center gap-1">
          {links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className={cn(
                "px-4 py-2 text-sm rounded-md transition-colors",
                isActive(link.href)
                  ? "text-[var(--text)] bg-[var(--panel-2)]"
                  : "text-[var(--muted)] hover:text-[var(--text)] hover:bg-[var(--panel-2)]"
              )}
            >
              {link.label}
            </Link>
          ))}
        </div>

        <div className="hidden md:flex items-center">
          <Link
            href="/dashboard"
            className="text-sm font-medium text-[var(--text)] bg-violet-600 hover:bg-violet-500 px-4 py-2 rounded-md transition-colors"
          >
            Launch Console
          </Link>
        </div>

        <button
          className="md:hidden text-[var(--muted)] hover:text-[var(--text)] p-2"
          onClick={() => setMenuOpen((v) => !v)}
          aria-label="Toggle navigation"
        >
          {menuOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
        </button>
      </nav>

      {menuOpen && (
        <div className="md:hidden glass-strong border-t border-[var(--line)] px-6 py-3">
          {links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              onClick={() => setMenuOpen(false)}
              className={cn(
                "block py-3 text-sm border-b border-[var(--line)] last:border-0",
                isActive(link.href) ? "text-[var(--text)]" : "text-[var(--muted)]"
              )}
            >
              {link.label}
            </Link>
          ))}
          <Link
            href="/dashboard"
            onClick={() => setMenuOpen(false)}
            className="mt-3 block text-center text-sm font-medium text-[var(--text)] bg-violet-600 hover:bg-violet-500 py-2.5 rounded-md transition-colors"
          >
            Launch Console
          </Link>
        </div>
      )}
    </header>
  );
}
