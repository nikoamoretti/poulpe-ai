import { SignIn } from "@clerk/nextjs";

const hasClerkKey = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

export default function SignInPage() {
  if (!hasClerkKey) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div style={{ textAlign: "center", padding: "2rem" }}>
          <h2 style={{ marginBottom: "1rem" }}>Auth not configured</h2>
          <p style={{ color: "var(--ink-2)" }}>
            Set <code>NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY</code> and <code>CLERK_SECRET_KEY</code> to enable sign-in.
          </p>
        </div>
      </div>
    );
  }
  return (
    <div className="flex min-h-screen items-center justify-center">
      <SignIn />
    </div>
  );
}
