; full circle made of two G3 half-arcs (radius 10 about origin), relative E
G21
G90
M83
G92 E0
; CHANGE_LAYER
; Z_HEIGHT: 0.2
; FEATURE: Outer wall
G1 X10 Y0 F6000
G3 X-10 Y0 I-10 J0 E2.0
G3 X10 Y0 I10 J0 E2.0
