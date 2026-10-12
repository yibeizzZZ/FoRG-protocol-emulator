# FoRG execution policy

The assistant is responsible for implementation, debugging, and analysis. The
user is responsible for running tests and long-running validation.

1. Do not execute long-running tests, full regressions, synthesis, place-and-route,
   GDS builds, or physical verification without explicit user approval.
2. Do not start background builds, repeatedly poll logs, or wait for EDA processes.
3. Before any test expected to exceed 60 seconds, provide its exact WSL command,
   working directory, dependencies, estimated runtime, log/result locations, and
   success/failure criteria. Let the user run it and provide the results.
4. Analyze supplied results and propose necessary fixes.
5. Do not automatically retry failing physical flows or change timing constraints
   to force a pass.
6. Do not rerun identical tests unless the relevant implementation or configuration
   has changed.
7. Keep output concise; inspect relevant errors, timing reports, and summaries.
8. Before pushing changes that trigger expensive CI builds, explain the impact
   and request approval.

Lightweight static checks are allowed. Simulations and physical verification
require authorization. Prior task authorization does not override this standing
policy for new validation runs.
