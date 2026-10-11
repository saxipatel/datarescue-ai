import { useState } from "react";
import type { FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { Button, ErrorState, Field, Input } from "@/components/ui";
import { useAuth } from "@/hooks/useAuth";

// Development-only accounts seeded by scripts/seed.py (see docs/security.md).
const DEMO_ACCOUNTS = [
  ["engineer@example.test", "Runs pipelines, injects failures, proposes repairs"],
  ["steward@example.test", "Reviews quarantine, approves or rejects repairs"],
  ["admin@example.test", "Edits rules, reads the audit log"],
  ["analyst@example.test", "Read-only"],
] as const;

export function LoginPage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("engineer@example.test");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to="/" replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email.trim().toLowerCase(), password);
      const from = (location.state as { from?: string } | null)?.from ?? "/";
      navigate(from, { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center gap-2">
          <img src="/favicon.svg" alt="" className="h-8 w-8" />
          <div>
            <h1 className="text-lg font-semibold">DataRescue</h1>
            <p className="text-sm text-slate-500">Data reliability and safe recovery</p>
          </div>
        </div>
        <form
          onSubmit={submit}
          className="space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900"
        >
          <Field label="Email">
            {(id) => (
              <Input id={id} type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
            )}
          </Field>
          <Field label="Password">
            {(id) => (
              <Input
                id={id}
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            )}
          </Field>
          {error != null && <ErrorState error={error} />}
          <Button type="submit" variant="primary" busy={busy} className="w-full">
            Sign in
          </Button>
        </form>
        <div className="mt-4 rounded-lg border border-dashed border-slate-300 p-4 text-sm dark:border-slate-700">
          <p className="font-medium">Local demo accounts</p>
          <p className="mb-2 text-xs text-slate-500">
            Password for all: <code className="font-mono">datarescue-demo</code> (development only).
          </p>
          <ul className="space-y-1">
            {DEMO_ACCOUNTS.map(([account, role]) => (
              <li key={account}>
                <button
                  type="button"
                  className="text-left text-teal-700 underline-offset-2 hover:underline dark:text-teal-400"
                  onClick={() => setEmail(account)}
                >
                  {account}
                </button>
                <span className="text-xs text-slate-500"> — {role}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
