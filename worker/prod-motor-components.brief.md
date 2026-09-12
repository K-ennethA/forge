# Task: populate MOTOR_COMPONENTS in service/electronics_motors.py

You are editing exactly one file: `service/electronics_motors.py`.

The module docstring at the top of the file documents the entry shape.
DO NOT change the module docstring. DO NOT change anything except the
`MOTOR_COMPONENTS = {}` dict, which you fill with 10 entries.

Each entry: a stable snake_case key mapping to a dict with EXACTLY these keys:
`name`, `kind`, `size_mm`, `voltage_v`, `current_ma`, `purchase_link`,
`purchase_note`, `caveats`.

Rules:
- `kind` must be one of: `motor`, `fan`, `driver`, `sensor`, `battery`.
  Use at least 4 different kinds across your 10 entries.
- `size_mm` is `[width, depth, height]`, realistic millimetre numbers.
- `voltage_v` is `[lo, hi]` workable supply range in volts.
- `current_ma` is the typical draw in milliamps (a number).
- `purchase_link` is an Adafruit SEARCH url, e.g.
  `https://www.adafruit.com/search?q=5v%20blower%20fan%2030mm` —
  never a product page, never anything with cart/checkout/buy in it.
- `purchase_note` and `caveats` are one honest sentence each.

The 10 entries to write (use realistic real-world specs for each):
1. `blower_fan_30mm` — 5 V 30x30x10 mm centrifugal blower fan (kind: fan)
2. `axial_fan_25mm` — 5 V 25x25x7 mm axial fan (kind: fan)
3. `coreless_motor_716` — 7x16 mm 3.7 V coreless DC motor (kind: motor)
4. `n20_gear_motor` — N20 micro metal gearmotor, 6 V (kind: motor)
5. `mosfet_driver_board` — logic-level MOSFET driver breakout (kind: driver)
6. `npn_transistor_2n2222` — 2N2222 NPN transistor for small loads (kind: driver)
7. `pir_sensor_am312` — AM312 mini PIR motion sensor (kind: sensor)
8. `ir_proximity_sensor` — IR reflective obstacle sensor module (kind: sensor)
9. `aaa_3cell_holder` — 3xAAA battery holder with switch, 4.5 V (kind: battery)
10. `lipo_1s_500` — 1S 500 mAh LiPo pouch cell, 3.7 V (kind: battery)

`caveats` should say the thing that bites beginners: e.g. a PIR sees warm
moving bodies, not insects; a coreless motor must not be stalled; a LiPo
needs a protection/charger board; a transistor needs a base resistor.

Write plain ASCII Python. Keep the file importable (no syntax errors).
