"use client";

import { UserButton } from "@clerk/nextjs";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

const PLANS = [
  {
    name: "Starter",
    price: 29,
    priceId: process.env.NEXT_PUBLIC_STRIPE_PRICE_STARTER ?? "",
    features: ["5 active projects", "10 sessions/month", "Community support"],
    highlight: false,
  },
  {
    name: "Pro",
    price: 99,
    priceId: process.env.NEXT_PUBLIC_STRIPE_PRICE_PRO ?? "",
    features: [
      "Unlimited projects",
      "Unlimited sessions",
      "Priority support",
      "Custom integrations",
    ],
    highlight: true,
  },
];

function BillingContent() {
  const params = useSearchParams();
  const success = params.get("success");
  const canceled = params.get("canceled");
  const [loading, setLoading] = useState<string | null>(null);

  async function checkout(priceId: string) {
    setLoading(priceId);
    try {
      const res = await fetch("/api/billing/create-checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ priceId }),
      });
      const data = await res.json();
      if (data.url) window.location.href = data.url;
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className="min-h-screen" style={{ background: "var(--bg)" }}>
      <header className="flex items-center justify-between px-8 py-4 border-b" style={{ borderColor: "var(--line)" }}>
        <h1 className="text-xl font-semibold" style={{ color: "var(--ink)", fontFamily: "var(--font-display)" }}>
          Poulpe AI — Billing
        </h1>
        <UserButton />
      </header>

      <main className="max-w-4xl mx-auto px-6 py-16">
        {success && (
          <div className="mb-8 rounded-lg px-4 py-3 text-sm font-medium" style={{ background: "var(--green-soft)", color: "var(--green)" }}>
            Subscription activated! Welcome aboard.
          </div>
        )}
        {canceled && (
          <div className="mb-8 rounded-lg px-4 py-3 text-sm font-medium" style={{ background: "var(--yellow-soft)", color: "var(--yellow)" }}>
            Checkout canceled. No charge was made.
          </div>
        )}

        <div className="text-center mb-12">
          <h2 className="text-3xl font-bold mb-3" style={{ color: "var(--ink)", fontFamily: "var(--font-display)" }}>
            Simple, transparent pricing
          </h2>
          <p style={{ color: "var(--ink-2)" }}>
            Start free. Upgrade when you need more.
          </p>
        </div>

        <div className="grid md:grid-cols-2 gap-6">
          {PLANS.map((plan) => (
            <div
              key={plan.name}
              className="rounded-xl p-8 flex flex-col gap-6 transition-all"
              style={{
                background: plan.highlight ? "var(--accent)" : "var(--bg-raised)",
                border: `1px solid ${plan.highlight ? "var(--accent)" : "var(--line)"}`,
                boxShadow: plan.highlight ? "0 8px 32px var(--accent-glow)" : "none",
              }}
            >
              <div>
                <h3
                  className="text-lg font-semibold mb-1"
                  style={{ color: plan.highlight ? "#fff" : "var(--ink)", fontFamily: "var(--font-display)" }}
                >
                  {plan.name}
                </h3>
                <div className="flex items-baseline gap-1">
                  <span
                    className="text-4xl font-bold"
                    style={{ color: plan.highlight ? "#fff" : "var(--ink)" }}
                  >
                    ${plan.price}
                  </span>
                  <span style={{ color: plan.highlight ? "rgba(255,255,255,0.7)" : "var(--ink-3)" }}>
                    /month
                  </span>
                </div>
              </div>

              <ul className="flex flex-col gap-2 flex-1">
                {plan.features.map((f) => (
                  <li
                    key={f}
                    className="flex items-center gap-2 text-sm"
                    style={{ color: plan.highlight ? "rgba(255,255,255,0.9)" : "var(--ink-2)" }}
                  >
                    <span style={{ color: plan.highlight ? "#fff" : "var(--green)" }}>✓</span>
                    {f}
                  </li>
                ))}
              </ul>

              <button
                onClick={() => checkout(plan.priceId)}
                disabled={loading === plan.priceId}
                className="w-full rounded-lg px-4 py-3 text-sm font-medium transition-all cursor-pointer disabled:opacity-60"
                style={{
                  background: plan.highlight ? "#fff" : "var(--accent)",
                  color: plan.highlight ? "var(--accent)" : "#fff",
                }}
              >
                {loading === plan.priceId ? "Loading…" : `Get ${plan.name}`}
              </button>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}

export default function BillingPage() {
  return (
    <Suspense>
      <BillingContent />
    </Suspense>
  );
}
