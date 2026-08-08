; tiny square, relative extrusion (Bambu default)
G21
G90
M83
G92 E0
; CHANGE_LAYER
; Z_HEIGHT: 0.2
; FEATURE: Outer wall
G1 X10 Y10 F6000
G1 X20 Y10 E0.5
G1 X20 Y20 E0.5
G1 X10 Y20 E0.5
G1 X10 Y10 E0.5
