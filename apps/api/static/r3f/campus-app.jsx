import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import * as THREE from "three";

const CATALOG = [
  { id: "command", name: "Ayven Command", shortName: "COMMAND", theme: "#d4b45a", type: "command", x: 0, z: 0 },
  { id: "research", name: "Research & Intelligence", shortName: "RESEARCH", theme: "#4aa3c7", type: "research", x: -20, z: -12 },
  { id: "outreach", name: "Sales & Outreach", shortName: "OUTREACH", theme: "#c77a4a", type: "outreach", x: -18, z: 14 },
  { id: "travel", name: "Travel", shortName: "TRAVEL", theme: "#5cbf8a", type: "travel", x: 20, z: -10 },
  { id: "trades", name: "Trades & Property", shortName: "TRADES", theme: "#8aa05a", type: "trades", x: -32, z: 2 },
  { id: "procurement", name: "Procurement & Suppliers", shortName: "PROCUREMENT", theme: "#6a8ad1", type: "procurement", x: 32, z: 2 },
  { id: "grassroots", name: "Grassroots Football", shortName: "GRASSROOTS", theme: "#d16a8a", type: "grassroots", x: 0, z: 26 },
  { id: "labs", name: "Ayven Labs", shortName: "LABS", theme: "#b07ad1", type: "labs", x: 18, z: 16 },
];
const CAT = Object.fromEntries(CATALOG.map((d) => [d.id, d]));

let state = {
  departments: CATALOG.map((d) => ({ id: d.id, name: d.name, x: d.x, z: d.z })),
  agents: [], events: [], tasks: [], approvals: [], projects: [],
  selectedId: "milo", focus: { kind: "campus" },
  brief: "Research whether a European football trips business can obtain match tickets for Borussia Dortmund, Ajax, Sparta Prague and Rosenborg without purchasing inventory upfront.",
};
const listeners = new Set();
function set(partial) {
  state = { ...state, ...(typeof partial === "function" ? partial(state) : partial) };
  listeners.forEach((l) => l());
}
function useCampus(sel) {
  return React.useSyncExternalStore(
    (cb) => { listeners.add(cb); return () => listeners.delete(cb); },
    () => sel(state)
  );
}
useCampus.getState = () => state;
useCampus.focusCampus = () => set({ focus: { kind: "campus" } });
useCampus.focusBuilding = (id) => set({ focus: { kind: "building", id } });
useCampus.focusAgent = (id) => set({ focus: { kind: "agent", id }, selectedId: id });
useCampus.hydrate = async () => {
  const s = await (await fetch("/state")).json();
  const byId = Object.fromEntries((s.departments || []).map((d) => [d.id, d]));
  set({
    departments: CATALOG.map((c) => ({ id: c.id, name: byId[c.id]?.name || c.name, x: byId[c.id]?.x ?? c.x, z: byId[c.id]?.z ?? c.z })),
    agents: s.agents || [],
    events: (s.events || []).slice().reverse(),
    tasks: s.tasks || [],
    approvals: s.approvals || [],
    projects: s.projects || [],
  });
};

const STATUS = { idle: "#8fb3a3", working: "#3db4ff", researching: "#3dffb0", using_tool: "#7ee0ff", needs_approval: "#ffb03d", error: "#ff5a3d", waiting: "#c9c14a", completed: "#9ad47a" };

function Mass({ color, args, position }) {
  return (
    <mesh position={position} castShadow receiveShadow>
      <boxGeometry args={args} />
      <meshStandardMaterial color={color} roughness={0.45} metalness={0.12} />
    </mesh>
  );
}

function Building({ department, selected, attention, onClick }) {
  const cfg = CAT[department.id];
  const color = cfg.theme;
  const type = cfg.type;
  return (
    <group position={[department.x, 0, department.z]} onClick={(e) => { e.stopPropagation(); onClick(); }}>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.02, 0]} receiveShadow>
        <circleGeometry args={[8.2, 28]} />
        <meshStandardMaterial color={attention ? "#3a2a12" : selected ? "#2a3d30" : "#163024"} />
      </mesh>
      {type === "command" && (<><Mass color={color} args={[8.6, 5.4, 6.4]} position={[0, 2.7, 0]} /><Mass color="#e8d9a0" args={[3.4, 2.6, 3.4]} position={[0, 6.7, 0]} /></>)}
      {type === "research" && (<><Mass color={color} args={[9.4, 2.5, 4.6]} position={[0, 1.25, 0]} /><Mass color="#2b4a58" args={[4.4, 1.8, 4.4]} position={[2.2, 3.4, 0]} /></>)}
      {type === "travel" && (<><Mass color={color} args={[7.6, 2.7, 5.2]} position={[0, 1.35, 0]} /></>)}
      {type === "outreach" && (<><Mass color={color} args={[6.6, 3.2, 5.4]} position={[0, 1.6, 0]} /><Mass color="#7a4a2c" args={[2.2, 4.6, 2.2]} position={[-2.5, 2.3, 1.5]} /></>)}
      {type === "labs" && (<><Mass color={color} args={[5.4, 2.3, 5.4]} position={[0, 1.15, 0]} /><Mass color="#7a4aa0" args={[3.5, 2.9, 3.5]} position={[1.2, 3.6, 0.4]} /></>)}
      {type === "trades" && (<><Mass color={color} args={[8.6, 2.9, 5.8]} position={[0, 1.45, 0]} /></>)}
      {type === "procurement" && (<><Mass color={color} args={[7.4, 3.5, 5.0]} position={[0, 1.75, 0]} /></>)}
      {type === "grassroots" && (<><mesh position={[0, 0.08, 0]} rotation={[-Math.PI / 2, 0, 0]}><circleGeometry args={[3.6, 28]} /><meshStandardMaterial color="#2f6a3a" /></mesh><Mass color={color} args={[5.8, 2.1, 4.6]} position={[0, 1.05, 0]} /></>)}
    </group>
  );
}

function agentPos(dept, index, isMilo) {
  if (isMilo) return [dept.x + 0.2, 0, dept.z + 4.6];
  const col = index % 3, row = Math.floor(index / 3);
  return [dept.x - 2.4 + col * 1.8, 0, dept.z + 4.0 + row * 1.55];
}

function Avatar({ agent, position, selected, onClick }) {
  const ref = useRef();
  const color = STATUS[agent.status] || "#889";
  const busy = ["working", "researching", "using_tool"].includes(agent.status);
  useFrame(({ clock }) => {
    if (!ref.current) return;
    ref.current.position.y = busy ? 0.02 + Math.sin(clock.elapsedTime * 6) * 0.05 : 0.02;
    if (agent.status === "needs_approval") ref.current.rotation.y = clock.elapsedTime * 1.4;
  });
  return (
    <group position={position} onClick={(e) => { e.stopPropagation(); onClick(); }}>
      <group ref={ref}>
        <mesh position={[0, 0.55, 0]} castShadow>
          <capsuleGeometry args={[agent.id === "milo" ? 0.28 : 0.22, agent.id === "milo" ? 0.72 : 0.55, 4, 8]} />
          <meshStandardMaterial color={selected ? "#fff" : color} emissive={color} emissiveIntensity={agent.status === "needs_approval" || agent.status === "error" ? 0.45 : 0.18} />
        </mesh>
      </group>
    </group>
  );
}

function CameraRig() {
  const controls = useRef();
  const { camera } = useThree();
  const focus = useCampus((s) => s.focus);
  const departments = useCampus((s) => s.departments);
  const agents = useCampus((s) => s.agents);
  const target = useRef(new THREE.Vector3(0, 0, 0));
  const pos = useRef(new THREE.Vector3(38, 30, 38));
  useFrame(() => {
    let look = new THREE.Vector3(0, 0.5, 0);
    let dest = new THREE.Vector3(38, 30, 38);
    if (focus.kind === "building") {
      const d = departments.find((x) => x.id === focus.id);
      if (d) { look.set(d.x, 1.2, d.z); dest.set(d.x + 14, 11, d.z + 14); }
    } else if (focus.kind === "agent") {
      const a = agents.find((x) => x.id === focus.id);
      const d = departments.find((x) => x.id === a?.department_id);
      if (a && d) {
        const idx = agents.filter((x) => x.department_id === a.department_id).findIndex((x) => x.id === a.id);
        const p = agentPos(d, idx, a.id === "milo");
        look.set(p[0], 0.8, p[2]); dest.set(p[0] + 6, 5.5, p[2] + 6);
      }
    }
    pos.current.lerp(dest, 0.06); target.current.lerp(look, 0.08);
    camera.position.copy(pos.current);
    if (controls.current) { controls.current.target.copy(target.current); controls.current.update(); }
  });
  return <OrbitControls ref={controls} makeDefault maxPolarAngle={Math.PI / 2.2} minDistance={8} maxDistance={80} />;
}

function Scene() {
  const departments = useCampus((s) => s.departments);
  const agents = useCampus((s) => s.agents);
  const approvals = useCampus((s) => s.approvals);
  const focus = useCampus((s) => s.focus);
  const selectedId = useCampus((s) => s.selectedId);
  const pending = new Set(approvals.filter((a) => a.status === "pending").map((a) => agents.find((x) => x.id === a.agent_id)?.department_id));
  agents.filter((a) => a.status === "needs_approval" || a.status === "error").forEach((a) => pending.add(a.department_id));
  const cmd = departments.find((d) => d.id === "command") || { x: 0, z: 0 };
  return (
    <>
      <color attach="background" args={["#0b1611"]} />
      <fog attach="fog" args={["#0b1611", 60, 130]} />
      <hemisphereLight args={["#c5ddd0", "#121c16", 0.75]} />
      <directionalLight position={[28, 36, 16]} intensity={1.15} castShadow />
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow><circleGeometry args={[64, 56]} /><meshStandardMaterial color="#143222" /></mesh>
      {departments.filter((d) => d.id !== "command").map((d) => {
        const mx = (cmd.x + d.x) / 2, mz = (cmd.z + d.z) / 2;
        const dx = d.x - cmd.x, dz = d.z - cmd.z;
        const len = Math.hypot(dx, dz), rot = Math.atan2(dx, dz);
        return <mesh key={d.id} position={[mx, 0.03, mz]} rotation={[-Math.PI / 2, 0, -rot]} receiveShadow><planeGeometry args={[1.5, len]} /><meshStandardMaterial color="#2a3d32" /></mesh>;
      })}
      {departments.map((d) => (
        <Building key={d.id} department={d} selected={focus.kind === "building" && focus.id === d.id} attention={pending.has(d.id)} onClick={() => useCampus.focusBuilding(d.id)} />
      ))}
      {departments.map((d) => agents.filter((a) => a.department_id === d.id).map((a, i) => (
        <Avatar key={a.id} agent={a} position={agentPos(d, i, a.id === "milo")} selected={selectedId === a.id} onClick={() => useCampus.focusAgent(a.id)} />
      )))}
      <CameraRig />
    </>
  );
}

function humanEvent(e) {
  const map = {
    "project.created": "Project opened", "task.created": "Task created", "task.assigned": "Task assigned",
    "agent.started_task": "Started work", "agent.using_tool": "Using a tool", "agent.researching": "Researching",
    "agent.needs_approval": "Needs approval", "agent.completed": "Completed work", "agent.failed": "Failed",
    "agent.idle": "Idle", "approval.requested": "Approval requested", "approval.resolved": "Approval resolved",
    "milo.synthesized": "Milo consolidated findings",
  };
  return `${map[e.type] || e.type}${e.summary ? " — " + e.summary : ""}`;
}

function App() {
  const agents = useCampus((s) => s.agents);
  const events = useCampus((s) => s.events);
  const tasks = useCampus((s) => s.tasks);
  const approvals = useCampus((s) => s.approvals);
  const projects = useCampus((s) => s.projects);
  const selectedId = useCampus((s) => s.selectedId);
  const brief = useCampus((s) => s.brief);
  const [text, setText] = useState(brief);
  useEffect(() => {
    useCampus.hydrate();
    const es = new EventSource("/events/stream");
    es.onmessage = () => useCampus.hydrate();
    const t = setInterval(() => useCampus.hydrate(), 2500);
    return () => { es.close(); clearInterval(t); };
  }, []);
  const selected = agents.find((a) => a.id === selectedId);
  const pending = approvals.filter((a) => a.status === "pending");
  const project = projects[0];
  const currentTask = tasks.filter((t) => t.agent_id === selected?.id)[0];
  const waiting = selected?.status === "needs_approval" ? "Lee approval before any external send" : selected?.status === "error" ? "Recovery / retry" : "—";
  return (
    <>
      <Canvas shadows camera={{ position: [38, 30, 38], fov: 42 }}>
        <Scene />
      </Canvas>
      <div className="overlay">
        <div className="panel top">
          <div className="brand">AYVEN CAMPUS</div>
          <div className="muted">Headquarters · live company state</div>
          <textarea value={text} onChange={(e) => setText(e.target.value)} />
          <div className="row">
            <button onClick={() => fetch("/projects", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ objective: text }) })}>Ask Milo</button>
            <button className="ghost" onClick={() => useCampus.focusCampus()}>Campus view</button>
          </div>
          <div className="row">
            <button className="ghost" onClick={() => fetch("/demo/fail", { method: "POST" }).then(() => useCampus.hydrate())}>Demo fail</button>
            <button className="ghost" onClick={() => fetch("/demo/retry", { method: "POST" }).then(() => useCampus.hydrate())}>Recover</button>
          </div>
          {pending.map((p) => (
            <div key={p.id} className="approval">
              <strong>Approval required</strong>
              <p className="muted">{p.summary}</p>
              <button onClick={() => fetch(`/approvals/${p.id}/resolve`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision: "approved" }) }).then(() => useCampus.hydrate())}>Approve</button>
              <button className="ghost" onClick={() => fetch(`/approvals/${p.id}/resolve`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision: "rejected" }) }).then(() => useCampus.hydrate())}>Reject</button>
            </div>
          ))}
          <div className="cmd-block">
            <div className="muted">Command</div>
            <div>Working: {agents.filter((a) => ["working", "researching", "using_tool"].includes(a.status)).length}</div>
            <div>Approvals waiting: {pending.length}</div>
            <div>Errors: {agents.filter((a) => a.status === "error").length}</div>
            <div>Est. cost: ${agents.reduce((n, a) => n + Number(a.cost_usd || 0), 0).toFixed(4)}</div>
            {project && <p className="muted">{project.result ? String(project.result).slice(0, 280) : project.title}</p>}
          </div>
        </div>
        <div className="panel right">
          <h1>Departments</h1>
          {CATALOG.map((d) => <div key={d.id} className="dept-line" onClick={() => useCampus.focusBuilding(d.id)}><span className="swatch" style={{ background: d.theme }} />{d.shortName}</div>)}
          {selected && (
            <div className="inspector">
              <h1>{selected.name}</h1>
              <div className="muted">{selected.role} · {CAT[selected.department_id]?.name}</div>
              <p><b>Status</b> {selected.status}</p>
              <p><b>Project</b> {project?.title || "—"}</p>
              <p><b>Task</b> {currentTask?.title || "—"}</p>
              <p><b>Tool</b> {selected.current_tool || "—"}</p>
              <p><b>Progress</b> {Math.round((selected.progress || 0) * 100)}%</p>
              <p><b>Waiting for</b> {waiting}</p>
              <p><b>Approval</b> {selected.status === "needs_approval" ? "YES" : "no"}</p>
              <p className="muted">{selected.last_summary}</p>
            </div>
          )}
        </div>
        <div className="panel feed">
          <strong>Activity</strong>
          {events.slice(-16).reverse().map((e) => <div key={e.event_id} className="muted">{humanEvent(e)}</div>)}
        </div>
      </div>
    </>
  );
}

createRoot(document.getElementById("root")).render(<App />);
