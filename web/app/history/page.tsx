"use client"

import { useEffect, useState, useCallback } from "react"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  Zap, ArrowLeft, Settings, ChevronDown, ChevronUp, ChevronLeft, ChevronRight,
  CheckCircle2, XCircle, Loader2, Clock, Download, RefreshCw, Filter,
  GitBranch, FileCode, AlertTriangle,
} from "lucide-react"
import { toast } from "sonner"

interface JobFile {
  file: string
  fixed: number
  skipped: number
  error?: string
}

interface Job {
  id: string
  status: string
  created_at: string
  started_at?: string
  request?: {
    project_key?: string
    repo_url?: string
    branch?: string
    pull_request?: string
    max_issues?: number
    dry_run?: boolean
  }
  result?: {
    fixed_count?: number
    fixed?: number
    skipped_count?: number
    skipped?: number
    total_issues?: number
    total?: number
    diff_stat?: string
    files?: JobFile[]
    dry_run?: boolean
  }
  fix_branch?: string
  error?: string
  total_issues?: number
}

interface PageData {
  jobs: Job[]
  total: number
  limit: number
  offset: number
}

const PAGE_SIZE = 20

const STATUS_STYLES: Record<string, string> = {
  completed: "border-emerald-500/20 bg-emerald-500/10 text-emerald-700",
  failed: "border-red-500/20 bg-red-500/10 text-red-700",
  running: "border-blue-500/20 bg-blue-500/10 text-blue-700",
  queued: "border-yellow-500/20 bg-yellow-500/10 text-yellow-700",
  cancelled: "border-gray-500/20 bg-gray-500/10 text-gray-600",
}

function StatusIcon({ status }: { status: string }) {
  if (status === "completed") return <CheckCircle2 className="h-4 w-4 text-emerald-500" />
  if (status === "failed") return <XCircle className="h-4 w-4 text-red-500" />
  if (status === "running") return <Loader2 className="h-4 w-4 animate-spin text-blue-500" />
  return <Clock className="h-4 w-4 text-gray-400" />
}

function formatDate(iso: string) {
  const d = new Date(iso)
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
}

function repoShortName(url?: string): string {
  if (!url || url === "local") return "local"
  const m = url.match(/[/:]([\w.-]+)\/([\w.-]+?)(?:\.git)?$/)
  if (m) return `${m[1]}/${m[2]}`
  return url.split("/").pop() || url
}

function detectLanguages(job: Job): string[] {
  const files = job.result?.files ?? []
  const exts = new Set(files.map(f => f.file.split(".").pop() ?? ""))
  const map: Record<string, string> = {
    py: "Python", go: "Go", js: "JavaScript", ts: "TypeScript",
    java: "Java", cs: "C#", rb: "Ruby", php: "PHP", sh: "Shell",
  }
  return [...exts].map(e => map[e]).filter(Boolean)
}

export default function HistoryPage() {
  const [data, setData] = useState<PageData>({ jobs: [], total: 0, limit: PAGE_SIZE, offset: 0 })
  const [loading, setLoading] = useState(true)
  const [offset, setOffset] = useState(0)
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())
  const [downloadingIds, setDownloadingIds] = useState<Set<string>>(new Set())

  // Filters
  const [filterStatus, setFilterStatus] = useState("all")
  const [filterRepo, setFilterRepo] = useState("")
  const [sortBy, setSortBy] = useState<"date" | "fixed" | "repo">("date")

  const fetchJobs = useCallback(async (off: number) => {
    setLoading(true)
    try {
      const r = await fetch(`/api/jobs?limit=${PAGE_SIZE}&offset=${off}`)
      if (!r.ok) throw new Error(await r.text())
      const d = await r.json()
      setData(d)
    } catch (e: any) {
      toast.error(`Failed to load jobs: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchJobs(offset) }, [offset, fetchJobs])

  const toggleExpand = (id: string) => {
    setExpandedIds(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const handleDownloadPatch = async (job: Job) => {
    if (!job.fix_branch) {
      toast.error("No fix branch available for this job")
      return
    }
    setDownloadingIds(prev => new Set(prev).add(job.id))
    try {
      // Fetch the full job to get diff_stat
      const r = await fetch(`/api/jobs/${job.id}`)
      if (!r.ok) throw new Error(await r.text())
      const full = await r.json()
      const diffStat = full.result?.diff_stat || ""
      const files = full.result?.files || []

      // Build a synthetic patch text from available data
      const lines = [
        `# SonarFix Patch — Job ${job.id}`,
        `# Branch: ${job.fix_branch}`,
        `# Project: ${job.request?.project_key ?? "—"}`,
        `# Repo: ${repoShortName(job.request?.repo_url)}`,
        `# Created: ${formatDate(job.created_at)}`,
        `# Fixed: ${job.result?.fixed_count ?? job.result?.fixed ?? 0} issues`,
        "",
        "## Diff Summary",
        diffStat || "(diff stat not available — run git diff manually on the fix branch)",
        "",
        "## Files Modified",
        ...files.map((f: JobFile) =>
          `  ${f.error ? "ERROR" : f.fixed > 0 ? "FIXED" : "SKIP "} ${f.file}${f.error ? ` — ${f.error}` : f.fixed > 0 ? ` (${f.fixed} issue${f.fixed > 1 ? "s" : ""} fixed)` : ""}`
        ),
      ]

      const blob = new Blob([lines.join("\n")], { type: "text/plain" })
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `sonarfix-${job.id}-${job.fix_branch.replace(/\//g, "-")}.txt`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      toast.success("Patch summary downloaded")
    } catch (e: any) {
      toast.error(`Download failed: ${e.message}`)
    } finally {
      setDownloadingIds(prev => {
        const next = new Set(prev)
        next.delete(job.id)
        return next
      })
    }
  }

  // Client-side filter + sort on fetched page
  let displayed = [...data.jobs]

  if (filterStatus !== "all") {
    displayed = displayed.filter(j => j.status === filterStatus)
  }
  if (filterRepo.trim()) {
    const q = filterRepo.trim().toLowerCase()
    displayed = displayed.filter(j => repoShortName(j.request?.repo_url).toLowerCase().includes(q))
  }

  displayed.sort((a, b) => {
    if (sortBy === "fixed") {
      const af = a.result?.fixed_count ?? a.result?.fixed ?? 0
      const bf = b.result?.fixed_count ?? b.result?.fixed ?? 0
      return bf - af
    }
    if (sortBy === "repo") {
      return repoShortName(a.request?.repo_url).localeCompare(repoShortName(b.request?.repo_url))
    }
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  })

  const totalPages = Math.ceil(data.total / PAGE_SIZE)
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1

  return (
    <div className="min-h-screen bg-gradient-to-b from-background via-background to-primary/5">
      {/* Header */}
      <header className="sticky top-0 z-50 border-b border-border/50 bg-card/95 backdrop-blur">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="flex h-16 items-center justify-between">
            <div className="flex items-center gap-2">
              <Zap className="h-7 w-7 text-primary" />
              <span className="text-xl font-bold tracking-tight">
                <span className="bg-gradient-to-r from-primary via-blue-400 to-primary bg-clip-text text-transparent">
                  SonarFix
                </span>
              </span>
            </div>
            <nav className="flex items-center gap-1">
              <Button variant="ghost" size="sm" asChild>
                <Link href="/">
                  <ArrowLeft className="mr-2 h-4 w-4" />
                  Dashboard
                </Link>
              </Button>
              <Button variant="ghost" size="sm" asChild>
                <Link href="/settings">
                  <Settings className="mr-2 h-4 w-4" />
                  Settings
                </Link>
              </Button>
            </nav>
          </div>
        </div>
      </header>

      <main className="relative mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <div className="pointer-events-none absolute -left-40 top-20 h-96 w-96 rounded-full bg-primary/8 blur-3xl" />
        <div className="pointer-events-none absolute -right-40 top-60 h-80 w-80 rounded-full bg-accent/8 blur-3xl" />

        <div className="relative mb-6 flex items-start justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">
              <span className="bg-gradient-to-r from-primary to-blue-400 bg-clip-text text-transparent">
                Fix History
              </span>
            </h1>
            <p className="mt-2 text-muted-foreground">
              All past fix jobs — {data.total} total
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="gap-1"
            onClick={() => fetchJobs(offset)}
            disabled={loading}
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            Refresh
          </Button>
        </div>

        {/* Filters */}
        <Card className="relative mb-6 shadow-sm ring-1 ring-border/30">
          <CardContent className="pt-4">
            <div className="flex flex-wrap items-end gap-3">
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground shrink-0">
                <Filter className="h-3.5 w-3.5" />
                Filters
              </div>
              <div className="flex-1 min-w-[120px] max-w-[160px]">
                <Select value={filterStatus} onValueChange={setFilterStatus}>
                  <SelectTrigger className="h-8 text-xs">
                    <SelectValue placeholder="Status" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All statuses</SelectItem>
                    <SelectItem value="completed">Completed</SelectItem>
                    <SelectItem value="failed">Failed</SelectItem>
                    <SelectItem value="running">Running</SelectItem>
                    <SelectItem value="queued">Queued</SelectItem>
                    <SelectItem value="cancelled">Cancelled</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="flex-1 min-w-[140px] max-w-[220px]">
                <Input
                  className="h-8 text-xs"
                  placeholder="Filter by repo..."
                  value={filterRepo}
                  onChange={e => setFilterRepo(e.target.value)}
                />
              </div>
              <div className="flex-1 min-w-[120px] max-w-[160px]">
                <Select value={sortBy} onValueChange={v => setSortBy(v as any)}>
                  <SelectTrigger className="h-8 text-xs">
                    <SelectValue placeholder="Sort by" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="date">Sort: Date</SelectItem>
                    <SelectItem value="fixed">Sort: Issues Fixed</SelectItem>
                    <SelectItem value="repo">Sort: Repository</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {(filterStatus !== "all" || filterRepo || sortBy !== "date") && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 text-xs text-muted-foreground"
                  onClick={() => { setFilterStatus("all"); setFilterRepo(""); setSortBy("date") }}
                >
                  Clear
                </Button>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Job List */}
        <div className="relative space-y-3">
          {loading && displayed.length === 0 && (
            <div className="flex items-center justify-center py-16 text-muted-foreground">
              <Loader2 className="mr-2 h-5 w-5 animate-spin" />
              Loading jobs…
            </div>
          )}

          {!loading && displayed.length === 0 && (
            <Card className="border-dashed">
              <CardContent className="flex flex-col items-center justify-center py-16 text-center">
                <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/10">
                  <Zap className="h-8 w-8 text-primary" />
                </div>
                <h3 className="text-lg font-semibold">No jobs found</h3>
                <p className="mt-1 max-w-sm text-sm text-muted-foreground">
                  {data.total === 0
                    ? "No fix jobs have been run yet. Go to the Dashboard and start a fix job."
                    : "No jobs match the current filters."}
                </p>
                <Button variant="outline" size="sm" className="mt-4" asChild>
                  <Link href="/">Go to Dashboard</Link>
                </Button>
              </CardContent>
            </Card>
          )}

          {displayed.map(job => {
            const isExpanded = expandedIds.has(job.id)
            const fixedCount = job.result?.fixed_count ?? job.result?.fixed ?? 0
            const skippedCount = job.result?.skipped_count ?? job.result?.skipped ?? 0
            const totalIssues = job.result?.total_issues ?? job.result?.total ?? job.total_issues ?? 0
            const repo = repoShortName(job.request?.repo_url)
            const langs = detectLanguages(job)
            const isDownloading = downloadingIds.has(job.id)

            return (
              <Card
                key={job.id}
                className={`transition-all duration-150 hover:shadow-md ${
                  job.status === "completed"
                    ? "ring-1 ring-emerald-500/10"
                    : job.status === "failed"
                    ? "ring-1 ring-red-500/10"
                    : ""
                }`}
              >
                {/* Row header */}
                <div
                  className="flex items-center gap-3 px-4 py-3 cursor-pointer select-none"
                  onClick={() => toggleExpand(job.id)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={e => e.key === "Enter" && toggleExpand(job.id)}
                >
                  <StatusIcon status={job.status} />

                  {/* Job ID + time */}
                  <div className="min-w-0 flex-1 space-y-0.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <code className="text-xs font-mono text-primary">{job.id}</code>
                      <Badge variant="outline" className={`text-[10px] ${STATUS_STYLES[job.status] ?? ""}`}>
                        {job.status}
                      </Badge>
                      {job.result?.dry_run && (
                        <Badge variant="outline" className="text-[10px] text-muted-foreground">
                          dry-run
                        </Badge>
                      )}
                      {langs.map(l => (
                        <Badge key={l} variant="outline" className="text-[10px] text-muted-foreground hidden sm:inline-flex">
                          {l}
                        </Badge>
                      ))}
                    </div>
                    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      <span className="flex items-center gap-1">
                        <GitBranch className="h-3 w-3" />
                        {repo}
                        {job.request?.branch && <span>/{job.request.branch}</span>}
                        {job.request?.pull_request && <span> PR#{job.request.pull_request}</span>}
                      </span>
                      {job.request?.project_key && (
                        <span className="flex items-center gap-1">
                          <FileCode className="h-3 w-3" />
                          {job.request.project_key}
                        </span>
                      )}
                      <span>{formatDate(job.created_at)}</span>
                    </div>
                  </div>

                  {/* Stats */}
                  {job.status === "completed" && (
                    <div className="hidden sm:flex items-center gap-4 text-sm shrink-0">
                      <div className="text-center">
                        <p className="text-base font-bold text-emerald-600">{fixedCount}</p>
                        <p className="text-[10px] text-muted-foreground">fixed</p>
                      </div>
                      <div className="text-center">
                        <p className="text-base font-bold text-yellow-600">{skippedCount}</p>
                        <p className="text-[10px] text-muted-foreground">skipped</p>
                      </div>
                      <div className="text-center">
                        <p className="text-base font-bold text-primary">{totalIssues}</p>
                        <p className="text-[10px] text-muted-foreground">total</p>
                      </div>
                    </div>
                  )}
                  {job.status === "failed" && job.error && (
                    <div className="hidden sm:flex items-center gap-1 text-xs text-red-600 max-w-[220px]">
                      <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                      <span className="truncate">{job.error}</span>
                    </div>
                  )}

                  {/* Expand toggle */}
                  <div className="shrink-0 text-muted-foreground">
                    {isExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                  </div>
                </div>

                {/* Expanded detail */}
                {isExpanded && (
                  <div className="border-t border-border/50 px-4 py-4 space-y-4">
                    {/* Fix branch */}
                    {job.fix_branch && (
                      <div className="flex items-center gap-2 text-xs">
                        <GitBranch className="h-3.5 w-3.5 text-muted-foreground" />
                        <span className="text-muted-foreground">Fix branch:</span>
                        <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]">{job.fix_branch}</code>
                      </div>
                    )}

                    {/* Diff stat */}
                    {job.result?.diff_stat && (
                      <div>
                        <p className="text-xs font-medium text-muted-foreground mb-1">Diff summary</p>
                        <pre className="rounded bg-muted/40 p-2 text-[10px] font-mono text-muted-foreground overflow-x-auto whitespace-pre-wrap">
                          {job.result.diff_stat}
                        </pre>
                      </div>
                    )}

                    {/* Files table */}
                    {job.result?.files && job.result.files.length > 0 && (
                      <div>
                        <p className="text-xs font-medium text-muted-foreground mb-2">
                          Modified files ({job.result.files.length})
                        </p>
                        <div className="max-h-64 overflow-y-auto rounded border border-border/40">
                          <table className="w-full text-xs">
                            <thead className="sticky top-0 bg-card border-b border-border/40">
                              <tr className="text-left text-muted-foreground">
                                <th className="px-3 py-1.5 font-medium">File</th>
                                <th className="px-2 py-1.5 font-medium text-right">Fixed</th>
                                <th className="px-2 py-1.5 font-medium text-right">Skipped</th>
                                <th className="px-3 py-1.5 font-medium">Status</th>
                              </tr>
                            </thead>
                            <tbody>
                              {job.result.files.map((f, i) => (
                                <tr key={i} className="border-b border-border/30 hover:bg-muted/40">
                                  <td className="px-3 py-1.5 font-mono text-[10px] max-w-[300px] truncate" title={f.file}>
                                    {f.file}
                                  </td>
                                  <td className={`px-2 py-1.5 text-right ${f.fixed > 0 ? "text-emerald-600 font-semibold" : "text-muted-foreground"}`}>
                                    {f.fixed}
                                  </td>
                                  <td className="px-2 py-1.5 text-right text-muted-foreground">{f.skipped}</td>
                                  <td className="px-3 py-1.5">
                                    {f.error
                                      ? <span className="text-red-500 truncate max-w-[200px] block" title={f.error}>Error: {f.error.slice(0, 60)}</span>
                                      : f.fixed > 0
                                      ? <span className="text-emerald-600">Fixed</span>
                                      : <span className="text-muted-foreground">Skipped</span>
                                    }
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}

                    {/* Error detail */}
                    {job.status === "failed" && job.error && (
                      <div className="rounded-lg bg-destructive/10 border border-destructive/20 p-3 text-xs text-destructive">
                        <p className="font-medium mb-1">Error</p>
                        <p className="font-mono">{job.error}</p>
                      </div>
                    )}

                    {/* Actions */}
                    {job.status === "completed" && !job.result?.dry_run && (
                      <div className="flex gap-2 pt-1">
                        <Button
                          variant="outline"
                          size="sm"
                          className="gap-1 text-xs h-7"
                          onClick={() => handleDownloadPatch(job)}
                          disabled={isDownloading}
                        >
                          {isDownloading
                            ? <Loader2 className="h-3 w-3 animate-spin" />
                            : <Download className="h-3 w-3" />}
                          Download patch summary
                        </Button>
                      </div>
                    )}
                  </div>
                )}
              </Card>
            )
          })}
        </div>

        {/* Pagination */}
        {data.total > PAGE_SIZE && (
          <div className="mt-6 flex items-center justify-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="gap-1"
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              disabled={offset === 0 || loading}
            >
              <ChevronLeft className="h-4 w-4" />
              Previous
            </Button>
            <span className="text-sm text-muted-foreground">
              Page {currentPage} of {totalPages} &nbsp;·&nbsp; {data.total} total
            </span>
            <Button
              variant="outline"
              size="sm"
              className="gap-1"
              onClick={() => setOffset(offset + PAGE_SIZE)}
              disabled={offset + PAGE_SIZE >= data.total || loading}
            >
              Next
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        )}
      </main>
    </div>
  )
}
