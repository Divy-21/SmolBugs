import Dashboard from "./dashboard";

// The machine-condition dashboard. It fetches everything in the browser from
// this app's own /api/v1 routes (see dashboard.tsx).
export default function Home() {
  return <Dashboard />;
}
