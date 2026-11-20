"""openCFS input files: the simulation XML and the material XML for harmonic structure <-> acoustics.

Every element and attribute below was taken from the official test suite (gitlab.com/openCFS/Testsuite:
Singlefield/Acoustics/Abc3d, Coupledfield/MechAcou/RingHarm2d and Piston2d_harm, Singlefield/Acoustics/
RayleighDamping3D) and from the XSD files shipped in the release (share/xml/CFS-Simulation/Schemas, CFS-Material/
Schemas). The release's own schema check does not run (it looks for CFS_PDEmagneticScalarPotentialAdj.xsd, which is
shipped as PDEmagneticScalarPotentialAdj.xsd: "WARN parsing the xml file ... unable to open primary document entity"),
so a wrong element is found by openCFS when it reads it, not by validation: the tests of this package re-read the
written XML and check the element names against the XSD when the release is present.
"""
from __future__ import annotations

from xml.sax.saxutils import quoteattr

import numpy as np

NS_SIM = "http://www.cfs++.org/simulation"
NS_MAT = "http://www.cfs++.org/material"


def _num(x) -> str:
    return f"{float(x):.10g}"


def frequencies(f_min: float, f_max: float, n: int, sampling: str = "linear") -> list:
    """The analysis frequencies (Hz). One frequency when n == 1 or f_min == f_max."""
    f_min, f_max, n = float(f_min), float(f_max), int(n)
    if f_min <= 0 or f_max < f_min:
        raise ValueError("The frequency range must be positive and from low to high.")
    if n <= 1 or f_max == f_min:
        return [f_min]
    if sampling == "log":
        return [float(v) for v in np.geomspace(f_min, f_max, n)]
    return [float(v) for v in np.linspace(f_min, f_max, n)]


def material_xml(solids: dict, fluids: dict, loss_factor: float, f_center: float) -> str:
    """solids: {name: {E, nu, rho}}; fluids: {name: {rho, c}} (bulk modulus K = rho c^2).

    The structural damping is a Rayleigh damping that gives the loss factor `loss_factor` (= tan delta = 2 zeta) at
    f_center (<computeFromTanDeltaAtFrequency>, VERIFIED in the test suite's RingHarm2d/mat.xml); 0 writes none.
    """
    out = ['<?xml version="1.0" encoding="utf-8"?>', f'<cfsMaterialDataBase xmlns="{NS_MAT}">']
    for name, m in solids.items():
        out += [f"  <material name={quoteattr(name)}>", "    <mechanical>",
                f"      <density><linear><real>{_num(m['rho'])}</real></linear></density>",
                "      <elasticity><linear><isotropic>",
                f"        <elasticityModulus><real>{_num(m['E'])}</real></elasticityModulus>",
                f"        <poissonNumber><real>{_num(m['nu'])}</real></poissonNumber>",
                "      </isotropic></linear></elasticity>"]
        if loss_factor > 0:
            out += ["      <damping><rayleigh><computeFromTanDeltaAtFrequency>",
                    f"        <centerFreq>{_num(f_center)}</centerFreq>",
                    "        <ratioDeltaF>1e-2</ratioDeltaF>",
                    f"        <lossTangensDelta>{_num(loss_factor)}</lossTangensDelta>",
                    "      </computeFromTanDeltaAtFrequency></rayleigh></damping>"]
        out += ["    </mechanical>", "  </material>"]
    for name, m in fluids.items():
        k = float(m["rho"]) * float(m["c"]) ** 2
        out += [f"  <material name={quoteattr(name)}>", "    <acoustic>",
                f"      <density><linear><real>{_num(m['rho'])}</real></linear></density>",
                f"      <compressionModulus><linear><real>{_num(k)}</real></linear></compressionModulus>",
                "    </acoustic>", "  </material>"]
    out.append("</cfsMaterialDataBase>")
    return "\n".join(out) + "\n"


def simulation_xml(model: dict) -> str:
    """The simulation XML of a harmonic structure + acoustics run.

    model keys
      mesh: file name of the .msh; material_file: file name of the material XML; job: not used in the XML
      solid_regions: [(region name, material name)]; fluid_regions: [(region name, material name)]
      surface_regions: [names]            every surface region of the mesh file (must be declared)
      node_regions: [names]               every node (point) region of the mesh file
      frequencies: [Hz]
      coupling: name of the fluid-structure interface surface region, or None (no coupling)
      fixed: [surface region]             structure: all three displacements zero
      tractions: [(surface region, (tx, ty, tz) N/m^2, phase_deg)]   structure: uniform traction (force / area)
      pressures: [(surface region, Pa, phase_deg)]                   structure: pressure on the face (pushes in)
      forces: [(node/surface region, (fx, fy, fz) N each node)]      (kept for completeness)
      absorbing: [(surface region, fluid region)]                    acoustic: first-order radiation boundary
      velocities: [(surface region, m/s, phase_deg)]                 acoustic: prescribed normal velocity
      eigen: number of natural frequencies to compute (replaces the sweep); monitors: [node region names]       history output (text) of pressure and displacement at these nodes
    """
    sol = [r for r, _ in model["solid_regions"]]
    flu = [r for r, _ in model["fluid_regions"]]
    freqs = model.get("frequencies") or []
    L = ['<?xml version="1.0"?>', f'<cfsSimulation xmlns="{NS_SIM}">',
         "  <fileFormats>",
         f"    <input><gmsh fileName={quoteattr(model['mesh'])}/></input>",
         '    <output><gmsh id="gm" binaryFormat="no"/>']
    if model.get("monitors"):
        L.append('      <text id="txt"/>')
    L += ["    </output>",
          f'    <materialData file={quoteattr(model["material_file"])} format="xml"/>',
          "  </fileFormats>",
          '  <domain geometryType="3d">', "    <regionList>"]
    L += [f"      <region name={quoteattr(r)} material={quoteattr(m)}/>"
          for r, m in list(model["solid_regions"]) + list(model["fluid_regions"])]
    L.append("    </regionList>")
    if model.get("surface_regions"):
        L.append("    <surfRegionList>")
        L += [f"      <surfRegion name={quoteattr(s)}/>" for s in model["surface_regions"]]
        L.append("    </surfRegionList>")
    if model.get("node_regions"):
        L.append("    <nodeList>")
        L += [f"      <nodes name={quoteattr(s)}/>" for s in model["node_regions"]]
        L.append("    </nodeList>")
    L.append("  </domain>")
    L.append("  <sequenceStep>")
    if model.get("eigen"):  # natural frequencies (of the air cavity / the coupled system) instead of a sweep
        L += ["    <analysis><eigenFrequency><isQuadratic>no</isQuadratic>",
              f"      <numModes>{int(model['eigen'])}</numModes><freqShift>1</freqShift>",
              "      <writeModes>yes</writeModes></eigenFrequency></analysis>"]
    else:
        L += ["    <analysis><harmonic>", f"      <numFreq>{len(freqs)}</numFreq>", "      <frequencyList>"]
        L += [f'        <freq value="{_num(f)}"/>' for f in freqs]
        L += ["      </frequencyList>", "    </harmonic></analysis>"]
    L.append("    <pdeList>")
    mon = "".join(f'<nodes name={quoteattr(n)}/>' for n in model.get("monitors") or [])
    if sol:
        L += ['      <mechanic subType="3d">', "        <regionList>"]
        L += [f"          <region name={quoteattr(r)}/>" for r in sol]
        L += ["        </regionList>", "        <bcsAndLoads>"]
        for s in model.get("fixed", []):
            L.append(f'          <fix name={quoteattr(s)}><comp dof="x"/><comp dof="y"/><comp dof="z"/></fix>')
        for s, (tx, ty, tz), ph in model.get("tractions", []):
            L.append(f"          <traction name={quoteattr(s)}>"
                     f'<comp dof="x" value="{_num(tx)}" phase="{_num(ph)}"/>'
                     f'<comp dof="y" value="{_num(ty)}" phase="{_num(ph)}"/>'
                     f'<comp dof="z" value="{_num(tz)}" phase="{_num(ph)}"/></traction>')
        for s, p, ph in model.get("pressures", []):
            L.append(f'          <pressure name={quoteattr(s)} value="{_num(p)}" phase="{_num(ph)}"/>')
        for s, (fx, fy, fz) in model.get("forces", []):
            L.append(f"          <force name={quoteattr(s)}>"
                     f'<comp dof="x" value="{_num(fx)}"/><comp dof="y" value="{_num(fy)}"/>'
                     f'<comp dof="z" value="{_num(fz)}"/></force>')
        L += ["        </bcsAndLoads>", "        <storeResults>",
              '          <nodeResult type="mechDisplacement"><allRegions/>'
              + (f"<nodeList>{mon}</nodeList>" if mon else "") + "</nodeResult>",
              "        </storeResults>", "      </mechanic>"]
    if flu:
        L += ['      <acoustic formulation="acouPressure">', "        <regionList>"]
        L += [f"          <region name={quoteattr(r)}/>" for r in flu]
        L += ["        </regionList>"]
        bc = []
        for s, reg in model.get("absorbing", []):
            bc.append(f"          <absorbingBCs name={quoteattr(s)} volumeRegion={quoteattr(reg)}/>")
        for s, v, ph in model.get("velocities", []):
            bc.append(f'          <normalVelocity name={quoteattr(s)} value="{_num(v)}" phase="{_num(ph)}"/>')
        if bc:
            L += ["        <bcsAndLoads>"] + bc + ["        </bcsAndLoads>"]
        L += ["        <storeResults>",
              '          <nodeResult type="acouPressure"><allRegions/>'
              + (f"<nodeList>{mon}</nodeList>" if mon else "") + "</nodeResult>",
              "        </storeResults>", "      </acoustic>"]
    L.append("    </pdeList>")
    if model.get("coupling") and sol and flu:
        regions = [model["coupling"]] if isinstance(model["coupling"], str) else list(model["coupling"])
        L += ["    <couplingList><direct><acouMechDirect><surfRegionList>"]
        L += [f"      <surfRegion name={quoteattr(r)}/>" for r in regions]
        L += ["    </surfRegionList></acouMechDirect></direct></couplingList>"]
    eig = "<eigenSolverList><arpack/></eigenSolverList>" if model.get("eigen") else ""
    L += [f"    <linearSystems><system><solverList><directLU/></solverList>{eig}</system></linearSystems>",
          "  </sequenceStep>", "</cfsSimulation>"]
    return "\n".join(L) + "\n"
