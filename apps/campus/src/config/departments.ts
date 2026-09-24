export type BuildingType =
  | "command"
  | "research"
  | "outreach"
  | "travel"
  | "trades"
  | "procurement"
  | "grassroots"
  | "labs";

export type DepartmentConfig = {
  id: string;
  name: string;
  shortName: string;
  buildingType: BuildingType;
  theme: string;
  fallback: { x: number; z: number };
};

export const DEPARTMENT_CATALOG: DepartmentConfig[] = [
  { id: "command", name: "Ayven Command", shortName: "COMMAND", buildingType: "command", theme: "#d4b45a", fallback: { x: 0, z: 0 } },
  { id: "research", name: "Research & Intelligence", shortName: "RESEARCH", buildingType: "research", theme: "#4aa3c7", fallback: { x: -18, z: -10 } },
  { id: "outreach", name: "Sales & Outreach", shortName: "OUTREACH", buildingType: "outreach", theme: "#c77a4a", fallback: { x: -16, z: 12 } },
  { id: "travel", name: "Travel", shortName: "TRAVEL", buildingType: "travel", theme: "#5cbf8a", fallback: { x: 18, z: -8 } },
  { id: "trades", name: "Trades & Property", shortName: "TRADES", buildingType: "trades", theme: "#8aa05a", fallback: { x: -28, z: 2 } },
  { id: "procurement", name: "Procurement & Suppliers", shortName: "PROCUREMENT", buildingType: "procurement", theme: "#6a8ad1", fallback: { x: 28, z: 2 } },
  { id: "grassroots", name: "Grassroots Football", shortName: "GRASSROOTS", buildingType: "grassroots", theme: "#d16a8a", fallback: { x: 0, z: 22 } },
  { id: "labs", name: "Ayven Labs", shortName: "LABS", buildingType: "labs", theme: "#b07ad1", fallback: { x: 16, z: 14 } },
];

export const catalogById = Object.fromEntries(DEPARTMENT_CATALOG.map((d) => [d.id, d]));
