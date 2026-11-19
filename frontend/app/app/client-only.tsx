"use client";

// The matching UI reads the profile from localStorage on first render, so it must not be prerendered.
import dynamic from "next/dynamic";

const AppClient = dynamic(() => import("./app-client"), {
  ssr: false,
  loading: () => <p className="mx-auto max-w-6xl px-4 py-8 text-sm text-zinc-500">Loading…</p>,
});

export default function ClientOnly() {
  return <AppClient />;
}
