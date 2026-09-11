"""Concurrent Junos monitoring with persistent alarms and MYCOM event handoff."""
import concurrent.futures
import copy
from datetime import datetime, timezone
import json
import logging
from email.mime.text import MIMEText
import smtplib
from credentials import DEVICE_PASSWORD, DEVICE_USERNAME, EMAIL_SENDER, EMAIL_PASSWORD
from credentials import (
    SMTP_HOST, SMTP_PORT, MONITORING_EMAIL_RECIPIENTS, ADMIN_EMAIL_RECIPIENTS,
    COMMANDS, MAX_WORKERS, CONNECTIVITY_TIMEOUT, CONNECTIVITY_PORT, DEVICES,
)
import os
from pathlib import Path
import re
import socket
import tempfile

from netmiko import ConnectHandler, NetmikoAuthenticationException, NetmikoTimeoutException



ALARM_TRACK_FILE = Path(__file__).with_name('fabric_drops_alarms.json')
LOG_FILE = Path(__file__).with_name('fabric_drop_monitor.log')
LOGGER = logging.getLogger(__name__)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def monitoring_error(result, error_type, message):
    result.update(status='MONITORING_ERROR', error_type=error_type,
                  error_message=message, alarms={})
    LOGGER.error('Device %s: %s: %s', result['host'], error_type, message)
    return result


def test_connectivity(host, port, timeout=CONNECTIVITY_TIMEOUT):
    """A TCP response proves reachability; silence does not prove the NE is down."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return 'REACHABLE'
    except ConnectionRefusedError:
        return 'REACHABLE'
    except OSError:
        return 'UNREACHABLE'


def parse_fabric_drops(output):
    matches = re.findall(r'^\s*Fabric drops\s*:\s*(\d+)\s*$', output, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError('Expected one Fabric drops line with a nonnegative integer')
    return int(matches[0])


def execute_commands(device, commands):
    """Only a complete successful poll can produce NORMAL or ALARM."""
    result = dict(host=device['host'], hostname=None, status='MONITORING_ERROR',
                  connectivity=None, alarms={}, counters={}, error_type=None,
                  error_message=None, timestamp=timestamp())
    connection = None
    stage = 'CONNECTIVITY_TEST_FAILED'
    try:
        port = device.get('connectivity_port', CONNECTIVITY_PORT) or device.get('port', 22)
        result['connectivity'] = test_connectivity(device['host'], port)
        if result['connectivity'] == 'UNREACHABLE':
            return monitoring_error(result, 'DEVICE_UNREACHABLE',
                                    'TCP connectivity test failed; Fabric Drop status could not be determined')
        params = {'username': DEVICE_USERNAME}
        params.update({k: v for k, v in device.items() if k != 'connectivity_port'})
        stage = 'CREDENTIAL_ERROR'
        params['password'] = params.get('password', DEVICE_PASSWORD)
        if not params.get('username') or not params['password'] or params['password'] == '<password>':
            return monitoring_error(result, 'MISSING_CREDENTIALS', 'Device username or password is not configured')
        stage = 'SSH_CONNECTION_FAILED'
        LOGGER.info('Connecting to %s', device['host'])
        connection = ConnectHandler(**params)
        LOGGER.info('SSH connected to %s', device['host'])
        stage = 'HOSTNAME_RETRIEVAL_FAILED'
        output = connection.send_command('show configuration system host-name | display set')
        names = re.findall(r'^set system host-name ([A-Za-z0-9][A-Za-z0-9_.-]*)\s*$', output, re.MULTILINE)
        if len(names) != 1:
            return monitoring_error(result, stage, 'Could not parse Junos hostname')
        result['hostname'] = names[0]
        if not commands:
            return monitoring_error(result, 'CONFIGURATION_ERROR', 'No FPC commands configured')
        for command in commands:
            stage = 'CONFIGURATION_ERROR'
            match = re.search(r'\bfpc\s+(\d+)\b', command)
            if not match:
                return monitoring_error(result, stage, 'Command has no FPC identifier')
            stage = 'CLI_EXECUTION_FAILED'
            output = connection.send_command(command)
            stage = 'CLI_PARSE_ERROR'
            value = parse_fabric_drops(output)
            result['counters'][match[1]] = value
            if value > 0:
                result['alarms'][match[1]] = value
                LOGGER.info('Fabric drop: %s FPC %s value %s', device['host'], match[1], value)
        result['status'] = 'ALARM' if result['alarms'] else 'NORMAL'
        return result
    except NetmikoAuthenticationException:
        return monitoring_error(result, 'SSH_AUTHENTICATION_FAILED', 'SSH authentication failed')
    except NetmikoTimeoutException:
        return monitoring_error(result, 'SSH_CONNECTION_FAILED', 'SSH connection/session timed out')
    except Exception as exc:
        # Vendor exceptions can contain credentials: report type, never raw text.
        return monitoring_error(result, stage, f'Operation failed ({type(exc).__name__})')
    finally:
        if connection is not None:
            try:
                connection.disconnect()
            except Exception:
                LOGGER.warning('SSH disconnect failed for %s', device['host'])


def load_alarm_file():
    """Migrate legacy counters; fail closed on invalid JSON rather than erase alarms."""
    if not Path(ALARM_TRACK_FILE).exists():
        return {}
    with open(ALARM_TRACK_FILE, encoding='utf-8') as stream:
        state = json.load(stream)
    if not isinstance(state, dict):
        raise ValueError('Alarm state must be an object')
    for hostname, record in state.items():
        if not isinstance(record, dict):
            raise ValueError('Invalid device record')
        if 'fpcs' not in record:
            state[hostname] = {'hostname': hostname, 'host': None, 'fpcs': {
                fpc: dict(value=value, state='ALARM' if value > 0 else 'CLEARED',
                          first_detected=None, last_seen=None) for fpc, value in record.items()}}
        for alarm in state[hostname]['fpcs'].values():
            if not isinstance(alarm['value'], int) or alarm['value'] < 0 or alarm['state'] not in ('ALARM', 'CLEARED'):
                raise ValueError('Invalid alarm record')
    return state


def save_alarm_file(alarms):
    """Write and flush a temporary file before atomic replacement."""
    target = Path(ALARM_TRACK_FILE)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                         delete=False) as stream:
            name = stream.name
            json.dump(alarms, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
        LOGGER.info('JSON state updated')
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def update_alarm_state(previous, results):
    """Preserve failed/unpolled devices and FPCs; zero from a full poll clears alarms."""
    state = copy.deepcopy(previous)
    events = []
    for result in results:
        if result['status'] == 'MONITORING_ERROR':
            events.append(dict(result, event='MONITORING_ERROR'))
            continue
        hostname, host = result['hostname'], result['host']
        old_key = next((k for k, r in state.items() if r.get('host') == host), hostname)
        record = state.pop(old_key, {'fpcs': {}})
        record.update(hostname=hostname, host=host)
        state[hostname] = record
        for fpc, value in result['counters'].items():
            old = record['fpcs'].get(fpc, {})
            previous_value = old.get('value', 0)
            active = old.get('state') == 'ALARM'
            event = ('ONGOING_ALARM' if active and value == previous_value else
                     'UPDATED_ALARM' if active else 'NEW_ALARM') if value > 0 else ('CLEARED' if active else None)
            LOGGER.info('%s FPC %s: %s -> %s (%s)', host, fpc, previous_value, value, event or 'NORMAL')
            if event:
                events.append(dict(event=event, hostname=hostname, host=host, fpc=fpc,
                                   fabric_drops=value, previous_value=previous_value,
                                   timestamp=result['timestamp']))
                record['fpcs'][fpc] = dict(old, value=value, state='ALARM' if value else 'CLEARED',
                    first_detected=old.get('first_detected') if active else result['timestamp'],
                    last_seen=result['timestamp'])
    return state, events


def handle_ticket_event(event):
    """MYCOM replaces this hook; receives NEW_ALARM, UPDATED_ALARM and CLEARED only."""
    LOGGER.info('MYCOM event: %s', json.dumps(event))


def build_reports(results, events):
    """Only unreachable errors appear in the monitoring-team report."""
    successful = [r for r in results if r['status'] != 'MONITORING_ERROR']
    unreachable = [r for r in results if r['error_type'] == 'DEVICE_UNREACHABLE']
    errors = [r for r in results if r['status'] == 'MONITORING_ERROR']
    monitoring = None
    if successful or unreachable:
        alarm = any(r['status'] == 'ALARM' for r in successful)
        subject = ('Fabric Drop Alarm Alert: BNG/CGNAT Requires Immediate Action' if alarm else
                   'No Fabric Drop Alarm Found on BNG/CGNAT' if successful else
                   'Fabric Drop Monitor - Connectivity Information')
        sections = []
        for result in successful:
            sections.append(f"Device: {result['hostname']}\n" + '\n'.join(
                f'FPC {fpc}: Fabric drops: {value}' for fpc, value in result['counters'].items()))
        for event in events:
            if event['event'] == 'CLEARED':
                sections.append(f"Device: {event['hostname']} FPC {event['fpc']}: CLEARED")
        if unreachable:
            sections.append('Connectivity Information\n\n' + '\n\n'.join(
                f"Device/IP: {r['host']}\nStatus: UNREACHABLE\nFabric Drop Status: NOT AVAILABLE\n"
                'Fabric Drop status could not be determined for this device.' for r in unreachable))
        if not errors and not alarm:
            sections = ['No changes in fabric drop alarms were detected across all BNG/CGNAT devices.']
        monitoring = subject, '\n\n'.join(sections)
    admin = None
    if errors:
        admin = 'Fabric Drop Monitor - Monitoring Error', '\n\n'.join(
            f"Device IP: {r['host']}\nDevice: {r['hostname'] or 'Unknown'}\nStatus: MONITORING_ERROR\n"
            f"Error Type: {r['error_type']}\nError: {r['error_message']}\n"
            f"Monitoring Result: NOT AVAILABLE\nTimestamp: {r['timestamp']}" for r in errors)
    return monitoring, admin






def send_email(subject, body, sender_email, receiver_email, *, password, smtp_host, smtp_port):
    """Return delivery success and log failures without exposing SMTP credentials."""
    if not receiver_email:
        LOGGER.error('Email not sent: recipient list is empty (%s)', subject)
        return False
    try:
        if not password or password == '<email password>':
            LOGGER.error('Email credentials missing')
            return False
        message = MIMEText(body)
        message['Subject'], message['From'] = subject, sender_email
        message['To'] = ', '.join(receiver_email)
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
            server.login(sender_email, password)
            refused = server.sendmail(sender_email, receiver_email, message.as_string())
        if refused:
            LOGGER.error('Email rejected for one or more recipients')
            return False
        LOGGER.info('Email sent: %s', subject)
        return True
    except Exception as exc:
        LOGGER.error('Email failed (%s)', type(exc).__name__)
        return False


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                        handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
    LOGGER.info('Monitoring started')
    try:
        previous = load_alarm_file()
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(execute_commands, d, COMMANDS): d for d in DEVICES}
            for future in concurrent.futures.as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append(monitoring_error(dict(host=futures[future]['host'], hostname=None,
                        connectivity=None, timestamp=timestamp()), 'WORKER_ERROR', type(exc).__name__))
        state, events = update_alarm_state(previous, results)
        save_alarm_file(state)
        for event in events:
            if event['event'] in ('NEW_ALARM', 'UPDATED_ALARM', 'CLEARED'):
                handle_ticket_event(event)
        monitoring, admin = build_reports(results, events)
        delivery_ok = True
        if monitoring:
            delivery_ok = send_email(*monitoring, EMAIL_SENDER, MONITORING_EMAIL_RECIPIENTS,
                                     password=EMAIL_PASSWORD, smtp_host=SMTP_HOST, smtp_port=SMTP_PORT)
        if admin:
            delivery_ok = send_email(*admin, EMAIL_SENDER, ADMIN_EMAIL_RECIPIENTS,
                              password=EMAIL_PASSWORD, smtp_host=SMTP_HOST, smtp_port=SMTP_PORT) and delivery_ok
        return 0 if delivery_ok else 1
    except Exception as exc:
        LOGGER.error('Monitoring cycle failed (%s)', type(exc).__name__)
        send_email('Fabric Drop Monitor - Monitoring Error',
                   f'Monitoring cycle failed: {type(exc).__name__}. Check logs/state.\nTimestamp: {timestamp()}',
                   EMAIL_SENDER, ADMIN_EMAIL_RECIPIENTS,
                              password=EMAIL_PASSWORD, smtp_host=SMTP_HOST, smtp_port=SMTP_PORT)
        return 1
    finally:
        LOGGER.info('Monitoring ended')


if __name__ == '__main__':
    raise SystemExit(main())
