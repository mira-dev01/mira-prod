"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/lib/auth-context";
import { ApiError } from "@/lib/api";

export default function LoginPage() {
  const { login, register } = useAuth();
  const [submitting, setSubmitting] = useState(false);

  // Pre-filled with the public demo account so anyone opening this URL for a
  // demo can just click "Log in" without being handed credentials separately.
  const [loginEmail, setLoginEmail] = useState("demo@mira.ai");
  const [loginPassword, setLoginPassword] = useState("MiraDemo2024");

  const [regEmail, setRegEmail] = useState("");
  const [regPassword, setRegPassword] = useState("");
  const [regName, setRegName] = useState("");
  const [regPhone, setRegPhone] = useState("");

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await login(loginEmail, loginPassword);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRegister(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await register(regEmail, regPassword, regName || undefined, regPhone || undefined);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Registration failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-1 items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-2xl">MIRA</CardTitle>
          <CardDescription>Sign in to manage your properties and live calls</CardDescription>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue="login">
            <TabsList className="grid w-full grid-cols-2">
              <TabsTrigger value="login">Log in</TabsTrigger>
              <TabsTrigger value="register">Register</TabsTrigger>
            </TabsList>
            <TabsContent value="login">
              <form onSubmit={handleLogin} className="space-y-4 pt-4">
                <div className="space-y-2">
                  <Label htmlFor="login-email">Email</Label>
                  <Input
                    id="login-email"
                    type="email"
                    required
                    value={loginEmail}
                    onChange={(e) => setLoginEmail(e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="login-password">Password</Label>
                  <Input
                    id="login-password"
                    type="password"
                    required
                    value={loginPassword}
                    onChange={(e) => setLoginPassword(e.target.value)}
                  />
                </div>
                <Button type="submit" className="w-full" disabled={submitting}>
                  Log in
                </Button>
                <p className="text-center text-xs text-muted-foreground">
                  Demo credentials pre-filled — just click Log in.
                </p>
              </form>
            </TabsContent>
            <TabsContent value="register">
              <form onSubmit={handleRegister} className="space-y-4 pt-4">
                <div className="space-y-2">
                  <Label htmlFor="reg-name">Name</Label>
                  <Input id="reg-name" value={regName} onChange={(e) => setRegName(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="reg-email">Email</Label>
                  <Input
                    id="reg-email"
                    type="email"
                    required
                    value={regEmail}
                    onChange={(e) => setRegEmail(e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="reg-phone">Phone</Label>
                  <Input id="reg-phone" value={regPhone} onChange={(e) => setRegPhone(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="reg-password">Password</Label>
                  <Input
                    id="reg-password"
                    type="password"
                    required
                    minLength={8}
                    value={regPassword}
                    onChange={(e) => setRegPassword(e.target.value)}
                  />
                </div>
                <Button type="submit" className="w-full" disabled={submitting}>
                  Create account
                </Button>
              </form>
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  );
}
