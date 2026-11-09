# Experimental shadow planner — NOT ACTIVATION-READY

Only our wrappers/tests included. One repository finance kernel is referenced/hash-guarded; no Predbat code, binary, archive, environment, real forecast/plan or database is bundled.

Manual dependency instructions: obtain `springfall2008/batpred` at commit `950a948366d453e825c8d883f6fff1e18c355a6e` (v9.7.1), review that exact upstream personal-use licence/dependency requirements, verify commit, build a separate immutable runtime. See `../../provenance/upstream.json`. No automatic download/install CI or licence grant.

`plan_job.py` calls genuine `calculate_plan(recompute=True, debug_mode=False, publish=False)`, not `initialize()` or monitor mode. Run only after chroot/drop-uid/gid/capabilities/seccomp isolation with immutable inputs and no HA/Supervisor/controller/network/live-mount path. `sys.frozen` disables updater, not a safety boundary. Process limits are not demonstrated cgroup bandwidth.

Archive attempts are counted before send, shared UTC-day budget across two sites, with receipt-time availability and continuous virtual stock. Other Solcast consumers must share the same accounting. Never re-anchor virtual stock to actual SOC or substitute actual PV for forecasts. Replay requires corrected boundary-split inputs. Fourth-card reducer is not wired to the live UI.

Offline tests need no upstream or credentials. `service.py` is a disabled-by-default prototype, NOT an authorised network endpoint; runtime options are absent. Source does not read any live worksheet/provision key. No credential CLI/env/log workflow supplied. Packaging, resource enforcement, credential/snapshot transport, broker/UTC/retry hardening, full physical/cash validation and actual HA/card integration remain gates. Do not run it on a production network.
