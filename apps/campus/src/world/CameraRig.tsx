import { useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import { useCampus } from "../state/store";
import { agentWorldPos } from "./AgentAvatar";

export default function CameraRig() {
  const controls = useRef<any>();
  const { camera } = useThree();
  const focus = useCampus((s) => s.focus);
  const departments = useCampus((s) => s.departments);
  const agents = useCampus((s) => s.agents);
  const target = useRef(new THREE.Vector3(0, 0, 0));
  const pos = useRef(new THREE.Vector3(36, 28, 36));
  useFrame(() => {
    let look = new THREE.Vector3(0, 0.5, 0);
    let dest = new THREE.Vector3(36, 28, 36);
    if (focus.kind === "building") {
      const d = departments.find((x) => x.id === focus.id);
      if (d) {
        look.set(d.x, 1.2, d.z);
        dest.set(d.x + 14, 11, d.z + 14);
      }
    } else if (focus.kind === "agent") {
      const a = agents.find((x) => x.id === focus.id);
      const d = departments.find((x) => x.id === a?.department_id);
      if (a && d) {
        const peers = agents.filter((x) => x.department_id === a.department_id);
        const idx = Math.max(0, peers.findIndex((x) => x.id === a.id));
        const p = agentWorldPos(d, idx, a.id === "milo");
        look.set(p[0], 0.8, p[2]);
        dest.set(p[0] + 6, 5.5, p[2] + 6);
      }
    }
    pos.current.lerp(dest, 0.06);
    target.current.lerp(look, 0.08);
    camera.position.copy(pos.current);
    if (controls.current) {
      controls.current.target.copy(target.current);
      controls.current.update();
    }
  });
  return <OrbitControls ref={controls} makeDefault maxPolarAngle={Math.PI / 2.2} minDistance={8} maxDistance={70} />;
}
