"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const router = useRouter();
  const [passcode, setPasscode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const res = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passcode }),
    });
    setLoading(false);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(body.error ?? "Login failed");
      return;
    }
    router.replace("/");
    router.refresh();
  }

  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <form
        onSubmit={handleSubmit}
        style={{
          background: "#fdfaf5",
          border: "1px solid #e8e0d5",
          borderRadius: 12,
          padding: 32,
          width: 320,
        }}
      >
        <h1 style={{ fontSize: 20, marginTop: 0, color: "#d94f3d" }}>
          Founder Console
        </h1>
        <p style={{ fontSize: 13, color: "#635747", marginTop: -8 }}>
          Internal only. Not part of the host product.
        </p>
        <input
          type="password"
          value={passcode}
          onChange={(e) => setPasscode(e.target.value)}
          placeholder="Passcode"
          autoFocus
          style={{
            width: "100%",
            boxSizing: "border-box",
            padding: "10px 12px",
            borderRadius: 8,
            border: "1px solid #e8e0d5",
            marginTop: 12,
            fontSize: 14,
          }}
        />
        {error && (
          <p style={{ color: "#d94f3d", fontSize: 13 }}>{error}</p>
        )}
        <button
          type="submit"
          disabled={loading}
          style={{
            width: "100%",
            marginTop: 16,
            padding: "10px 12px",
            borderRadius: 8,
            border: "none",
            background: "#d94f3d",
            color: "#fff",
            fontWeight: 600,
            fontSize: 14,
            cursor: "pointer",
          }}
        >
          {loading ? "Checking..." : "Enter"}
        </button>
      </form>
    </main>
  );
}
