import os
import sys
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from netmiko import ConnectHandler
import concurrent.futures
import keyring

# File to track previously reported alarms
ALARM_TRACK_FILE = "fabric_drops_alarms.json"

def load_alarm_file():
    """Load the alarm tracking JSON file."""
    if os.path.exists(ALARM_TRACK_FILE):
        with open(ALARM_TRACK_FILE, "r") as file:
            return json.load(file)
    return {}

def save_alarm_file(alarms):
    """Save the updated alarms to the JSON file."""
    with open(ALARM_TRACK_FILE, "w") as file:
        json.dump(alarms, file, indent=4)

def execute_commands(device, commands):
    """Execute commands on a device and return alarm data."""
    try:
        # Retrieve device password from keyring
        device['password'] = keyring.get_password("HUAWEI_Device", device['username'])
        if not device['password']:
            return None, {}

        net_connect = ConnectHandler(**device)

        # Retrieve hostname
        hostname_output = net_connect.send_command('show configuration system host-name | display set')
        hostname = hostname_output.split()[-1]  # Extract hostname

        device_alarms = {}
        for command in commands:
            output = net_connect.send_command(command)

            # Extract FPC number and Fabric drops value
            fpc_number = command.split("fpc")[1].strip().split()[0]
            fabric_drops_value = 0

            for line in output.splitlines():
                if "Fabric drops" in line:
                    fabric_drops_value = int(line.split(":")[-1].strip())
                    break

            # Only save if fabric drops are detected
            if fabric_drops_value > 0:
                device_alarms[fpc_number] = fabric_drops_value

        net_connect.disconnect()
        return hostname, device_alarms

    except:
        return None, {}

def send_email(subject, body, sender_email, receiver_email):
    """Send an email with the provided subject and body."""
    try:
        sender_password = keyring.get_password("email", sender_email)
        if not sender_password:
            return

        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = ", ".join(receiver_email) if isinstance(receiver_email, list) else receiver_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, receiver_email, msg.as_string())
        server.quit()

    except:
        pass

def main():
    devices = [
        {'device_type': 'juniper_junos', 'host': '10.166.38.209', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.44.241', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.205.251.149', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.38.161', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.38.145', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.205.252.21', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.38.193', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.188.45.45', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.205.252.149', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.38.184', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.38.225', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.205.248.21', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.44.193', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.44.209', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.205.249.149', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.205.249.21', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.44.225', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.205.248.149', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.56.201', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.66.90', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.56.200', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.64.64', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.64.66', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.56.203', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.56.248', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.56.202', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.64.68', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.64.67', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.56.204', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.64.70', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.66.92', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.56.205', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.66.91', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.66.99', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.66.103', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.56.206', 'username': '10012272'},
        {'device_type': 'juniper_junos', 'host': '10.166.56.207', 'username': '10012272'},
    ]


    commands = [
        'show pfe statistics traffic fpc 2 | match drop',
        'show pfe statistics traffic fpc 4 | match drop',
        'show pfe statistics traffic fpc 8 | match drop',
    ]

    # Load existing alarm data
    previous_alarm_data = load_alarm_file()
    current_alarm_data = {}
    alarm_outputs = []

    # Execute commands on all devices concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(execute_commands, device, commands) for device in devices]
        for future in concurrent.futures.as_completed(futures):
            hostname, device_alarms = future.result()
            if hostname and device_alarms:
                current_alarm_data[hostname] = device_alarms
                alarm_outputs.append(f"Device: {hostname}\n" +
                                     "\n".join([f"FPC {k}: Fabric drops: {v}" for k, v in device_alarms.items()]) +
                                     "\n" + "-" * 40)

    # Compare current alarms to previous alarms
    if current_alarm_data == previous_alarm_data or not alarm_outputs:
        email_subject = "No Fabric Drop Alarm Found on BNG/CGNAT"
        email_body = "No changes in fabric drop alarms were detected across all BNG/CGNAT devices."
    else:
        email_subject = "Fabric Drop Alarm Alert: BNG/CGNAT Requires Immediate Action"
        email_body = '\n'.join(alarm_outputs)

    # Save the current alarm data
    save_alarm_file(current_alarm_data)

    # Send the email
    send_email(
        subject=email_subject,
        body=email_body,
        sender_email="nocwncore@globe.com.ph",
        receiver_email=["nocwncore@globe.com.ph", "jctuazon@globe.com.ph"]
    )

    sys.exit()

if __name__ == "__main__":
    main()
