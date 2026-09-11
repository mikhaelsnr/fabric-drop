import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import FABRIC_DROPS as monitor
import email_sender


def result(value=0, error=None, host='192.0.2.1'):
    return dict(host=host, hostname='router', status='MONITORING_ERROR' if error else 'ALARM' if value else 'NORMAL',
                connectivity='UNREACHABLE' if error == 'DEVICE_UNREACHABLE' else 'REACHABLE',
                counters={'2': value}, alarms={'2': value} if value else {},
                error_type=error, error_message='Test failure' if error else None, timestamp='2026-09-12T00:00:00Z')


class MonitoringTests(unittest.TestCase):
    @patch.object(monitor, 'DEVICE_USERNAME', 'shared-user')
    @patch.object(monitor, 'DEVICE_PASSWORD', 'shared-password')
    @patch.object(monitor, 'test_connectivity', return_value='REACHABLE')
    @patch.object(monitor, 'ConnectHandler')
    def test_shared_device_username(self, ssh, probe):
        for host in ('192.0.2.1', '192.0.2.2'):
            ssh.return_value.send_command.side_effect = ['set system host-name router', 'Fabric drops: 0']
            device = dict(device_type='juniper_junos', host=host)
            self.assertEqual(monitor.execute_commands(device, monitor.COMMANDS[:1])['status'], 'NORMAL')
            ssh.assert_called_with(**device, username='shared-user', password='shared-password')


    @patch.object(monitor, 'test_connectivity', return_value='REACHABLE')
    @patch.object(monitor, 'ConnectHandler')
    def test_coded_device_credentials(self, ssh, probe):
        device = dict(device_type='juniper_junos', host='192.0.2.1', username='test-user', password='test-password')
        ssh.return_value.send_command.side_effect = ['set system host-name router', 'Fabric drops: 0']
        polled = monitor.execute_commands(device, monitor.COMMANDS[:1])
        self.assertEqual(polled['status'], 'NORMAL')
        ssh.assert_called_once_with(**device)
        self.assertNotIn('password', polled)

    @patch.object(monitor, 'DEVICE_PASSWORD', '<password>')
    @patch.object(monitor, 'test_connectivity', return_value='REACHABLE')
    @patch.object(monitor, 'ConnectHandler')
    def test_missing_coded_password(self, ssh, probe):
        polled = monitor.execute_commands(dict(host='192.0.2.1', username='test-user'), monitor.COMMANDS)
        self.assertEqual(polled['error_type'], 'MISSING_CREDENTIALS')
        ssh.assert_not_called()

    def test_transitions(self):
        state = {}
        for value, expected in [(10, 'NEW_ALARM'), (10, 'ONGOING_ALARM'), (20, 'UPDATED_ALARM'), (0, 'CLEARED'), (10, 'NEW_ALARM')]:
            state, events = monitor.update_alarm_state(state, [result(value)])
            self.assertEqual(events[0]['event'], expected)
        self.assertEqual(state['router']['host'], '192.0.2.1')

    def test_errors_preserve_alarm_and_metadata(self):
        state, _ = monitor.update_alarm_state({}, [result(10)])
        state['router']['fpcs']['2']['snow_sys_id'] = 'external-reference'
        for error in ['DEVICE_UNREACHABLE', 'SSH_AUTHENTICATION_FAILED', 'SSH_CONNECTION_FAILED', 'CLI_PARSE_ERROR']:
            updated, events = monitor.update_alarm_state(state, [result(error=error)])
            self.assertEqual(updated, state)
            self.assertEqual([e['event'] for e in events], ['MONITORING_ERROR'])

    def test_partial_failure(self):
        state, _ = monitor.update_alarm_state({}, [result(10)])
        healthy = result(host='192.0.2.2')
        healthy['hostname'] = 'other'
        updated, _ = monitor.update_alarm_state(state, [result(error='DEVICE_UNREACHABLE'), healthy])
        self.assertEqual(updated['router'], state['router'])
        self.assertIn('other', updated)

    def test_unpolled_fpc_preserved(self):
        state, _ = monitor.update_alarm_state({}, [result(10)])
        poll = result()
        poll['counters'] = {'4': 0}
        updated, events = monitor.update_alarm_state(state, [poll])
        self.assertEqual(updated, state)
        self.assertEqual(events, [])

    def test_parser(self):
        self.assertEqual(monitor.parse_fabric_drops(' Fabric drops : 0\n'), 0)
        for output in ['', 'Fabric drops: abc', 'Fabric drops: -1', 'Fabric drops: 1.2', 'Fabric drops: 0\nFabric drops: 1']:
            with self.assertRaises(ValueError):
                monitor.parse_fabric_drops(output)

    @patch.object(monitor, 'ConnectHandler')
    @patch.object(monitor, 'test_connectivity', return_value='UNREACHABLE')
    def test_unreachable_skips_ssh(self, probe, ssh):
        polled = monitor.execute_commands(dict(device_type='juniper_junos', host='192.0.2.1', username='test-user'), monitor.COMMANDS)
        self.assertEqual(polled['error_type'], 'DEVICE_UNREACHABLE')
        ssh.assert_not_called()

    @patch.object(monitor, 'DEVICE_PASSWORD', 'test-secret')
    @patch.object(monitor, 'test_connectivity', return_value='REACHABLE')
    def test_reachable_ssh_errors(self, probe):
        for exception, error in [(monitor.NetmikoAuthenticationException, 'SSH_AUTHENTICATION_FAILED'),
                                 (monitor.NetmikoTimeoutException, 'SSH_CONNECTION_FAILED')]:
            with patch.object(monitor, 'ConnectHandler', side_effect=exception('secret')):
                polled = monitor.execute_commands(dict(device_type='juniper_junos', host='192.0.2.1', username='test-user'), monitor.COMMANDS)
            self.assertEqual(polled['error_type'], error)
            monitoring, admin = monitor.build_reports([polled], [])
            self.assertIsNone(monitoring)
            self.assertIn(error, admin[1])
            self.assertNotIn('secret', admin[1])

    @patch.object(monitor, 'DEVICE_PASSWORD', 'test-secret')
    @patch.object(monitor, 'test_connectivity', return_value='REACHABLE')
    @patch.object(monitor, 'ConnectHandler')
    def test_success_and_partial_parse_failure(self, ssh, probe):
        ssh.return_value.send_command.side_effect = ['set system host-name router', 'Fabric drops: 0', 'Fabric drops: 10', 'Fabric drops: 0']
        self.assertEqual(monitor.execute_commands(dict(device_type='juniper_junos', host='192.0.2.1', username='test-user'), monitor.COMMANDS)['status'], 'ALARM')
        ssh.return_value.send_command.side_effect = ['set system host-name router', 'Fabric drops: 0', 'bad output']
        polled = monitor.execute_commands(dict(device_type='juniper_junos', host='192.0.2.1', username='test-user'), monitor.COMMANDS)
        self.assertEqual(polled['error_type'], 'CLI_PARSE_ERROR')
        state, _ = monitor.update_alarm_state({}, [result(10)])
        self.assertEqual(monitor.update_alarm_state(state, [polled])[0], state)
        self.assertEqual(ssh.return_value.disconnect.call_count, 2)

    def test_email_routing(self):
        monitoring, admin = monitor.build_reports([result(), result(error='DEVICE_UNREACHABLE'), result(error='SSH_AUTHENTICATION_FAILED')], [])
        self.assertIn('Connectivity Information', monitoring[1])
        self.assertIn('NOT AVAILABLE', monitoring[1])
        self.assertNotIn('SSH_AUTHENTICATION_FAILED', monitoring[1])
        self.assertIn('SSH_AUTHENTICATION_FAILED', admin[1])
        self.assertEqual(monitoring[0], 'No Fabric Drop Alarm Found on BNG/CGNAT')
        only_unreachable, _ = monitor.build_reports([result(error='DEVICE_UNREACHABLE')], [])
        self.assertNotIn('No Fabric Drop', only_unreachable[0])

    @patch.object(monitor.socket, 'create_connection')
    def test_tcp_classification(self, connect):
        connect.return_value = MagicMock()
        self.assertEqual(monitor.test_connectivity('192.0.2.1', 22), 'REACHABLE')
        connect.side_effect = ConnectionRefusedError()
        self.assertEqual(monitor.test_connectivity('192.0.2.1', 22), 'REACHABLE')
        connect.side_effect = TimeoutError()
        self.assertEqual(monitor.test_connectivity('192.0.2.1', 22), 'UNREACHABLE')

    def test_atomic_write_migration_and_corruption(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            with patch.object(monitor, 'ALARM_TRACK_FILE', path):
                path.write_text(json.dumps({'router': {'2': 10}}))
                state = monitor.load_alarm_file()
                self.assertEqual(state['router']['fpcs']['2']['state'], 'ALARM')
                monitor.save_alarm_file(state)
                before = path.read_text()
                with patch.object(monitor.os, 'replace', side_effect=OSError('test')):
                    with self.assertRaises(OSError):
                        monitor.save_alarm_file({})
                self.assertEqual(path.read_text(), before)
                path.write_text('{broken')
                with self.assertRaises(ValueError):
                    monitor.load_alarm_file()
                self.assertEqual(path.read_text(), '{broken')

    @patch.object(email_sender.smtplib, 'SMTP_SSL')
    def test_email_delivery_failure(self, smtp):
        smtp.return_value.__enter__.return_value.sendmail.return_value = {}
        self.assertTrue(email_sender.send_email('test', 'body', 'sender', ['recipient'], password='test-secret', smtp_host='smtp.example.com', smtp_port=465))
        smtp.return_value.__enter__.return_value.login.assert_called_once_with('sender', 'test-secret')
        smtp.side_effect = OSError('test')
        self.assertFalse(email_sender.send_email('test', 'body', 'sender', ['recipient'], password='test-secret', smtp_host='smtp.example.com', smtp_port=465))
        self.assertFalse(email_sender.send_email('test', 'body', 'sender', [], password='test-secret', smtp_host='smtp.example.com', smtp_port=465))

    @patch.object(email_sender.smtplib, 'SMTP_SSL')
    def test_missing_email_password(self, smtp):
        self.assertFalse(email_sender.send_email('test', 'body', 'sender', ['recipient'], password='<email password>', smtp_host='smtp.example.com', smtp_port=465))
        smtp.assert_not_called()


if __name__ == '__main__':
    unittest.main()
