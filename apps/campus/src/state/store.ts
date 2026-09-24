import { create } from "zustand";
import { catalogById } from "../config/departments";

export const API = (import.meta as any).env?.VITE_API_URL || "";

export type Agent = {
  id: string;
  name: string;
  role: string;
  department_id: string;
  status: string;
  current_task_id?: string;
  current_tool?: string;
  last_summary?: string;
  progress?: number;
  tokens?: number;
  cost_usd?: number;
};

export type Dept = { id: string; name: string; x: number; z: number };
export type Ev = {
  event_id: string;
  timestamp: string;
  type: string;
  summary: string;
  agent_id?: string;
  department_id?: string;
  status?: string;
  tool?: string;
  project_id?: string;
  task_id?: string;
};

export type Focus =
  | { kind: "campus" }
  | { kind: "building"; id: string }
  | { kind: "agent"; id: string };

type Store = {
  departments: Dept[];
  agents: Agent[];
  events: Ev[];
  tasks: any[];
  approvals: any[];
  projects: any[];
  selectedId: string | null;
  focus: Focus;
  brief: string;
  setSelected: (id: string | null) => void;
  focusBuilding: (id: string) => void;
  focusAgent: (id: string) => void;
  focusCampus: () => void;
  hydrate: () => Promise<void>;
  submitObjective: (text: string) => Promise<void>;
  resolve: (id: string, decision: string) => Promise<void>;
};

function mergeDepts(rows: Dept[]): Dept[] {
  const byId = Object.fromEntries((rows || []).map((d) => [d.id, d]));
  return Object.keys(catalogById).map((id) => {
    const c = catalogById[id];
    const row = byId[id];
    return {
      id: c.id,
      name: row?.name || c.name,
      x: row?.x ?? c.fallback.x,
      z: row?.z ?? c.fallback.z,
    };
  });
}

export const useCampus = create<Store>((set, get) => ({
  departments: [],
  agents: [],
  events: [],
  tasks: [],
  approvals: [],
  projects: [],
  selectedId: "milo",
  focus: { kind: "campus" },
  brief:
    "Research whether a European football trips business can obtain match tickets for Borussia Dortmund, Ajax, Sparta Prague and Rosenborg without purchasing inventory upfront.",
  setSelected: (id) => set({ selectedId: id }),
  focusBuilding: (id) =>
    set({
      focus: { kind: "building", id },
      selectedId: get().agents.find((a) => a.department_id === id)?.id || get().selectedId,
    }),
  focusAgent: (id) => set({ focus: { kind: "agent", id }, selectedId: id }),
  focusCampus: () => set({ focus: { kind: "campus" } }),
  hydrate: async () => {
    const r = await fetch(`${API}/state`);
    const s = await r.json();
    set({
      departments: mergeDepts(s.departments),
      agents: s.agents,
      events: (s.events || []).slice().reverse(),
      tasks: s.tasks,
      approvals: s.approvals,
      projects: s.projects,
    });
  },
  submitObjective: async (text) => {
    await fetch(`${API}/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ objective: text }),
    });
  },
  resolve: async (id, decision) => {
    await fetch(`${API}/approvals/${id}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    });
    get().hydrate();
  },
}));

export function connectStream() {
  const es = new EventSource(`${API}/events/stream`);
  es.onmessage = () => {
    useCampus.getState().hydrate();
  };
  return es;
}

export function humanEvent(e: Ev): string {
  const map: Record<string, string> = {
    "project.created": "Project opened",
    "task.created": "Task created",
    "task.assigned": "Task assigned",
    "agent.started_task": "Started work",
    "agent.using_tool": "Using a tool",
    "agent.researching": "Researching",
    "agent.needs_approval": "Needs approval",
    "agent.completed": "Completed work",
    "agent.failed": "Failed",
    "agent.idle": "Idle",
    "approval.requested": "Approval requested",
    "approval.resolved": "Approval resolved",
    "milo.synthesized": "Milo consolidated findings",
  };
  const label = map[e.type] || e.type;
  return e.summary ? `${label} — ${e.summary}` : label;
}
