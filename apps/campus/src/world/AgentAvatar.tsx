import { useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { Html } from "@react-three/drei";

const STATUS_COLOR: Record<string, string> = {
  idle: "#9bb7a8",
  working: "#3db4ff",
  researching: "#3dffb0",
  needs_approval: "#ffb03d",
  error: "#ff5a3d",
  waiting: "#c9c14a",
  complete: "#7ad1a0",
};

export function agentWorldPos(dept: { x: number; z: number }, indexInDept: number, isMilo: boolean) {
  if (isMilo) return [dept.x + 0.2, 0, dept.z + 4.2] as [number, number, number];
  const col = indexInDept % 3;
  const row = Math.floor(indexInDept / 3);
  return [dept.x - 2.2 + col * 1.7, 0, dept.z + 3.6 + row * 1.5] as [number, number, number];
}

export default function AgentAvatar({ agent, position, selected, onClick }: any) {
  const ref = useRef<any>();
  const color = STATUS_COLOR[agent.status] || "#889";
  const busy = ["working", "researching"].includes(agent.status);
  const hot = agent.status === "needs_approval" || agent.status === "error";
  useFrame(({ clock }) => {
    if (!ref.current) return;
    ref.current.position.y = busy ? 0.02 + Math.sin(clock.elapsedTime * 6) * 0.05 : 0.02;
    if (agent.status === "needs_approval") ref.current.rotation.y = clock.elapsedTime * 1.4;
  });
  return (
    <group position={position} onClick={(e) => { e.stopPropagation(); onClick(); }}>
      <mesh position={[0, 0.18, 0.55]} receiveShadow>
        <boxGeometry args={[1.1, 0.36, 0.7]} />
        <meshStandardMaterial color="#24352c" />
      </mesh>
      <mesh position={[0, 0.52, 0.28]}>
        <boxGeometry args={[0.7, 0.42, 0.08]} />
        <meshStandardMaterial color={busy ? "#7ee0ff" : "#1b2a24"} emissive={busy ? "#3aa0c8" : "#000"} emissiveIntensity={busy ? 0.6 : 0} />
      </mesh>
      <group ref={ref}>
        <mesh position={[0, 0.55, 0]} castShadow>
          <capsuleGeometry args={[agent.id === "milo" ? 0.28 : 0.22, agent.id === "milo" ? 0.72 : 0.55, 4, 8]} />
          <meshStandardMaterial color={selected ? "#ffffff" : color} emissive={color} emissiveIntensity={hot ? 0.45 : 0.18} />
        </mesh>
        {agent.id === "milo" && (
          <mesh position={[0, 1.22, 0]}>
            <sphereGeometry args={[0.12, 10, 10]} />
            <meshStandardMaterial color="#d4b45a" emissive="#d4b45a" emissiveIntensity={0.5} />
          </mesh>
        )}
      </group>
      {selected && (
        <Html position={[0, 1.7, 0]} center distanceFactor={22} style={{ pointerEvents: "none" }}>
          <div className="agent-chip">{agent.name}</div>
        </Html>
      )}
    </group>
  );
}
