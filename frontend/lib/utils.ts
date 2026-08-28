import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDate(date: string | Date) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(date));
}

export function formatRelativeTime(date: string | Date) {
  const now = new Date();
  const then = new Date(date);
  const diff = now.getTime() - then.getTime();
  const minutes = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);

  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  if (hours < 24) return `${hours}h ago`;
  return `${days}d ago`;
}

export const AGENTS = [
  { id: "pm_agent",           name: "PM Agent",           description: "Generates a complete Product Requirements Document", color: "violet" },
  { id: "architecture_agent", name: "Architecture Agent", description: "Designs system architecture and tech stack",         color: "blue" },
  { id: "developer_agent",    name: "Developer Agent",    description: "Writes production-ready source code",                color: "emerald" },
  { id: "qa_agent",           name: "QA Agent",           description: "Reviews code quality and generates tests",            color: "amber" },
];
