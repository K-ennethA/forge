# Measure a rig's reach off the rig, not off a formula

## WHEN

Any threshold or clip-authoring step needs to know how far a limb (a leg, an
arm) can actually go — for a reach cap, a stretch budget, or a crouch/jump
extension limit.

## THE RECIPE

1. **Ask the rig, don't sum the rest chain.** Push the IK target a long way
   past the limb's reach, key `IK_Stretch = 0`, evaluate the solver, and read
   back the hip-to-ankle (or shoulder-to-wrist) distance it actually
   delivers. That number **is** the limit.
2. Do this as **two evaluations**, before anything is keyed for real — one to
   measure, one to author against the measurement.
3. Do not derive the limit by summing the rest-pose bone lengths along a
   **pre-bent** chain, and do not use a hip→ankle-distance / rest-chain-length
   ratio as a proxy.

## WHY

`addon/forge/tools/rigforge_anim.py` (comment, ~line 2117): *"how far this
leg actually goes, asked of the rig itself... Not the rest chain: `jump_legs`
sums hip-to-knee-to-ankle along the pre-bent rest pose, and the IK
straightens past that — measured on the synthetic rig, **528.0 mm** delivered
against a **510.3 mm** summed chain, a **3.5%** gap that a clamp built on the
chain can never close."*

`docs/lane-conventions.md` names the second dead metric and why it fails
too: a hip→ankle/rest-chain ratio *"saturates once `IK_Stretch = 0`"* — once
stretch is disabled (which it always should be for a planted limb, see
`plant_ik_stretch`), the ratio stops moving and stops being informative
exactly when it matters most.

## REJECTED

- **Summing the pre-bent rest chain's segment lengths** — measured 3.5% short
  of what the solver actually delivers (510.3 mm vs. 528.0 mm on the
  synthetic rig).
- **Hip→ankle-distance / rest-chain-length ratio** as a reach proxy —
  saturates once `IK_Stretch` is keyed to 0, which is the normal case for a
  planted limb, so the metric goes dead exactly when the rig is configured
  correctly.

## SOURCE

- `addon/forge/tools/rigforge_anim.py` — inline comment above the leg-reach
  probe (`max_span` block, ~line 2117–2127), documenting both the method used
  and the two rejected alternatives with their measured numbers.
- `docs/lane-conventions.md` — "Measurement conventions" section, "Leg
  reach" entry (names both dead metrics explicitly).
