# Deployment Guide

This document describes what a human operator needs to do to finish wiring up the live deployment.

The frontend scaffold is already live at **https://frontend-yard-logix.vercel.app**.

Auth (Clerk) and billing (Stripe) are integrated in code but require real credentials to activate.

---

## 1. Get Clerk credentials

1. Go to https://dashboard.clerk.com and create (or select) your application.
2. From **API Keys**, copy:
   - `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`
   - `CLERK_SECRET_KEY`

Add them to Vercel:

```bash
vercel env add NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
vercel env add CLERK_SECRET_KEY
```

Choose **Production**, **Preview**, and **Development** scopes as appropriate.

---

## 2. Get Stripe test credentials

1. Go to https://dashboard.stripe.com/test/apikeys
2. Copy:
   - `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY` (starts with `pk_test_`)
   - `STRIPE_SECRET_KEY` (starts with `sk_test_`)

Add them to Vercel:

```bash
vercel env add NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY
vercel env add STRIPE_SECRET_KEY
```

3. (Optional) Set up a webhook endpoint in the Stripe dashboard pointing at `https://frontend-yard-logix.vercel.app/api/webhooks/stripe` and copy the signing secret:

```bash
vercel env add STRIPE_WEBHOOK_SECRET
```

---

## 3. Deploy the backend to Railway

The backend is a FastAPI app located in `./backend`.

```bash
# Install the Railway CLI if you don't have it
npm install -g @railway/cli

# Log in interactively
railway login

# From the backend directory, initialise and deploy
cd backend
railway init          # creates a new project or links an existing one
railway up            # builds and deploys the service
```

After deploy, Railway will give you a public URL (e.g. `https://poulpe-backend.up.railway.app`).

---

## 4. Wire the backend URL into Vercel

```bash
vercel env add NEXT_PUBLIC_API_URL
# enter the Railway URL when prompted, e.g. https://poulpe-backend.up.railway.app
```

Then redeploy the frontend so it picks up the new variable:

```bash
vercel --prod
```

---

## 5. Database

The backend uses SQLAlchemy with Alembic migrations. Railway can provision a Postgres database automatically:

1. In the Railway dashboard, add a **Postgres** plugin to your project.
2. Railway injects `DATABASE_URL` automatically — no manual step needed.
3. On first boot the app runs `alembic upgrade head` to apply migrations.

If you prefer Supabase or another provider, set `DATABASE_URL` manually:

```bash
railway variables set DATABASE_URL=postgresql://user:pass@host:5432/dbname
```

---

## 6. Environment variable summary

| Variable | Where set | Source |
|---|---|---|
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Vercel | Clerk dashboard |
| `CLERK_SECRET_KEY` | Vercel | Clerk dashboard |
| `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY` | Vercel | Stripe test dashboard |
| `STRIPE_SECRET_KEY` | Vercel | Stripe test dashboard |
| `STRIPE_WEBHOOK_SECRET` | Vercel | Stripe webhook config |
| `NEXT_PUBLIC_API_URL` | Vercel | Railway deploy URL |
| `DATABASE_URL` | Railway | Auto-injected by Railway Postgres plugin |

---

## Current status

| Component | Status |
|---|---|
| Frontend (Next.js + Tailwind) | Live at https://frontend-yard-logix.vercel.app |
| Auth (Clerk) | Code integrated, needs env vars wired in |
| Billing (Stripe) | Code integrated, needs env vars wired in |
| Backend (FastAPI) | Ready to deploy, needs `railway up` |
| Database schema | Migrations written, will run on first boot |
