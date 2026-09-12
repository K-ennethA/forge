"""Motor, fan and sensor component entries for powered devices.

The device lane's sibling of the LED/switch/cell entries in components.py:
data only, no behaviour.  Each entry describes one real, commonly available
part an artist can buy -- a blower fan for an ankle-worn bug deterrent, the
MOSFET that lets a coin-cell-class controller switch it, the sensor tier
that decides when to gust.  ``purchase_link`` is an informational search
URL and never a cart, checkout or buy link: the owner's law is links to
products, never auto purchase.

Entry shape (all sizes millimetres, electrical units in the key names):

    {"name": str,            # what the artist calls it
     "kind": str,            # motor | fan | driver | sensor | battery
     "size_mm": [w, d, h],   # bounding box of the part
     "voltage_v": [lo, hi],  # workable supply range
     "current_ma": float,    # typical draw at the nominal voltage
     "purchase_link": str,   # https search URL, informational only
     "purchase_note": str,   # one honest sentence about buying it
     "caveats": str}         # the thing that bites first-time users

MOTOR_COMPONENTS maps a stable snake_case key to one entry.
"""

MOTOR_COMPONENTS = {
    "blower_fan_30mm": {
        "name": "Blower Fan 30mm",
        "kind": "fan",
        "size_mm": [30, 30, 10],
        "voltage_v": [4.5, 5.5],
        "current_ma": 100,
        "purchase_link": "https://www.adafruit.com/search?q=5v+blower+fan+30mm",
        "purchase_note": "This fan is suitable for small projects.",
        "caveats": "Ensure the fan is not obstructed to prevent overheating."
    },
    "axial_fan_25mm": {
        "name": "Axial Fan 25mm",
        "kind": "fan",
        "size_mm": [25, 25, 7],
        "voltage_v": [4.5, 5.5],
        "current_ma": 80,
        "purchase_link": "https://www.adafruit.com/search?q=5v+axial+fan+25mm",
        "purchase_note": "This fan is ideal for cooling small electronics.",
        "caveats": "Be cautious of dust buildup that can reduce airflow."
    },
    "coreless_motor_716": {
        "name": "Coreless Motor 7x16mm",
        "kind": "motor",
        "size_mm": [7, 7, 16],
        "voltage_v": [3.0, 4.2],
        "current_ma": 120,
        "purchase_link": "https://www.adafruit.com/search?q=coreless+motor+7x16mm",
        "purchase_note": "This motor is great for small robotics projects.",
        "caveats": "Do not stall the motor to avoid damaging it."
    },
    "n20_gear_motor": {
        "name": "N20 Gear Motor",
        "kind": "motor",
        "size_mm": [12, 10, 26],
        "voltage_v": [3.0, 6.0],
        "current_ma": 150,
        "purchase_link": "https://www.adafruit.com/search?q=N20+gear+motor",
        "purchase_note": "This motor is perfect for small robots with gears.",
        "caveats": "Ensure the gear ratio is appropriate for your application."
    },
    "mosfet_driver_board": {
        "name": "MOSFET Driver Board",
        "kind": "driver",
        "size_mm": [25, 25, 5],
        "voltage_v": [3.3, 5.5],
        "current_ma": 1000,
        "purchase_link": "https://www.adafruit.com/search?q=MOSFET+driver+board",
        "purchase_note": "This board is useful for driving high-power MOSFETs.",
        "caveats": "Ensure the MOSFET is properly heat-sinked."
    },
    "npn_transistor_2n2222": {
        "name": "NPN Transistor 2N2222",
        "kind": "driver",
        "size_mm": [5, 4, 5],
        "voltage_v": [0.3, 24],
        "current_ma": 800,
        "purchase_link": "https://www.adafruit.com/search?q=NPN+transistor+2N2222",
        "purchase_note": "This transistor is suitable for small switching applications.",
        "caveats": "Use a base resistor to limit current."
    },
    "pir_sensor_am312": {
        "name": "PIR Sensor AM312",
        "kind": "sensor",
        "size_mm": [10, 23, 12],
        "voltage_v": [2.7, 12],
        "current_ma": 0.1,
        "purchase_link": "https://www.adafruit.com/search?q=PIR+sensor+AM312",
        "purchase_note": "This sensor detects motion within a range.",
        "caveats": "The sensor detects warm bodies, not insects."
    },
    "ir_proximity_sensor": {
        "name": "IR Proximity Sensor",
        "kind": "sensor",
        "size_mm": [20, 20, 10],
        "voltage_v": [3.3, 5.5],
        "current_ma": 20,
        "purchase_link": "https://www.adafruit.com/search?q=IR+proximity+sensor",
        "purchase_note": "This sensor detects objects within a short range.",
        "caveats": "Ensure the sensor is not obstructed by ambient light."
    },
    "aaa_3cell_holder": {
        "name": "AAA 3-Cell Holder",
        "kind": "battery",
        "size_mm": [52, 38, 13],
        "voltage_v": [3.0, 4.5],
        "current_ma": 1000,
        "purchase_link": "https://www.adafruit.com/search?q=AAA+3-cell+holder",
        "purchase_note": "This holder is suitable for small battery-powered devices.",
        "caveats": "Ensure the batteries are of the same type and voltage."
    },
    "lipo_1s_500": {
        "name": "LiPo 1S 500mAh",
        "kind": "battery",
        "size_mm": [30, 35, 6],
        "voltage_v": [3.0, 4.2],
        "current_ma": 500,
        "purchase_link": "https://www.adafruit.com/search?q=LiPo+1S+500mAh",
        "purchase_note": "This battery is suitable for small electronic devices.",
        "caveats": "Use a protection/charger board to prevent damage."
    }
}
