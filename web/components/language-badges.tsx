"use client"

import { useState } from "react"
import { Badge } from "@/components/ui/badge"

interface Language {
  name: string
  color: string
  supported: boolean
  tooltip?: string
}

const LANGUAGES: Language[] = [
  { name: "Python", color: "bg-blue-500/15 text-blue-700 border-blue-300/40", supported: true },
  { name: "Go", color: "bg-cyan-500/15 text-cyan-700 border-cyan-300/40", supported: true },
  { name: "JavaScript", color: "bg-yellow-500/15 text-yellow-700 border-yellow-300/40", supported: true },
  { name: "TypeScript", color: "bg-blue-600/15 text-blue-800 border-blue-400/40", supported: true },
  { name: "Java", color: "bg-orange-500/15 text-orange-700 border-orange-300/40", supported: true },
  { name: "Shell", color: "bg-gray-500/15 text-gray-700 border-gray-300/40", supported: true },
  { name: "Ruby", color: "bg-red-500/15 text-red-700 border-red-300/40", supported: true },
  { name: "PHP", color: "bg-purple-500/15 text-purple-700 border-purple-300/40", supported: true },
  { name: "C#", color: "bg-emerald-500/15 text-emerald-700 border-emerald-300/40", supported: true },
  {
    name: "+ more via LLM",
    color: "bg-primary/10 text-primary border-primary/20",
    supported: true,
    tooltip: "Any language supported by your LLM (Kotlin, Swift, Rust, C/C++, Scala, …)",
  },
]

export function LanguageBadges() {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {LANGUAGES.map((lang, i) => (
        <div
          key={lang.name}
          className="relative"
          onMouseEnter={() => setHoveredIndex(i)}
          onMouseLeave={() => setHoveredIndex(null)}
        >
          <Badge
            variant="outline"
            className={`gap-1 cursor-default select-none text-[11px] font-medium transition-all duration-150 hover:-translate-y-0.5 ${lang.color}`}
          >
            {lang.supported && lang.tooltip == null && (
              <span className="text-[9px] text-emerald-500">&#10003;</span>
            )}
            {lang.name}
          </Badge>
          {lang.tooltip && hoveredIndex === i && (
            <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 z-50 w-64 rounded-lg border border-border bg-card px-3 py-2 text-xs text-muted-foreground shadow-lg">
              {lang.tooltip}
              <div className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-border" />
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
