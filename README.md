# Fabric Drop monitor turnover

Configure DEVICES, COMMANDS, polling settings, SMTP_HOST, SMTP_PORT and both recipient lists in credentials.py. FABRIC_DROPS.py imports these settings. All device usernames/passwords and the email login are in local credentials.py: DEVICE_USERNAME and DEVICE_PASSWORD shared by all devices, plus EMAIL_SENDER and EMAIL_PASSWORD. A per-device password overrides DEVICE_PASSWORD. Existing local credentials were preserved. credentials.py can be committed as sanitized configuration with placeholders. Fill in local credentials and device settings before running; keep actual passwords out of commits.

The send_email function stays in email_sender.py. Keep both files beside FABRIC_DROPS.py; the main script imports them automatically. Keep real passwords out of Git commits.

Run `python FABRIC_DROPS.py`. No ServiceNow dependency or API call is included.

## Poll and connectivity flow

Each worker first performs a TCP probe with CONNECTIVITY_TIMEOUT (seconds). CONNECTIVITY_PORT defaults to the device SSH port (22); a device can override connectivity_port. An accepted connection or explicit connection refusal is REACHABLE: refusal demonstrates a response but does not establish SSH availability. Timeout and other socket/network lookup failures are UNREACHABLE from this monitor's perspective. A separate probe port tests that service/path only; SSH still has to succeed.

UNREACHABLE skips SSH and returns MONITORING_ERROR / DEVICE_UNREACHABLE. Reachable devices proceed through credentials, SSH, hostname validation and every configured FPC command. Valid zero counters produce NORMAL; any positive counter produces ALARM. Missing/malformed output is an error, never zero. An incomplete poll cannot clear even an FPC that returned zero earlier in that poll.

## Email routing

One monitoring-team email is attempted each cycle that has a successful device poll or an unreachable device:

- Any successfully polled current alarm (including ongoing alarms): Fabric Drop Alarm Alert: BNG/CGNAT Requires Immediate Action.
- Successful polls with no current alarms: No Fabric Drop Alarm Found on BNG/CGNAT. Results apply only to listed successful devices.
- No successful polls, but unreachable devices: Fabric Drop Monitor - Connectivity Information.

The body lists successfully polled FPC values, clear transitions, and a separate Connectivity Information section for unreachable devices with Fabric Drop Status: NOT AVAILABLE. SSH authentication, session, credential, hostname and parser failures are excluded. If all devices have only those technical failures, no monitoring-team email is sent.

One aggregated admin email is attempted when any device has MONITORING_ERROR, including unreachable devices. Cycle/state failures also trigger an admin-only notification. Configure ADMIN_EMAIL_RECIPIENTS before production: the default is empty and logs a delivery failure. SMTP failures are logged and return failure; an unavailable SMTP service cannot deliver its own error report.

## Persistent state and MYCOM handoff

State retains hostname, IP, FPC counters, ALARM/CLEARED state, first_detected and last_seen UTC timestamps. Legacy hostname-to-FPC JSON is migrated on load; unknown historical timestamps remain null. Existing extra fields, including MYCOM incident references, are preserved. Failed devices and unpolled FPCs retain their state. Only a full successful poll with a zero counter clears an active FPC. Corrupt state stops the cycle without overwriting the file. Writes use a flushed temporary file and atomic replacement. Schedule only one process at a time; atomic writes do not provide multi-process locking.

update_alarm_state returns structured NEW_ALARM, ONGOING_ALARM, UPDATED_ALARM, CLEARED and MONITORING_ERROR events. main passes only NEW_ALARM, UPDATED_ALARM and CLEARED to handle_ticket_event. MYCOM can replace this hook; each event includes hostname, host, FPC, current/previous values and timestamp. The default hook logs events and makes no external calls. State is saved before the hook: this is not a durable delivery queue. MYCOM must add durable delivery/retry and idempotency if their integration needs guaranteed event processing across crashes.

## Test checklist

Run `python -m unittest -v test_fabric_drops` from this directory. Tests mock network and email operations.

- TCP accepted/refused versus timeout; unreachable skips SSH.
- Reachable authentication/timeout failures appear only in admin mail.
- Mixed success/unreachable/authentication results route correctly.
- NEW, ONGOING, UPDATED, CLEARED and reactivated alarm transitions.
- Failure after an earlier FPC zero preserves previous active alarms.
- Missing, negative, noninteger and duplicate Fabric drops lines fail parsing.
- Failed devices and unpolled FPCs preserve state and incident metadata.
- Legacy JSON migration, corrupt JSON rejection, atomic replacement failure.
- SMTP success, failure and absent admin recipients.

Before turnover, validate with approved lab devices: actual Junos output, hostname format, configured SSH credentials and configured SMTP credentials, the selected TCP port/path, timeout behavior, and both recipient lists. Confirm the MYCOM hook independently if replaced. No live devices or email recipients are contacted by the automated tests.
