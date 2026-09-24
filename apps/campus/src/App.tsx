import { useEffect, useState } from "react";
import { Canvas } from "@react-three/fiber";
import Campus from "./world/Campus";
import { API, connectStream, humanEvent, useCampus } from "./state/store";
import { catalogById } from "./config/departments";

export default function App() {
  const hydrate = useCampus((s) => s.hydrate);
  const agents = useCampus((s) => s.agents);
  const events = useCampus((s) => s.events);
  const tasks = useCampus((s) => s.tasks);
  const approvals = useCampus((s) => s.approvals);
  const projects = useCampus((s) => s.projects);
  const selectedId = useCampus((s) => s.selectedId);
  const brief = useCampus((s) => s.brief);
  const submitObjective = useCampus((s) => s.submitObjective);
  const resolve = useCampus((s) => s.resolve);
  const focusCampus = useCampus((s) => s.focusCampus);
  const focusBuilding = useCampus((s) => s.focusBuilding);
  const [text, setText] = useState(brief);
  useEffect(() => {
    hydrate();
    const es = connectStream();
    const t = setInterval(hydrate, 2500);
    return () => { es.close(); clearInterval(t); };
  }, [hydrate]);
  const selected = agents.find((a) => a.id === selectedId);
  const pending = approvals.filter((a) => a.status === "pending");
  const selTasks = tasks.filter((t) => t.agent_id === selected?.id);
  const currentTask = selTasks.find((t) => ["queued", "needs_approval", "running"].includes(t.status)) || selTasks[0];
  const project = projects[0];
  const waiting = selected?.status === "needs_approval" ? "Lee approval before any external send" : selected?.status === "error" ? "Recovery / retry" : "—";
  return (
    <>
      <Canvas shadows camera={{ position: [36, 28, 36], fov: 42 }}>
        <Campus />
      </Canvas>
      <div className="overlay">
        <div className="panel top">
          <div className="brand">AYVEN CAMPUS</div>
          <div className="muted">Headquarters · live company state</div>
          <textarea value={text} onChange={(e) => setText(e.target.value)} />
          <div className="row">
            <button onClick={() => submitObjective(text)}>Ask Milo</button>
            <button className="ghost" onClick={focusCampus}>Campus view</button>
          </div>
          <div className="row">
            <button className="ghost" onClick={() => fetch(`${API}/demo/fail`, { method: "POST" }).then(() => hydrate())}>Demo fail</button>
            <button className="ghost" onClick={() => fetch(`${API}/demo/retry`, { method: "POST" }).then(() => hydrate())}>Recover</button>
          </div>
          {pending.map((p) => (
            <div key={p.id} className="approval">
              <strong>Approval required</strong>
              <p className="muted">{p.summary}</p>
              <button onClick={() => resolve(p.id, "approved")}>Approve</button>
              <button className="ghost" onClick={() => resolve(p.id, "rejected")}>Reject</button>
            </div>
          ))}
          <div className="cmd-block">
            <div className="muted">Command</div>
            <div>Working: {agents.filter((a) => ["working", "researching"].includes(a.status)).length}</div>
            <div>Approvals waiting: {pending.length}</div>
            <div>Errors: {agents.filter((a) => a.status === "error").length}</div>
            <div>Est. cost: ${agents.reduce((n, a) => n + Number(a.cost_usd || 0), 0).toFixed(4)}</div>
            {project && <p className="muted">{project.result ? String(project.result).slice(0, 280) : project.title}</p>}
          </div>
        </div>
        <div className="panel right">
          <h1>Departments</h1>
          {Object.values(catalogById).map((d) => (
            <div key={d.id} className="dept-line" onClick={() => focusBuilding(d.id)}>
              <span className="swatch" style={{ background: d.theme }} />
              {d.shortName}
            </div>
          ))}
          {selected && (
            <div className="inspector">
              <h1>{selected.name}</h1>
              <div className="muted">{selected.role} · {catalogById[selected.department_id]?.name}</div>
              <p><b>Status</b> {selected.status}</p>
              <p><b>Project</b> {project?.title || "—"}</p>
              <p><b>Task</b> {currentTask?.title || "—"}</p>
              <p><b>Tool</b> {selected.current_tool || "—"}</p>
              <p><b>Progress</b> {Math.round((selected.progress || 0) * 100)}%</p>
              <p><b>Waiting for</b> {waiting}</p>
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
