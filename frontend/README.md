# AgentForge Frontend

Professional Next.js 14 frontend for the AgentForge multi-agent code generation system.

## Stack

- **Next.js 14** (App Router)
- **TypeScript**
- **Tailwind CSS** with custom design system
- **Framer Motion** for animations
- **Lucide React** for icons

## Setup

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000)

## Pages

| Route | Description |
|-------|-------------|
| `/` | Landing page with hero, features, how-it-works |
| `/login` | Sign in |
| `/register` | Create account |
| `/dashboard` | Stats, recent projects, activity feed |
| `/projects` | Project list with grid/list toggle and filters |
| `/projects/new` | Create new project + launch workflow |
| `/profile` | User info, LLM provider settings, usage stats |

## Design System

- **Background**: `#050508`
- **Surface**: `#0c0c10` / `#111116`
- **Accent**: Violet `#7c3aed` → `#a78bfa`
- **Font**: Inter
- Custom utilities: `.glass`, `.glass-strong`, `.gradient-text`, `.shimmer-btn`
