"""Optional MuJoCo/Mink refinement backend for :mod:`retargeting`.

This module is imported lazily.  A normal QPlay install therefore has no
MuJoCo dependency; install ``requirements-retarget.txt`` and restart the host
to enable it.  Mink tracks the geometric solver's metric joint targets while
MuJoCo supplies the articulated human model and joint limits.
"""

from __future__ import annotations

import math
import os

import mujoco
import mink
import numpy as np

from retargeting import (
    BodyProfile,
    GeometricRetargeter,
    L_ANK, L_ELB, L_HIP, L_KNE, L_SHO, L_WRI, NOSE,
    R_ANK, R_ELB, R_HIP, R_KNE, R_SHO, R_WRI,
)


def _mjcf(profile: BodyProfile) -> str:
    d = profile.dimensions
    pelvis_height = d["thigh"] + d["shin"]
    return f"""
<mujoco model="qplay_human">
  <compiler angle="degree" autolimits="true" boundmass="0.001"
            boundinertia="0.000001" balanceinertia="true"/>
  <option gravity="0 0 0" timestep="0.01"/>
  <default>
    <joint damping="1.5" armature="0.015"/>
    <geom type="capsule" size="0.045" density="450" contype="0" conaffinity="0"/>
    <site size="0.018" rgba="1 0.6 0 1"/>
  </default>
  <worldbody>
    <body name="pelvis" pos="0 0 {pelvis_height:.6f}">
      <freejoint/>
      <geom name="pelvis_geom" type="box" size="{d['hips'] * .5:.6f} .07 .07"/>
      <site name="pelvis_site"/>
      <body name="torso" pos="0 0 .02">
        <joint name="waist_roll" axis="1 0 0" range="-28 28"/>
        <joint name="waist_pitch" axis="0 1 0" range="-35 35"/>
        <joint name="waist_yaw" axis="0 0 1" range="-50 50"/>
        <geom fromto="0 0 0 0 0 {d['torso']:.6f}" size=".095"/>
        <site name="left_shoulder" pos="{-d['shoulders'] * .5:.6f} 0 {d['torso']:.6f}"/>
        <site name="right_shoulder" pos="{d['shoulders'] * .5:.6f} 0 {d['torso']:.6f}"/>
        <site name="nose" pos="0 0 {d['torso'] + profile.height_m * .20:.6f}"/>
        {_arm_xml('left', -d['shoulders'] * .5, d['torso'], d['upper_arm'], d['forearm'])}
        {_arm_xml('right', d['shoulders'] * .5, d['torso'], d['upper_arm'], d['forearm'])}
      </body>
      <site name="left_hip" pos="{-d['hips'] * .5:.6f} 0 0"/>
      <site name="right_hip" pos="{d['hips'] * .5:.6f} 0 0"/>
      {_leg_xml('left', -d['hips'] * .5, d['thigh'], d['shin'])}
      {_leg_xml('right', d['hips'] * .5, d['thigh'], d['shin'])}
    </body>
  </worldbody>
</mujoco>
"""


def _arm_xml(side: str, x: float, z: float, upper: float, fore: float) -> str:
    sign = -1 if side == "left" else 1
    return f"""
<body name="{side}_shoulder_yaw" pos="{x:.6f} 0 {z:.6f}">
  <joint name="{side}_shoulder_yaw_j" axis="0 0 1" range="-120 120"/>
  <body name="{side}_shoulder_pitch">
    <joint name="{side}_shoulder_pitch_j" axis="0 1 0" range="-150 150"/>
    <body name="{side}_upper_arm">
      <joint name="{side}_shoulder_roll_j" axis="1 0 0" range="-120 120"/>
      <geom fromto="0 0 0 0 0 {-upper:.6f}"/>
      <site name="{side}_elbow" pos="0 0 {-upper:.6f}"/>
      <body name="{side}_forearm" pos="0 0 {-upper:.6f}">
        <joint name="{side}_elbow_j" axis="1 0 0" range="0 155"/>
        <geom fromto="0 0 0 0 0 {-fore:.6f}" size=".038"/>
        <site name="{side}_wrist" pos="0 0 {-fore:.6f}"/>
      </body>
    </body>
  </body>
</body>"""


def _leg_xml(side: str, x: float, thigh: float, shin: float) -> str:
    return f"""
<body name="{side}_hip_yaw" pos="{x:.6f} 0 0">
  <joint name="{side}_hip_yaw_j" axis="0 0 1" range="-70 70"/>
  <body name="{side}_hip_roll">
    <joint name="{side}_hip_roll_j" axis="1 0 0" range="-55 55"/>
    <body name="{side}_thigh">
      <joint name="{side}_hip_pitch_j" axis="0 1 0" range="-125 55"/>
      <geom fromto="0 0 0 0 0 {-thigh:.6f}" size=".055"/>
      <site name="{side}_knee" pos="0 0 {-thigh:.6f}"/>
      <body name="{side}_shin" pos="0 0 {-thigh:.6f}">
        <joint name="{side}_knee_j" axis="0 1 0" range="0 155"/>
        <geom fromto="0 0 0 0 0 {-shin:.6f}" size=".045"/>
        <site name="{side}_ankle" pos="0 0 {-shin:.6f}"/>
      </body>
    </body>
  </body>
</body>"""


def _to_mujoco(point) -> np.ndarray:
    # QPlay canonical: x lateral, y up, z camera-depth.  MuJoCo: z up.
    return np.asarray((point[0], point[2], point[1]), dtype=np.float64)


def _from_mujoco(point) -> list[float]:
    return [float(point[0]), float(point[2]), float(point[1])]


class MinkRetargeter:
    backend = "mujoco-mink-cpu"

    def __init__(self, profile: BodyProfile, geometric: GeometricRetargeter):
        self.geometric = geometric
        self.profile = profile
        self._build()

    def _build(self) -> None:
        self.model = mujoco.MjModel.from_xml_string(_mjcf(self.profile))
        self.configuration = mink.Configuration(self.model)
        self.posture = mink.PostureTask(self.model, cost=2e-3)
        self.posture.set_target_from_configuration(self.configuration)
        self.task_specs = {
            "pelvis_site": (None, 8.0),
            "left_elbow": (L_ELB, 2.0), "right_elbow": (R_ELB, 2.0),
            "left_wrist": (L_WRI, 7.0), "right_wrist": (R_WRI, 7.0),
            "left_knee": (L_KNE, 3.0), "right_knee": (R_KNE, 3.0),
            "left_ankle": (L_ANK, 10.0), "right_ankle": (R_ANK, 10.0),
        }
        self.tasks = {}
        for name, (_, cost) in self.task_specs.items():
            self.tasks[name] = mink.FrameTask(
                frame_name=name, frame_type="site", position_cost=cost,
                orientation_cost=0.0, lm_damping=1.0,
            )
        self.limits = [mink.ConfigurationLimit(self.model)]
        self.solver = self._select_solver()

    @staticmethod
    def _select_solver() -> str:
        requested = os.environ.get("GF_MINK_SOLVER", "").strip()
        try:
            from qpsolvers import available_solvers
            available = set(available_solvers)
        except Exception:
            available = set()
        if requested:
            if available and requested not in available:
                raise RuntimeError(f"Mink QP solver '{requested}' is not installed")
            return requested
        for candidate in ("daqp", "quadprog", "osqp", "clarabel"):
            if candidate in available:
                return candidate
        raise RuntimeError("Mink is installed but no supported QP solver is available")

    def set_profile(self, profile: BodyProfile) -> None:
        self.profile = profile
        self.geometric.set_profile(profile)
        self._build()

    def solve(self, pose: dict) -> dict:
        result = self.geometric.solve(pose)
        points = result["p"]
        pelvis = [
            (points[L_HIP][i] + points[R_HIP][i]) * 0.5 for i in range(3)
        ]
        for name, (index, _) in self.task_specs.items():
            target = pelvis if index is None else points[index]
            self.tasks[name].set_target(mink.SE3.from_translation(_to_mujoco(target)))
        all_tasks = [*self.tasks.values(), self.posture]
        dt = 0.02
        for _ in range(4):
            velocity = mink.solve_ik(
                self.configuration, all_tasks, dt, self.solver,
                damping=1e-4, limits=self.limits,
            )
            if not np.all(np.isfinite(velocity)):
                raise RuntimeError("Mink returned a non-finite velocity")
            self.configuration.integrate_inplace(velocity, dt)
        mujoco.mj_forward(self.model, self.configuration.data)
        mapping = {
            NOSE: "nose",
            L_SHO: "left_shoulder", R_SHO: "right_shoulder",
            L_ELB: "left_elbow", R_ELB: "right_elbow",
            L_WRI: "left_wrist", R_WRI: "right_wrist",
            L_HIP: "left_hip", R_HIP: "right_hip",
            L_KNE: "left_knee", R_KNE: "right_knee",
            L_ANK: "left_ankle", R_ANK: "right_ankle",
        }
        for index, site_name in mapping.items():
            points[index] = _from_mujoco(self.configuration.data.site(site_name).xpos)
        result["p"] = [[round(value, 5) for value in point] for point in points]
        result["backend"] = self.backend
        result["diagnostics"].update({"solver": self.solver, "iterations": 4})
        return result
