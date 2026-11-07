import { useEffect, useState } from "react";

// Chart colours as plain hex (SVG attributes cannot read CSS variables reliably)
const LIGHT = {
  x: "#2a78d6", y: "#eb6834", z: "#1baf7a", temp: "#d03b3b",
  grid: "#e6e5e0", axis: "#8a8984", text: "#52514e", marker: "#6250d6", surface: "#fcfcfb",
};
const DARK = {
  x: "#3987e5", y: "#d95926", z: "#199e70", temp: "#e66767",
  grid: "#2e2e2c", axis: "#7a7974", text: "#c3c2b7", marker: "#9085e9", surface: "#1a1a19",
};

export type ChartTheme = typeof LIGHT;

export function useChartTheme(): ChartTheme {
  const query = "(prefers-color-scheme: dark)";
  const [dark, setDark] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const m = window.matchMedia(query);
    const on = () => setDark(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return dark ? DARK : LIGHT;
}
