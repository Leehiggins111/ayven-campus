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
  agents: [], events: [], tasks: [], approvals: [], projects: [], work_packages: [], sources: [], campus_brief: null, campus_view: null,
  selectedId: "milo", selectedPackageId: "", focus: { kind: "campus" },
  brief: "Compare public suppliers of office stationery. List a price only when a page states it, and say what is still unknown.",
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
  const selected = state.selectedPackageId;
  let view = s.campus_view || null;
  if (selected) {
    view = await (await fetch(`/campus/view?package_id=${encodeURIComponent(selected)}`)).json();
  }
  set({
    departments: CATALOG.map((c) => ({ id: c.id, name: byId[c.id]?.name || c.name, x: byId[c.id]?.x ?? c.x, z: byId[c.id]?.z ?? c.z })),
    agents: s.agents || [],
    events: (s.events || []).slice().reverse(),
    tasks: s.tasks || [],
    approvals: s.approvals || [],
    projects: s.projects || [],
    work_packages: s.work_packages || [],
    campus_brief: s.campus_brief || null,
    campus_view: view,
    sources: s.sources || [],
  });
};
useCampus.selectPackage = async (id) => {
  const view = await (await fetch(`/campus/view?package_id=${encodeURIComponent(id)}`)).json();
  set({ selectedPackageId: id, campus_view: view, focus: { kind: "campus" } });
};

const VISUAL = {
  IDLE: "#8fb3a3", PLANNING: "#7eb6ff", RESEARCHING: "#3dffb0", USING_TOOL: "#7ee0ff",
  WRITING: "#e2c56b", REVIEWING: "#c9a0ff", REPAIRING: "#ffb03d", WAITING: "#c9c14a",
  NEEDS_APPROVAL: "#ffb03d", COMPLETED: "#9ad47a", FAILED: "#ff5a3d",
};
function visualOf(agent) {
  return VISUAL[agent.visual_state] ? agent.visual_state : "IDLE";
}

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
      {type === "outreach" && (<><Mass color={color} args={[6.6, 3.2, 5.4]} position={[0, 1.6, 0]} /></>)}
      {type === "labs" && (<><Mass color={color} args={[5.4, 2.3, 5.4]} position={[0, 1.15, 0]} /></>)}
      {type === "trades" && (<><Mass color={color} args={[8.6, 2.9, 5.8]} position={[0, 1.45, 0]} /></>)}
      {type === "procurement" && (<><Mass color={color} args={[7.4, 3.5, 5.0]} position={[0, 1.75, 0]} /></>)}
      {type === "grassroots" && (<><Mass color={color} args={[5.8, 2.1, 4.6]} position={[0, 1.05, 0]} /></>)}
    </group>
  );
}

function agentPos(dept, index, isMilo) {
  if (isMilo) return [dept.x + 0.2, 0, dept.z + 4.6];
  const col = index % 3, row = Math.floor(index / 3);
  return [dept.x - 2.4 + col * 1.8, 0, dept.z + 4.0 + row * 1.55];
}

function Avatar({ agent, position, selected, onClick }) {
  const color = VISUAL[visualOf(agent)] || "#889";
  return (
    <group position={position} onClick={(e) => { e.stopPropagation(); onClick(); }}>
      <mesh position={[0, 0.55, 0]} castShadow>
        <capsuleGeometry args={[agent.id === "milo" ? 0.28 : 0.22, agent.id === "milo" ? 0.72 : 0.55, 4, 8]} />
        <meshStandardMaterial color={selected ? "#fff" : color} emissive={color} emissiveIntensity={0.25} />
      </mesh>
    </group>
  );
}

function CameraRig() {
  const controls = useRef();
  const { camera } = useThree();
  const focus = useCampus((s) => s.focus);
  const departments = useCampus((s) => s.departments);
  const target = useRef(new THREE.Vector3(0, 0, 0));
  const pos = useRef(new THREE.Vector3(38, 30, 38));
  useFrame(() => {
    let look = new THREE.Vector3(0, 0.5, 0);
    let dest = new THREE.Vector3(38, 30, 38);
    if (focus.kind === "building") {
      const d = departments.find((x) => x.id === focus.id);
      if (d) { look.set(d.x, 1.2, d.z); dest.set(d.x + 14, 11, d.z + 14); }
    }
    pos.current.lerp(dest, 0.06); target.current.lerp(look, 0.08);
    camera.position.copy(pos.current);
    if (controls.current) { controls.current.target.copy(target.current); controls.current.update(); }
  });
  return <OrbitControls ref={controls} makeDefault maxPolarAngle={Math.PI / 2.2} minDistance={8} maxDistance={80} />;
}

function packagePos(pkg, departments) {
  const stage = pkg.stage || "command";
  const research = departments.find((d) => d.id === "research") || { x: -20, z: -12 };
  const command = departments.find((d) => d.id === "command") || { x: 0, z: 0 };
  if (stage === "research" || stage === "researching") return [research.x + 3.2, 0.55, research.z + 1.2];
  if (stage === "distribution" || stage === "routing") return [(research.x + command.x) / 2, 0.7, (research.z + command.z) / 2];
  if (stage === "approval" || pkg.status === "needs_approval") return [command.x - 4.2, 0.7, command.z + 3.4];
  if (stage === "results" || stage === "complete") return [command.x + 3.4, 0.7, command.z + 3.2];
  return [command.x, 0.7, command.z + 2.2];
}

function CampusNow() {
  const view = useCampus((s) => s.campus_view) || {};
  const tools = (view.tools || []).join(", ") || "—";
  const skills = (view.skills || []).join(", ") || "—";
  const research = (view.research || []).slice(0, 3).join(" · ") || "—";
  return (
    <div className="cmd-block" data-testid="campus-glance" data-package={view.package_id || ""}>
      <div className="muted">What Ayven is doing</div>
      <div data-testid="glance-doing"><b>Doing</b> {view.doing || "Idle"}</div>
      <div data-testid="glance-who"><b>Who</b> {view.who || "—"}</div>
      <div data-testid="glance-why"><b>Why</b> {view.why || "—"}</div>
      <div data-testid="glance-stage"><b>Stage</b> {view.stage_label || view.stage || "—"}</div>
      <div><b>Tools</b> {tools}</div>
      <div><b>Skills</b> {skills}</div>
      <div><b>Research</b> {research}</div>
      <div data-testid="glance-trust"><b>Trust</b> {view.trust || "—"}</div>
      <div><b>Supervisor rejected</b> {view.supervisor_rejected || 0}</div>
      <div><b>Repairing</b> {view.repairing ? "Yes" : "No"}</div>
      <div><b>Manager</b> {view.manager || "—"}</div>
      <div data-testid="glance-needs"><b>Needs you</b> {view.needs_you ? "Yes" : "No"} · <b>Clarification</b> {view.needs_clarification ? "Yes" : "No"}</div>
      <div data-testid="glance-finished"><b>Finished</b> {view.finished ? "Yes" : "No"} · <b>Time</b> {view.elapsed || "unknown"} · <b>Cost</b> {view.cost || "unknown"}</div>
      {view.outcome && <div data-testid="glance-outcome"><b>Outcome</b> {view.outcome.label}</div>}
    </div>
  );
}

function WorkflowStrip() {
  const view = useCampus((s) => s.campus_view) || {};
  const steps = view.workflow || [];
  if (!steps.length) return null;
  return (
    <div className="workflow" data-testid="workflow">
      {steps.map((step) => (
        <span key={step.id} data-testid="workflow-step" data-stage={step.id} data-state={step.state}>{step.id}</span>
      ))}
    </div>
  );
}

function RepairPanel() {
  const view = useCampus((s) => s.campus_view) || {};
  const repairs = view.repairs || [];
  if (!repairs.length) return null;
  const open = repairs.some((item) => item.status !== "repaired");
  return (
    <div className="repair" data-testid="repairs">
      <strong>{`SUPERVISOR FOUND ${repairs.length} ISSUE${repairs.length === 1 ? "" : "S"} — ${open ? "REPAIRING" : "REPAIRED"}`}</strong>
      {repairs.map((item, index) => (
        <div key={index} data-testid="repair-issue">
          <div>{item.claim}</div>
          <div className="muted">{item.repair_type} · {item.status}</div>
          <div>{item.resolution}</div>
        </div>
      ))}
    </div>
  );
}

function EvidencePanel() {
  const view = useCampus((s) => s.campus_view) || {};
  const [open, setOpen] = useState(false);
  const rows = view.evidence || [];
  if (!view.package_id) return null;
  return (
    <div className="evidence">
      <button className="ghost" data-testid="evidence-toggle" onClick={() => setOpen((value) => !value)}>Evidence</button>
      {open && (
        <div data-testid="evidence-list">
          {rows.length === 0 && <div className="muted">No claims on this package.</div>}
          {rows.map((row, index) => (
            <div key={index} data-testid="evidence-claim">
              <div>{row.claim}</div>
              <div data-testid="evidence-sentence">{row.sentence || "UNRESOLVED. No opened page is linked to this claim."}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function OutcomeBadge({ outcome }) {
  if (!outcome) return null;
  return (
    <div className="outcome" data-testid="outcome" data-state={outcome.state} data-simulated={outcome.simulated ? "1" : "0"}>
      <b data-testid="outcome-label">{outcome.label}</b>
      {outcome.simulated && <span className="sim" data-testid="simulated-badge">SIMULATED — not real work</span>}
      {outcome.reason && <div className="muted" data-testid="outcome-reason">{outcome.reason}</div>}
      {outcome.next_action && <div className="muted">{outcome.next_action}</div>}
    </div>
  );
}

function resultHeading(view) {
  const state = (view.outcome || {}).state;
  if (view.rejected || state === "REJECTED") return "Rejected — not a deliverable";
  if (view.unresolved) return "Unresolved — not a deliverable";
  if (state === "FAILED") return "Failed — not a deliverable";
  if (state === "BLOCKED") return "Blocked — not accepted";
  if (state === "NEEDS_APPROVAL" || view.needs_you) return "Draft for review — not accepted yet";
  if (state === "COMPLETED") return "Final result";
  return "Result so far — not finished";
}

function Downloads({ view }) {
  const outcome = view.outcome || {};
  const base = "/work-packages/" + view.package_id;
  const names = (view.files || []).map((f) => f.name);
  if (outcome.diagnostic_only) {
    return (
      <div data-testid="downloads">
        <a data-testid="download-diagnostic" href={base + "/bundle.zip"}>Download the diagnostic report (not a deliverable)</a>
      </div>
    );
  }
  if (!outcome.accepted_deliverable && !outcome.draft_for_review) {
    return <div className="muted" data-testid="downloads">Nothing to download until the job finishes.</div>;
  }
  const draft = outcome.draft_for_review;
  return (
    <div data-testid="downloads">
      <a data-testid="download-deliverable" href={base + "/deliverable.md"}>{draft ? "Download the draft for review" : "Download the deliverable"}</a>
      {names.includes("main.py") && <>{" "}<a data-testid="download-main" href={base + "/files/main.py"}>main.py</a></>}
      {names.includes("test_main.py") && <>{" "}<a data-testid="download-test" href={base + "/files/test_main.py"}>test_main.py</a></>}
      {names.length > 0 && <>{" "}<a data-testid="download-bundle" href={base + "/bundle.zip"}>{draft ? "All draft files (.zip)" : "All files (.zip)"}</a></>}
    </div>
  );
}

function CriteriaList({ rows }) {
  if (!rows || !rows.length) return null;
  return (
    <div data-testid="criteria">
      <b>Acceptance checks</b>
      {rows.map((row, index) => (
        <div key={index} data-testid="criterion" data-status={row.status}>
          {row.status === "met" ? "Met" : row.status === "not_met" ? "Not met" : "Needs a person"} — {row.criterion}
          {row.evidence && row.evidence.missing && row.evidence.missing.length > 0 && (
            <span className="muted"> (missing: {row.evidence.missing.join(", ")})</span>
          )}
        </div>
      ))}
    </div>
  );
}

function ResultPanel() {
  const view = useCampus((s) => s.campus_view) || {};
  const result = view.result;
  if (!result) return null;
  return (
    <div className="result" data-testid="final-result">
      <strong>{resultHeading(view)}</strong>
      <OutcomeBadge outcome={view.outcome} />
      <div data-testid="result-summary">{result.summary}</div>
      <div><b>{view.outcome && view.outcome.accepted_deliverable ? "Deliverable" : "Draft"}</b> {result.deliverable || "—"}</div>
      <div><b>Findings</b> {result.findings || "—"}</div>
      <CriteriaList rows={view.criteria_evidence} />
      {view.package_id && <Downloads view={view} />}
      <div><b>Gaps</b> {(result.gaps || []).join("; ") || "none recorded"}</div>
      <div><b>Confidence</b> {result.confidence === 0 || result.confidence ? String(result.confidence) : "unknown"} · <b>Evidence</b> {result.evidence_status || "—"}</div>
      <div><b>Manager</b> {result.manager || "—"} · <b>Time</b> {result.time || "unknown"} · <b>Cost</b> {result.cost || "unknown"}</div>
    </div>
  );
}

function packageLabel(pkg) {
  const names = {
    DRAFT: "New request",
    IN_PROGRESS: "In progress",
    UNDER_REVIEW: "Being reviewed",
    REPAIRING: "Being repaired",
    READY: "Ready for a decision",
    AWAITING_APPROVAL: "Needs your approval",
    AWAITING_CLARIFICATION: "Needs clarification",
    APPROVED: "Approved",
    ACTIONING: "Finishing",
    COMPLETED: "Complete",
    FAILED: "Rejected",
    UNRESOLVED: "Unresolved",
    ESCALATED: "Needs a decision",
  };
  const state = names[pkg.workflow_state] || "Work in progress";
  const title = String(pkg.title || pkg.objective || "").replace(/\s+/g, " ").trim().slice(0, 48);
  return title ? `${state} — ${title}` : state;
}

function WorkCrate({ pkg, departments, onSelect }) {
  const ref = useRef();
  const dest = packagePos(pkg, departments);
  useFrame(() => {
    if (!ref.current) return;
    ref.current.position.lerp(new THREE.Vector3(dest[0], dest[1], dest[2]), 0.08);
  });
  const hot = pkg.status === "needs_approval" || pkg.stage === "approval" || pkg.workflow_state === "AWAITING_APPROVAL" || pkg.workflow_state === "AWAITING_CLARIFICATION";
  return (
    <mesh ref={ref} position={dest} castShadow onClick={(e) => { e.stopPropagation(); onSelect(pkg.parent_id || pkg.id); }}>
      <boxGeometry args={[0.7, 0.45, 0.55]} />
      <meshStandardMaterial color={hot ? "#d4b45a" : "#c4a07a"} emissive={hot ? "#c4a056" : "#000"} emissiveIntensity={hot ? 0.35 : 0} />
    </mesh>
  );
}

function Scene() {
  const departments = useCampus((s) => s.departments);
  const agents = useCampus((s) => s.agents);
  const approvals = useCampus((s) => s.approvals);
  const focus = useCampus((s) => s.focus);
  const selectedId = useCampus((s) => s.selectedId);
  const work_packages = useCampus((s) => s.work_packages);
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
      {(work_packages || []).map((p) => <WorkCrate key={p.id} pkg={p} departments={departments} onSelect={(id) => useCampus.selectPackage(id)} />)}
      <CameraRig />
    </>
  );
}

function humanEvent(e) {
  const map = {
    "project.created": "Project opened", "task.created": "Task created", "package.created": "Work package created",
    "package.routed": "Distribution routed package", "package.waiting_approval": "Package in approval bay",
    "agent.using_tool": "Using a tool", "agent.researching": "Researching", "approval.requested": "Approval requested",
    "approval.resolved": "Approval resolved", "milo.synthesized": "Milo published findings",
    "agent.visual": "Agent state", "package.stage": "Stage", "package.needs_clarification": "Needs clarification",
    "package.completed": "Package completed",
  };
  return `${map[e.type] || e.type}${e.summary ? " — " + e.summary : ""}`;
}

class SceneBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    if (this.state.failed) {
      return <div className="scene-fallback">3D campus unavailable. The status panel stays live.</div>;
    }
    return this.props.children;
  }
}

function NeedsYou() {
  const view = useCampus((s) => s.campus_view) || {};
  const card = view.approval;
  const [reason, setReason] = useState("");
  const [override, setOverride] = useState(false);
  const [error, setError] = useState("");
  if (!view.needs_you || !card) return null;
  const unmet = (view.criteria_evidence || []).filter((row) => row.status === "not_met");
  const decide = (decision) => {
    const text = reason.trim();
    setError("");
    if (decision === "rejected" && !text) { setError("Add a reason so Ayven knows what to change."); return; }
    if (decision === "approved" && unmet.length && !override) {
      setError(`${unmet.length} acceptance check${unmet.length === 1 ? " is" : "s are"} not met. Reject it, or tick the box and say why you accept it anyway.`);
      return;
    }
    if (decision === "approved" && unmet.length && !text) { setError("Say why you accept a result with an unmet check."); return; }
    const body = decision === "rejected" ? { decision, reason: text }
      : unmet.length ? { decision, reason: text, accept_unmet_criteria: true } : { decision };
    fetch(`/approvals/${card.id}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(async (response) => {
      if (!response.ok) setError(await errorText(response));
      useCampus.hydrate();
    }).catch(() => setError("Campus could not be reached. Nothing was decided."));
  };
  return (
    <div className="needs-you" data-testid="needs-you">
      <strong>NEEDS YOU</strong>
      <OutcomeBadge outcome={view.outcome} />
      <p>{card.what}</p>
      <p><b>Why</b> {card.why}</p>
      <p><b>If approved</b> {card.if_approved}</p>
      <p><b>If rejected</b> {card.if_rejected}</p>
      {(card.evidence || []).map((item, index) => <p key={index} className="muted">{item.source} {item.note}</p>)}
      <CriteriaList rows={view.criteria_evidence} />
      {view.package_id && <a data-testid="download-draft" href={"/work-packages/" + view.package_id + "/deliverable.md"}>Read the draft</a>}
      <textarea data-testid="reject-reason" value={reason} placeholder={unmet.length ? "Reason (needed to reject, or to accept with an unmet check)" : "Reason for a rejection"} onChange={(e) => setReason(e.target.value)} />
      {unmet.length > 0 && (
        <label data-testid="accept-unmet">
          <input type="checkbox" checked={override} onChange={(e) => setOverride(e.target.checked)} />
          {" "}Accept although {unmet.length} check{unmet.length === 1 ? " is" : "s are"} not met
        </label>
      )}
      {error && <div className="error" data-testid="decision-error" role="alert">{error}</div>}
      <button data-testid="approve" onClick={() => decide("approved")}>Approve</button>
      <button className="ghost" data-testid="reject" onClick={() => decide("rejected")}>Reject</button>
    </div>
  );
}

async function errorText(response) {
  try {
    const body = await response.json();
    const detail = body && body.detail;
    if (typeof detail === "string") return detail;
    if (detail && detail.message) return detail.message;
    if (Array.isArray(detail) && detail.length) return detail.map((item) => item.msg || String(item)).join("; ");
  } catch (_) {}
  return `Campus refused this (HTTP ${response.status}). Nothing was changed.`;
}

function NeedsClarification() {
  const view = useCampus((s) => s.campus_view) || {};
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState("");
  if (!view.needs_clarification || !view.package_id) return null;
  const submit = () => {
    setError("");
    if (!answer.trim()) { setError("Type an answer first."); return; }
    fetch(`/work-packages/${view.package_id}/clarification`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answer }),
    }).then(async (response) => {
      if (!response.ok) { setError(await errorText(response)); return; }
      useCampus.selectPackage(view.package_id);
    }).catch(() => setError("Campus could not be reached. Your answer was not sent."));
  };
  return (
    <div className="needs-clarify" data-testid="needs-clarification">
      <strong>NEEDS CLARIFICATION</strong>
      <p data-testid="clarify-question">{view.question}</p>
      <textarea data-testid="clarify-input" value={answer} onChange={(e) => setAnswer(e.target.value)} />
      {error && <div className="error" data-testid="clarify-error" role="alert">{error}</div>}
      <button data-testid="clarify-submit" onClick={submit}>Answer</button>
    </div>
  );
}

const DRAFT_KEY = "ayven-campus-brief-draft";

function loadDraft() {
  try { return JSON.parse(window.localStorage.getItem(DRAFT_KEY) || "null"); } catch (_) { return null; }
}

function saveDraft(draft) {
  try {
    if (draft) window.localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
    else window.localStorage.removeItem(DRAFT_KEY);
  } catch (_) {}
}

// The brief Lee submits: objective, acceptance criteria and a budget in the currency he chose.
// The budget is sent as entered. Campus keeps it or refuses it with a reason; it never shrinks it.
function BriefForm({ initial, onChange }) {
  const saved = loadDraft() || {};
  const [objective, setObjective] = useState(saved.objective || initial || "");
  const [criteria, setCriteria] = useState(saved.criteria || "");
  const [amount, setAmount] = useState(saved.amount || "");
  const [currency, setCurrency] = useState(saved.currency || "GBP");
  const [status, setStatus] = useState({ kind: "", text: "" });
  useEffect(() => { saveDraft({ objective, criteria, amount, currency }); }, [objective, criteria, amount, currency]);
  const submit = () => {
    setStatus({ kind: "", text: "" });
    if (!objective.trim()) { setStatus({ kind: "error", text: "Write the objective first." }); return; }
    const body = {
      objective: objective.trim(),
      acceptance_criteria: criteria.split("\n").map((line) => line.trim()).filter(Boolean),
    };
    if (amount.trim()) {
      const value = Number(amount);
      if (!Number.isFinite(value) || value <= 0) { setStatus({ kind: "error", text: "The budget must be a number above 0." }); return; }
      if (currency === "USD") body.budget_usd = value; else body.job_cap_gbp = value;
    }
    fetch("/projects", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(async (response) => {
        if (!response.ok) { setStatus({ kind: "error", text: await errorText(response) }); return; }
        const job = await response.json();
        setStatus({ kind: "ok", text: job.duplicate ? "That brief is already running." : `Submitted (${job.mode || "mode unknown"}).` });
        setObjective(""); setCriteria(""); setAmount(""); onChange && onChange("");
        saveDraft(null);
        useCampus.hydrate();
      })
      .catch(() => setStatus({ kind: "error", text: "Campus could not be reached. The brief was not sent." }));
  };
  return (
    <div data-testid="brief-form">
      <textarea data-testid="objective" value={objective} placeholder="What should Ayven do?" onChange={(e) => { setObjective(e.target.value); onChange && onChange(e.target.value); }} />
      <textarea data-testid="criteria-input" value={criteria} placeholder="Acceptance checks, one per line (optional)" onChange={(e) => setCriteria(e.target.value)} />
      <div className="row">
        <input data-testid="budget-amount" inputMode="decimal" value={amount} placeholder="Budget (optional)" onChange={(e) => setAmount(e.target.value)} />
        <select data-testid="budget-currency" value={currency} onChange={(e) => setCurrency(e.target.value)}>
          <option value="GBP">£ GBP</option>
          <option value="USD">$ USD</option>
        </select>
      </div>
      {status.text && <div className={status.kind === "error" ? "error" : "muted"} data-testid={status.kind === "error" ? "brief-error" : "brief-status"} role={status.kind === "error" ? "alert" : undefined}>{status.text}</div>}
      <div className="row">
        <button data-testid="ask-milo" onClick={submit}>Ask Milo</button>
        <button className="ghost" onClick={() => useCampus.focusCampus()}>Campus view</button>
      </div>
    </div>
  );
}

function App() {
  const agents = useCampus((s) => s.agents);
  const events = useCampus((s) => s.events);
  const approvals = useCampus((s) => s.approvals);
  const work_packages = useCampus((s) => s.work_packages);
  const selectedId = useCampus((s) => s.selectedId);
  const selectedPackageId = useCampus((s) => s.selectedPackageId);
  const view = useCampus((s) => s.campus_view) || {};
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
  const parents = (work_packages || []).filter((pkg) => !pkg.parent_id);
  const visual = selected ? visualOf(selected) : "";
  return (
    <>
      <SceneBoundary>
        <Canvas shadows camera={{ position: [38, 30, 38], fov: 42 }}>
          <Scene />
        </Canvas>
      </SceneBoundary>
      <div className="overlay" data-testid="campus-root">
        <NeedsYou />
        <NeedsClarification />
        <div className="panel top">
          <div className="brand">AYVEN CAMPUS</div>
          <div className="muted">Headquarters · live company state</div>
          <BriefForm initial={text} onChange={setText} />
          <CampusNow />
        </div>
        <div className="panel right">
          <h1>Work packages</h1>
          {parents.map((pkg) => (
            <button key={pkg.id} className="pkg-btn" data-testid="work-package" data-package={pkg.id} data-hot={pkg.id === (selectedPackageId || view.package_id) ? "1" : "0"} onClick={() => useCampus.selectPackage(pkg.id)}>
              {packageLabel(pkg)}
            </button>
          ))}
          <WorkflowStrip />
          <RepairPanel />
          <EvidencePanel />
          <ResultPanel />
          <h1>Departments</h1>
          {CATALOG.map((d) => <div key={d.id} className="dept-line" onClick={() => useCampus.focusBuilding(d.id)}><span className="swatch" style={{ background: d.theme }} />{d.shortName}</div>)}
          <div data-testid="agent-board">
            {agents.map((agent) => (
              <div key={agent.id} data-testid="agent-state" data-agent={agent.id} data-visual={visualOf(agent)} onClick={() => useCampus.focusAgent(agent.id)}>
                {agent.name}: {visualOf(agent)}
              </div>
            ))}
          </div>
          {selected && (
            <div className="inspector">
              <h1>{selected.name}</h1>
              <div className="muted">{selected.role}</div>
              <p data-testid="selected-visual"><b>State</b> {visual}</p>
              <p className="muted">{selected.last_summary}</p>
            </div>
          )}
          <div className="muted">Approvals waiting: {pending.length}</div>
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
