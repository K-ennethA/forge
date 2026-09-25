# Mocap sources, base meshes, generative motion, and a motion-quality gate

Research only: no code was changed and nothing was built. Written 2026-09-24.

Context: the artist called the procedural run "clearly broken" and said the character "needs to be much better quality", even though every mechanical gate (foot slide, seam closure, stretch) passed (`projects/werewolf/design/review-log.md`). Hard rules: never pay, run everything locally, and ship outputs in commercial indie games on Steam and mobile worldwide.

**How to read the labels**
- **VERIFIED** means I read it in the cited source or in the repo on 2026-09-24.
- **ASSUMPTION** means it has not been verified. Each one names the experiment that would settle it (see §9).
- **License wording.** The load-bearing license clauses are **paraphrased**, with the exact place to find each one (URL plus FAQ question, section number or line). They are not copied out in full. Read the linked text before relying on any verdict.

**The clip contract.** `werewolf/docs/design.md` §"Model swap contract" lists **23** clip names, not 21:
- Locomotion: `idle, walk, run, sprint, crouch_idle, crouch_walk`
- Air: `jump, fall, land`
- Traversal: `climb`
- Combat: `attack_1..3, heavy, counter, dodge, hit, stagger, death`
- Story: `transform, talk, feed, bite`

Clips are in place (root travel zeroed). They ship as GLB per `<kind>`.

---

## 0. Bottom line

1. **Humanoid motion should be retargeted mocap. Procedural keyframing should not be the source.**
   - The safe free corpus is **CMU** (commercial use allowed, no resale of the data) plus **100STYLE** (CC BY 4.0, credit required).
   - **Mixamo** has the widest coverage (combat, deaths, hit reactions, climbing). Its terms allow games and forbid only distributing the raw files and any AI/ML use. It needs a free Adobe ID, which you have to create yourself.
   - **Quaternius UAL** free tiers are CC0 and fill stylized gaps.
2. **Story clips that no free library has** (`feed`, `transform`, `bite`, parts of `counter`): the one generative model that passes the license bar today is **NVIDIA Kimodo-SOMA-RP**.
   - License: NVIDIA Open Model License. Commercial use is allowed, NVIDIA claims no ownership of outputs, and deployment is global.
   - It runs on the local RTX 5070 (12 GB) with the text encoder on the CPU.
   - Everything else in text-to-motion is either trained on non-commercial data (MDM, MotionGPT, MoMask, T2M-GPT, OmniControl, all via HumanML3D/AMASS) or bars using its outputs outside a territory (HY-Motion).
   - Treat Kimodo as promising but unproven for these particular verbs until experiment E4 runs.
3. **Quadrupeds:** no free mocap has a license that clears the bar.
   - Use **Quaternius' CC0 animated wolf/husky/fox** clips (hand-keyed, stylized) as reference or as a starting point.
   - Keep the rest procedural, gated by quadruped gait statistics (Hildebrand duty factor and phase lag).
   - Truebones' "free" packs fail on both license and provenance.
4. **Human base mesh:** use **MPFB2** (MakeHuman for Blender).
   - Core assets and outputs are CC0 (VERIFIED in the MPFB FAQ and `LICENSE.ASSETS.md`).
   - It runs headless (its own suite runs under `blender -b ... -P test_headless.py`).
   - It ships a Python service API, CC0 clothing packs, proxy topologies for LODs, and built-in rigs whose bone names match CMU (`cmu_mb`) and Mixamo (`mixamo`).
5. **`rigforge_retarget` is plumbing-tested, not fidelity-tested.** Before any mocap is judged, the three code risks in §1.2 have to be settled, because each one can make good mocap look robotic:
   - rest pose and bone roll are never compensated;
   - name heuristics mis-map SOMA and UE-style skeletons;
   - there is no fps resampling, cycle extraction or foot-lock pass.
6. **A motion-quality gate can be built without any training.** It should use:
   - speed-conditioned gait timing;
   - center-of-mass phase;
   - arm/leg phase;
   - spectral and smoothness measures;
   - distances to a reference corpus of licensed mocap (nearest-neighbor pose distance, Fréchet distance on hand-built kinetic and geometric features).

   A first check against the shipped run already shows one likely failure. Its cadence is **4.0 steps/s** (12-frame stride at 24 fps, 1.05 m steps), against a published band of about 2.75–3.25 steps/s for distance runners. That is a prediction for E3 to confirm, not a verdict.

---

## 1. What `rigforge_retarget` does today (from reading `addon/forge/tools/rigforge_anim.py`)

### 1.1 Capabilities (VERIFIED by reading the code, lines ~5248–5885)

- **Input.** A user-supplied `.bvh` (Blender's `import_anim.bvh`) or `.fbx` (`import_scene.fbx`). Nothing is downloaded, and everything imported is deleted in a `finally`.
  - BVH import settings: `global_scale=1.0`, `use_fps_scale=False`, `rotate_mode=NATIVE`.
  - FBX import settings: `automatic_bone_orientation=True`, `ignore_leaf_bones=True`.
- **Parameters:**
  - `target_rig`, `source_path`, `action_name`, `loop` (applies the `-loop` suffix), `replace`
  - `frame_step` (1–10), `fk_switch` (default true)
  - `scale` (`"auto"` = the ratio of armature heights, or a number)
  - `mapping` (`"auto"`, or an explicit `{source: target}` dict)
- **Mapping.** `BONE_RULES` is an ordered table of name fragments mapped to slots (hips, spine, chest, neck, head, shoulder, upperarm, forearm, hand, thigh, shin, foot, toe).
  - It understands the `mixamorig:`, `bip01`, `armature|` and `character1_` prefixes, and CMU-style `l`/`r` prefixes.
  - Fingers, end sites, eyes, jaw, props and IK bones are skipped.
  - Slots map to **Rigify FK controls** through `SLOT_TARGETS` (`torso`, `spine_fk.001`, `chest`, `upper_arm_fk.L`, `thigh_fk.L`, …).
  - A target already taken by an earlier source bone is reported as unmapped, with the reason.
- **Transfer.**
  - Every mapped control gets a `COPY_ROTATION` constraint (WORLD→WORLD, `mix_mode=REPLACE`).
  - The hips slot also gets `COPY_LOCATION` (WORLD, no offset).
  - The source is scaled by the height ratio and translated so its rest root head sits on the target's.
  - Then `nla.bake(visual_keying=True, only_selected=True)` runs over the clip's frame range.
  - The IK/FK switches are moved to FK and keyframed into the action.
- **Tests.** `addon/tests/headless_phase5.py::test_retarget` checks plumbing only: mapping sanity, the action exists, controls are animated, the hips have a location curve, the arm rotates, and nothing leaks.
  - **No joint-position or orientation fidelity is measured.**
  - A parallel lane added `addon/tests/fixtures/mocap/make_bvh_fixtures.py` (timestamped 2026-09-24 19:03). It generates a synthetic CMU-style Z-up/inches/120 fps walk, a Mixamo-style Y-up/cm/30 fps walk yawed 30°, and three broken files. Its `truth()` function returns the world joint positions, so fidelity *can* now be measured.
  - When I read the repo, no test consumed those fixtures.

### 1.2 Risks that matter for "mocap that reads human" (analysis of the code; each settled by E1/E2/E4)

1. **Rest pose and bone roll are never compensated.**
   - WORLD→WORLD `COPY_ROTATION` with `REPLACE` sets the target bone's *absolute* world orientation equal to the source bone's.
   - Limb direction survives because both importers point bone Y along the limb. **Twist** (roll about the limb) and any control whose axis is not anatomical (Rigify `torso`, `hips`, `chest`, feet) inherit the source's arbitrary rest roll.
   - Expected symptoms: forearms or hands twisted, feet toed in or out, and possibly the whole torso pitched.
   - FBX `automatic_bone_orientation=True` changes rolls again.
   - *ASSUMPTION until E1:* the size of the error on the forge rig.
2. **Name heuristics mis-map two real source conventions.**
   - **Kimodo/SOMA BVH** (VERIFIED from `somaskel77_standard_tpose.bvh`):
     - `ROOT Root` sits at the floor and has 6 channels, with `Hips` below it (also 6 channels).
     - `Root` matches the hips rule first and takes `torso`, so `Hips` is dropped as "taken". Pelvis bob, tilt and height then come from a floor-level root.
     - SOMA names the thigh `LeftLeg` and the shin `LeftShin`. The trailing `"leg"` rule sends `LeftLeg` to the **shin** slot, and `LeftShin` then collides with it.
   - **UE-convention skeletons** (`root` → `pelvis`, e.g. UE exports, and probably the Quaternius UAL rig, which is an *ASSUMPTION*) hit the same Root-versus-pelvis collision.
   - **CMU cgspeed** (`Hips, LowerBack, Spine, Spine1, …`): `Spine1` collides with `Spine` on the spine slot and is dropped, so upper-spine twist is lost.
   - The fix is data, not code logic: ship **explicit mapping presets** per convention. The `mapping` dict parameter already exists.
3. **Frame rate is not normalised.**
   - `use_fps_scale=False` keys the clip at its native frame numbers. A 120 fps CMU file baked into a 24 fps scene plays **5× slow**.
   - The new fixtures deliberately include 120 fps and 30 fps files, which suggests the parallel lane found this.
4. **Missing post-steps.** There is no root-travel extraction (the contract wants in-place clips), no cycle extraction or seam blend (mocap takes are not loops), no foot-contact lock after proportion change, and no leg-length-aware stride scaling (only an overall height ratio).
5. **No quadruped or digitigrade path.**
   - `SLOT_TARGETS` is biped-only.
   - Werewolf Form C's `foot_metatarsal` has no source bone in human mocap. `requirements.md` already predicts slide and float there.

---

## 2. Q1: Humanoid mocap sources

### 2.1 Licensing verdict table

"Ship baked in game" means retargeted onto our rig, baked and exported inside a `.glb`/PCK.

| Source | Commercial game use | Ship retargeted clips in a game | Attribution | Account needed | Clause location (read before relying on it) | Verdict |
|---|---|---|---|---|---|---|
| **CMU Graphics Lab** (mocap.cs.cmu.edu) | Yes. The homepage says the data is free for all uses. | Yes. The FAQ allows including the data in commercially sold products but forbids reselling the data itself, **even converted**. | Only *requested*, for published papers. | None | mocap.cs.cmu.edu homepage "Welcome" paragraph, and the FAQ page's usage paragraph | **SAFE**. Don't sell or redistribute the clips as a pack. |
| **cgspeed BVH conversion of CMU** (B. Hahne) | Yes | Yes | None | None | cgspeed README, "USAGE RIGHTS": CMU places no restrictions and Hahne adds none | **SAFE**. It inherits CMU's terms. |
| **100STYLE** (Mason, Starke, Komura) | Yes (CC BY 4.0) | Yes. Adapted material is allowed. | **Required**: creator, license, link, and a note that the clips were modified. It can be a credits screen or a linked page (CC BY 4.0 §3(a)(1)–(2)). CC BY 4.0 §2(a)(5)(B) bars adding DRM or terms that restrict recipients' rights *in the Licensed Material*. | None | Zenodo record 8127870 (license `cc-by-4.0`); CC BY 4.0 legal code §3(a) | **SAFE** with a credits entry. *ASSUMPTION:* platform DRM on the game as a whole is fine, since it is common practice for CC-BY assets in games. It has not been legally reviewed. |
| **100STYLE retarget** (orangeduck) | Same as 100STYLE | Same | Same | None | github.com/orangeduck/100style-retarget README "License". Note the bundled `Geno.fbx` *mesh* is non-commercial only. | **SAFE for the clips.** Do not ship Geno. |
| **Mixamo** (Adobe) | Yes. helpx FAQ "What type of projects can I create…": characters and animations are royalty free for commercial projects, and video games are named. | Yes when embedded in the game. Adobe staff FAQ: the only prohibition is distributing the **raw** character and animation files (including engine templates or asset packs). | Not required (same FAQ) | **Free Adobe ID** (helpx FAQ "How do I get access"). Not available to Enterprise/Federated IDs or China country codes. | helpx.adobe.com/creative-cloud/faq/mixamo-faq.html; community.adobe.com Mixamo licensing FAQ; **Mixamo Additional Terms (2021-06-23) §1** | **SAFE for shipping.** §1 **forbids using Mixamo content, directly or indirectly, to create, train, *test* or improve any AI/ML system.** So keep Mixamo **out** of the quality-gate reference corpus and never feed it to Kimodo or similar. Ship clips inside the PCK, not as loose files. |
| **Quaternius Universal Animation Library 1 and 2** (free "Standard" tier) | Yes (CC0) | Yes | None | None; itch "name your own price", $0 works | quaternius.itch.io/universal-animation-library; OpenGameArt UAL and UAL2 pages (License: CC0) | **SAFE**, and the best license available. The free tier only: UAL1 has 45 of 120+ clips; UAL2 is about 70% of 130+. |
| **Rokoko free packs** (263 magic, 16 walk/run, 15 superhero, 12 sports) | The marketing page says usable in any game, including commercially. | Presumably. There is no formal asset license text. | Not stated | *ASSUMPTION:* an email or Rokoko ID gate | rokoko.com/resources/download-263-rokoko-motion-capture-assets. The formal terms (rokoko.com/terms and the Studio EULA) have **no asset clause**. Search excerpts mention the license lasting only while you hold an account and a ban on redistributing raw files, but **I could not locate that text**. | **CAUTION.** Only marketing wording exists. Use only if E6-style verification finds a real license. |
| **HDM05** (MPI) | Yes (CC BY-SA 3.0) | ShareAlike: the **retargeted clips** must stay CC BY-SA, so anyone could legally extract and reuse them. | Required | None | re3data / HDM05 page (CC BY-SA 3.0 Unported) | **AVOID** unless share-alike on the clips is acceptable. |
| **ActorCore free motions** (Reallusion) | The Standard License allows incorporation into commercial games (Content EULA §2.1(B)). | Contradictory: the same EULA's restrictions forbid use "as embedded content within applications", and forbid ML/AI use. | — | Reallusion account; rights are tied to the purchase account | actorcore.reallusion.com/eula, "Restrictions" | **AVOID.** The license is ambiguous and account-bound. |
| **LaFAN1** (Ubisoft) | **No** (CC BY-NC-ND 4.0) | No | — | — | github.com/ubisoft/ubisoft-laforge-animation-dataset `license.txt` | **REJECT** |
| **ZeroEGGS** (Ubisoft) | **No** (CC BY-NC-ND 4.0) | No | — | — | ubisoft-laforge-ZeroEGGS `License.md` | **REJECT** |
| **Bandai-Namco Research Motiondataset 1 and 2** | **No** (CC BY-NC 4.0) | No | — | — | GitHub README "License" | **REJECT** |
| **AMASS** and the **SMPL/SMPL-H/X body models**; anything derived (HumanML3D, Motion-X) | **No.** Non-commercial research only. Incorporation in a commercial product and "production of other artefacts for commercial purposes" are both banned, and so is training networks for commercial use. | No | — | Registration | amass.is.tue.mpg.de/license.html; smpl.is.tue.mpg.de/modellicense.html | **REJECT** |
| **SFU Motion Capture DB** | **No.** The homepage says the data cannot be used for commercial products or resale. | No | — | — | mocap.cs.sfu.ca homepage | **REJECT** |
| **Motorica Dance** | **No** (non-commercial; commercial use needs written consent) | No | — | — | MotoricaDanceDataset `LICENSE.txt` | **REJECT** |
| **BONES-SEED** (Bones Studio) | **No.** Only academic users or "qualifying startups" get rights; everyone else needs a commercial license. | No | — | HF gated | huggingface.co/datasets/bones-studio/seed `LICENSE.md` (per search excerpt) | **REJECT** as a data source. See §5 for the model trained on it. |

### 2.2 Per-source detail

**CMU (via cgspeed "MotionBuilder-friendly" BVH)**
- **Formats.** CMU distributes ASF/AMC, C3D and video (VERIFIED on the site listing). The cgspeed releases convert them to BVH:
  - MotionBuilder-named joints, **120 fps** Frame Time fixed (amc2bvh wrote a wrong 30 fps);
  - a **T-pose added** as a calibration frame (VERIFIED in the cgspeed README and search excerpt);
  - 31 joints, matching MPFB's `cmu_mb` rig bone list (VERIFIED: `rig.cmu_mb.json` has `Hips, LHipJoint, LeftUpLeg, LeftLeg, LeftFoot, LeftToeBase, LowerBack, Spine, Spine1, Neck, Neck1, Head, LeftShoulder, LeftArm, LeftForeArm, LeftHand, …`).
- **Coverage.** I parsed the site's full motion listing: **1,673 unique trials across subjects 1–144**. The site's own total may differ.

  | Keyword | Trials | Notes |
  |---|---|---|
  | walk | 380 | |
  | run or jog | ~130 | |
  | jump/leap/hop | 152 | Also gives `land` |
  | climb | 36 | Playground and ladder, **not** wall or ledge climbing |
  | sneak/tiptoe | 17 | |
  | crouch/squat | 7 | |
  | boxing/punch | 20 / 8 | |
  | kick | 22 | |
  | stumble/push | ~18 | Mostly pushing objects; a few stumbles |
  | duck | 2 | Includes "duck to avoid flying object" |
  | conversation with hand gestures and "quarrel – angry hand gestures" | 10 | Subjects 18/19 → `talk` |
  | eating/drinking | 19 | E.g. 79_12 "eating dinner", 79_38 "drinking water" → partial `feed` |
  | zombie march; human "bear/dog/dinosaur/monkey" impressions | ~136 | Subjects 27–30 → flavor for `transform`/werewolf gait |
  | limping ("hurt right leg") | several | |
  | death/fall | **none** | |
  | hit reaction | **none** | |
  | sword/weapon combat | **none** | |
  | dodge roll | **none** | |

- **Quality.** Captured with a Vicon optical system around 2003. Usable, but with known noise and occasional foot slide. There are no fingers (VERIFIED in the cgspeed README: finger joints carry no data). *ASSUMPTION:* the typical per-clip cleanup load. Settled by E2.
- **Retarget difficulty to a 29-bone Rigify basic-human rig:** medium.
  - Names mostly auto-map, but `Spine1` is dropped (§1.2).
  - The fps must be resampled.
  - The T-pose frame provides the rest-pose calibration that §1.2(1) needs.

**100STYLE**
- **Content.** Over 4M frames of **locomotion only**, in 100 styles, all performed by one actor (VERIFIED).
  - Movements per style: forward, backward and sidestep walking and running, idle, and transitions.
  - Relevant styles (VERIFIED from the file list): `Neutral`, `Crouched`, `OnToesCrouched`, `InTheDark`, `Tiptoe` (→ `crouch_walk`/`crouch_idle`/sneak); `Zombie`, `Dinosaur`, `Heavyset`, `Old`, `Proud`, `Drunk`, `LimpLeft/Right` (→ character and werewolf gait flavor); `Punch`, `Kick`, `KarateChop`, `ShieldedLeft/Right` (strikes *while walking*, not standing attack clips).
- **Formats.** Raw BVH (100STYLE.zip, 1.47 GB on Zenodo, or per-style files from ianxmason.com). orangeduck's retarget adds BVH and FBX on one common "Geno" skeleton, which is also shared with the NC LaFAN1 and ZeroEGGS retargets. Do not mix those.
- **Quality.** *ASSUMPTION:* optical capture at studio quality (the paper setup was not re-read). E3 measures it.
- **Retarget difficulty:** medium. *ASSUMPTION:* the joint naming of the raw BVH. Settled by E3.

**Mixamo**
- **Formats.** FBX (binary or ASCII, "for Unity"), DAE; with or without skin; 30 fps default; keyframe reduction. **No BVH.**
  - Forge's FBX path covers this: download "Without Skin" onto the default Y-Bot, then `rigforge_retarget` the FBX offline.
  - Nothing in the FAQ or Additional Terms restricts where retargeting happens. The FAQ itself advises saving rigged characters locally.
  - *ASSUMPTION:* the "In Place" toggle exists for locomotion clips. Check in E6.
- **Skeleton.** `mixamorig:` with 65 bones including fingers. MPFB ships a matching `mixamo` rig (52 bones, VERIFIED).
- **Coverage.** Over 2,000 clips, **bipeds only** (helpx FAQ: the auto-rigger and animation library are for bipedal humanoids only).
  - *ASSUMPTION, from memory and not verified:* it has sword and unarmed combos, hit reactions, stagger, several deaths, dodge/roll, crouch walks, jump/fall/land sets, wall and ledge climbing, talking/arguing, zombie bite and neck-bite, and "mutant" roars.
  - An Adobe community post (2023) asks for "eating and more drinking animations", which suggests eating (`feed`) is thin.
  - The catalog is behind login, so E6 has to confirm the list.
- **Quality.** Professional mocap, cleaned. The downside is recognisability: many games use these exact clips.

**Quaternius UAL1 and UAL2 (free tiers)**
- **License.** CC0 (VERIFIED on itch.io and OpenGameArt).
- **Formats.** FBX and GLB. `.blend` is in the paid Source tier only, which is excluded.
- **Content.** Locomotion in 8 directions, jog, sprint, crawl, swim, sit, **deaths**, melee and weapon combat and combos, parkour, farming/drinking/fishing, zombie locomotion, emotes and gestures (VERIFIED as categories). Which clips are in the free subset is **not listed anywhere**, so that is an *ASSUMPTION* settled by E7 (downloads of 14.5 MB + 10.3 MB).
- **Hand-keyed or mocap:** not disclosed. *ASSUMPTION:* hand-keyed, stylized.
- **Fit.** Good for the stylized products and as fallback combat or death clips. It may clash with a semi-realistic target.

**Rokoko free packs.**
- FBX at 30 fps, pre-retargeted to Mixamo, UE and HumanIK skeletons (VERIFIED on the page).
- Captured with Smartsuit (inertial). *ASSUMPTION:* more foot slide than optical capture.
- Coverage: walk/run cycles, spell-casting, superhero moves, sports.

### 2.3 Coverage of the 23-clip contract by license-safe sources

Key: ✓ good · ~ partial or needs editing · — none. Anything marked with * rests on the Mixamo catalog ASSUMPTION, which E6 settles.

| Clip | CMU | 100STYLE | Mixamo* | Quaternius UAL (free)† | Best route |
|---|---|---|---|---|---|
| idle | ✓ | ✓ | ✓ | ✓ | CMU/100STYLE |
| walk / run | ✓ | ✓ (Neutral FW/FR) | ✓ | ✓ | 100STYLE Neutral, CMU as backup |
| sprint | ~ (fast runs) | ~ (FR) | ✓ | ✓ | Mixamo, or CMU fastest runs |
| crouch_idle / crouch_walk | ~ | ✓ (Crouched, InTheDark) | ✓ | ~ | 100STYLE |
| jump / fall / land | ✓ / ~ / ✓ | ~ | ✓ | ✓ | CMU, with jumps split into three |
| climb | ~ (playground/ladder) | — | ✓ | ✓ (parkour) | Mixamo, or UAL |
| attack_1..3 / heavy | ~ (boxing, kicks) | — | ✓ | ✓ | Mixamo, or UAL |
| counter | ~ | — | ~ | ~ | Mixamo, or Kimodo |
| dodge | ~ (duck) | — | ✓ | ~ | Mixamo |
| hit / stagger | ~ (stumbles) | — | ✓ | ~ | Mixamo |
| death | — | — | ✓ | ✓ | Mixamo, or UAL |
| transform | — (animal impressions for flavor) | — | ~ | — | **Kimodo** plus hand polish |
| talk | ✓ (conversation gestures) | — | ✓ | ~ (gestures) | CMU |
| feed (vampire drinking) | ~ (eating/drinking, not predatory) | — | ~ (zombie bite) | ~ (drinking) | **Kimodo** |
| bite (werewolf lunge-bite) | — | — | ~ (zombie bite) | — | **Kimodo**, or a Mixamo zombie bite |

† The free-tier clip list is unverified (E7).

### 2.4 Ranking (license safety × coverage × quality)

1. **CMU via cgspeed BVH**: safety high, coverage medium, quality medium.
2. **100STYLE**: safety high (credits), coverage locomotion only, quality high (*ASSUMPTION*). This is the locomotion and crouch source and the reference-corpus core.
3. **Mixamo**: safety high for shipping, but the AI/ML clause shuts it out of the gate corpus and generation. Coverage highest, quality high. Needs an Adobe ID.
4. **Quaternius UAL free tiers**: safety highest (CC0), coverage medium, quality stylized.
5. **Kimodo-SOMA-RP (generated)**: safety medium-high (§5), fills the gaps. Unproven.
6. **Rokoko free packs**: the license is marketing-page only.
7. **HDM05**: share-alike. **ActorCore**: contradictory terms.

Everything else in §2.1 is rejected.

---

## 3. Q2: Quadruped and creature motion

| Source | License reality | Verdict |
|---|---|---|
| **Quaternius Ultimate Animated Animal Pack** (12 animals including **Wolf, Husky, Fox, Shiba Inu, Stag, Horse**) | CC0 (VERIFIED on quaternius.com and poly.pizza). "Each with more than 12 unique animations (Attack, Death, Kicks, Gallops, Walk, Jump…)". FBX, OBJ, glTF, Blend. | **USE** as reference or a starting point for the wolf. Low-poly, hand-keyed (*ASSUMPTION*), on its own quadruped skeleton, so it needs an explicit mapping onto forge's Rigify quadruped. *ASSUMPTION:* an eat or idle-variant clip exists. Settled by E8. |
| Quaternius LowPoly Animated Animals (farm animals) | CC0 | Not relevant (no canid) |
| **AI4Animation / MANN dog mocap** (Starke / Zhang 2018) | The README says the motion data is available only under **CC BY-NC 4.0**, and that the project is not freely available for commercial use | **REJECT** |
| **Truebones "FREE" Zoo** (75+ animals, FBX/BVH) and the "free masterlist" | The Gumroad listing is priced "$99+", with a free code. It claims a "100% royalty free license", but a buyer review says the *included* license text restricts use to personal or educational, and the seller's reply only says not to redistribute. The same seller's free list includes packs named after commercial game IPs (Mortal Kombat, PUBG, Star Wars, TMNT, SpongeBob…). | **REJECT.** The license is contradictory and the provenance is unsafe. |
| **AnyTop** (arbitrary-skeleton diffusion, SIGGRAPH 2025) | Code is MIT, but the released weights are trained on Truebones Zoo | **REJECT** for shipping. The weights inherit the Truebones problem. |
| T2M4LVO / "How to Move Your Dragon" (Truebones-derived) | Non-commercial research (search excerpt) | **REJECT** |
| SMAL-based animal models | Same Max-Planck non-commercial family as SMPL (*ASSUMPTION*, not re-read) | **REJECT** |
| CMU "dog/bear/dinosaur (human subject)" | Human skeleton | Flavor for the werewolf `transform` or hunched gait only |

**Recommendation for quadrupeds.** Quadruped gait is far more regular than human gait, so procedural authoring is defensible there *if* it is gated by the quadruped statistics in §6.3. Use the Quaternius wolf as the visual reference and as seed clips for attack, death and idle variants.

**Digitigrade werewolf (Form C).** Retarget human mocap in **IK space for the legs** (§7, step 5): drive the foot targets from the source feet's scaled world trajectories and let the digitigrade chain solve. The `foot_metatarsal` bone then gets a solved pose instead of nothing. 100STYLE `Dinosaur`, `Zombie` and `Heavyset` and CMU "bear/dinosaur" give hunched-gait flavor.

---

## 4. Q3: Base meshes

### 4.1 MPFB2 (MakeHuman Plugin For Blender): recommended

- **License of outputs.** VERIFIED on three official FAQ pages and in the repo:
  - MPFB FAQ "Can I use models made with MPFB in a closed-source game?": yes. All core assets (base mesh, targets, skins) are CC0, and third-party assets carry their own licenses.
  - FAQ "Is it really free?": no fees, no paywall, no email needed to download, and in practice no restrictions on output or bundled assets.
  - The repo has `LICENSE.ASSETS.md` = CC0 1.0 and `LICENSE.CODE.md` (GPL), and the GPL covers only the add-on code.
  - The *old* MakeHuman 1.x license (AGPL with an optional CC0 exception for exports under conditions) is a different, older regime. MPFB2's FAQ supersedes it for MPFB.
- **Clothing and assets.** The asset-packs page (VERIFIED) lists them by license:
  - **CC0:** system assets (proxies, eyes, teeth…), skins 01–03 (natural female and male), Pants 01, Shirts 01, Shoes 01, Suits 01–02, Gloves, Hats 01–02, Hair 01 (low-poly/stylized), Eyebrows/Eyelashes, Glasses 01, Masks 01, Equipment 01 (weapons), Dress 01, Underwear 01/04, Bodyparts 01/04/05, target packs (Animal 01, realistic Arms/Cheek/Ears/Hands/Nose), and functional packs (hair editor, visemes 01/02, ARKit-style face units).
  - **CC-BY** (attribution needed): Pants 02–03, Shirts 02–03, Shoes 02–03, Hair 02–03, Suits 03, Dress 02–03, Hats 03–04, Equipment 02–03, Bodyparts 02/03/06, Animal 02–04.
  - Prefer CC0 packs, and track CC-BY ones in a credits manifest.
- **Topology, UV, rig.**
  - The base mesh `hm08` has been unchanged for more than a decade. The body is 13,380 vertices; the documentation cites 14,766 faces including helper geometry (search excerpt of MakeHuman docs).
  - Helpers (joint cubes, clothing-fit geometry) are masked.
  - **Proxies** are alternative topologies for LOD, e.g. `male1591` with 1,591 faces (VERIFIED on the proxies doc).
  - Rigs shipped (VERIFIED from `src/mpfb/data/rigs`, bones counted from the JSON):
    - `default` 163 bones, `default_no_toes` 137
    - `game_engine` **53** (UE-mannequin names: `pelvis, spine_01.., thigh_l, calf_l, foot_l, ball_l, clavicle_l, upperarm_l, lowerarm_l, hand_l` plus fingers)
    - `mixamo` **52**, `cmu_mb` **31**, `openpose`
    - `rigify/human` and `human_toes` (a Rigify metarig with weights)
  - With fingers stripped, `game_engine` is about 23 bones, which fits the 22–34-bone target.
  - *ASSUMPTION:* UV and face quality at semi-realistic close-up distance. The hm08 face is often read as generic "MakeHuman". Settled by E5.
- **Headless scriptability:** high.
  - `test/execute_tests_headless.bash` runs `"$BLENDER_EXE" -b testdata/test_scene.blend -P test_headless.py` (VERIFIED).
  - `docs/services/humanservice.md` documents a pure-Python API:
    - `HumanService.create_human(scale, feet_on_ground, macro_detail_dict)`
    - `add_builtin_rig(basemesh, "game_engine"|"mixamo"|"cmu_mb"|…)`
    - `add_mhclo_asset(...)`
    - `set_character_skin(...)`
    - `deserialize_from_json_file / from_mhm`
    - `refit(...)`
  - The repo was active on 2026-09-23 with 608 stars.
  - *ASSUMPTION:* it installs cleanly as an extension in **Blender 5.0**, which is what is installed here. E5 checks.
- **Where the likeness and style pass fits**, in order:
  1. Macro phenotype (gender, age, muscle, weight, proportions) plus the approximately 1k modifier targets, fitted numerically against forge's reference-sheet landmarks. Forge already has a landmark pipeline in `rigforge_landmarks.py`.
  2. Custom `.target` shape deltas authored with forge's sculpt tools (MPFB has MakeTarget) and stored per character, so the likeness is reproducible data.
  3. Skin textures and "ink" layers from references.
  4. Clothes via `.mhclo` (fitted, with delete groups).
  5. Forge's gates and Godot export stay as they are.
  - Because every character shares hm08 topology, one rig and one weights file serve all of them, which suits Form A/B sharing a skeleton.
  - **Anny** (NAVER Labs, **Apache-2.0**, a differentiable PyTorch body model built on MakeHuman work) is a candidate for *fitting* body shape to reference photos. *ASSUMPTION:* its parameters can be transferred back to MPFB/hm08.

### 4.2 Alternatives

| Base | License | Notes | Verdict |
|---|---|---|---|
| **Quaternius Universal Base Characters** | CC0 | 6 bodies (superhero, regular and teen proportions, male and female), about 13k tris, humanoid rig compatible with UAL, 20 hairstyles (VERIFIED from a search excerpt of the pack page) | **Stylized products**: yes |
| **Blender Studio Human Base Meshes** bundle | CC0 (CG Channel, archive.org mirror) | Sculpt bases: stylized male/female/head plus realistic parts (eye, foot, hand, jaw), quad topology, UVs, closed volumes, **unrigged** | A good sculpt start for hero heads or hands, not a pipeline body |
| **CharMorph** | Code GPL. Bases: **Vitruvian CC0**, Antonia and Reom CC-BY, MB-Lab AGPL | Parametric like MPFB | Vitruvian only; smaller ecosystem than MPFB |
| MB-Lab | AGPL on generated models (MB-Lab docs) | Closed-source games are a problem | **REJECT** |
| Human Generator Ultimate | Paid (about $128 personal, commercial extra) | — | **REJECT** (never pay) |
| Kenney, OpenGameArt CC0 characters | CC0 | Very low-poly | Placeholders only |

---

## 5. Q4: Generative motion (local, open-weight)

| Model | Weights license | Training data | Output skeleton | Commercial verdict |
|---|---|---|---|---|
| **NVIDIA Kimodo-SOMA-RP v1.1** (released 2026-04-10) | **NVIDIA Open Model License**. The models are commercially usable, derivative models may be distributed, and NVIDIA claims no ownership of outputs, while you are responsible for them (VERIFIED, license preamble and §"Ownership"). The model card says it is "ready for commercial use", deployment geography is **Global**, and use cases include "Animations for game and media development". | Proprietary Bones Rigplay dataset, 700 h optical mocap | SOMA: generated on 30 joints, exported as 77 joints (`somaskel77`). **BVH export** via `--bvh` (optionally `--bvh_standard_tpose`), 30 fps, max 10 s, with foot-contact labels and optional foot-skate cleanup | **CANDIDATE**, the only one |
| Kimodo code | Apache-2.0 | — | — | OK |
| Kimodo text encoder | LLM2Vec adapters are MIT, but the base is **Meta-Llama-3-8B-Instruct** under the **Llama 3 license** (HF-gated; you must accept Meta's terms) | — | — | Commercial use is allowed under 700M MAU. §1.b.i requires the "Built with Meta Llama 3" notice if you distribute "a product or service that uses" Llama materials. *ASSUMPTION:* a game shipping only baked clips does not "use" Llama. A credits line would remove the question cheaply. |
| Kimodo-SOMA-SEED, G1-SEED | NVIDIA Open Model License | BONES-SEED, whose own license is restrictive, but the models are licensed separately | SOMA / G1 | The weights license permits it; RP is higher quality |
| Kimodo-SMPLX-RP | **NVIDIA R&D (non-commercial) model license** | — | SMPL-X | **REJECT** |
| **HY-Motion 1.0** (Tencent, Dec 2025) | Tencent Hunyuan Community License. The license **does not apply in the EU, UK or South Korea**, and §5(c) forbids using or displaying the **Output** outside the Territory (VERIFIED, `LICENSE.txt` lines 3, 17, 38). | 3,000 h + 400 h | SMPL-H 22 joints | **REJECT.** It is the same template forge already rejected for Hunyuan3D (`docs/research/hunyuan-license-review.md`). |
| MDM, MotionGPT, MoMask, OmniControl (MIT); T2M-GPT (Apache-2.0) | Code permissive; **released weights trained on HumanML3D = AMASS**, which is non-commercial and bans training for commercial use | SMPL-based 22 joints | **REJECT** as shipped weights. The data license taints them. |
| CAMDM (SIGGRAPH 2024) | Code Apache-2.0 (Unity part GPL-3) | 100STYLE (CC BY) | Locomotion controller | Legal if you train it yourself on 100STYLE, but it adds nothing over using 100STYLE directly. **Not worth it.** |
| AnyTop (quadrupeds, arbitrary skeletons) | Code MIT | Truebones Zoo | Arbitrary | **REJECT** (§3) |

**Honest verdict.** For *humanoid* gap clips, Kimodo is plausibly usable **now**:
- The license is clean for global shipping. The one open point is the Llama 3 credits question.
- Hardware fits. The README says about 17 GB VRAM with everything on the GPU, and under 3 GB with `TEXT_ENCODER_DEVICE=cpu`. This machine has an **RTX 5070 with 12 GB** (Blackwell, which is on the supported list) and **31.6 GB RAM**, enough to run the 8B encoder on the CPU.
- It exports BVH and works on Windows. The README says Windows "should work especially if using Docker", so that is an *ASSUMPTION*.

Things that are unproven:
- Whether it can produce **"vampire drinks from a victim's neck"**, **"doubles over and transforms"** or **"lunging bite"** convincingly. Its training set is proprietary, so coverage is unknown.
- Whether outputs pass the §6 gate after retargeting.
- It is humanoid-only. **Nothing open and commercially licensed generates quadruped motion today.**

So the generative route is **not a trap for humanoid gap-filling, but it is not a replacement for mocap either**. Generate 5–10 samples per clip, gate them, and have the artist pick. It **is** a trap for quadrupeds and for anything trained on AMASS or HumanML3D.

---

## 6. Q5: Motion-quality gate (deterministic, headless, training-free)

**Principle.** The current gates prove the *mechanics*: no slide, closed seams, no stretch. The new gate measures *statistics a human reader is sensitive to*, compared against a **reference corpus of licensed mocap retargeted onto the same forge rig**, so skeleton differences cancel.

**Calibration rule (defect → gate).** A metric joins the gate only if it passes two controls:
- **Negative control:** today's shipped procedural walk, run and sprint **fail** it.
- **Positive control:** held-out mocap clips of the same action **pass** it.

Thresholds come from leave-one-clip-out percentiles of the corpus, not from hand-picked numbers.

**Corpus licensing.**
- Use **CMU and 100STYLE only.** The corpus is internal, but CC BY attribution is kept anyway.
- Exclude **Mixamo**: its Additional Terms §1 bans using its content to "test" AI systems, so keep it out of anything that evaluates generated clips.
- Exclude every NC dataset: internal QA use for a commercial product is still commercial use under the AMASS-style wording.

### 6.1 Tier 1: speed-conditioned gait timing (feet and pelvis only; cheap and very diagnostic)

Detect contact per foot with the same thresholds as the foot-skate metric (height and speed, below).

| Metric | Definition | Reference band (literature sanity check; the corpus sets the real band) |
|---|---|---|
| Cadence | steps/s | Distance runners about 2.75–3.25 Hz (165–195 spm) (search excerpt of PMC5685089 / van Oeveren et al. 2021). Cavanagh & Kram: about +7 spm per +1 m/s over 3.15–4.12 m/s. Walking about 1.8–2.0 Hz (*ASSUMPTION*, standard gait texts). |
| Step length | Relative to leg length and conditioned on speed | Example: 3.0 Hz × 1.50 m = 4.5 m/s |
| Duty factor and flight fraction | Stance time ÷ stride time; walk > 0.5 with double support; run < 0.5 with flight | Running duty factor falls from about 0.45 at slow speeds to about 0.28 near 6.1 m/s (search excerpt attributing this to van Oeveren et al. 2021, *ASSUMPTION* until read). Recreational runners at 2.1–2.6 m/s: 42.5–56.5% (PMC11571469, citing Bonnaerens et al. and van Oeveren et al.). |
| Pelvis vertical phase | Phase of the pelvis-height minimum relative to mid-stance | Walk: center of mass **highest** at mid-stance (inverted pendulum). Run: **lowest** at mid-stance (spring-mass) (Farley & Ferris 1998). |
| Pelvis vertical excursion | Relative to leg length | From the corpus |
| Arm/leg phase | Cross-correlation lag between arm swing and the ipsilateral thigh | About 180° (from the corpus) |
| Pelvis/thorax counter-rotation | Phase between pelvis yaw and chest yaw | From the corpus |

**Worked prediction on the shipped run** (from `projects/werewolf/export/game-drop/clips_protagonist_human.py`: `cycle 12` frames at 24 fps, `stride 2.10` m, `stance 0.30`, and the reach clamp lowers the hips 95 mm):
- 2 steps in 0.5 s gives **4.0 steps/s (240 spm)**, with **1.05 m steps**.
- Contact is 0.30 × 0.5 s = **0.15 s**.
- At 4.2 m/s, the literature above implies about 2.8–3.0 steps/s and about 1.4–1.5 m steps. The clip therefore takes short, fast steps in a crouch. Its pelvis phase is already correct (lowest at mid-stance), so that check would *pass*.
- This is a **prediction** of which Tier-1 metric fails, not proof that it is what the artist saw. E3 settles it.

### 6.2 Tier 2: signal-level naturalness (whole body, no corpus lookup)

- **Foot skate** (the standard motion-generation metric): the fraction of frames in which a foot is in contact (height < 5 cm) yet slides more than 2.5 cm. Defined in GMD (Karunratanakul et al. 2023) and reused widely. Kimodo's Apache-2.0 metrics module implements `foot_skate_ratio`, `foot_skate_from_height` and `foot_contact_consistency`, so the definitions can be reused.
- **Physical Foot Contact (PFC)** (EDGE, Tseng et al. CVPR 2023, code MIT): relates horizontal and vertical root acceleration to foot velocities. It catches a body accelerating while no foot is planted.
- **Smoothness:** **SPARC** (spectral arc length) and **LDLJ** (log dimensionless jerk) on end-effector speed profiles. Balasubramanian et al. 2015 show these two are the valid and consistent smoothness measures. They catch linear-interpolation kinks and jitter.
- **Spectral richness:** the share of per-joint angular-velocity power above the first two harmonics of the gait frequency. A sum-of-sinusoids procedural cycle packs its energy into one or two harmonics, while mocap spreads it. The metric is two-sided and checked against the corpus band.
- **Harmonic ratio** of pelvis acceleration (Menz et al. 2003: even over odd harmonic amplitudes in the anteroposterior and vertical axes, inverted mediolaterally). *ASSUMPTION:* a perfectly mirrored procedural cycle scores **implausibly high**, since it is too symmetric. Human mocap has finite harmonic ratios, so the check is two-sided.

### 6.3 Tier 3: distance to the reference corpus (all actions, including non-cyclic clips)

- **Pose nearest-neighbor distance.**
  - Build per-frame features in the Holden-style motion-matching layout: joint positions in the root frame plus joint velocities, on a shared set of about 20 joints, normalised by leg length.
  - Put them in a KD-tree over the corpus clips of the same action class.
  - Report p50 and p95 nearest-neighbor distances. The threshold is the corpus leave-one-clip-out p95.
  - This catches "never seen in human motion" poses. It is deterministic, and scipy or numpy is enough.
- **Fréchet distance on hand-crafted features.** This is FID_k and FID_g from AI Choreographer / AIST++ (Li et al. ICCV 2021), which uses **no learned encoder**:
  - a **kinetic** extractor (Onuma et al. 2008, FMDistance) that gives a 72-D vector of velocity and acceleration energies;
  - a **geometric** extractor (Müller et al. 2005, relational features) that gives a 33-D boolean vector.
  - Both are implemented in **fairmotion (BSD license, VERIFIED)**.
  - Compute over sliding windows of the clip versus the corpus windows.
- **Distribution distances:** 1-D Wasserstein distance between the clip's and the corpus's per-joint |angular speed| and |angular acceleration| distributions, per action class.
- **Kinematic bands:** knee-flexion curve over the gait cycle against the corpus mean ± k·σ (swing peak, stance flexion), and hip, knee and ankle ranges of motion. This also covers the new 115° knee cap `bce96d6` added for the jeans.

**Learned FID (optional, later).** A TMR-style encoder (e.g. NVIDIA's `TMR-SOMA-RP-v1`) would give a learned FID. *ASSUMPTION:* its license. It is not required for the gate.

### 6.4 Quadruped gate

Use Hildebrand's symmetric-gait parameters:
- **duty factor** (walk > 0.5);
- **lateral phase lag**: lateral-sequence walk; a trot pairs the diagonals at about 0.5 phase; a transverse gallop has a forelimb-initiated flight phase (eLife 2017, "Work minimization accounts for footfall phasing in slow quadrupedal gaits"; Hildebrand's framework);
- plus the §6.2 smoothness and foot-skate metrics.

Numeric bands for canids are an *ASSUMPTION* to be pulled from the literature when the gate is built.

### 6.5 Where it lives

A new `animation_quality` check next to `animation_check`. It is pure numpy over baked world-space joint samples, so it runs headless.
- Corpus features are cached once as `.npz` under `datasets/` (derived statistics only; the raw clips are not redistributed).
- Reports quote each metric against its band, following the house style.

---

## 7. Retarget pipeline design against the current `rigforge_retarget`

The current command is kept as the "bake constraints to FK" core. Around it:

1. **Source adapter (new, data-only).**
   - Per-convention **mapping presets** passed through the existing `mapping` dict: `cmu_cgspeed`, `cmu_asf`, `mixamo`, `soma77`, `ue_mannequin`, `quaternius_ual`, `rokoko`.
   - The SOMA preset must map `Hips→torso`, skip `Root`, and map `LeftLeg→thigh_fk.L` and `LeftShin→shin_fk.L`.
   - The CMU preset maps `Spine1` to `chest` rather than dropping it.
   - Presets are verified by E1/E2/E4 against truth joint positions.
2. **Time normalisation (new).** Resample to the scene fps before baking, with an explicit `source_fps` (120 for CMU, 30 for Mixamo/Kimodo) and slerp between frames.
3. **Rest calibration (new, the core fix).**
   - Put the source in its T-pose frame (cgspeed frame 1, SOMA `--bvh_standard_tpose`, Mixamo's rest) and the target in a matching T-pose.
   - Compute per-pair offsets `R_off = inv(R_src_rest_world) · R_tgt_rest_world`.
   - Apply `R_tgt(t) = R_src(t) · R_off`, either with `COPY_ROTATION` in an offset mix mode or by direct matrix evaluation per frame. That is deterministic and still bakes with `visual_keying`.
   - *ASSUMPTION:* which Blender constraint mix mode reproduces this exactly. E1 settles it numerically.
4. **Root handling (new).** Extract horizontal root travel to a report value (`natural_speed_mps` → `manifest.walk_speed_mps`), then zero it to satisfy the in-place contract. Keep vertical pelvis motion. For loops, detect heading from travel (the Mixamo fixture is yawed 30° for this reason).
5. **Legs in IK space (new, needed for proportions and the digitigrade werewolf).**
   - Instead of copying thigh, shin and foot FK rotations, drive the leg **IK targets** from the source foot world positions, scaled by the leg-length ratio (not the whole-body height ratio), plus pole targets from the source knee.
   - During detected contacts, **pin** the targets (reusing the foot-lock logic `rigforge_walk` already has).
   - This is also what lets Form C's `foot_metatarsal` get a solved pose.
6. **Cycle extraction for loops (new).** Find the frame pair with the smallest pose distance at the same gait phase (contact events), crop, cross-fade the seam over N frames, then run the existing seam-closure gate.
7. **Gates:** existing (slide, seams, stretch) plus the new §6 `animation_quality` gate.
8. **Provenance manifest (new).** Every shipped clip records its source dataset, clip ID, license and required credit line, so the credits screen for CC BY items (100STYLE, CC-BY MPFB packs) and a possible Llama notice are generated automatically.

---

## 8. Ranked recommendation per need

| Need | 1st | 2nd | 3rd | Avoid |
|---|---|---|---|---|
| **Humanoid locomotion** (idle/walk/run/sprint/crouch) | 100STYLE Neutral and Crouched (CC BY) | CMU walks and runs (cgspeed) | Mixamo | LaFAN1, Bandai-Namco, AMASS (NC) |
| **Combat** (attacks, heavy, counter, dodge, hit, stagger, death) | Mixamo (the coverage leader; *ASSUMPTION* on the exact catalog) | Quaternius UAL free tier (CC0) | CMU boxing and kicks; Kimodo for `counter` | ActorCore (ambiguous), Truebones |
| **Story clips** (talk/feed/transform/bite; climb) | talk: CMU conversation trials; climb: Mixamo or UAL | feed/transform/bite: **Kimodo-SOMA-RP**, 5–10 samples each, gated, artist-picked | Mixamo zombie bite as a fallback for `bite` | HY-Motion (territory), MDM-family (AMASS) |
| **Quadruped wolf** | Procedural plus the Hildebrand-gated quadruped gate | Quaternius CC0 wolf/husky clips as reference or seed | — | MANN dog (NC), Truebones, AnyTop |
| **Digitigrade werewolf** | Human mocap with IK-space leg retarget (§7.5) plus 100STYLE Dinosaur/Zombie/Heavyset flavor | Kimodo "hunched beast" prompts | — | — |
| **Human base mesh** | **MPFB2** (CC0 outputs, headless, rigs matching CMU and Mixamo, CC0 clothes) | CharMorph Vitruvian (CC0) | Blender Studio base meshes for sculpt detail | MB-Lab (AGPL), Human Generator (paid) |
| **Stylized base** | Quaternius Universal Base Characters (CC0) | Blender Studio stylized bases (CC0) | — | — |

---

## 9. Settling experiments (ordered; costs are estimates)

| # | Experiment | Settles | Cost | Needs from the user |
|---|---|---|---|---|
| **E1** | Retarget the committed synthetic fixtures (`walk_cmu_zup_in120.bvh`, `walk_mixamo_yup_cm30.bvh`) onto the protagonist rig. Measure per-joint world-position error against `truth()` (normalised by leg length), forearm and foot twist error, hip travel, and duration after fps handling. Then repeat with the rest-calibration and fps fixes. | §1.2 risks 1 and 3, and the `COPY_ROTATION` mix mode in §7.3 | About 0.5 day of agent time, headless, no downloads | Nothing |
| **E2** | The same on 3–5 real cgspeed CMU clips (e.g. 09_xx runs, 02_xx walks, 18_08 conversation, 79_38 drinking) | CMU cleanup load, the Spine1 preset, real rest poses | About 2 h plus one cgspeed archive download | Approve the download (file and size named when asking) |
| **E3** | Build the §6 gate. Corpus: 100STYLE Neutral/Crouched (per-style BVH files, not the 1.47 GB zip) plus CMU runs and walks, retargeted to the forge rig. Score today's procedural walk/run/sprint against held-out mocap. | Whether the gate separates procedural from mocap, and whether cadence/step length (§6.1) is the run's failure | 1.5–2 days | Approve the downloads |
| **E4** | Install Kimodo (Apache-2.0 code; SOMA-RP weights). Generate 5 samples each of `feed`, `transform`, `bite`, `counter`, `talk`, export BVH, retarget with the `soma77` preset, gate, and show the artist. | §5 viability; the SOMA mapping hazard in §1.2 | About 0.5 day setup plus multi-GB downloads (*ASSUMPTION*: about 16 GB for Llama-3-8B, about 1 GB for Kimodo) | A Hugging Face account and accepting Meta's Llama 3 gate, which you have to do yourself; approve the downloads |
| **E5** | Install MPFB2 into Blender 5.0. Script the protagonist headless: `create_human` → macro fit to reference landmarks → `game_engine` or Rigify rig → a proxy for LOD → CC0 clothes → forge gates → Godot export. Put it side by side with the current mesh. | Blender 5.0 compatibility, face and UV quality at semi-realistic distance, and the pipeline fit | About 1 day; the MPFB extension plus the system-assets pack download | Approve the downloads |
| **E6** | Mixamo catalog audit: search the 23 contract names (and synonyms) and record hits and the "In Place" toggle | §2.3 Mixamo column | About 30 min | You create or sign in to an Adobe ID. I don't create accounts. |
| **E7** | Download the Quaternius UAL1 and UAL2 free tiers (14.5 MB + 10.3 MB, CC0). List the clips and the rig bone names. | §2.3 UAL column; the UE-root hazard | About 15 min | Approve the downloads |
| **E8** | Quaternius wolf and husky clips → explicit mapping onto forge's Rigify quadruped. Score them with the §6.4 gate and compare with the procedural wolf. | Whether CC0 animal clips beat procedural | About 0.5 day | Approve the download |

---

## 10. Sources

**Repo (read 2026-09-24)**
- `addon/forge/tools/rigforge_anim.py` (lines 1–60 and 5248–5885)
- `addon/tests/headless_phase5.py` (lines 683–815)
- `addon/tests/fixtures/mocap/make_bvh_fixtures.py`
- `addon/forge/tools/rigforge_rig.py` (METARIG_OPS)
- `projects/werewolf/design/requirements.md`
- `projects/werewolf/design/review-log.md`
- `projects/werewolf/export/game-drop/clips_protagonist_human.py`
- `werewolf/docs/design.md`
- `docs/research/hunyuan-license-review.md`
- `docs/architecture.md`

**Mocap licenses and content**
- CMU: http://mocap.cs.cmu.edu/ (homepage and FAQ), full listing http://mocap.cs.cmu.edu/search.php?subjectnumber=%25&motion=%25
- cgspeed BVH README: https://sites.google.com/a/cgspeed.com/cgspeed/motion-capture/the-motionbuilder-friendly-bvh-conversion-release-of-cmus-motion-capture-database/readme-file-for-the-bvh-conversion-release
- 100STYLE: https://zenodo.org/records/8127870 · https://www.ianxmason.com/100style/ · https://github.com/orangeduck/100style-retarget
- CC BY 4.0 legal code: https://creativecommons.org/licenses/by/4.0/legalcode.en
- Mixamo FAQ: https://helpx.adobe.com/creative-cloud/faq/mixamo-faq.html
- Mixamo Additional Terms: https://wwwimages2.adobe.com/content/dam/cc/en/legal/servicetou/Mixamo-Addl-Terms-en_US-20210623.pdf
- Adobe community licensing FAQ: https://community.adobe.com/t5/mixamo-discussions/mixamo-faq-licensing-royalties-ownership-eula-and-tos/td-p/13234775
- Adobe community animation suggestions (2023): https://community.adobe.com/questions-696/a-few-animation-suggestions-589498
- Quaternius UAL: https://quaternius.itch.io/universal-animation-library · https://opengameart.org/content/universal-animation-library · https://opengameart.org/content/universal-animation-library-2 · https://quaternius.com/packs/universalanimationlibrary2.html · https://digitalproduction.com/2026/02/10/130-animations-one-rig-zero-drama/
- Rokoko: https://www.rokoko.com/resources/download-263-rokoko-motion-capture-assets · https://www.rokoko.com/terms · https://cdn.rokoko.com/legal/rokoko-studio/rokoko_studio_eula_v2.pdf
- ActorCore / Reallusion EULA: https://actorcore.reallusion.com/eula · https://www.reallusion.com/Content/EULA/EULA.htm
- HDM05: https://www.re3data.org/repository/r3d100011968
- LaFAN1: https://github.com/ubisoft/ubisoft-laforge-animation-dataset
- ZeroEGGS: https://github.com/ubisoft/ubisoft-laforge-ZeroEGGS
- Bandai-Namco: https://github.com/BandaiNamcoResearchInc/Bandai-Namco-Research-Motiondataset
- AMASS license: https://amass.is.tue.mpg.de/license.html
- SMPL license: https://smpl.is.tue.mpg.de/modellicense.html
- SFU: https://mocap.cs.sfu.ca/
- Motorica: https://github.com/simonalexanderson/MotoricaDanceDataset
- BONES-SEED: https://huggingface.co/datasets/bones-studio/seed

**Quadruped**
- Quaternius animals: https://quaternius.com/packs/ultimateanimatedanimals.html · https://poly.pizza/bundle/Animated-Animal-Pack-ILAPXeUYiS · https://quaternius.itch.io/lowpoly-animated-animals
- AI4Animation README (dog data CC BY-NC): https://github.com/sebastianstarke/AI4Animation
- Truebones: https://truebones.gumroad.com/l/skZMC · https://truebones.gumroad.com/l/gnwcyc
- AnyTop: https://github.com/Anytop2025/Anytop
- Quadruped gait: https://elifesciences.org/articles/29495

**Base meshes**
- MPFB FAQ: https://static.makehumancommunity.org/mpfb/faq/use_in_closed_source.html · https://static.makehumancommunity.org/mpfb/faq/is_it_really_free.html · https://static.makehumancommunity.org/mpfb/faq/can_i_sell_models.html
- MPFB asset packs: https://static.makehumancommunity.org/assets/assetpacks.html
- MPFB concepts: https://static.makehumancommunity.org/mpfb/docs/assets/concept_proxymeshes.html · https://static.makehumancommunity.org/mpfb/docs/assets/concept_basemesh_and_helpers.html
- MPFB repo (rigs, `LICENSE.ASSETS.md`, `docs/services/humanservice.md`, `test/execute_tests_headless.bash`): https://github.com/makehumancommunity/mpfb2
- Old MakeHuman license: http://www.makehumancommunity.org/content/license.html
- Anny: https://github.com/naver/anny
- Quaternius Universal Base Characters: https://quaternius.com/packs/universalbasecharacters.html
- Blender Studio Human Base Meshes: https://www.cgchannel.com/2023/06/download-blender-studios-free-human-base-meshes/ · https://archive.org/details/human-base-meshes-bundle-v1.0.0
- CharMorph: https://github.com/Upliner/CharMorph
- MB-Lab licensing: https://mb-lab-docs.readthedocs.io/en/latest/license.html
- Human Generator pricing: https://humgen3d.com/pricing

**Generative**
- Kimodo: https://github.com/nv-tlabs/kimodo · https://huggingface.co/nvidia/Kimodo-SOMA-RP-v1.1 · https://research.nvidia.com/labs/sil/projects/kimodo/docs/user_guide/output_formats.html · https://research.nvidia.com/labs/sil/projects/kimodo/docs/benchmark/metrics.html
- NVIDIA Open Model License: https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/
- Llama 3 license: https://raw.githubusercontent.com/meta-llama/llama3/main/LICENSE
- HY-Motion: https://huggingface.co/tencent/HY-Motion-1.0 (`LICENSE.txt`)
- MDM: https://github.com/GuyTevet/motion-diffusion-model
- MotionGPT: https://github.com/OpenMotionLab/MotionGPT
- MoMask: https://github.com/EricGuo5513/momask-codes
- T2M-GPT: https://github.com/Mael-zys/T2M-GPT
- OmniControl: https://github.com/neu-vi/OmniControl
- CAMDM: https://github.com/AIGAnimation/CAMDM
- HumanML3D: https://github.com/EricGuo5513/HumanML3D

**Quality metrics and biomechanics**
- AI Choreographer (FID_k/FID_g, feature extractors): https://arxiv.org/html/2101.08779
- fairmotion (BSD): https://github.com/facebookresearch/fairmotion
- GMD foot-skate ratio: https://arxiv.org/pdf/2305.12577
- EDGE PFC: https://github.com/Stanford-TML/EDGE
- SPARC and LDLJ: https://link.springer.com/article/10.1186/s12984-015-0090-9
- Harmonic ratio: https://pmc.ncbi.nlm.nih.gov/articles/PMC3032432/
- Running cadence and duty factor: https://pmc.ncbi.nlm.nih.gov/articles/PMC5685089/ · https://www.tandfonline.com/doi/full/10.1080/14763141.2021.1873411 · https://pmc.ncbi.nlm.nih.gov/articles/PMC11571469/ · https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0184273
- Walk/run center-of-mass mechanics (Farley & Ferris 1998): https://journals.biologists.com/jeb/article-abstract/201/21/2935/7897/
- Godot 3D retargeting: https://docs.godotengine.org/en/stable/tutorials/assets_pipeline/retargeting_3d_skeletons.html
