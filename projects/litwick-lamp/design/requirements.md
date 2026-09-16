# Litwick Lamp — requirements (rev 2: AAA power)

Push the flame down to toggle the light on and off. Printer: Elegoo Centauri Carbon,
PLA — 256 mm bed, and this design stays well under it (prints in one piece, no split
needed).

## 1. Size and form — CHANGED from rev 1
- Base diameter: **46 mm** (was 40 mm — the 2×AAA holder needs the extra room; see
  section 4). Still reads as roughly the same slim waist you approved.
- Body height: **95 mm** (was 75 mm) — the AAA pack stands upright below the switch,
  and this is where the extra height goes.
- Top opening: 1.5× base = 69 mm
- Flame visible height above the neck: ~45 mm — unchanged
- Assembled total height: ~140 mm (95 body + 45 flame) — still a normal shelf size
- Body profile: same soft rounded taper as the stub, just taller
- Wall thickness: 3 mm — unchanged

## 2. The mechanism — unchanged from rev 1
- Press travel: 2.1 mm total (0.3 mm free play + 1.5 mm switch stroke + 0.3 mm overtravel)
- Hard stop at 2.1 mm on the guide's rim; overtravel clamped to 0.3 mm by the switch's
  own datasheet
- Guide sleeve: 12 mm long, 11.8 mm OD, 6.4 mm bore
- Plunger: 20.3 mm long, 6 mm **keyed** stem (confirmed — Q5), 10.4 mm retention flange
- Latching switch: stays down once lit; flame sits ~1.5 mm lower while glowing —
  correct behaviour, not a fault
- Press force: ~250 gf

## 3. Electronics — CHANGED from rev 1
- LED: 5 mm diffused, **warm white** (confirmed — Q2). Electrically the same family as
  cool white (~3 V forward drop), so this is a straight part swap, nothing structural.
- Power: **2×AAA battery holder** (confirmed — Q3), 3 V nominal, 1000 mAh — five times
  a coin cell's capacity
- Switch: 6×6 mm self-locking tactile (push-on/push-off) — unchanged
- **Resistor: 22 Ω, required.** Unlike the coin cell, this holder has almost no
  internal resistance to protect the LED, and a fresh pair can read enough above
  nominal to push far more current than the LED or the switch (rated 50 mA) can
  safely take. The calculator flagged 400 mA with no resistor — a real risk, not a
  formality. 22 Ω removes it at no visible cost in brightness.
- Current: ~20 mA. Runtime: ~35 hours of light per battery set.
- Wiring: one loop, in series — cell(+) → switch → resistor → LED(+, long leg) →
  LED(−, short leg) → cell(−).

## 4. Assembly and access — CHANGED from rev 1
- Four printed/bought pieces: **body** (LED pocket, switch seat, plunger guide, and
  now a standing bay for the AAA holder), **base door** (screws/snaps on, holds the
  AAA holder — confirmed removable, Q4), **plunger**, **flame** (press-fits on last)
- AAA holder: 52 × 26 × 14 mm envelope, stood on end in the body's lower cavity, ~4 mm
  of clearance around it for the wires and for lifting it in/out through the door
- The base grew from 40 → 46 mm specifically to give that clearance — without it,
  there's under 1 mm of margin around the holder, which is not buildable reliably
- CR2032 holder is no longer used; ignore that line from rev 1

## 5. Resolved from rev 1 (all five questions answered)
- Q1 size: 75/40 approved, but see section 1 — size changed again for the AAA swap
- Q2 color: warm white — confirmed
- Q3 power: 2×AAA — confirmed, drives sections 1, 3 and 4
- Q4 battery access: removable door — confirmed
- Q5 keyed stem: confirmed
