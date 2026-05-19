"use client"

import { useState } from "react"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Progress } from "@/components/ui/progress"
import {
  Bug, GitBranch, GitPullRequest, Play, Settings, Zap, Shield,
  AlertTriangle, Info, CheckCircle2, XCircle, Loader2, FileCode,
  Send, ExternalLink, FileText, ChevronDown, ChevronUp, History,
} from "lucide-react"
import { toast } from "sonner"
import { LanguageBadges } from "@/components/language-badges"

interface Issue {
  key: string; rule: string; severity: string; type: string
  message: string; file: string; line: number | null
}

interface Job {
  id: string; status: string; total_issues?: number
  fix_branch?: string; result?: any; log?: { ts: string; msg: string }[]
}

const SEVERITY_COLORS: Record<string, string> = {
  BLOCKER: "bg-red-600 text-white",
  CRITICAL: "bg-red-500 text-white",
  MAJOR: "bg-yellow-500 text-white",
  MINOR: "bg-blue-400 text-white",
  INFO: "bg-gray-400 text-white",
}

const TYPE_ICONS: Record<string, any> = {
  BUG: Bug,
  VULNERABILITY: Shield,
  CODE_SMELL: AlertTriangle,
  SECURITY_HOTSPOT: Shield,
}

export default function DashboardPage() {
  const [projectKey, setProjectKey] = useState("")
  const [repoUrl, setRepoUrl] = useState("")
  const [localRepo, setLocalRepo] = useState("")
  const [branch, setBranch] = useState("")
  const [prId, setPrId] = useState("")
  const [mode, setMode] = useState<"branch" | "pr">("branch")
  const [maxIssues, setMaxIssues] = useState("50")
  const [severity, setSeverity] = useState("all")
  const [dryRun, setDryRun] = useState(false)
  const [smartUrl, setSmartUrl] = useState("")

  const [issues, setIssues] = useState<Issue[]>([])
  const [issuesLoading, setIssuesLoading] = useState(false)
  const [activeJob, setActiveJob] = useState<Job | null>(null)
  const [pollingId, setPollingId] = useState<NodeJS.Timeout | null>(null)
  const [applying, setApplying] = useState(false)
  const [pushing, setPushing] = useState(false)
  const [showInstructions, setShowInstructions] = useState(true)

  // Smart URL parser: paste a PR URL and auto-extract repo SSH URL + PR ID
  const handleSmartUrl = (url: string) => {
    setSmartUrl(url)
    // Match: https://host/org/repo/pull/123
    const prMatch = url.match(/https?:\/\/([^/]+)\/([^/]+)\/([^/]+)\/pull\/(\d+)/)
    if (prMatch) {
      const [, host, org, repo, pr] = prMatch
      setRepoUrl(`git@${host}:${org}/${repo}.git`)
      setPrId(pr)
      setMode("pr")
      toast.success(`Parsed: ${org}/${repo} PR #${pr}`)
      return
    }
    // Match: https://host/org/repo/tree/branch-name
    const branchMatch = url.match(/https?:\/\/([^/]+)\/([^/]+)\/([^/]+)\/tree\/(.+)/)
    if (branchMatch) {
      const [, host, org, repo, br] = branchMatch
      setRepoUrl(`git@${host}:${org}/${repo}.git`)
      setBranch(br)
      setMode("branch")
      toast.success(`Parsed: ${org}/${repo} branch ${br}`)
      return
    }
    // Match: git@host:org/repo.git or https://host/org/repo.git
    if (url.match(/^git@/) || url.match(/\.git$/)) {
      setRepoUrl(url)
      return
    }
    // Match: https://host/org/repo (plain repo URL)
    const repoMatch = url.match(/https?:\/\/([^/]+)\/([^/]+)\/([^/]+)\/?$/)
    if (repoMatch) {
      const [, host, org, repo] = repoMatch
      setRepoUrl(`git@${host}:${org}/${repo}.git`)
      toast.success(`Parsed: ${org}/${repo} (SSH)`)
      return
    }
  }

  const fetchIssues = async () => {
    if (!projectKey) { toast.error("Project key is required"); return }
    setIssuesLoading(true)
    try {
      const params = new URLSearchParams({ project_key: projectKey, max_issues: maxIssues })
      if (mode === "branch" && branch) params.set("branch", branch)
      if (mode === "pr" && prId) params.set("pull_request", prId)
      const r = await fetch(`/api/issues?${params}`)
      if (!r.ok) throw new Error(await r.text())
      const data = await r.json()
      setIssues(data.issues || [])
      if (data.total === 0) {
        toast.warning("Found 0 issues. Check the project key (try including namespace, e.g. my-org::my-project) and PR ID.")
      } else {
        toast.success(`Found ${data.total} issues`)
      }
    } catch (e: any) {
      toast.error(`Failed: ${e.message?.slice(0, 200)}`)
    } finally {
      setIssuesLoading(false)
    }
  }

  const startFix = async () => {
    if (!projectKey || (!repoUrl && !localRepo)) {
      toast.error("Project key and repo URL (or local path) are required"); return
    }
    try {
      const body: any = {
        project_key: projectKey,
        repo_url: repoUrl || "local",
        max_issues: parseInt(maxIssues),
        dry_run: dryRun,
      }
      if (localRepo) body.local_repo = localRepo
      if (mode === "branch" && branch) body.branch = branch
      if (mode === "pr" && prId) body.pull_request = prId
      if (severity !== "all") body.severities = [severity]

      const r = await fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      if (!r.ok) throw new Error(await r.text())
      const data = await r.json()
      toast.success(`Job started: ${data.job_id}`)
      pollJob(data.job_id)
    } catch (e: any) {
      toast.error(`Failed: ${e.message?.slice(0, 200)}`)
    }
  }

  const pollJob = (jobId: string) => {
    if (pollingId) clearInterval(pollingId)
    const id = setInterval(async () => {
      try {
        const r = await fetch(`/api/jobs/${jobId}`)
        if (!r.ok) {
          clearInterval(id)
          setPollingId(null)
          toast.error("Job not found (server may have restarted). Please re-submit.")
          return
        }
        const job = await r.json()
        setActiveJob(job)
        if (job.status === "completed" || job.status === "failed") {
          clearInterval(id)
          setPollingId(null)
          if (job.status === "completed") toast.success("Fix job completed!")
          else toast.error("Fix job failed")
        }
      } catch { /* ignore */ }
    }, 2000)
    setPollingId(id)
    // Initial fetch
    fetch(`/api/jobs/${jobId}`).then(r => r.json()).then(setActiveJob).catch(() => {})
  }

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
              <Button variant="ghost" size="sm" className="font-semibold text-foreground">Dashboard</Button>
              <Button variant="ghost" size="sm" asChild>
                <Link href="/history">
                  <History className="mr-2 h-4 w-4" />
                  History
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
        {/* Background blobs */}
        <div className="pointer-events-none absolute -left-40 top-20 h-96 w-96 rounded-full bg-primary/8 blur-3xl" />
        <div className="pointer-events-none absolute -right-40 top-60 h-80 w-80 rounded-full bg-accent/8 blur-3xl" />

        {/* Welcome */}
        <div className="relative mb-8">
          <h1 className="text-3xl font-bold tracking-tight">
            <span className="bg-gradient-to-r from-primary to-blue-400 bg-clip-text text-transparent">
              Fix SonarQube Issues
            </span>
          </h1>
          <p className="mt-2 text-muted-foreground">
            Point to any repo branch or PR, preview issues, and auto-fix them with LLM-powered code generation.
          </p>
          <div className="mt-3 flex items-center gap-2">
            <span className="text-xs text-muted-foreground shrink-0">Supported languages:</span>
            <LanguageBadges />
          </div>
        </div>

        <div className="relative grid gap-8 lg:grid-cols-3">
          {/* Left: Input Form */}
          <div className="lg:col-span-1 space-y-6">
            <Card className="shadow-lg shadow-primary/5 ring-1 ring-border/30">
              <CardHeader className="bg-gradient-to-br from-primary/5 to-blue-400/5 pb-4">
                <CardTitle className="text-lg flex items-center gap-2">
                  <FileCode className="h-5 w-5 text-primary" />
                  Target
                </CardTitle>
                <CardDescription>Configure the repo and branch to scan</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4 pt-4">
                <div className="space-y-2">
                  <Label>SonarQube Project Key</Label>
                  <Input placeholder="my-org::my-project" value={projectKey} onChange={e => setProjectKey(e.target.value)} />
                </div>

                <div className="space-y-2">
                  <Label className="flex items-center gap-1">PR / Branch URL <span className="text-xs text-muted-foreground font-normal">(paste any GitHub/GitLab URL)</span></Label>
                  <Input
                    placeholder="https://github.com/my-org/my-repo/pull/42"
                    value={smartUrl}
                    onChange={e => handleSmartUrl(e.target.value)}
                    className={repoUrl && prId ? "border-accent ring-1 ring-accent/30" : ""}
                  />
                  {repoUrl && (
                    <p className="text-xs text-muted-foreground">
                      Parsed → <code className="rounded bg-muted px-1 py-0.5 text-[10px] font-mono">{repoUrl}</code>
                      {prId && <> PR <code className="rounded bg-muted px-1 py-0.5 text-[10px] font-mono">#{prId}</code></>}
                      {branch && <> branch <code className="rounded bg-muted px-1 py-0.5 text-[10px] font-mono">{branch}</code></>}
                    </p>
                  )}
                </div>

                <details className="text-sm">
                  <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">Advanced options</summary>
                  <div className="mt-3 space-y-3">
                    <div className="space-y-2">
                      <Label className="text-xs">Git Clone URL <span className="text-muted-foreground font-normal">(auto-filled from above)</span></Label>
                      <Input placeholder="git@host:org/repo.git" value={repoUrl} onChange={e => setRepoUrl(e.target.value)} className="text-xs" />
                    </div>
                    <div className="space-y-2">
                      <Label className="text-xs">Local Repo Path <span className="text-muted-foreground font-normal">(skip clone)</span></Label>
                      <Input placeholder="/home/user/my-repo" value={localRepo} onChange={e => setLocalRepo(e.target.value)} className="text-xs" />
                    </div>
                  </div>
                </details>

                <Separator />

                <div className="flex gap-2">
                  <Button
                    variant={mode === "branch" ? "default" : "outline"} size="sm"
                    className="flex-1 gap-1" onClick={() => setMode("branch")}
                  >
                    <GitBranch className="h-4 w-4" /> Branch
                  </Button>
                  <Button
                    variant={mode === "pr" ? "default" : "outline"} size="sm"
                    className="flex-1 gap-1" onClick={() => setMode("pr")}
                  >
                    <GitPullRequest className="h-4 w-4" /> Pull Request
                  </Button>
                </div>

                {mode === "branch" ? (
                  <div className="space-y-2">
                    <Label>Branch Name</Label>
                    <Input placeholder="feature/my-branch" value={branch} onChange={e => setBranch(e.target.value)} />
                  </div>
                ) : (
                  <div className="space-y-2">
                    <Label>PR ID</Label>
                    <Input placeholder="42" value={prId} onChange={e => setPrId(e.target.value)} />
                  </div>
                )}

                <Separator />

                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-2">
                    <Label>Max Issues</Label>
                    <Input type="number" value={maxIssues} onChange={e => setMaxIssues(e.target.value)} />
                  </div>
                  <div className="space-y-2">
                    <Label>Severity</Label>
                    <Select value={severity} onValueChange={setSeverity}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All</SelectItem>
                        <SelectItem value="BLOCKER">Blocker</SelectItem>
                        <SelectItem value="CRITICAL">Critical</SelectItem>
                        <SelectItem value="MAJOR">Major</SelectItem>
                        <SelectItem value="MINOR">Minor</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                <div className="flex items-center gap-2 pt-2">
                  <input type="checkbox" id="dry" checked={dryRun} onChange={e => setDryRun(e.target.checked)} className="rounded" />
                  <Label htmlFor="dry" className="text-sm text-muted-foreground cursor-pointer">Dry run (instructions only, no fix)</Label>
                </div>

                <div className="flex gap-2 pt-2">
                  <Button variant="outline" className="flex-1 gap-1 font-semibold" onClick={fetchIssues} disabled={issuesLoading}>
                    {issuesLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bug className="h-4 w-4" />}
                    Preview
                  </Button>
                  <Button className="flex-1 gap-1 font-semibold shadow-md shadow-primary/20" onClick={startFix} disabled={!!pollingId}>
                    {pollingId ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                    Fix Issues
                  </Button>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Right: Results */}
          <div className="lg:col-span-2 space-y-6">
            {/* Active Job */}
            {activeJob && (
              <Card className={`shadow-lg ${activeJob.status === "completed" ? "ring-1 ring-accent/30" : activeJob.status === "failed" ? "ring-1 ring-destructive/30" : "ring-1 ring-primary/20"}`}>
                <CardHeader className="pb-3">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-lg flex items-center gap-2">
                      {activeJob.status === "running" && <Loader2 className="h-5 w-5 animate-spin text-primary" />}
                      {activeJob.status === "completed" && <CheckCircle2 className="h-5 w-5 text-accent" />}
                      {activeJob.status === "failed" && <XCircle className="h-5 w-5 text-destructive" />}
                      Job {activeJob.id}
                    </CardTitle>
                    <Badge variant={activeJob.status === "completed" ? "default" : activeJob.status === "failed" ? "destructive" : "secondary"}
                      className={activeJob.status === "completed" ? "bg-accent" : ""}>
                      {activeJob.status}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3">
                  {activeJob.status === "running" && (
                    <Progress value={undefined} className="h-2 animate-pulse" />
                  )}
                  {activeJob.result && (
                    <div className="grid grid-cols-3 gap-3">
                      <div className="rounded-lg bg-accent/10 p-3 text-center">
                        <p className="text-2xl font-bold text-accent">{activeJob.result.fixed_count ?? activeJob.result.fixed ?? 0}</p>
                        <p className="text-xs text-muted-foreground">Fixed</p>
                      </div>
                      <div className="rounded-lg bg-yellow-500/10 p-3 text-center">
                        <p className="text-2xl font-bold text-yellow-600">{activeJob.result.skipped_count ?? activeJob.result.skipped ?? 0}</p>
                        <p className="text-xs text-muted-foreground">Skipped</p>
                      </div>
                      <div className="rounded-lg bg-primary/10 p-3 text-center">
                        <p className="text-2xl font-bold text-primary">{activeJob.result.total ?? activeJob.total_issues ?? 0}</p>
                        <p className="text-xs text-muted-foreground">Total</p>
                      </div>
                    </div>
                  )}
                  {activeJob.fix_branch && (
                    <p className="text-sm text-muted-foreground">
                      Branch: <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">{activeJob.fix_branch}</code>
                    </p>
                  )}

                  {/* Dry-run instructions */}
                  {activeJob.result?.dry_run && activeJob.result?.instructions?.length > 0 && (
                    <div className="space-y-2">
                      <button
                        onClick={() => setShowInstructions(!showInstructions)}
                        className="flex items-center gap-1 text-sm font-medium text-primary hover:underline"
                      >
                        <FileText className="h-4 w-4" />
                        Fix Instructions ({activeJob.result.instructions.length} batch{activeJob.result.instructions.length > 1 ? "es" : ""})
                        {showInstructions ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                      </button>
                      {showInstructions && activeJob.result.instructions.map((inst: any, i: number) => (
                        <div key={i} className="rounded-lg border border-border/50 bg-muted/30">
                          <div className="px-3 py-2 border-b border-border/30 text-xs font-medium text-muted-foreground">
                            Batch {inst.batch} &mdash; <code className="text-[10px]">{inst.path}</code>
                          </div>
                          <pre className="max-h-[400px] overflow-auto p-3 text-xs font-mono text-foreground/80 whitespace-pre-wrap">{inst.content}</pre>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Action buttons for completed dry-run */}
                  {activeJob.status === "completed" && activeJob.result?.dry_run && (
                    <div className="flex gap-2 pt-2">
                      <Button
                        className="flex-1 gap-1 font-semibold shadow-md shadow-primary/20"
                        onClick={async () => {
                          setApplying(true)
                          try {
                            const r = await fetch(`/api/jobs/${activeJob.id}/apply`, { method: "POST" })
                            if (!r.ok) throw new Error(await r.text())
                            const data = await r.json()
                            toast.success(`Apply job started: ${data.job_id}`)
                            pollJob(data.job_id)
                          } catch (e: any) {
                            toast.error(`Failed: ${e.message?.slice(0, 200)}`)
                          } finally {
                            setApplying(false)
                          }
                        }}
                        disabled={applying}
                      >
                        {applying ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                        Apply Fixes
                      </Button>
                    </div>
                  )}

                  {/* Push & Create PR button for completed non-dry-run */}
                  {activeJob.status === "completed" && !activeJob.result?.dry_run && activeJob.fix_branch && (
                    <div className="flex gap-2 pt-2">
                      <Button
                        className="flex-1 gap-1 font-semibold bg-accent hover:bg-accent/90 shadow-md"
                        onClick={async () => {
                          setPushing(true)
                          try {
                            const r = await fetch(`/api/jobs/${activeJob.id}/push`, { method: "POST" })
                            if (!r.ok) throw new Error(await r.text())
                            const data = await r.json()
                            toast.success(data.message)
                            if (data.pr_url) {
                              window.open(data.pr_url, "_blank")
                            }
                          } catch (e: any) {
                            toast.error(`Failed: ${e.message?.slice(0, 200)}`)
                          } finally {
                            setPushing(false)
                          }
                        }}
                        disabled={pushing}
                      >
                        {pushing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                        Push & Create PR
                      </Button>
                    </div>
                  )}

                  {activeJob.log && activeJob.log.length > 0 && (
                    <div className="max-h-48 overflow-y-auto rounded-lg bg-muted/50 p-3 text-xs font-mono space-y-1">
                      {activeJob.log.map((l, i) => (
                        <div key={i} className="text-muted-foreground">
                          <span className="text-primary/60">{new Date(l.ts).toLocaleTimeString()}</span>{" "}{l.msg}
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            )}

            {/* Issues Table */}
            {issues.length > 0 && (
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-lg">Issues Preview ({issues.length})</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="max-h-[500px] overflow-y-auto">
                    <table className="w-full text-sm">
                      <thead className="sticky top-0 bg-card">
                        <tr className="border-b text-left text-xs text-muted-foreground">
                          <th className="pb-2 font-medium">Severity</th>
                          <th className="pb-2 font-medium">Type</th>
                          <th className="pb-2 font-medium">Rule</th>
                          <th className="pb-2 font-medium">File</th>
                          <th className="pb-2 font-medium">Line</th>
                          <th className="pb-2 font-medium">Message</th>
                        </tr>
                      </thead>
                      <tbody>
                        {issues.map((issue, i) => {
                          const Icon = TYPE_ICONS[issue.type] || Info
                          return (
                            <tr key={i} className="border-b border-border/50 hover:bg-muted/50">
                              <td className="py-2 pr-2">
                                <Badge className={`text-[10px] ${SEVERITY_COLORS[issue.severity] || "bg-gray-400 text-white"}`}>
                                  {issue.severity}
                                </Badge>
                              </td>
                              <td className="py-2 pr-2">
                                <span className="flex items-center gap-1 text-xs text-muted-foreground">
                                  <Icon className="h-3 w-3" />{issue.type}
                                </span>
                              </td>
                              <td className="py-2 pr-2 font-mono text-xs text-primary">{issue.rule}</td>
                              <td className="py-2 pr-2 max-w-[200px] truncate text-xs">{issue.file}</td>
                              <td className="py-2 pr-2 text-xs text-muted-foreground">{issue.line ?? "-"}</td>
                              <td className="py-2 max-w-[250px] truncate text-xs text-muted-foreground">{issue.message}</td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Empty state */}
            {!activeJob && issues.length === 0 && (
              <Card className="border-dashed">
                <CardContent className="flex flex-col items-center justify-center py-16 text-center">
                  <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/10">
                    <Zap className="h-8 w-8 text-primary" />
                  </div>
                  <h3 className="text-lg font-semibold">Ready to fix</h3>
                  <p className="mt-1 max-w-sm text-sm text-muted-foreground">
                    Enter your SonarQube project key and repo URL, then click <strong>Preview</strong> to see issues
                    or <strong>Fix Issues</strong> to auto-fix them.
                  </p>
                  <div className="mt-6 flex flex-wrap justify-center gap-2">
                    {["SonarQube API", "LLM Fixer", "Git Clone", "Syntax Check", "Auto-commit"].map(f => (
                      <div key={f} className="rounded-full border border-border/60 bg-card px-3 py-1.5 text-xs font-medium text-muted-foreground transition-all hover:bg-primary/10 hover:text-primary hover:border-primary/20 hover:-translate-y-0.5">
                        {f}
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}
          </div>
        </div>
      </main>
    </div>
  )
}
