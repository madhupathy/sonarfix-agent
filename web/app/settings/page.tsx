"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  Zap, CheckCircle2, XCircle, Shield, GitBranch, Brain,
  ArrowLeft, Loader2, Unplug, FlaskConical, KeyRound, Globe,
  Database, BarChart3, History, Activity,
} from "lucide-react"
import { toast } from "sonner"

interface ConnectorState {
  name: string
  description: string
  status: string
  auth_type: string | null
  url?: string
  llm_model?: string
  llm_base_url?: string
}

interface RAGStats {
  fix_examples: number
  standard_docs: number
  error?: string
}

interface JobStats {
  total: number
  completed: number
  failed: number
}

const CONNECTOR_META: Record<string, { icon: any; color: string; emoji: string }> = {
  sonarqube: { icon: Shield, color: "bg-blue-500/10 text-blue-600", emoji: "🔍" },
  llm: { icon: Brain, color: "bg-purple-500/10 text-purple-600", emoji: "🧠" },
  git: { icon: GitBranch, color: "bg-emerald-500/10 text-emerald-600", emoji: "🔀" },
}

function StatusDot({ status }: { status: string }) {
  if (status === "connected") {
    return (
      <span
        className="inline-block h-2.5 w-2.5 rounded-full bg-emerald-500 ring-2 ring-emerald-500/20 animate-pulse"
        title="Connected"
      />
    )
  }
  if (status === "error" || status === "failing") {
    return (
      <span
        className="inline-block h-2.5 w-2.5 rounded-full bg-red-500 ring-2 ring-red-500/20"
        title="Configured but failing"
      />
    )
  }
  return (
    <span
      className="inline-block h-2.5 w-2.5 rounded-full bg-gray-300 ring-2 ring-gray-300/20"
      title="Not configured"
    />
  )
}

export default function SettingsPage() {
  const [connectors, setConnectors] = useState<Record<string, ConnectorState>>({})
  const [loading, setLoading] = useState(true)
  const [ragStats, setRagStats] = useState<RAGStats | null>(null)
  const [jobStats, setJobStats] = useState<JobStats | null>(null)
  const [testingConnectors, setTestingConnectors] = useState<Record<string, boolean>>({})
  const [testResults, setTestResults] = useState<Record<string, { success: boolean; message: string } | null>>({})

  const fetchConnectors = async () => {
    try {
      const r = await fetch("/api/connections")
      const data = await r.json()
      setConnectors(data.connections || {})
    } catch {
      toast.error("Failed to load connections")
    } finally {
      setLoading(false)
    }
  }

  const fetchStats = async () => {
    try {
      const [ragR, jobsR] = await Promise.all([
        fetch("/api/rag/stats"),
        fetch("/api/jobs?limit=1&offset=0"),
      ])
      if (ragR.ok) setRagStats(await ragR.json())
      if (jobsR.ok) {
        const jobsData = await jobsR.json()
        // Fetch all jobs to compute stats (up to 500 for stat purposes)
        const allJobsR = await fetch("/api/jobs?limit=500&offset=0")
        if (allJobsR.ok) {
          const allJobs = await allJobsR.json()
          const jobs: any[] = allJobs.jobs || []
          setJobStats({
            total: allJobs.total ?? jobs.length,
            completed: jobs.filter((j: any) => j.status === "completed").length,
            failed: jobs.filter((j: any) => j.status === "failed").length,
          })
        }
      }
    } catch {
      // Stats are optional — don't show error
    }
  }

  const quickTestConnector = async (key: string) => {
    const conn = connectors[key]
    if (!conn || conn.status !== "connected") return
    setTestingConnectors(prev => ({ ...prev, [key]: true }))
    setTestResults(prev => ({ ...prev, [key]: null }))
    try {
      const savedR = await fetch("/api/connections")
      const savedData = await savedR.json()
      const savedConn = savedData.connections?.[key] ?? {}

      // Build config from saved connection
      const config: Record<string, string> = {
        auth_type: savedConn.auth_type || "token",
        ...(savedConn.url ? { url: savedConn.url } : {}),
      }

      const r = await fetch(`/api/connections/${key}/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ connector: key, config }),
      })
      const result = await r.json()
      setTestResults(prev => ({ ...prev, [key]: result }))
      if (result.success) toast.success(`${conn.name}: ${result.message}`)
      else toast.error(`${conn.name}: ${result.message}`)
    } catch (e: any) {
      toast.error(`Test failed: ${e.message}`)
    } finally {
      setTestingConnectors(prev => ({ ...prev, [key]: false }))
    }
  }

  useEffect(() => {
    fetchConnectors()
    fetchStats()
  }, [])

  const totalFixed = jobStats?.completed ?? 0
  const totalIssuesScanned = "—"

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
            <div className="flex items-center gap-1">
              <Button variant="ghost" size="sm" asChild>
                <Link href="/history">
                  <History className="mr-2 h-4 w-4" />
                  History
                </Link>
              </Button>
              <Button variant="ghost" size="sm" asChild>
                <Link href="/">
                  <ArrowLeft className="mr-2 h-4 w-4" />
                  Dashboard
                </Link>
              </Button>
            </div>
          </div>
        </div>
      </header>

      <main className="relative mx-auto max-w-4xl px-4 py-8 sm:px-6 lg:px-8">
        <div className="pointer-events-none absolute -left-40 top-20 h-96 w-96 rounded-full bg-primary/8 blur-3xl" />
        <div className="pointer-events-none absolute -right-40 top-60 h-80 w-80 rounded-full bg-accent/8 blur-3xl" />

        <div className="relative mb-8">
          <h1 className="text-3xl font-bold tracking-tight">
            <span className="bg-gradient-to-r from-primary to-blue-400 bg-clip-text text-transparent">
              Settings
            </span>
          </h1>
          <p className="mt-2 text-muted-foreground">
            Configure connections for SonarQube, LLM, and Git.
          </p>
        </div>

        {/* Statistics Panel */}
        <Card className="relative mb-8 shadow-md shadow-primary/5 ring-1 ring-border/30">
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2">
              <BarChart3 className="h-4 w-4 text-primary" />
              Statistics
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <StatTile
                icon={<Activity className="h-4 w-4 text-emerald-500" />}
                label="Fix Jobs Completed"
                value={jobStats ? String(jobStats.completed) : "—"}
                sub={jobStats ? `${jobStats.failed} failed` : undefined}
              />
              <StatTile
                icon={<History className="h-4 w-4 text-blue-500" />}
                label="Total Jobs"
                value={jobStats ? String(jobStats.total) : "—"}
              />
              <StatTile
                icon={<Database className="h-4 w-4 text-purple-500" />}
                label="RAG Fix Examples"
                value={ragStats ? String(ragStats.fix_examples) : "—"}
                sub={ragStats ? `${ragStats.standard_docs} standard docs` : undefined}
              />
              <StatTile
                icon={<Shield className="h-4 w-4 text-orange-500" />}
                label="Connections Active"
                value={String(Object.values(connectors).filter(c => c.status === "connected").length)}
                sub="of 3 configured"
              />
            </div>
          </CardContent>
        </Card>

        {/* Connector Cards Overview */}
        <div className="relative grid gap-4 sm:grid-cols-3 mb-8">
          {Object.entries(connectors).map(([key, conn]) => {
            const meta = CONNECTOR_META[key]
            if (!meta) return null
            const isTesting = testingConnectors[key]
            const testResult = testResults[key]
            return (
              <Card
                key={key}
                className={`group transition-all duration-200 hover:shadow-lg hover:-translate-y-1 ${
                  conn.status === "connected"
                    ? "ring-1 ring-emerald-500/20 border-emerald-200/50"
                    : "border-border/60"
                }`}
              >
                <CardHeader className="pb-3">
                  <div className="flex items-start justify-between">
                    <div className={`flex h-12 w-12 items-center justify-center rounded-2xl ${meta.color} transition-transform duration-200 group-hover:scale-110`}>
                      <span className="text-xl">{meta.emoji}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <StatusDot status={testResult ? (testResult.success ? "connected" : "error") : conn.status} />
                      {conn.status === "connected" ? (
                        <Badge variant="outline" className="gap-1 border-emerald-500/20 bg-emerald-500/10 text-emerald-600 text-xs">
                          <CheckCircle2 className="h-3 w-3" />
                          Connected
                        </Badge>
                      ) : (
                        <Badge variant="outline" className="gap-1 text-muted-foreground text-xs">
                          Not connected
                        </Badge>
                      )}
                    </div>
                  </div>
                  <CardTitle className="text-base font-bold">{conn.name}</CardTitle>
                  <CardDescription className="text-xs">{conn.description}</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      {conn.status === "connected" ? (
                        <span className="flex items-center gap-1 text-sm text-emerald-600">
                          <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
                          {conn.auth_type === "sso_saml" ? "SSO/SAML" : conn.auth_type === "token" ? "Token" : "Basic"}
                        </span>
                      ) : (
                        <span className="text-sm text-muted-foreground">Not configured</span>
                      )}
                    </div>
                    {key === "llm" && conn.status === "connected" && conn.llm_model && (
                      <p className="text-xs text-muted-foreground font-mono truncate" title={conn.llm_model}>
                        {conn.llm_model}
                      </p>
                    )}
                    {conn.status === "connected" && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 w-full gap-1 text-xs text-muted-foreground hover:text-foreground"
                        onClick={() => quickTestConnector(key)}
                        disabled={isTesting}
                      >
                        {isTesting
                          ? <Loader2 className="h-3 w-3 animate-spin" />
                          : <FlaskConical className="h-3 w-3" />}
                        {isTesting ? "Testing…" : "Test connection"}
                      </Button>
                    )}
                    {testResult && (
                      <div className={`flex items-center gap-1.5 rounded p-1.5 text-xs ${testResult.success ? "bg-emerald-500/10 text-emerald-700" : "bg-destructive/10 text-destructive"}`}>
                        {testResult.success
                          ? <CheckCircle2 className="h-3 w-3 shrink-0" />
                          : <XCircle className="h-3 w-3 shrink-0" />}
                        <span className="truncate">{testResult.message}</span>
                      </div>
                    )}
                  </div>
                </CardContent>
              </Card>
            )
          })}
        </div>

        {/* Configuration Tabs */}
        <Tabs defaultValue="sonarqube" className="relative">
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="sonarqube" className="gap-1">
              <Shield className="h-4 w-4" /> SonarQube
            </TabsTrigger>
            <TabsTrigger value="llm" className="gap-1">
              <Brain className="h-4 w-4" /> LLM
            </TabsTrigger>
            <TabsTrigger value="git" className="gap-1">
              <GitBranch className="h-4 w-4" /> Git
            </TabsTrigger>
          </TabsList>

          <TabsContent value="sonarqube">
            <ConnectorForm
              connectorKey="sonarqube"
              current={connectors.sonarqube}
              onSaved={fetchConnectors}
              authOptions={[
                { value: "basic", label: "Username & Password" },
                { value: "token", label: "API Token" },
                { value: "sso_saml", label: "SSO / SAML" },
              ]}
              fields={(authType) => (
                <>
                  <div className="space-y-2">
                    <Label>SonarQube Server URL</Label>
                    <Input name="url" placeholder="https://sonarqube.example.com" />
                  </div>
                  {authType === "basic" && (
                    <>
                      <div className="space-y-2">
                        <Label>Username</Label>
                        <Input name="username" placeholder="your_username" />
                      </div>
                      <div className="space-y-2">
                        <Label>Password</Label>
                        <Input name="password" type="password" placeholder="••••••••" />
                      </div>
                    </>
                  )}
                  {authType === "token" && (
                    <div className="space-y-2">
                      <Label>API Token</Label>
                      <Input name="token" type="password" placeholder="squ_xxxxxxxxxxxx" />
                    </div>
                  )}
                  {authType === "sso_saml" && (
                    <>
                      <div className="rounded-lg border border-primary/20 bg-primary/5 p-3 text-sm text-primary">
                        <Globe className="inline h-4 w-4 mr-1" />
                        SSO/SAML authentication will redirect through your identity provider when accessing SonarQube.
                      </div>
                      <div className="space-y-2">
                        <Label>SSO Login URL</Label>
                        <Input name="sso_url" placeholder="https://sso.corp.example.com/saml/login" />
                      </div>
                      <div className="space-y-2">
                        <Label>Entity ID (optional)</Label>
                        <Input name="sso_entity_id" placeholder="sonarqube-prod" />
                      </div>
                    </>
                  )}
                </>
              )}
            />
          </TabsContent>

          <TabsContent value="llm">
            <ConnectorForm
              connectorKey="llm"
              current={connectors.llm}
              onSaved={fetchConnectors}
              authOptions={[
                { value: "token", label: "LLM API Key" },
              ]}
              fields={() => (
                <>
                  <div className="rounded-lg border border-primary/20 bg-primary/5 p-3 text-sm text-primary">
                    <Brain className="inline h-4 w-4 mr-1" />
                    SonarFix uses an LLM via an OpenAI-compatible API (e.g. vLLM, OpenAI, Azure) to directly fix code issues.
                  </div>
                  <div className="space-y-2">
                    <Label>LLM API Key</Label>
                    <Input name="llm_api_key" type="password" placeholder="dummy (or sk-xxxxxxxxxxxx for OpenAI)" />
                    <p className="text-xs text-muted-foreground">
                      For vLLM use <code className="bg-muted px-1 rounded">dummy</code>. For OpenAI/Azure, use your real API key.
                    </p>
                  </div>
                  <div className="space-y-2">
                    <Label>Model</Label>
                    <Input
                      name="llm_model"
                      placeholder="Qwen/Qwen2.5-Coder-32B-Instruct"
                      defaultValue={connectors.llm?.llm_model || "Qwen/Qwen2.5-Coder-32B-Instruct"}
                    />
                    <p className="text-xs text-muted-foreground">
                      Must match a model served by your endpoint. Examples: Qwen/Qwen2.5-Coder-32B-Instruct, gpt-4o, etc.
                    </p>
                  </div>
                  <div className="space-y-2">
                    <Label>API Base URL</Label>
                    <Input
                      name="llm_base_url"
                      placeholder="http://localhost:8000/v1"
                      defaultValue={connectors.llm?.llm_base_url || "http://localhost:8000/v1"}
                    />
                    <p className="text-xs text-muted-foreground">
                      OpenAI-compatible endpoint (e.g. vLLM, OpenAI, Azure OpenAI).
                    </p>
                  </div>

                  {/* LLM Model info panel (read-only current config) */}
                  {connectors.llm?.status === "connected" && (connectors.llm?.llm_model || connectors.llm?.llm_base_url) && (
                    <div className="rounded-lg border border-border/50 bg-muted/30 p-3 space-y-1.5">
                      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Current Configuration</p>
                      {connectors.llm?.llm_model && (
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-muted-foreground">Model</span>
                          <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px]">{connectors.llm.llm_model}</code>
                        </div>
                      )}
                      {connectors.llm?.llm_base_url && (
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-muted-foreground">Base URL</span>
                          <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] max-w-[200px] truncate">{connectors.llm.llm_base_url}</code>
                        </div>
                      )}
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-muted-foreground">Temperature</span>
                        <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px]">0.0 (deterministic)</code>
                      </div>
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-muted-foreground">Max tokens</span>
                        <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px]">8192</code>
                      </div>
                    </div>
                  )}
                </>
              )}
            />
          </TabsContent>

          <TabsContent value="git">
            <ConnectorForm
              connectorKey="git"
              current={connectors.git}
              onSaved={fetchConnectors}
              authOptions={[
                { value: "token", label: "Personal Access Token" },
                { value: "basic", label: "Username & Password" },
                { value: "sso_saml", label: "SSO / SAML" },
              ]}
              fields={(authType) => (
                <>
                  {authType === "token" && (
                    <div className="space-y-2">
                      <Label>Personal Access Token</Label>
                      <Input name="token" type="password" placeholder="ghp_xxxxxxxxxxxx" />
                      <p className="text-xs text-muted-foreground">
                        Required for cloning private repos. Go to your Git host &rarr; Settings &rarr; Developer settings &rarr; Personal access tokens &rarr; Generate new token (with <code className="bg-muted px-1 rounded">repo</code> scope).
                      </p>
                    </div>
                  )}
                  {authType === "basic" && (
                    <>
                      <div className="space-y-2">
                        <Label>Username</Label>
                        <Input name="username" placeholder="your_username" />
                      </div>
                      <div className="space-y-2">
                        <Label>Password</Label>
                        <Input name="password" type="password" placeholder="••••••••" />
                      </div>
                    </>
                  )}
                  {authType === "sso_saml" && (
                    <>
                      <div className="rounded-lg border border-primary/20 bg-primary/5 p-3 text-sm text-primary">
                        <Globe className="inline h-4 w-4 mr-1" />
                        SSO/SAML authentication will use your corporate identity provider for Git operations.
                      </div>
                      <div className="space-y-2">
                        <Label>SSO Login URL</Label>
                        <Input name="sso_url" placeholder="https://sso.corp.example.com/saml/login" />
                      </div>
                      <div className="space-y-2">
                        <Label>Entity ID (optional)</Label>
                        <Input name="sso_entity_id" placeholder="git-enterprise" />
                      </div>
                    </>
                  )}
                  <div className="space-y-2">
                    <Label>Default Remote URL (optional)</Label>
                    <Input name="url" placeholder="https://github.example.com" />
                  </div>
                </>
              )}
            />
          </TabsContent>
        </Tabs>
      </main>
    </div>
  )
}


// ---------------------------------------------------------------------------
// StatTile — single statistic display
// ---------------------------------------------------------------------------

function StatTile({
  icon, label, value, sub,
}: {
  icon: React.ReactNode
  label: string
  value: string
  sub?: string
}) {
  return (
    <div className="rounded-lg border border-border/50 bg-card p-3 space-y-1">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        {icon}
        <span>{label}</span>
      </div>
      <p className="text-2xl font-bold leading-none">{value}</p>
      {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
    </div>
  )
}


// ---------------------------------------------------------------------------
// ConnectorForm — reusable form for each connector tab
// ---------------------------------------------------------------------------

interface AuthOption { value: string; label: string }

function ConnectorForm({
  connectorKey,
  current,
  onSaved,
  authOptions,
  fields,
}: {
  connectorKey: string
  current?: ConnectorState
  onSaved: () => void
  authOptions: AuthOption[]
  fields: (authType: string) => React.ReactNode
}) {
  const [authType, setAuthType] = useState(current?.auth_type || authOptions[0].value)
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null)

  const getFormData = () => {
    const form = document.getElementById(`form-${connectorKey}`) as HTMLFormElement
    if (!form) return {}
    const fd = new FormData(form)
    const data: Record<string, string> = {}
    fd.forEach((v, k) => { if (typeof v === "string" && v) data[k] = v })
    return data
  }

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const data = getFormData()
      const r = await fetch(`/api/connections/${connectorKey}/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ connector: connectorKey, config: { auth_type: authType, ...data } }),
      })
      const result = await r.json()
      setTestResult(result)
      if (result.success) toast.success(result.message)
      else toast.error(result.message)
    } catch (e: any) {
      toast.error(`Test failed: ${e.message}`)
    } finally {
      setTesting(false)
    }
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      const data = getFormData()
      const r = await fetch(`/api/connections/${connectorKey}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ auth_type: authType, ...data }),
      })
      if (!r.ok) throw new Error(await r.text())
      toast.success(`${current?.name || connectorKey} connected!`)
      onSaved()
    } catch (e: any) {
      toast.error(`Save failed: ${e.message}`)
    } finally {
      setSaving(false)
    }
  }

  const handleDisconnect = async () => {
    try {
      await fetch(`/api/connections/${connectorKey}`, { method: "DELETE" })
      toast.success("Disconnected")
      onSaved()
    } catch {
      toast.error("Failed to disconnect")
    }
  }

  return (
    <Card className="mt-4 shadow-lg shadow-primary/5">
      <CardHeader>
        <CardTitle className="text-lg flex items-center gap-2">
          <KeyRound className="h-5 w-5 text-primary" />
          {current?.name || connectorKey} Authentication
        </CardTitle>
        <CardDescription>Choose an authentication method and provide credentials.</CardDescription>
      </CardHeader>
      <CardContent>
        <form id={`form-${connectorKey}`} onSubmit={(e) => e.preventDefault()} className="space-y-4">
          <div className="space-y-2">
            <Label>Authentication Method</Label>
            <Select value={authType} onValueChange={setAuthType}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {authOptions.map(opt => (
                  <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {fields(authType)}

          {testResult && (
            <div className={`flex items-center gap-2 rounded-lg p-3 text-sm ${testResult.success ? "bg-emerald-500/10 text-emerald-700" : "bg-destructive/10 text-destructive"}`}>
              {testResult.success ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
              {testResult.message}
            </div>
          )}

          <Separator />

          <div className="flex gap-2">
            <Button variant="outline" className="gap-1" onClick={handleTest} disabled={testing}>
              {testing ? <Loader2 className="h-4 w-4 animate-spin" /> : <FlaskConical className="h-4 w-4" />}
              Test Connection
            </Button>
            <Button className="gap-1 shadow-sm shadow-primary/20" onClick={handleSave} disabled={saving}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
              Save & Connect
            </Button>
            {current?.status === "connected" && (
              <Button variant="ghost" className="gap-1 text-destructive hover:text-destructive" onClick={handleDisconnect}>
                <Unplug className="h-4 w-4" />
                Disconnect
              </Button>
            )}
          </div>
        </form>
      </CardContent>
    </Card>
  )
}
