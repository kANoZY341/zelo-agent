# Live Core two-Computer HTTP qualification plan

## Goal and boundary

Close the gap between the existing native two-Computer proof and the normal Core HTTP/Runner proof. Use the same disposable database, private Docker namespace, exact pinned images and two separately approved AgentInstances in `scripts/qualify_two_computer_space.py`. Keep the production service and default grants unchanged.

## Tasks

1. Add a bounded loopback Uvicorn stage using `create_app` with the script's explicit isolated Settings. Hold the listening socket for the whole stage; refuse startup failure and stop the server in `finally`.
2. Through authenticated TCP HTTP, reject a parent-for-child Space activation, submit real `files_read` and `files_list` Tasks for the child, await persisted success and exact fixture results, and observe the completion audits. Reject unauthenticated access.
3. Revoke the delegation through HTTP. Verify a new Task is refused and the Task count is unchanged. Preserve the existing direct native checks and exact owned-resource cleanup after success or failure.
4. Run focused tests, Ruff and diff checks. Obtain independent review and exact-head CI, then run one native qualification on the Zelo host at that exact source. Require zero run-owned Docker residue and no production database or service change before integration. Verify separate main CI after merge.

## Acceptance and recovery

The pass receipt must identify exact Git SHA, distinct AgentInstance IDs, Core backend identity, HTTP read/list and negative outcomes, pinned native container state, and cleanup. A refusal remains a refusal. This proves the stated loopback case only; it does not grant broad Computer/Space authority. If any check fails, stop the disposable server and remove only resources verified as owned by that run. Leave the live `zelo-core.service` and its database untouched.
