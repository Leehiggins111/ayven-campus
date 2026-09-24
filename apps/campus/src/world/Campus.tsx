import { catalogById } from "../config/departments";
import { useCampus } from "../state/store";
import DepartmentBuilding from "./DepartmentBuilding";
import AgentAvatar, { agentWorldPos } from "./AgentAvatar";
import CameraRig from "./CameraRig";

function Path({ from, to }: { from: [number, number]; to: [number, number] }) {
  const mx = (from[0] + to[0]) / 2;
  const mz = (from[1] + to[1]) / 2;
  const dx = to[0] - from[0];
  const dz = to[1] - from[1];
  const len = Math.hypot(dx, dz);
  const rot = Math.atan2(dx, dz);
  return (
    <mesh position={[mx, 0.03, mz]} rotation={[-Math.PI / 2, 0, -rot]} receiveShadow>
      <planeGeometry args={[1.35, len]} />
      <meshStandardMaterial color="#2a3d32" />
    </mesh>
  );
}

export default function Campus() {
  const departments = useCampus((s) => s.departments);
  const agents = useCampus((s) => s.agents);
  const approvals = useCampus((s) => s.approvals);
  const focus = useCampus((s) => s.focus);
  const selectedId = useCampus((s) => s.selectedId);
  const focusBuilding = useCampus((s) => s.focusBuilding);
  const focusAgent = useCampus((s) => s.focusAgent);
  const pendingDepts = new Set(
    approvals.filter((a) => a.status === "pending").map((a) => agents.find((x) => x.id === a.agent_id)?.department_id)
  );
  agents.filter((a) => a.status === "needs_approval" || a.status === "error").forEach((a) => pendingDepts.add(a.department_id));
  const cmd = departments.find((d) => d.id === "command") || { x: 0, z: 0 };
  return (
    <>
      <color attach="background" args={["#0b1611"]} />
      <fog attach="fog" args={["#0b1611", 55, 120]} />
      <hemisphereLight args={["#c5ddd0", "#121c16", 0.75]} />
      <directionalLight position={[28, 36, 16]} intensity={1.15} castShadow />
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <circleGeometry args={[58, 48]} />
        <meshStandardMaterial color="#143222" />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.01, 0]}>
        <ringGeometry args={[16, 18.2, 48]} />
        <meshStandardMaterial color="#1c3a2a" />
      </mesh>
      {departments.filter((d) => d.id !== "command").map((d) => (
        <Path key={d.id} from={[cmd.x, cmd.z]} to={[d.x, d.z]} />
      ))}
      {departments.map((d) => (
        <DepartmentBuilding key={d.id} department={d} selected={focus.kind === "building" && focus.id === d.id} attention={pendingDepts.has(d.id)} onClick={() => focusBuilding(d.id)} />
      ))}
      {departments.map((d) => {
        const members = agents.filter((a) => a.department_id === d.id);
        return members.map((a, i) => (
          <AgentAvatar key={a.id} agent={a} position={agentWorldPos(d, i, a.id === "milo")} selected={selectedId === a.id} onClick={() => focusAgent(a.id)} />
        ));
      })}
      <CameraRig />
    </>
  );
}
