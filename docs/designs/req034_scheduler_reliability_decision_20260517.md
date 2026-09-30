# SA Decision: REQ-034-v3 Scheduler Reliability Follow-up (2026-05-17)

## Current State

- PM approved REQ-034-v3 production schedule.
- PM approved reliability Path 2 after reviewing SA recommendation.
- Windows Task Scheduler task: `\TW_Stock_My_Holdings_Risk_Cut_0630`
- Schedule: weekday 06:30
- Current status: `Ready`
- Current logon mode: `Interactive only`
- Production launch remains approved; this document is a reliability follow-up, not a launch blocker.
- Latest non-secret status check timestamp: 2026-05-17 15:30 +08:00

## SA Path Selection

SA selects Path 2 as the reliability target:

`Run whether user is logged on or not` with Windows-managed encrypted credentials.

Reason:

- The 06:30 task is an unattended operational reminder.
- `Interactive only` will miss runs after Windows reboot, logout, or user session termination.
- Missed runs are observable but still create first-week operational noise.
- Path 1 is acceptable only if the user explicitly commits to staying logged in 24/7, which is an operational promise rather than a scheduler reliability control.

## Implementation Gate

Rebuild is approved, but not yet executed.

Path 2 requires user interactive credential entry because it stores task credentials through Windows Task Scheduler. No password, token, local notification secret, or account credential detail may be written to git, scripts, logs, markdown, `.env`, `.reg`, or CLI command history.

Credential-handling terms locked by PM:

1. Credential input must be through the Windows GUI.
2. CLI password, `.env`, `.reg`, git, commit, document, and log plaintext paths are forbidden.
3. Documentation may record only non-secret timestamps and status outcomes.
4. Rebuild must not be done within 30 minutes before a 06:30 trigger; recommended after 09:00.
5. If rebuild fails, restore the existing `Interactive only` task.

Required completion checks:

1. User enters Windows credentials interactively in Task Scheduler.
2. SA/RD verifies `schtasks /Query` shows `Logon Mode` is not `Interactive only`.
3. SA/RD records only the verification timestamp and status, not account details.

## Temporary Operating Mode

Until Path 2 is signed and executed:

- Keep current `Interactive only` task active.
- First week missed-run triage remains enabled.
- If a 06:30 run is missed, classify the reason before changing report logic:
  - user not logged in
  - Windows asleep/offline
  - task credentials/session issue
  - script/runtime failure

## PM Response

SA recommendation to PM:

Path 2 is approved and ready for user GUI credential entry. No automated rebuild will be attempted through CLI password channels.
