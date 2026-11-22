"""Study catalogue: what each study type needs, its boundary condition types and parameters.

This single description drives the GUI forms, input validation and the LLM tool descriptions.
Temperatures exposed to the user are in degC; solvers convert where needed.
"""
from __future__ import annotations

from . import materials

P = lambda key, label, unit, default, **kw: {"key": key, "label": label, "unit": unit, "default": default, **kw}  # noqa: E731

STUDIES = {
    "heat_conduction": {
        "label": "Heat transfer in solids",
        "icon": "thermo",
        "solver": "builtin_fem",
        "solver_label": "Built-in FEM (steady or transient conduction)",
        "description": "Temperatures in solid parts from heat sources and cooling, steady or over time.",
        "examples": ["heated_plate.step"],
        "face_bc_types": {
            "fixed_temperature": {
                "label": "Fixed temperature", "color": "#e4572e",
                "help": "Face held at a constant temperature (e.g. clamped to a cold plate).",
                "params": [P("T", "Temperature", "°C", 25.0)],
            },
            "convection": {
                "label": "Convection to air", "color": "#3a86ff",
                "help": "Heat lost to surrounding air. Natural convection h ≈ 5-10, fan-cooled h ≈ 20-100 W/m²K.",
                "params": [P("h", "Heat transfer coefficient", "W/m²K", 10.0, min=0),
                           P("T_inf", "Air temperature", "°C", 25.0)],
            },
            "radiation_convection": {
                "label": "Convection + radiation", "color": "#8338ec",
                "help": "Convection plus thermal radiation to surroundings (important in natural convection).",
                "params": [P("h", "Heat transfer coefficient", "W/m²K", 5.0, min=0),
                           P("T_inf", "Air temperature", "°C", 25.0),
                           P("emissivity", "Emissivity", "-", 0.85, min=0, max=1)],
            },
            "natural_convection": {
                "label": "Natural convection + radiation (still air)", "color": "#06aed5",
                "help": "No fan: the air around the part is still. The heat transfer coefficient is worked out on "
                        "every face from its size, its orientation to gravity and its temperature (textbook "
                        "correlations, about +-25 %), plus radiation to the room. Emissivity 0 = no radiation.",
                "params": [P("T_inf", "Room / air temperature", "°C", 25.0),
                           P("emissivity", "Emissivity (0 = no radiation)", "-", 0.85, min=0, max=1),
                           P("up", "Up direction (against gravity)", "", "+z",
                             options=["+z", "-z", "+y", "-y", "+x", "-x"])],
            },
            "heat_flux": {
                "label": "Heat flux", "color": "#ff006e",
                "help": "Heat flux INTO the part through the face, per unit area.",
                "params": [P("q", "Heat flux", "W/m²", 1000.0)],
            },
            "heat_load": {
                "label": "Heat load (total)", "color": "#fb5607",
                "help": "Total power INTO the part spread uniformly over the selected faces.",
                "params": [P("Q", "Power", "W", 5.0)],
            },
        },
        "default_face_bc": {"label": "Insulated (no heat flow)"},
        "body_roles": ["solid"],
        "body_params": [P("heat_W", "Heat generated", "W", 0.0, min=0)],
        "settings": [
            P("analysis", "Analysis", "", "steady", options=["steady", "transient"],
              help="steady: the final temperatures. transient: how the temperatures develop over time from a "
                   "uniform start temperature, all loads switched on at t = 0."),
            P("end_time", "End time", "s", 600.0, min=1e-6, max=1e9, show_if={"analysis": "transient"},
              help="How long the heating lasts, in seconds. Set it to the duty cycle you care about, or long enough "
                   "for the part to stop warming (several thermal time constants). The cost grows with it."),
            P("time_step", "Time step (0 = auto)", "s", 0.0, min=0, max=1e9, show_if={"analysis": "transient"},
              help="Size of each time step. 0 = end time / 200 (at most 20000 steps). Make it smaller only when the "
                   "temperature history looks stepped around a fast change such as a switching load; larger saves "
                   "time for slow, smooth heating."),
            P("T_initial", "Start temperature", "°C", 25.0, show_if={"analysis": "transient"},
              help="Temperature of every part at t = 0. Use the ambient or soak temperature the part starts from "
                   "(25 °C room, or a cold start such as -20 °C)."),
            P("n_frames", "Saved time points", "", 21, min=2, max=201, show_if={"analysis": "transient"},
              help="Temperature fields kept for the results time slider (the temperature-history plot uses every "
                   "step)."),
        ],
        "needs": ["at least one fixed_temperature, convection, radiation_convection or natural_convection face "
                  "(steady)"],
    },
    "vibration_modal": {
        "label": "Vibration (modal)",
        "icon": "wave",
        "solver": "builtin_fem",
        "solver_label": "Built-in FEM (modal analysis, quadratic elements)",
        "description": "Natural frequencies and mode shapes of solid parts.",
        "examples": ["camera_mount.step"],
        "face_bc_types": {
            "fixed_support": {
                "label": "Fixed support", "color": "#e4572e",
                "help": "Face held completely still (bolted or clamped to a much stiffer structure).",
                "params": [],
            },
            "added_mass": {
                "label": "Added mass", "color": "#8338ec",
                "help": "Mass of a component that is not modelled (battery, camera, PCB parts) on the face it is "
                        "mounted to. On one flat face it acts at its centre of gravity (set the CG height); on several "
                        "faces it is spread over them. Adds inertia only, no stiffness.",
                "params": [P("mass_kg", "Mass", "kg", 0.05, min=0, max=10000),
                           P("cg_height_mm", "CG height above the face", "mm", 0.0, min=0, max=10000,
                             help="Distance from the mounting face to the component's centre of gravity, e.g. half "
                                  "the height of a camera body. 0 = a thin layer on the face.")],
            },
        },
        "default_face_bc": {"label": "Free"},
        "body_roles": ["solid"],
        "body_params": [],
        "settings": [
            P("structural_solver", "Solver", "", "built-in", options=["built-in", "calculix"],
              help="Which program solves this study. Built-in: the workbench's own finite-element solver (always "
                   "available, free-free modes and added masses). CalculiX: the separate open-source CalculiX program "
                   "(install it from the Solve step): same mesh and loads, independent solver - use it to cross-check "
                   "the built-in results."),
            P("n_modes", "Number of modes", "", 10, min=1, max=50,
              help="How many natural frequencies to compute (lowest first)."),
            P("excitation_min_hz", "Excitation from", "Hz", 0.0, min=0, max=100000,
              help="Lowest frequency of the vibration source, e.g. motor speed in rpm / 60 at idle (0 = from zero)."),
            P("excitation_max_hz", "Excitation up to", "Hz", 0.0, min=0, max=100000,
              help="Highest frequency of the source, e.g. full-throttle rpm / 60 x number of blades for blade "
                   "passing. Modes within ±15 % of this range are flagged. Both 0 = no check; only 'from' = that "
                   "single frequency."),
            P("shake_axis", "Shake test (sine sweep)", "", "off", options=["off", "x", "y", "z"],
              help="Simulates a vibration-table test: the fixed supports are shaken along this axis with a sine of "
                   "constant acceleration, swept over the excitation range above (or around the computed modes "
                   "when no range is set). Gives the stress, movement and acceleration at every frequency and the "
                   "peaks at the resonances. Needs a fixed support; built-in solver only."),
            P("shake_accel_g", "Shake amplitude", "g", 1.0, min=0.001, max=1000,
              show_if={"shake_axis": ["x", "y", "z"]},
              help="Acceleration amplitude of the sine at the supports, in g (1 g = 9.81 m/s²). The response is "
                   "linear: twice the amplitude gives twice the stress."),
            P("damping_percent", "Damping", "% of critical", 2.0, min=0.05, max=50,
              show_if={"shake_axis": ["x", "y", "z"]},
              help="Damping ratio used for every mode. It sets the height of the resonance peaks (amplification "
                   "about 1 / (2 x damping): 2 % gives 25x). Typical: 0.5-2 % for welded or one-piece metal parts, "
                   "2-5 % for bolted assemblies and plastics, more with rubber mounts. Measure it if the peak "
                   "stress matters."),
        ],
        "needs": ["materials for all parts", "fixed supports for a mounted part (none = free-free modes)"],
    },
    "aeroelastic_flutter": {
        "label": "Flutter (structure + air)",
        "icon": "flutter",
        "category": "multiphysics",
        "physics": ["Structure: modal finite elements (built in)", "Air: unsteady strip aerodynamics (Theodorsen)"],
        "solver": "builtin_fem",
        "solver_label": "Built-in FEM modes coupled to strip aerodynamics (p-k method)",
        "description": "Aeroelastic stability of a wing, blade, fin or plate: the air speed at which the structure "
                       "starts to flutter or diverge, with the frequency and shape of the motion.",
        "examples": ["airplane_wing.step", "flutter_plate.step"],
        "face_bc_types": {
            "fixed_support": {
                "label": "Fixed support (root)", "color": "#e4572e",
                "help": "Face held completely still: the root of the wing, blade or fin where it is clamped or bolted.",
                "params": [],
            },
            "aero_surface": {
                "label": "Aerodynamic surface", "color": "#3a86ff",
                "help": "The faces the air flows over and that carry the air loads: the upper and lower skin of the "
                        "wing, blade or fin (select both sides). Faces not marked here (a root block, a pod, a "
                        "mount) feel no air. None marked = every face counts.",
                "params": [],
            },
            "added_mass": {
                "label": "Added mass", "color": "#8338ec",
                "help": "Mass of a component that is not modelled (a pod, tip tank, motor) on the face it is "
                        "mounted to: inertia only, no stiffness. Wing-mounted masses change the flutter speed a lot.",
                "params": [P("mass_kg", "Mass", "kg", 0.05, min=0, max=10000),
                           P("cg_height_mm", "CG height above the face", "mm", 0.0, min=0, max=10000,
                             help="Distance from the mounting face to the component's centre of gravity.")],
            },
        },
        "default_face_bc": {"label": "Free"},
        "body_roles": ["solid"],
        "body_params": [],
        "settings": [
            P("flow_direction", "Air flows towards", "", "+x", options=["+x", "-x", "+y", "-y", "+z", "-z"],
              help="The direction the air moves relative to the structure, along the chord of the wing (from the "
                   "leading edge to the trailing edge)."),
            P("span_axis", "Span direction", "", "y", options=["x", "y", "z"],
              help="The axis along the wing from root to tip. The air must flow across it. The aerodynamic faces "
                   "are cut into strips along this axis."),
            P("n_modes", "Number of modes", "", 8, min=2, max=40,
              help="Natural modes used for the structure (lowest first). Flutter usually involves the first bending "
                   "and torsion modes; more modes make little difference once these are in."),
            P("n_strips", "Strips along the span", "", 16, min=2, max=100,
              help="Number of strips the aerodynamic faces are cut into. 10-20 is enough for a wing."),
            P("altitude_m", "Altitude", "m", 0.0, min=-500, max=20000,
              help="Sets the air density and the speed of sound from the standard atmosphere (0 = sea level, "
                   "density 1.225 kg/m³). Flutter speeds are lower in denser air."),
            P("speed_max_ms", "Highest air speed", "m/s", 0.0, min=0, max=2000,
              help="Speeds from near zero up to this value are examined. 0 = automatic: up to Mach 0.6, the limit "
                   "of the incompressible aerodynamics used here."),
            P("design_speed_ms", "Design speed (for the margin)", "m/s", 0.0, min=0, max=2000,
              help="The highest speed the structure is used at (a dive or maximum speed). The result then states "
                   "the flutter margin = flutter speed / design speed; aircraft rules ask for 1.15 or more. "
                   "0 = no margin is computed."),
            P("damping_percent", "Structural damping", "% of critical", 0.0, min=0, max=20,
              help="Damping ratio of every mode. 0 gives the lowest (conservative) flutter speed; measured values "
                   "are 0.5-2 % for metal structures. Damping raises the flutter speed but cannot be predicted "
                   "reliably: use a measured value or 0."),
            P("finite_wing", "Finite-wing correction", "", "on", options=["on", "off"],
              help="Reduces the lift of every strip for the finite aspect ratio (a short wing carries less lift per "
                   "unit span than a 2-D airfoil): AR / (AR + 2). Standard 'modified strip theory'. Off = pure 2-D "
                   "strip theory, which gives lower (conservative) flutter speeds."),
            P("n_speeds", "Speeds examined", "", 100, min=20, max=400, adv=True,
              help="Number of air speeds between zero and the highest speed. The crossing is then refined exactly."),
        ],
        "needs": ["materials for all parts", "a fixed support at the root", "aerodynamic faces (the wing skin)"],
    },
    "static_structural": {
        "label": "Static structural",
        "icon": "beam",
        "solver": "builtin_fem",
        "solver_label": "Built-in FEM (linear static, quadratic elements)",
        "description": "Stress, deformation and safety factor under loads.",
        "examples": ["camera_mount.step"],
        "face_bc_types": {
            "fixed_support": {
                "label": "Fixed support", "color": "#e4572e",
                "help": "Face held completely still (bolted or clamped to a much stiffer structure).",
                "params": [],
            },
            "frictionless_support": {
                "label": "Frictionless support / pin", "color": "#f4a261",
                "help": "The face cannot move along its normal but may slide. On a flat face: a smooth support or a "
                        "symmetry plane. On a hole: a pin or bearing - the part can still turn about it.",
                "params": [],
            },
            "displacement": {
                "label": "Prescribed displacement", "color": "#e9c46a",
                "help": "The face moves by a given amount (blank = free in that direction), e.g. a press fit or a "
                        "known deflection.",
                "params": [P("ux_mm", "Displacement x (blank = free)", "mm", None),
                           P("uy_mm", "Displacement y (blank = free)", "mm", None),
                           P("uz_mm", "Displacement z (blank = free)", "mm", None)],
            },
            "force": {
                "label": "Force", "color": "#3a86ff",
                "help": "Total force on the selected faces, spread evenly over their area (e.g. a load hanging from "
                        "a hole, a person standing on a step).",
                "params": [P("fx_N", "Force x", "N", 0.0), P("fy_N", "Force y", "N", 0.0),
                           P("fz_N", "Force z", "N", -100.0)],
            },
            "pressure": {
                "label": "Pressure", "color": "#06d6a0",
                "help": "Pressure normal to the faces; positive pushes into the part (e.g. fluid pressure in a "
                        "vessel, wind load).",
                "params": [P("pressure_Pa", "Pressure", "Pa", 1e5)],
            },
        },
        "default_face_bc": {"label": "Free"},
        "body_roles": ["solid"],
        "body_params": [],
        "settings": [
            P("gravity", "Gravity", "", "off", options=["off", "-z", "+z", "-y", "+y", "-x", "+x"],
              help="Own weight of the parts (9.81 m/s² in the chosen direction)."),
            P("structural_solver", "Solver", "", "built-in", options=["built-in", "calculix"],
              help="Which program solves this study. Built-in: the workbench's own finite-element solver (always "
                   "available, free-free modes and added masses). CalculiX: the separate open-source CalculiX program "
                   "(install it from the Solve step): same mesh and loads, independent solver - use it to cross-check "
                   "the built-in results."),
        ],
        "needs": ["materials for all parts", "supports that hold the part", "at least one load (or gravity)"],
    },
    "cht_enclosure": {
        "label": "Electronics cooling",
        "icon": "fan",
        "solver": "openfoam_cht",
        "solver_label": "OpenFOAM chtMultiRegionSimpleFoam (conjugate heat transfer)",
        "description": "Air cooling of heated parts in a duct, enclosure or open air.",
        "examples": ["heatsink_duct.step"],
        "domain": {
            "enclosure_mode": {"label": "Air volume", "options": {
                "body": "One of the STEP bodies is the air volume (enclosure interior / duct)",
                "auto_box": "Create a box of air around the parts automatically"}, "default": "body"},
        },
        "face_bc_types": {
            "velocity_inlet": {
                "label": "Air inlet (fan / velocity)", "color": "#3a86ff",
                "help": "Air enters with a given speed normal to the face (or along a fixed axis), or a given "
                        "volume flow.",
                "params": [P("velocity", "Air speed", "m/s", 2.0, min=0, max=100),
                           P("flow_rate", "…or volume flow (overrides speed if > 0)", "m³/h", 0.0, min=0),
                           P("T", "Inlet air temperature", "°C", 25.0),
                           P("direction", "Flow direction", "", "normal",
                             options=["normal", "+x", "-x", "+y", "-y", "+z", "-z"],
                             help="normal: straight into the face (a fan, a duct). An axis: uniform flow along "
                                  "it, like wind - use this on a curved inlet (C-shaped air volume); the volume "
                                  "flow then counts the area seen along that axis.")],
                "face_kinds": ["enclosure"],
            },
            "pressure_outlet": {
                "label": "Air outlet / opening (open to the room)", "color": "#06d6a0",
                "help": "Open to the room at ambient pressure: air leaves here (with no fan, warm air leaves and "
                        "room air comes in through openings like this, e.g. vents).",
                "params": [P("T_backflow", "Ambient temperature (if air flows back in)", "°C", 25.0)],
                "face_kinds": ["enclosure"],
            },
            "slip_wall": {
                "label": "Open side / symmetry (slip, adiabatic)", "color": "#ced4da",
                "help": "Frictionless, adiabatic boundary: the sides of a box of free air, or a symmetry plane. "
                        "The sides of an automatic air box are treated like this unless you assign them.",
                "params": [], "face_kinds": ["enclosure"],
            },
            "wall_fixed_temperature": {
                "label": "Wall at fixed temperature", "color": "#e4572e",
                "help": "Enclosure wall or part face held at a fixed temperature.",
                "params": [P("T", "Temperature", "°C", 25.0)],
                "face_kinds": ["enclosure", "solid_outer"],
            },
            "wall_convection": {
                "label": "Wall losing heat to outside", "color": "#8338ec",
                "help": "Wall that exchanges heat with the room outside through a heat transfer coefficient.",
                "params": [P("h", "Heat transfer coefficient", "W/m²K", 5.0, min=0),
                           P("T_inf", "Outside temperature", "°C", 25.0)],
                "face_kinds": ["enclosure", "solid_outer"],
            },
        },
        "default_face_bc": {"label": "Adiabatic wall (automatic air box sides: slip)"},
        "body_roles": ["solid", "fluid"],
        "body_params": [P("heat_W", "Heat generated", "W", 0.0, min=0)],
        "settings": [
            P("air_flow", "Air flow", "", "fan", options=["fan", "natural"],
              help="fan: air is driven through an inlet (fan / duct) to an outlet. natural: no fan - the air only "
                   "moves because warm air rises (natural convection); air comes and goes through the openings "
                   "(outlet / opening faces, and the sides of an automatic air box)."),
            P("T_room", "Room temperature", "°C", 25.0, show_if={"air_flow": "natural"},
              help="Temperature of the room air entering through the openings (and of the sides of an "
                   "automatic air box)."),
            P("radiation", "Radiation between surfaces", "", "off", options=["off", "on"],
              help="on: heat radiated between the parts, the walls and out through the openings (fvDOM model). "
                   "Matters with no fan (often 30-50 % of the heat); slows the run down."),
            P("emissivity", "Surface emissivity", "-", 0.85, min=0.01, max=1, show_if={"radiation": "on"},
              help="All part and wall surfaces: ~0.85-0.95 painted, anodised, plastic, PCB; ~0.05-0.1 bare "
                   "polished aluminium or copper. Openings radiate like the room (1)."),
            P("fluid", "Coolant", "", "air", options=["air"], fluid_kinds=("gas", "liquid"),
              help="The fluid around the parts: a gas (air, nitrogen, helium) or a liquid (water, coolant ...), or one you added in "
                   "the materials library. It sets density, viscosity, specific heat and conductivity. A liquid is a forced flow "
                   "(Air flow: fan, i.e. a pump or inlet) with a constant density: natural convection needs a gas."),
            P("turbulence", "Flow model", "", "auto",
              options=["auto", "laminar", "kOmegaSST", "kOmega", "kEpsilon", "realizableKE", "SpalartAllmaras"],
              help="auto: chosen from the Reynolds number of the fin channels (laminar below ~2300)."),
            P("contact_resistance", "Contact resistance between touching parts", "K·cm²/W", 0.0, min=0, max=100,
              help="Thermal interface: 0 = perfect contact, ~0.1-0.5 thermal paste/pad, ~1-3 dry metal joint."),
            P("gravity", "Gravity direction", "", "-z", options=["off", "-x", "+x", "-y", "+y", "-z", "+z"],
              help="Which way is down, for natural convection (hot air rises against it). Set it to the real "
                   "orientation of the part (default -z). Choose off for fan-cooled cases where buoyancy is small."),
            P("analysis", "Analysis", "", "steady", options=["steady", "transient"],
              help="steady: the settled flow (iterations). transient: the flow over time from the start "
                   "(time steps), e.g. vortex shedding or a warm-up; much slower."),
            P("end_time", "End time", "s", 60.0, min=1e-6, max=1e7, show_if={"analysis": "transient"},
              help="How many seconds of real time to simulate (transient only). Cover the warm-up you care about; "
                   "the run cost grows in proportion."),
            P("time_step", "Time step (0 = auto)", "s", 0.0, min=0, max=1e6, show_if={"analysis": "transient"},
              help="0 = from the flow speed and the cell size (Courant number about 1), adjusted as the run goes."),
            P("n_frames", "Saved time points", "", 21, min=2, max=201, show_if={"analysis": "transient"},
              help="Fields kept for the results time slider (the history plots use every step)."),
            P("T_initial", "Initial temperature", "°C", 25.0,
              help="Temperature of the parts and the air at the start. Use the room temperature. For steady runs "
                   "it is only the starting guess: a value near the expected result converges slightly faster."),
            P("iterations", "Max iterations", "", 2000, min=50, max=100000, show_if={"analysis": "steady"},
              help="Upper limit of solver iterations for a steady run. It stops earlier once the temperatures stop "
                   "changing. Raise it (or use Continue) if the run ends not converged; lower it for previews."),
            P("n_procs", "CPU cores", "", 0, min=0, max=256,
              help="0 = Auto: all physical cores for large meshes, fewer for small ones."),
        ],
        "needs": ["fan: one velocity_inlet and one pressure_outlet; natural (no fan): gravity on, and an opening "
                  "(pressure_outlet or an automatic air box) or a cooled wall", "at least one body producing heat"],
    },
    "external_aero": {
        "label": "External aerodynamics",
        "icon": "drone",
        "solver": "openfoam_simple",
        "solver_label": "OpenFOAM simpleFoam / pimpleFoam (RANS, k-ω SST by default), actuator-disk rotors, rotating zones",
        "description": "Forces and flow around any body, with optional rotors.",
        "examples": ["quadcopter.step"],
        "face_bc_types": {
            "wall": {
                "label": "Wall (no-slip)", "color": "#adb5bd",
                "help": "Solid surface. Forces on these faces are reported.",
                "params": [], "face_kinds": ["body"],
            },
            "slip_wall": {
                "label": "Slip wall (frictionless)", "color": "#ced4da",
                "help": "Frictionless surface, e.g. a symmetry plane or ground plane ignoring friction.",
                "params": [], "face_kinds": ["body", "farfield"],
            },
            "symmetry": {
                "label": "Symmetry plane (mirror)", "color": "#8ecae6",
                "help": "A mirror: the flow on the other side is the mirror image of this side, so only half of a "
                        "symmetric body is meshed and solved (half the cells, about half the time). Put it on a flat "
                        "side of the air box that cuts the body through its plane of symmetry. Forces, moments and "
                        "areas are then those of the half model: double the forces for the whole body. Not with "
                        "rotors or rotating zones that cross the plane (their flow is not mirror-symmetric).",
                "params": [], "face_kinds": ["farfield"],
            },
            "moving_wall": {
                "label": "Moving part (oscillating, mesh deforms)", "color": "#ff5fa2",
                "help": "These faces move back and forth (a flapping wing, a vibrating flap, a pitching plate); the "
                        "mesh around them stretches to follow (OpenFOAM displacementLaplacian). Needs Analysis = "
                        "transient. Keep the amplitude small compared with the gap to other parts.",
                "params": [P("motion", "Motion", "", "translate", options=["translate", "rotate"],
                             option_help={"translate": "Slides along the axis: x(t) = A sin(2 pi f t).",
                                          "rotate": "Pitches about the axis through the centre of these faces: "
                                                    "angle(t) = A sin(2 pi f t)."}),
                           P("axis", "Axis", "", "+z", options=["+x", "-x", "+y", "-y", "+z", "-z"]),
                           P("amplitude", "Amplitude (mm, or degrees when rotating)", "", 2.0, min=0),
                           P("frequency", "Frequency", "Hz", 10.0, min=0)],
                "face_kinds": ["body"],
            },
        },
        "default_face_bc": {"label": "Wall (no-slip)"},
        "body_roles": ["fluid"],
        "body_params": [],
        "settings": [
            P("fluid", "Fluid", "", "air", options=["air", "water"], fluid_kinds=("gas", "liquid"),
              help="The fluid the body moves through: air for drones and vehicles, water or another liquid for submerged bodies, "
                   "or a fluid you added in the materials library. It sets density and viscosity, so forces and the Reynolds number."),
            P("turbulence", "Flow model", "", "kOmegaSST", options=["kOmegaSST", "kOmega", "kEpsilon", "realizableKE", "SpalartAllmaras", "kOmegaSSTDDES", "laminar"],
              help="Turbulence model. k-omega SST (RANS) is the standard choice for bodies in a flow; DES resolves "
                   "vortices and wakes in a transient run."),
            P("speed", "Flow speed", "m/s", 10.0, min=0, max=100,
              help="Incompressible air model: keep below ~100 m/s (Mach 0.3)."),
            P("forward_axis", "Forward axis", "", "+x", options=["+x", "-x", "+y", "-y", "+z", "-z"],
              help="The axis the body points along when it moves forward; the oncoming flow arrives from that "
                   "side. Change it when your CAD model is oriented differently (or turn the parts in Geometry)."),
            P("up_axis", "Up axis", "", "+z", options=["+x", "-x", "+y", "-y", "+z", "-z"],
              help="The axis that points up: it defines lift against drag and the pitch angle. Change it only if "
                   "your model's up is not +z."),
            P("pitch_deg", "Pitch angle (nose down +)", "deg", 0.0,
              help="Angle of the body to the flow (angle of attack = minus this). A multirotor tilts nose-down "
                   "5-20 deg to fly forward; it is set to 10 deg when rotors are found."),
            P("yaw_deg", "Sideslip angle", "deg", 0.0,
              help="Angle between the forward axis and the oncoming flow in the horizontal plane. 0 = straight "
                   "flight; 5-15 deg to study a crosswind or a turn."),
            P("mass_kg", "Vehicle mass", "kg", 0.0, min=0, help="Only used with rotors: sets the automatic thrust and the trim."),
            P("rotor_drag_coeff", "Rotor drag coefficient k", "s/m", 0.025, min=0, max=0.2,
              help="In-plane rotor drag (blade flapping + induced H-force) per newton of thrust and m/s of "
                   "in-plane airspeed. Identified values for small quadrotors are 0.02-0.03 s/m (Faessler et al., "
                   "IEEE RA-L 2018). 0 = frame drag only."),
            P("auto_trim", "Auto trim (re-run once)", "", "on", options=["on", "off"], show_if={"analysis": "steady"},
              help="If the first run is more than 1° (or 3 % thrust) from level-flight trim, set the suggested "
                   "pitch and rotor thrusts and solve again, so the reported forces are at the trimmed attitude."),
            P("cg_up_mm", "Moment point: up offset", "mm", 0.0,
              help="Moments (and with rotors the thrust split and the trim) are taken about this point."),
            P("cg_forward_mm", "Moment point: forward offset", "mm", 0.0,
              help="Offsets from the centre of the body's bounding box."),
            P("ref_area_m2", "Reference area (0 = frontal)", "m²", 0.0, min=0,
              help="E.g. the wing area for a wing, the frontal area for a car. 0 uses the projected frontal area."),
            P("ref_length_m", "Reference length (0 = body)", "m", 0.0, min=0,
              help="E.g. the mean chord for a wing, the wheelbase for a car."),
            P("turbulence_intensity", "Free-stream turbulence", "%", 1.0, min=0.1,
              help="Turbulence of the incoming air as % of the flow speed. 1 % is clean air; use 5-10 % for gusty "
                   "or near-ground flow. Higher values thicken boundary layers and move flow separation."),
            P("analysis", "Analysis", "", "steady", options=["steady", "transient"],
              help="steady: the settled flow (iterations). transient: the flow over time from the start "
                   "(time steps), e.g. vortex shedding or a warm-up; much slower."),
            P("end_time", "End time", "s", 1.0, min=1e-6, max=1e7, show_if={"analysis": "transient"},
              help="How many seconds of real flow to simulate. For a sliding-mesh propeller use 1-2 revolutions "
                   "(revolutions / (rpm / 60)) for a check, more for averaged forces; the cost is proportional."),
            P("time_step", "Time step (0 = auto)", "s", 0.0, min=0, max=1e6, show_if={"analysis": "transient"},
              help="0 = from the flow speed and the cell size (Courant number about 1), adjusted as the run goes."),
            P("n_frames", "Saved time points", "", 21, min=2, max=201, show_if={"analysis": "transient"},
              help="Fields kept for the results time slider (the history plots use every step)."),
            P("frame_deg", "Saved frame every", "deg", 8.0, min=0, max=90, show_if={"analysis": "transient"},
              help="Sliding-mesh (rotating) zones: save a time every this many degrees of turn of the fastest zone "
                   "(0 = use Saved time points). 6-10 degrees gives a smooth video in which the blades, the vortices "
                   "and the field are always from the same instant; 30 degrees looks stepped. The end time is rounded "
                   "to a whole number of frames. Each saved time takes about 150 bytes per cell (3.6 million cells: "
                   "0.5 GB): the setup checks warn when the disk will not hold them."),
            P("iterations", "Max iterations", "", 1500, min=50, max=100000, show_if={"analysis": "steady"},
              help="Upper limit for a steady run; it stops earlier once the forces stop changing. Raise it (or use "
                   "Continue) if the run ends not converged; rotating-zone (MRF) cases often need 500-1000."),
            P("max_hours", "Time limit", "h", 0.0, min=0, max=1000,
              help="0 = none. A wall-clock limit for the whole run, counted from its start (preparing the case, "
                   "splitting the mesh and the solver): when it has passed, the solver writes the current solution "
                   "and stops, and the results so far are kept (like Stop and keep results). Leave time for joining "
                   "the results and the video: for a 12 h deadline use about 10.5 h."),
            P("n_procs", "CPU cores", "", 0, min=0, max=256,
              help="0 = Auto: all physical cores for large meshes, fewer for small ones."),
        ],
        "rotor_params": [
            P("center", "Centre (x,y,z)", "m", [0, 0, 0]),
            P("axis", "Thrust direction", "", [0, 0, 1]),
            P("radius", "Rotor radius", "m", 0.1, min=0),
            P("thickness", "Disk thickness", "m", 0.01, min=0),
            P("thrust_N", "Thrust (blank = automatic: carries the weight at the set pitch)", "N", None),
        ],
        "needs": ["flow speed > 0 or at least one rotor"],
    },
}


# ---- plain-language explanations shown in the GUI when a study or a model option is selected
# Settings a bundled example (examples/*.step) gets when it is imported, so that it can run at once. They are
# assumptions for the example, not data of the CAD model: the user changes them in Setup.
EXAMPLE_SETTINGS = {
    "airplane_wing.step": {"design_speed_ms": 30.0},  # a small aircraft's top speed (flutter margin)
    "quadcopter.step": {"mass_kg": 1.2},  # all-up mass of a ~450 mm class quad (rotors carry it: ~2.9 N each in hover)
}

# Materials a bundled example gets (body name -> material key), where the aluminium default would make no sense
EXAMPLE_MATERIALS = {
    "airplane_wing.step": {"Wing": "pla"},  # a 3D-printed model wing: flutters at model-aircraft speeds
}

EXPLAIN = {
    "heat_conduction": (
        "Heat conduction inside solid parts (Fourier's law), solved with the built-in finite-element solver. The air "
        "is not simulated: its cooling enters through boundary conditions (a heat transfer coefficient h, or the "
        "still-air condition that works h out for you). Fast (seconds). Use it for heat spreaders, brackets, "
        "enclosure walls and warm-up over time when the cooling is known or the air is still."),
    "vibration_modal": (
        "Modal analysis: the natural frequencies at which the parts like to vibrate, and the shape of each "
        "vibration. No loads are needed, only how the parts are held (and any added masses). Keep these "
        "frequencies away from what shakes the part (motors, rotors, road) to avoid resonance."),
    "aeroelastic_flutter": (
        "Flutter and divergence (a multiphysics study: the structure and the air are solved together). The natural "
        "modes of the structure from the built-in finite elements are put in a stream of air; the air forces on every "
        "strip of the wing follow its motion (unsteady thin-airfoil theory). The air speed is raised until the "
        "motion stops decaying and grows: the flutter speed, with the frequency and shape of the flutter motion. A "
        "fast engineering method (seconds) for wings, blades, fins and panels; it does not replace a CFD-coupled "
        "check for transonic flow or large motion."),
    "static_structural": (
        "Linear static stress analysis: how much the parts deform and how high the stress is under steady loads "
        "(forces, pressure, gravity), with the safety factor against yield. Assumes small deformations and that the "
        "material stays elastic."),
    "cht_enclosure": (
        "Conjugate heat transfer (CFD with OpenFOAM): the air flow and the heat conduction in the parts are solved "
        "together, so the cooling comes out of the simulation instead of an assumed h. Air driven by a fan "
        "(forced convection) or rising on its own (natural convection), with optional radiation. Minutes to hours."),
    "external_aero": (
        "External aerodynamics (CFD with OpenFOAM, RANS): air or water flowing around a body - drag, lift, side "
        "force, moments and the flow field. Optional rotors (actuator disks) and rotating zones. Minutes to hours."),
}

OPTION_HELP = {
    "analysis": {
        "steady": "Steady: the settled state after everything has run long enough. Fastest - use it unless the "
                  "change over time matters.",
        "transient": "Transient: follows the solution through time from the start (warm-up, pulses, vortex "
                     "shedding, rotating parts). Gives a time slider; takes much longer than steady.",
    },
    "air_flow": {
        "fan": "Forced convection: a fan or duct pushes air in through the inlet and out through the outlet.",
        "natural": "Natural convection: no fan - warm air rises off the parts and room air flows in to replace it. "
                   "Switch radiation on too: it often carries 30-50 % of the heat.",
    },
    "radiation": {
        "off": "No radiation: heat leaves only with the air (fine with a fan and at moderate temperatures).",
        "on": "Surface-to-surface radiation (OpenFOAM fvDOM, discrete ordinates): every surface radiates by its "
              "emissivity; important with no fan or above ~60 °C. Slows the run down.",
    },
    "turbulence": {
        "auto": "Chosen for you from the Reynolds number (fan) or Rayleigh number (no fan): laminar for slow air "
                "and narrow gaps, k-omega SST otherwise.",
        "laminar": "Laminar: smooth, layered flow without turbulence. Right for slow air, small gaps and most "
                   "natural convection (channel Reynolds number below ~2300).",
        "kOmegaSST": "k-omega SST (Menter): the industry-standard turbulence model for flows along walls, with "
                     "separation and heat transfer. The best default for turbulent flow.",
        "kEpsilon": "Standard k-epsilon: the classic robust model for fully turbulent flow away from walls; less "
                    "accurate where the flow separates (uses wall functions).",
        "realizableKE": "Realizable k-epsilon: k-epsilon with better behaviour for swirl, jets and moderate "
                        "separation; wall functions.",
        "SpalartAllmaras": "Spalart-Allmaras: one-equation model from aerospace; good for attached boundary layers on "
                           "wings and bodies, cheap. Weak for heat transfer and strong separation.",
        "kOmega": "Wilcox k-omega: accurate near walls, sensitive to the free-stream turbulence values.",
        "kOmegaSSTDDES": "Vortex-resolving DES (k-omega SST delayed detached-eddy simulation): RANS in the boundary "
                         "layers, the eddies away from the walls resolved by the mesh, with a low-dissipation "
                         "velocity scheme. Tip vortices and wakes stay sharp where the cells are fine enough "
                         "(refine them with regions). Transient runs only; needs a fine mesh and small time steps.",
    },
    "gravity": {
        "off": "No gravity: no own weight (structures) / no buoyancy (air).",
    },
    "shake_axis": {
        "off": "No shake test: natural frequencies and mode shapes only.",
    },
    "auto_trim": {
        "on": "After the first run, the pitch that balances the forces is worked out and the case is run once more "
              "at that pitch.",
        "off": "One run at the pitch you set.",
    },
    "direction": {
        "normal": "Air enters straight into the face, like a fan or a duct.",
    },
    "fluid": {
        "air": "Air at about 20-25 °C and 1 atm.",
        "nitrogen": "Nitrogen gas at about 20 °C and 1 atm.",
        "helium": "Helium gas at about 20 °C and 1 atm (light, conducts heat well).",
        "water": "Water at about 20 °C (incompressible).",
        "seawater": "Seawater (35 g/kg salt) at about 20 °C.",
        "glycol_50": "A 50 % ethylene glycol and water coolant at about 20 °C.",
        "ethanol": "Ethanol at about 20 °C.",
        "transformer_oil": "Mineral transformer oil at about 20 °C.",
        "engine_oil": "Unused engine oil at about 20 °C (very viscous: the flow stays laminar).",
        "glycerin": "Glycerin at about 20 °C (very viscous).",
    },
    "air_box_shape": {},
}


# advanced OpenFOAM controls (0 / "default" = keep what the study writes)
FOAM_ADVANCED = [
    P("convection", "Convection scheme", "", "default", options=["default", "second order", "first order", "LUST"],
      option_help={
          "default": "What the study writes: second order (linearUpwind) for velocity and energy, first order for "
                     "turbulence where robustness matters.",
          "second order": "linearUpwind everywhere: accurate, the usual choice for final results.",
          "first order": "upwind everywhere: very robust but smears gradients (numerical diffusion). Use to get a "
                         "difficult case started, not for final numbers.",
          "LUST": "75 % central / 25 % linearUpwind: low dissipation for transient flow (vortex shedding, LES-like). "
                  "Can oscillate in steady runs."}),
    P("non_orth", "Non-orthogonal correctors", "", -1, min=-1, max=5,
      help="Extra pressure solves per iteration for skewed cells (-1 = default 1). Raise to 2-3 when the mesh check "
           "reports non-orthogonality above ~70 degrees or the pressure residual stalls; each costs about 10 % per "
           "step. A sliding-mesh run was 41 % faster with 1 than with 2 together with fewer outer correctors."),
    P("relax_p", "Relaxation: pressure (steady)", "", 0, min=0, max=1,
      help="Under-relaxation of pressure in steady runs: the share of each update that is kept (1 = all). 0 = "
           "default (0.3 aero, 0.7 cooling). Lower (0.2) if the run diverges or oscillates; raise only when it is "
           "stable but slow. SIMPLE rule: pressure + velocity ~ 1."),
    P("relax_U", "Relaxation: velocity (steady)", "", 0, min=0, max=1,
      help="Under-relaxation of velocity in steady runs. 0 = default (0.7 aero, 0.3 cooling). Lower (0.3-0.5) when "
           "the residuals blow up or velocity oscillates; raise (0.8) to speed up a run that is stable."),
    P("relax_h", "Relaxation: energy (steady)", "", 0, min=0, max=1,
      help="Under-relaxation of temperature in cooling runs. 0 = default (0.7). Lower (0.5) if the temperature "
           "oscillates between iterations; raise to speed up a stable run."),
    P("relax_turb", "Relaxation: turbulence (steady)", "", 0, min=0, max=1,
      help="Under-relaxation of the turbulence quantities (k, omega). 0 = default. Lower (0.5) when k or omega blow "
           "up (huge values in the log): the usual sign of a diverging rotating-zone case."),
    P("n_correctors", "Pressure correctors per time step (PIMPLE)", "", 0, min=0, max=10,
      help="Pressure corrections per time step in transient runs. 0 = default (2). Raise to 3 when forces are noisy "
           "or the velocity-pressure coupling is poor; fewer is cheaper but less accurate."),
    P("n_outer", "Outer correctors per time step (PIMPLE)", "", 0, min=0, max=50,
      help="How often the whole equation set is re-solved within one transient time step. 0 = the study's default "
           "(1; 5 for sliding meshes). More = stable at larger time steps but each costs a full pass: a 2.7 M cell "
           "sliding run was 41 % faster with 3 than with 5. Raise it if the run diverges, lower it for speed."),
    P("max_co", "Max Courant number (transient)", "", 0, min=0, max=50,
      help="The most cells the flow may cross in one time step when the step is automatic. 0 = default (1). Larger "
           "= fewer, faster steps but needs more outer correctors; lower it if the run oscillates or diverges."),
    P("tol_factor", "Linear-solver tolerance factor", "", 1, min=0.001, max=100,
      help="Multiplies every linear-solver tolerance: 0.1 = ten times tighter, 10 = looser and faster. Tighten "
           "(0.1) for final numbers or when the residuals stagnate; loosen (10) for quick previews."),
]


def validate_advanced(kw: dict) -> dict:
    spec = {p["key"]: p for p in FOAM_ADVANCED}
    out = {}
    for k, v in kw.items():
        if k not in spec:
            raise ValueError(f"Unknown advanced setting '{k}'. Options: {', '.join(spec)}")
        p = spec[k]
        if "options" in p:
            if v not in p["options"]:
                raise ValueError(f"{p['label']} must be one of {p['options']}")
        else:
            v = finite(v, p["label"])
            if v < p["min"] or v > p["max"]:
                raise ValueError(f"{p['label']} must be between {p['min']} and {p['max']}")
            if k in ("non_orth", "n_correctors", "n_outer") and v != int(v):
                raise ValueError(f"{p['label']} must be a whole number")
        out[k] = v
    return out


def _attach_help():
    for st_name, st in STUDIES.items():
        st.setdefault("explain", EXPLAIN.get(st_name, st.get("description", "")))
        for prm in st["settings"] + [q for b in st["face_bc_types"].values() for q in b["params"]]:
            if "options" in prm and prm["key"] in OPTION_HELP:
                prm.setdefault("option_help", {o: t for o, t in OPTION_HELP[prm["key"]].items() if o in prm["options"]})


_attach_help()


def sync_fluids():
    """The fluid choices of every study from the materials library (the built-in fluids and the user's own), in place: the lists are shared
    with everything that validates or shows them."""
    for st in STUDIES.values():
        for prm in st["settings"]:
            if prm["key"] != "fluid" or "fluid_kinds" not in prm:
                continue
            keys = materials.fluid_keys(prm["fluid_kinds"])
            prm["options"][:] = keys
            prm["option_labels"] = {k: materials.FLUIDS[k]["label"] for k in keys}
            fl = materials.FLUIDS
            prm["option_help"] = {k: OPTION_HELP["fluid"].get(k) or (
                f"{'Your own fluid' if fl[k].get('custom') else 'Fluid'}: density {fl[k]['rho']:g} kg/m3, viscosity {fl[k]['mu']:g} Pa s.") for k in keys}


materials.on_change.append(sync_fluids)
sync_fluids()

def study(name: str) -> dict:
    if name not in STUDIES:
        raise KeyError(f"Unknown study '{name}'. Options: {', '.join(STUDIES)}")
    return STUDIES[name]


def default_params(study_name: str, bc_type: str) -> dict:
    t = study(study_name)["face_bc_types"][bc_type]
    return {p["key"]: p["default"] for p in t["params"]}


def default_settings(study_name: str) -> dict:
    return {p["key"]: p["default"] for p in study(study_name)["settings"]}


def finite(v, label="Value") -> float:
    import math

    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number, got {v!r}")
    if not math.isfinite(x):
        raise ValueError(f"{label} must be a finite number")
    return x


def validate_params(study_name: str, bc_type: str, params: dict) -> dict:
    st = study(study_name)
    if bc_type not in st["face_bc_types"]:
        raise ValueError(
            f"Boundary type '{bc_type}' is not available for {st['label']}. "
            f"Options: {', '.join(st['face_bc_types'])}"
        )
    spec = st["face_bc_types"][bc_type]["params"]
    keys = [p["key"] for p in spec]
    extra = sorted(set(params or {}) - set(keys))
    if extra:  # a misspelt key would otherwise silently fall back to the default
        raise ValueError(f"Unknown parameter(s) {extra} for '{bc_type}'. Allowed: {', '.join(keys) or 'none'}")
    out = {}
    for p in spec:
        v = params.get(p["key"], p["default"])
        if v is None or v == "":
            v = p["default"]
        if v is None:  # optional value (e.g. a displacement component left free)
            out[p["key"]] = None
            continue
        if "options" in p:
            if v not in p["options"]:
                raise ValueError(f"{p['label']} must be one of {p['options']}")
            out[p["key"]] = v
            continue
        v = finite(v, p["label"])
        if p["unit"] == "°C" and v <= -273.15:
            raise ValueError(f"{p['label']} must be above absolute zero (-273.15 °C)")
        if "min" in p and v < p["min"]:
            raise ValueError(f"{p['label']} must be ≥ {p['min']} {p['unit']}")
        if "max" in p and v > p["max"]:
            raise ValueError(f"{p['label']} must be ≤ {p['max']} {p['unit']}")
        out[p["key"]] = v
    return out


AXES = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0), "+z": (0, 0, 1), "-z": (0, 0, -1)}


# The symmetry plane is a boundary condition of every study: a mirror. It is created from a flat face or from a plane
# placed in the 3D view (Setup, "Symmetry planes"): the model is cut there, the side that is not kept is removed and
# the faces on the plane carry this condition. Its faces are managed by the plane, not selected by hand.
SYMMETRY_BC = {
    "label": "Symmetry plane (mirror)", "color": "#8ecae6", "plane": True, "params": [],
    "help": "A mirror. Only one side of the model is kept, meshed and solved; the other side is the mirror image, "
            "shown again in the results with 'Show mirrored half'. Add it from a flat face (select the face, add "
            "this condition) or place a plane in the 3D view and drag it; flip the plane to keep the other side. "
            "Loads and supports must be mirror-symmetric as well (a load you enter acts on the kept half only: "
            "enter half of a total shared by two halves). Flows: the oncoming flow must run along the plane, and "
            "rotors or rotating zones must not cross it.",
}
SYMMETRY_HELP = {
    "static_structural": "Acts like a frictionless support on the plane: the faces cannot move along the plane "
                         "normal. Own weight must act along the plane.",
    "vibration_modal": "Constrains the movement along the plane normal: only the modes that are symmetric across "
                       "the plane are found (the antisymmetric ones need the flipped condition: a fixed support "
                       "on the plane for the in-plane movement, which is not offered). Run the whole model to see "
                       "all modes.",
    "heat_conduction": "No heat crosses the plane (adiabatic): heat sources and boundary conditions must be "
                       "symmetric. A heat load given as a total acts on the kept half only.",
    "cht_enclosure": "A mirror in the air and in the parts: no flow and no heat across the plane. Gravity and the "
                     "fan flow must run along the plane.",
    "external_aero": "A mirror: forces and moments are those of the whole body (the half model's, summed with its "
                     "image).",
}
for _k, _s in STUDIES.items():
    if "symmetry" not in _s["face_bc_types"]:
        _s["face_bc_types"]["symmetry"] = dict(SYMMETRY_BC, help=SYMMETRY_BC["help"] + " " + SYMMETRY_HELP.get(_k, ""))
    else:
        _s["face_bc_types"]["symmetry"].update(plane=True, help=SYMMETRY_BC["help"] + " " + SYMMETRY_HELP.get(_k, ""))
        _s["face_bc_types"]["symmetry"].pop("face_kinds", None)

# Settings shown in Expert mode only (the GUI's Simple mode hides them; their defaults suit a first run). Values
# set earlier, by the chat or by a script are kept and used either way.
EXPERT_SETTINGS = {"structural_solver", "time_step", "contact_resistance", "turbulence", "turbulence_intensity",
                   "rotor_drag_coeff", "cg_up_mm", "cg_forward_mm", "ref_area_m2", "ref_length_m", "iterations",
                   "frame_deg"}
for _s in STUDIES.values():
    for _p in _s["settings"]:
        if _p["key"] in EXPERT_SETTINGS:
            _p["adv"] = True
