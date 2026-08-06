# System Plan: Full-Silicon Game

**Status:** Design plan. Sections marked **[verified]** come from answered questions or shipped code;
everything else is a hypothesis to check against the repo.
**Companion to:** [`docs/server-architecture.md`](server-architecture.md), which is **validated
against the code** and takes precedence on any conflict about transport, sessions, or wire format.
References below in the form A§N point into that document; U§N points into this one.

---

## 0. For the coding agent: how to use this document

This plan describes *placement* — which processor does which work, and why. It does not re-specify
the protocol; A§5–A§9 already do that and are validated.

**One apparent contradiction to resolve up front.** A§2.3 concludes "there is nothing to offload"
and A§12 cuts offload rungs 3 and 4. That conclusion stands and this plan does not reverse it.
The distinction:

- **Offload** = shedding a laptop workload elsewhere at runtime under load. Still cut. There is no
  utilization signal to trigger on and no local ML to shed.
- **Placement** = deciding at design time where a workload lives. That is what this document does.

Moving the commentary LLM *onto* the laptop NPU is placement, not offload. It has no runtime policy,
no hysteresis, and no manager. A§12 rung 3 was cut because peer offload ran the wrong direction —
this plan runs it the right direction, from the saturated phone to the idle laptop, once, at design
time.

**Suggested first tasks:**

1. Answer U§10 questions 1–3 from the code and from the device.
2. Implement U§5.2 Change A (`hero()` as a fragment shader). It resolves two A§13 defects and lights
   the first dark unit.
3. Mark any section of this plan that the code contradicts as **[corrected]**, the way
   `server-architecture.md` does. A plan that survives contact unchanged means nobody checked it.

---

## 1. Goal

A profiler running during a live match shows **no zeros** across nine compute units.

### The honesty rule

**Every workload must be the natural home for that work** — defensible on engineering grounds even
if nobody were profiling. A busy-loop that spins a GPU produces a non-zero reading and teaches
nothing. If a placement below cannot be justified without reference to this goal, it is wrong and
should be flagged rather than implemented.

Every assignment in U§4 meets that bar. Several of them (the pose-pipeline split, the two-tier
classifier, the shader port) are things you would do anyway for performance reasons.

---

## 2. Hardware inventory **[verified]**

| Device | SoC | Compute units | RAM |
|---|---|---|---|
| Laptop | Snapdragon X Elite | Oryon CPU, Adreno GPU, Hexagon NPU (45 TOPS) | 32 GB |
| Phone (Galaxy S25) | Snapdragon 8 Elite | Oryon CPU, Adreno GPU, Hexagon NPU | 12 GB |
| Arduino UNO Q | Dragonwing QRB2210 | Quad Cortex-A CPU, Adreno 702 GPU, STM32 MCU | 4 GB |

**The UNO Q has no NPU.** Plan no tensor acceleration for it beyond what the Adreno 702 carries.
It does have an ISP and a hardware video encode/decode block — use both.

All three are Qualcomm. One toolchain family, one quantization path, three targets.

---

## 3. Resolved questions **[verified]**

| Question | Answer | Unblocks |
|---|---|---|
| Where does the TV browser run? | **On the laptop.** | U§5.2 entirely — WebGPU reaches the X Elite Adreno via D3D12 |
| Does the UNO Q have a camera? | **Yes.** Currently fed over an MQTT broker; **the camera will be attached directly to the UNO Q.** | U§5.8 |

**Scope note on the camera.** The MQTT arrangement is temporary and out of scope. Design and build
against a locally attached camera read over V4L2. Do not build anything that depends on the broker,
and do not let MQTT into the game protocol.

---

## 4. Allocation ledger

| # | Unit | Today | Assigned workload | Justification independent of the goal |
|---|---|---|---|---|
| 1 | Laptop CPU | Loaded | asyncio server, match logic, session registry | Already correct |
| 2 | Laptop GPU | **Zero** | `hero()` plate as a fragment shader; WebGPU stadium render | Per-pixel splatting *is* a shader — it is currently a shader written in Python on the wrong processor |
| 3 | Laptop NPU | **Zero** | Commentary LLM; object-classifier confirm tier | The only NPU-scale workloads in the system |
| 4 | Phone CPU | Loaded | Android app, session, sensor fusion | Already correct |
| 5 | Phone GPU | **Zero** | BlazePose stage-1 detector; camera preprocessing; AR overlay | Takes detector load off an NPU already running Whisper — measurable thermal relief |
| 6 | Phone NPU | Loaded | BlazePose stage-2 landmark, Whisper Tiny | Already correct. **Remove Qwen3 0.6B** |
| 7 | UNO Q CPU | **Zero** | Session stack, CBOR control plane, MCU fusion, stability pre-gate | Only OS-capable processor on the device |
| 8 | UNO Q GPU | **Zero** | Camera preprocessing + classifier gate tier | Cheap always-on detection; correct tier split regardless |
| 9 | UNO Q MCU | **Zero** | IMU ≥200 Hz, dive detection, haptics, LEDs, discovery beacon | The only hard-real-time processor, doing the only hard-real-time job |

Six of nine are dark today.

---

## 5. Per-unit design

### 5.1 Laptop CPU — unchanged, with one correction

Already loaded. Two notes that affect other sections:

- **[corrected]** "With the browser on the laptop, the WebSocket is loopback, so network cost is
  negligible" is true **for the TV only**. The phone is on LAN and received the same full snapshot —
  including `replay` frames it never reads (verified: `phone.html` and the Android `GameClient`
  read 7–8 keys; neither touches `replay`/`shotmap`). Serialization cost is real too, but the LAN
  cost was not negligible. **Fixed by D1's per-device snapshot filtering** —
  shipped in `laptop/server/snapshot.py`; acceptance test asserts the phone's outbound snapshot
  carries no `replay`/`shotmap` (`laptop/test_devices.py`).
- A§13's **event-loop stall** in `neural_fx.hero()` is resolved by U§5.2 Change A rather than by the
  `ProcessPoolExecutor` A§12 suggests. Moving it to the GPU is strictly better than moving it to
  another core.

### 5.2 Laptop GPU — WebGPU in the browser

No native executable. That route was considered and rejected: `tv.html` is ~1,834 lines of working
Canvas2D, and porting it is orthogonal to every defect in A§13. A browser reaches Adreno through
WebGPU over D3D12.

**Change A — `hero()` becomes a fragment shader. Highest-leverage change in the plan; start here.**

Ship plate *parameters*; the GPU generates the pixels. **[corrected]** Resolves **two** things,
not three:

- A§13 event-loop stall — the synchronous per-pixel loop leaves the asyncio thread entirely
- Laptop GPU at zero

Broadcast amplification is **untouched** by this change: the hero plate never crosses the
WebSocket. `tv.html:268-299` POSTs to `/fx/hero` and receives the PNG in the HTTP response;
`snapshot()` has no `plate` key. Also worth recording — the current round trip deserves deletion:
the phone sends skeleton frames **up** over WS, the server fans them to the TV inside
`snapshot().replay`, and the TV then **POSTs those same frames back** to the same server to have a
plate drawn. A browser-side shader deletes that whole loop; the TV already holds the frames.

Risk: the splatting math may carry per-pixel state that does not map to a stateless fragment shader.
Check this first (U§10 Q3). If it does not port, the fallback is numpy vectorization — a Python
per-pixel loop is typically two to three orders of magnitude slower than the numpy equivalent, so
that alone removes the stall even though it leaves the GPU dark.

**Change B — WebGPU stadium render.** **[corrected]** The original target list (crowd instancing,
pitch lighting, ball trail, replay skeleton) was mis-ranked against the code:

- **Crowd and pitch lighting are already baked** to offscreen canvases in `rebuild()` — per-frame
  cost is a handful of `drawImage` blits. Instancing buys per-fan animation, not frame time.
- **The ball trail is not separable**: the ball draws at three z-slots interleaved with the goal
  frame, keeper, and striker for occlusion, and is only ~40 of ~4,000 calls.
- **The real wins are confetti (~2,400 of ~4,000 worst-case Canvas2D calls) and the net
  (~600 calls, re-tessellated every frame though its geometry is static outside the 1.4 s ripple).**

Re-scoped target: **confetti + post-FX + replay skeleton** as a GPU over-layer (a second stacked
canvas at z-index 1–3; `#pitch` keeps its 2d context). Larger job. Do it only after Change A has
proven the shader path and the Canvas2D/WebGPU interop story.

**Environment gotchas:**

- **Run ARM64 Chrome, not x64.** Check `chrome://version`. The GPU driver path survives emulation;
  your render loop's JavaScript does not.
- **GPU usage appears under Chrome's GPU process**, not under anything named after the game. Easy to
  misread as zero when profiling.

### 5.3 Laptop NPU — GenieX and the classifier

**Commentary LLM.** Qualcomm's GenieX runtime targets Snapdragon X on Windows ARM64 and exposes an
**OpenAI-compatible local server**. A§12 establishes that the commentary desk is already async, off
the tick path, deadline-bounded, and template-fallback-guarded. **This is a URL change, not a
rebuild.** It also closes A§2.4 — no remote endpoint is needed, so the unknown stops being a blocker.

Sizing: W4A16, 3B-class. Reference figures on phone-class 8 Elite silicon are ~10 tok/s for a 3B and
~5 tok/s for an 8B; the X Elite should meet or beat that with better sustained thermals. A 40-token
line at 10 tok/s is ~4 s — inside the existing 8 s deadline, and the game already has a 450 ms
post-kick gap before `skel` arrives. Commentary lands after the kick, which is where it belongs.

**[corrected — partially shipped in the four-pillar branch (`updated/SentinelMesh`).]** That branch
implements exactly this via `laptop/geniex_client.py` (OpenAI-compatible
`http://127.0.0.1:18181/v1/chat/completions`), model **Qwen3-4B-Instruct-2507 W4A16** — 4B, not the
3B-class sized here (`geniex_client.py:14-17`). The "URL change, not a rebuild" claim held: the Desk
grew a ~13-line GenieX branch (`updated .../laptop/server.py:101-113`). Two deltas from this section:
the desk timeout is 20 s there, and the **circuit breaker below was NOT implemented** — only a
print-once warning; every call still pays the full timeout while GenieX is down.

Still add the circuit breaker A§12 asks for: trip to templates after 3 consecutive failures, retry
after 60 s. Local inference fails differently from remote, not never.

**Build environment — this will bite on day one:**

| Purpose | Python |
|---|---|
| Quantization, AOT compilation | **x64** — ORT quantization utilities are x86_64-only; `qai_hub_models` fails to install on Windows ARM64 |
| Inference on the HTP | **ARM64** |

Set `session.disable_cpu_ep_fallback = "1"` during development so silent CPU fallback raises instead
of quietly halving your performance. Set the HTP performance profile to
`sustained_high_performance`, not `burst` — this is a ten-minute session, not a benchmark.

**Contention.** The commentary LLM and the classifier confirm tier share the NPU. **Classifier takes
priority** — it gates gameplay content. Commentary yields; it is latency-tolerant by construction.
Verify whether both can hold sessions concurrently or whether one must evict the other (U§10 Q5).

### 5.4 Phone CPU — unchanged

Android app, session management, sensor fusion. Already correct.

### 5.5 Phone GPU — split the pose pipeline

Today the GPU is only a *fallback rung* in the NPU→GPU→CPU ladder, so it reads near-zero whenever
the NPU is healthy.

**Make it a deliberate stage.** BlazePose is two-stage: a detector that locates the person, and a
landmark model that runs on the crop. Put **stage 1 on the Adreno via QNN's GPU backend** and keep
**stage 2 on the HTP**. The QNN EP exposes a GPU backend alongside HTP for exactly this.

This is not a contrivance. It takes detector load off an NPU that is also running Whisper — the
thermal contention that causes the ladder to degrade in the first place.

**Secondary GPU work:** camera preprocessing (resize, crop, colour convert) and the striker's
on-device AR overlay — skeleton, aim indicator, trajectory preview.

**Success metric is phone skin temperature, not GPU percentage.** When the phone throttles, BlazePose
falls down the ladder and the striker's aim degrades mid-match. That is the gameplay reason this
work matters. Measure before and after.

### 5.6 Phone NPU — remove one thing

BlazePose stage-2 and Whisper Tiny stay. **Pull Qwen3 0.6B off the phone entirely** once U§5.3
ships. That is thermal headroom returned to the pose pipeline.

Whisper stays on-device permanently — `README_GalaxyS25.md` §14 forbids transcripts leaving the
phone, and that boundary is not negotiable by this plan.

**Privacy boundary, stated as a routing rule:** camera frames and ASR transcripts never leave the
phone. Landmarks (`skel`) already cross the wire and may continue to. Anything downstream of
landmarks may run anywhere.

### 5.7 UNO Q CPU — session stack, fusion, and the pre-gate

Four jobs, none of them per-pixel:

1. **Session stack.** V4L2 capture loop, network, CBOR control plane per A§8, heartbeat, discovery
   response.
2. **MCU fusion.** The MCU emits raw threshold crossings; the CPU decides they constitute a dive.
   Smoothing, debouncing, calibration.
3. **Stability pre-gate.** See U§6.2 — the cheapest and most important thing on this list.
4. **Detector post-processing.** NMS, confidence thresholding, temporal persistence, dedup.

### 5.8 UNO Q GPU — the classifier gate

**Two-tier design:**

| Tier | Runs on | Job |
|---|---|---|
| Gate | UNO Q Adreno 702, TFLite GPU delegate | Lightweight detector — is there a distinct object? Where? |
| Confirm | Laptop Hexagon NPU | Full classification, then dynamic game generation |

Cheap always-on gate, expensive occasional confirm. Correct regardless of profiling: a 45 TOPS
classifier should not run on every camera frame.

Full pipeline design in U§6.

**API choice — verify on the device before building around it.** The UNO Q's Linux side is Debian,
not Android, and driver coverage differs. Two hour-one checks:

1. Does `clinfo` enumerate the Adreno? An empty result is a two-day detour you want to find
   immediately.
2. Does the TFLite GPU delegate initialize, and does it report *all* ops delegated? Log the
   delegated node count and assert on it — same failure mode as `disable_cpu_ep_fallback`.

TFLite + GPU delegate over OpenCL is the pragmatic path. QNN's GPU backend is the fallback, but QNN
on a part with no HTP is less well-trodden.

### 5.9 UNO Q MCU — the goalkeeper

The only hard-real-time processor in the system, given the only hard-real-time job.

- **Sample an IMU at ≥200 Hz**, threshold-detect the dive, emit a discrete `dive` event.
- Maps directly onto A§5's discrete-event channel: sender retransmits until ACK, server dedups by
  `event_id`. At-least-once plus dedup is effectively-once.
- **Emits the fixed-layout 24-byte discovery datagram** from A§6.4, which was designed specifically
  so either side of the UNO Q could speak it without a CBOR decoder or mDNS responder.
- Drives haptics and LEDs on the same loop — save indication, ready state, connection status.

Division of labour with the Cortex-A: **the MCU detects motion; the CPU decides it was a dive.**
Keep model logic off the MCU.

---

## 6. The UNO Q camera pipeline

The most detailed piece of new engineering in this plan. On a quad-A53-class part with 4 GB, the
design lives or dies on **who touches the pixels**.

```
ISP  (sensor → NV12 in dmabuf)
  ↓
CPU  stability pre-gate      — downsampled luma only, microseconds
  ↓  (only when the scene changed and settled)
GPU  one shader pass         — YUV→RGB, resize, normalize, straight into the input tensor
  ↓
GPU  detector inference      — TFLite GPU delegate
  ↓
CPU  post-process + crop     — NMS, dedup, async readback
  ↓  (only when the gate fires)
Laptop NPU  confirm          — full classification
```

### 6.1 Nobody copies frames

A single `memcpy` of a 1080p frame per iteration will eat more CPU than all the real work combined.
The pipeline must be zero-copy from sensor to shader.

V4L2 exports the capture buffer as a **dmabuf** file descriptor. Import it directly as an EGLImage
and sample it in the shader.

**Do not build the tutorial pipeline:**

```
V4L2 MMAP → memcpy → cv::Mat → cvtColor → resize → glTexImage2D
```

Every arrow is a full-frame CPU pass. That is four passes over a buffer the GPU could have read in
place.

### 6.2 The stability pre-gate

Before waking the GPU at all, decide whether the frame is worth looking at. Read a heavily
downsampled luma plane — 32×24 is 768 bytes — and compare against the previous one. Fire the GPU
only when the frame has **changed and then settled**, which is exactly the "someone is holding an
object up to the camera" signal.

Costs microseconds. Saves the GPU from running on every frame, which matters for power and thermals
on this part, and stops camera noise from waking a 45 TOPS classifier downstream.

If the pre-gate is firing on every frame, the threshold is wrong and you have built an always-on
detector by accident. That is a measurable failure, not a judgement call.

### 6.3 One shader pass does three jobs

Colour conversion, downscale, and normalization are all pointwise or sampling operations. Single
fragment shader: read Y and UV planes as separate textures, write directly into the model's input
tensor at inference resolution.

The GPU's bilinear sampler does the downscale **for free** — rendering to 256×256 while sampling a
1080p source means the hardware filters as part of the fetch. Converting first and resizing second is
two passes doing the work of one.

Inference input should be small: 192×192 or 256×256. Do not let the sensor's native resolution set
the model's.

### 6.4 Readback is event-driven, not per-frame

Getting the crop from GPU memory to the network is the one unavoidable GPU→CPU transfer. A
synchronous `glReadPixels` stalls the pipeline hard.

Two things make it cheap:

1. **Asynchronous** — PBO, or map the dmabuf after a fence.
2. **Only on frames where the gate fired.** With a working pre-gate that is a handful of times per
   minute, not 30 times per second. The expensive operation is rare by construction.

If the crop goes over the wire as JPEG, use the **hardware video encoder**, not a CPU encode.

### 6.5 Frame pacing: latest-wins

Camera at 30 fps, gate at 5–10. **Never queue frames.** A new frame arriving while inference is in
flight overwrites the pending slot; the old one is dropped.

This is precisely A§5's latest-wins state slot applied one layer down. Same reasoning, same benefit:
a dropped frame costs nothing because a fresher one is 33 ms behind, and memory stays constant no
matter how far behind you fall. A queue here gives you a growing latency debt and eventually an OOM
on a 4 GB device.

### 6.6 Transport of the crop

The gate's output crosses to the laptop on **the game protocol's bulk channel** (A§5, the fourth
channel added for `skel`) — not MQTT. The crop has exactly `skel`'s profile: bulk, latency-tolerant,
loss-tolerant. One system for game data.

### 6.7 Keep this off the gameplay critical path

Camera → decode → pre-gate → inference → network → confirm inference is a latency chain with limited
headroom. "Generate game content when an object is classified" tolerates a second or two. "Detect
the kick" does not. **The classifier path must never gate a kick.**

---

## 7. Telemetry as architecture

Do not run Snapdragon Profiler, Task Manager, and Linux sysfs in three windows during a demo.

**Add a `telem` message on the control plane.** Each device self-reports per-unit utilization at
~1 Hz; the server aggregates; the TV renders a live nine-cell grid.

```
{"unit": "gpu", "util_pct": 41, "temp_c": 52, "window_ms": 1000}
```

One message per unit per device per second. Bounded, cheap, and it makes the capability registry from
A§6 earn its keep a second time.

**Why this is architecture and not tooling:**

- One dashboard instead of three vendor tools mid-demo
- Recordable and replayable after the fact
- The nine-cell grid updating during play, none dark, is a far better artifact than a profiler
  screenshot
- **It verifies every later step for free.** Build it early.

**Per-device sourcing:**

| Device | Source |
|---|---|
| Laptop | Windows performance counters; NPU via Task Manager's NPU engine, GPU under Chrome's GPU process |
| Phone | Android APIs / Snapdragon telemetry; report skin temperature too |
| UNO Q Linux | `/proc`, sysfs, Adreno debugfs |
| STM32 | **No OS counters.** Instrument directly: report main-loop duty cycle. Honest and trivial. |

**Pipeline-specific metrics that belong in `telem`, not just utilization percentages:**

- CPU time per frame in the pre-gate path (should be microseconds)
- GPU time per inference
- Pre-gate wake rate (how often the GPU is invoked per second)
- Frames dropped by latest-wins

---

## 8. Cross-cutting constraints

| Constraint | Applies to | Consequence |
|---|---|---|
| NPU unreachable from emulated x64 | Laptop | Inference process must be ARM64 (ARM64EC is the hybrid escape hatch) |
| Quantization is x64-only, inference is ARM64 | Laptop | Two Python installations, set up before writing code |
| Silent CPU fallback | Laptop NPU, UNO Q GPU | `disable_cpu_ep_fallback = "1"`; assert delegated node count on TFLite |
| Frames and transcripts never leave the phone | Phone | Whisper and stage-2 pose stay on-device permanently |
| No NPU on QRB2210 | UNO Q | Gate tier is GPU-only; keep the model small |
| 4 GB RAM, A53-class CPU | UNO Q | Zero-copy is mandatory, not an optimization |
| NPU is not faster than CPU — it is *parallel* and lower-power | All | Success is four busy domains, not a speed record |

That last row is the whole thesis. Adreno renders, Oryon runs the game loop, Hexagon generates
commentary, and none of them block each other. They do share memory bandwidth and a thermal
envelope — measure rather than assume.

---

## 9. Integrated phase map

Merges this plan with A§16. Phases are shippable increments; the count column tracks lit units.

**[corrected]** Tag column added: **[dev]** verifiable on the development machine (x86-64;
see §2 note), **[device]** needs target hardware in hand (X Elite laptop / S25),
**[hw]** needs hardware that may not exist yet (UNO Q).

| Phase | Contents | Tag | Lights | Count | Size |
|---|---|---|---|---|---|
| **A. Foundation** | A§16 Phase 1 (process boundary, reliable events, Android test source set) · U§5.2 Change A (`hero()` shader — **replaces** the executor fix) · U§5.3 (GenieX commentary) · U§7 telemetry in JSON | boundary+telemetry **[dev]** ✅ *shipped*; shader **[dev]**; GenieX **[device]** | Laptop GPU, Laptop NPU | 3 → **5** | 5–6 d |
| **B. Wire format** | A§16 Phase 2 (header, CBOR control plane, binary WS frames) · migrate `telem` to CBOR · U§5.5 (phone pose split) | header/CBOR **[dev]** (header shipped); pose split **[device]** | Phone GPU | 5 → **6** | 4–5 d |
| **C. Transport** | A§16 Phase 3 (UDP data plane) · A§16 Phase 4 (discovery beacon; time sync gated on P2) | UDP **[dev]**; discovery **[dev]** ✅ *shipped* | — | 6 | 7–9 d |
| **D. Goalkeeper** | U§5.9 (MCU dive events, IMU, haptics, discovery beacon) · U§5.7 (Cortex-A session stack, fusion) | protocol+simulator **[dev]** ✅ *shipped as `tools/unoq_sim.py`*; firmware **[hw]** | UNO Q MCU, UNO Q CPU | 6 → **8** | 4–5 d |
| **E. Vision** | U§6 full camera pipeline · U§5.8 gate tier · classifier confirm on laptop NPU | **[hw]** + **[device]** | UNO Q GPU | 8 → **9** | 5–7 d |
| **F. Polish** | U§5.2 Change B (WebGPU stadium) · A§16 Phase 5 (negotiated `aim_hz`, circuit breaker) | **[dev]** | — | 9 | 3–4 d |

**Rough total: 28–36 dev-days.**

### Sequencing rationale

- **Phase A lights two units and fixes two defects with the same change.** The shader port is both.
- **Telemetry ships in Phase A, in JSON.** It does not need the CBOR control plane to be useful, and
  every subsequent phase gets verified for free once it exists. Migrate the encoding in Phase B.
- **The phone pose split is transport-independent.** It sits in Phase B only because that phase is
  otherwise light on device-side work; it could move earlier if the Android test source set from
  Phase A lands cleanly.
- **The UNO Q cannot come online before Phase C.** The MCU speaks UDP and the fixed-layout discovery
  beacon; putting a WebSocket client on an MCU is the wrong shape. Discovery matters more here than
  anywhere — a hardcoded IP in MCU firmware is a reflash per network change.
- **Phase E is last because it depends on D.** The camera pipeline needs the UNO Q's session stack
  and bulk channel working first.

### Cut-line

If time runs short: **Phase A in full, plus discovery from Phase C.** That fixes the lost kick, kills
the broadcast storm, removes manual-IP misery, lights five of nine units, and leaves a live
telemetry dashboard — without ever touching the transport. Stop there and it is still a coherent
demo.

### Delivery risks

Carried forward from A§16, plus new:

1. *UDP regresses a working demo.* → WebSocket is never removed; UDP is opt-in behind a toggle with
   auto-fallback on a missing `WELCOME`. Hotel and enterprise APs block client-to-client UDP.
2. *Two processes means two things to fail on stage.* → `InProcLink` is the default; `TcpLink` is
   for CI.
3. *No Android test infrastructure exists.* → Phase A prerequisite.
4. **NEW:** *`hero()` does not port to a shader.* → numpy vectorization fallback removes the stall;
   the laptop GPU then depends entirely on Change B, which moves earlier.
5. **NEW:** *OpenCL does not enumerate on the UNO Q's Debian side.* → Check in hour one of Phase D,
   not Phase E. If it fails, the UNO Q GPU may need the QNN GPU backend or a GL ES compute path.
6. **NEW:** *NPU contention between LLM and classifier.* → Measure in Phase A; if they cannot
   coexist, the classifier evicts the LLM and commentary falls back to templates during
   classification. Degrades gracefully.

---

## 10. Open questions

Code- and device-dependent. Answer 1–3 before starting Phase A.

1. **Is `hero()`'s splatting math portable to a stateless fragment shader**, or does it carry
   per-pixel state that needs restructuring? Gates U§5.2 Change A and risk 4.
2. **What is the current Canvas2D render loop's frame budget**, and does WebGPU interop cleanly with
   the existing 2D layers during a phased migration? Gates Change B's difficulty.
3. **Does the laptop NPU hold an LLM session and a classifier session concurrently**, or must one
   evict the other? Gates U§5.3 contention handling and risk 6.
4. **Does `clinfo` enumerate the Adreno 702 on the UNO Q's Debian image**, and does the TFLite GPU
   delegate report all ops delegated? Gates U§5.8. Check in Phase D.
5. **What IMU is available to the STM32**, at what rate, and on what bus? Gates U§5.9.
6. **What camera and interface will attach to the UNO Q**, and does it expose dmabuf export via
   V4L2? Gates U§6.1 — without dmabuf export the zero-copy design needs rework.
7. **What is the object classifier actually classifying**, and what does "generate the game
   dynamically" produce? The gate model's size and class set depend on this, and it is currently
   the vaguest requirement in the system.

   **[corrected — half answered by the four-pillar branch.]** `updated/SentinelMesh` built the
   missing seam: after full time a `generating` phase runs a SceneEngine
   (`laptop/scene_engine.py`) in which Qwen3-4B designs the **next venue's atmosphere and keeper
   difficulty** (`keeperIq`, `keeperReaction`, `shootWindow`, `powerBeat` — applied at
   `server.py:500-503`, clamped at `scene_engine.py:175-191`), with a 5-level campaign that never
   regresses (`scene_engine.py:98-102`). So "generate the game dynamically" now has a concrete
   answer. The **object-classifier half is still unanswered** — nothing classifies anything, and
   the gate model's class set remains undefined.

Question 7 is the one most likely to change the plan. Pin it down before Phase E.
