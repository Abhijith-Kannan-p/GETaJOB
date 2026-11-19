import SearchClient from "./search-client";

// Static shell (prerendered); the search itself runs client-side against the backend API.
export default function Home() {
  return (
    <main className="flex-1">
      <SearchClient />
    </main>
  );
}
