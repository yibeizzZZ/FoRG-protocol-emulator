# ASIC CI repair

Investigated run: https://github.com/yibeizzZZ/FoRG-protocol-emulator/actions/runs/36222054598

Revision: `96e05b322e4c7da763fe0f79f44aea9689eac918`.

## Gate-level simulation

The original job failed before simulation: `ihp_dff_r`, `ihp_mux2`, and
`ihp_mux4` were missing. The Makefile now includes `sg13cmos5l_udp.v`.

The pinned CMOS5L cell model also uses delayed signals driven by timing
checks unsupported by Icarus. `scripts/prepare-functional-model.py` creates
a local functional copy with specify blocks removed and delayed signals
connected directly to the corresponding ports. It preserves the original
logic/UDP definitions and does not edit the installed PDK.

This is zero-delay functional simulation, not SDF timing verification.
Static timing and physical checks remain necessary.

The first program load is followed by an execution-state reset. Program
memory is not reset. Before loading is complete, unknown instruction bits
can contaminate optimized gate logic in four-state simulation. The reset
clears execution state after all eight program locations have been loaded.
Subsequent reprogramming is still tested without reset.

Validated locally against the actual CI netlist and the same IHP PDK
revision, `2bbec755dc67ca3db0261c3d6163e15735d66710`:

- RTL: 2 passed, 0 failed.
- Gate-level: 1 passed, 0 failed, 1 intentionally skipped.
- The skipped pause test inspects RTL register names that synthesis may
  rename; the existing external-interface pause check still executes.

## Physical precheck

The run reported 66 power-pin width errors. Generated Metal4 power pins
were 1.0 um wide; the CMOS5L precheck requires at least 2.1 um.
The resolved build configuration used `PDN_VWIDTH: 1`.
`src/config.json` now sets `PDN_VWIDTH: 2.1`.

This configuration fix is pending a fresh GDS build and precheck. Do not
edit generated LEF/GDS files or weaken the checker. Widening power straps
can affect routing, so the full build must pass before calling this fixed.

## GitHub Pages

The viewer failed with a Pages deployment 404. A repository administrator
must open Settings > Pages and select GitHub Actions as the build source:

https://github.com/yibeizzZZ/FoRG-protocol-emulator/settings/pages

No physical verification job has been disabled or marked continue-on-error.

## Finish validation on GitHub

1. Review and push the local changes on a branch, or apply them through a PR.
2. Enable GitHub Pages as described above.
3. Run the gds workflow on the branch containing the changes. Rerunning the
   old failed run will use the old code and cannot validate this patch.
4. Require gds, precheck, gl_test, and viewer to pass. Review the new timing
   and physical reports as well as the workflow status.

The authenticated account used for this investigation has read-only access
to the upstream repository, so no remote changes or workflow reruns were made.
