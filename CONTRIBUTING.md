# Development workflow

Use English for code comments, documentation, commit messages, issues, and pull requests.

Use a descriptive feature branch and a pull request. Do not push directly
to main or merge without review. Keep unrelated fixes in separate commits;
include the problem, behavior change, test commands, and limitations in the PR.

Before proposing a change, run the relevant local regression:

```bash
bash scripts/test-local.sh
bash scripts/test-local.sh TARGET=uart UART_CLKS_PER_BIT=4
bash scripts/test-local.sh TARGET=legacy
```

For ISA or host-interface changes, update docs/isa.md and the independent
reference model. Preserve reset, pause, loading and boundary tests. Keep
firmware examples consistent with the documented encoding.

ASIC changes require the relevant gds or uart-baseline workflow. Inspect
precheck, gate simulation and timing reports, not just RTL test success.
Never disable a failing checker to mark a milestone complete. Explain any
simulation assumptions, model adapters and intentionally skipped tests.

Issue reports should name the design/commit, provide a minimal reproduction,
state expected versus observed behavior, and link the failing CI run or log.
Do not include credentials or generated PDK contents. Generated simulation
and synthesis output is ignored by Git; record measured summaries and links
in docs/results.md.

The default-branch Pages viewer requires administrator setup. Feature
branches deliberately do not publish over that site.
