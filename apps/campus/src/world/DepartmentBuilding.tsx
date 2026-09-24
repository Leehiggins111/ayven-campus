import { Html } from "@react-three/drei";
import { catalogById } from "../config/departments";

function Mass({ color, args, position }: any) {
  return (
    <mesh position={position} castShadow receiveShadow>
      <boxGeometry args={args} />
      <meshStandardMaterial color={color} roughness={0.45} metalness={0.12} />
    </mesh>
  );
}

export default function DepartmentBuilding({ department, selected, attention, onClick }: any) {
  const cfg = catalogById[department.id];
  const color = cfg?.theme || "#888";
  const type = cfg?.buildingType || "research";
  const label = cfg?.shortName || department.name;
  return (
    <group position={[department.x, 0, department.z]} onClick={(e) => { e.stopPropagation(); onClick(); }}>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.02, 0]} receiveShadow>
        <circleGeometry args={[7.4, 24]} />
        <meshStandardMaterial color={attention ? "#3a2a12" : selected ? "#2a3d30" : "#1a2c22"} />
      </mesh>
      {type === "command" && (<><Mass color={color} args={[8.2, 5.2, 6.2]} position={[0, 2.6, 0]} /><Mass color="#e8d9a0" args={[3.2, 2.4, 3.2]} position={[0, 6.4, 0]} /></>)}
      {type === "research" && (<><Mass color={color} args={[9.2, 2.4, 4.4]} position={[0, 1.2, 0]} /><Mass color="#2b4a58" args={[4.2, 1.6, 4.2]} position={[2.2, 3.2, 0]} /></>)}
      {type === "travel" && (<><Mass color={color} args={[7.4, 2.6, 5]} position={[0, 1.3, 0]} /><mesh position={[0, 3.1, 0]} rotation={[0, 0, 0.15]}><boxGeometry args={[8.4, 0.18, 5.6]} /><meshStandardMaterial color="#d7efe4" /></mesh></>)}
      {type === "outreach" && (<><Mass color={color} args={[6.4, 3.1, 5.2]} position={[0, 1.55, 0]} /><Mass color="#7a4a2c" args={[2.2, 4.4, 2.2]} position={[-2.4, 2.2, 1.4]} /></>)}
      {type === "labs" && (<><Mass color={color} args={[5.2, 2.2, 5.2]} position={[0, 1.1, 0]} /><Mass color="#7a4aa0" args={[3.4, 2.8, 3.4]} position={[1.1, 3.5, 0.4]} /></>)}
      {type === "trades" && (<><Mass color={color} args={[8.4, 2.8, 5.6]} position={[0, 1.4, 0]} /><Mass color="#5a6a3a" args={[8.8, 0.2, 6]} position={[0, 2.9, 0]} /></>)}
      {type === "procurement" && (<><Mass color={color} args={[7.2, 3.4, 4.8]} position={[0, 1.7, 0]} /><Mass color="#3a4a78" args={[2.4, 1.2, 3.2]} position={[3.6, 0.7, 0]} /></>)}
      {type === "grassroots" && (<><mesh position={[0, 0.08, 0]} rotation={[-Math.PI / 2, 0, 0]}><circleGeometry args={[3.2, 24]} /><meshStandardMaterial color="#2f6a3a" /></mesh><Mass color={color} args={[5.6, 2.0, 4.4]} position={[0, 1.0, 0]} /></>)}
      <Html position={[0, type === "command" ? 8.2 : 5.2, 0]} center distanceFactor={28} style={{ pointerEvents: "none" }}>
        <div className="bld-label" data-hot={attention ? "1" : "0"}>{label}</div>
      </Html>
    </group>
  );
}
