"""Local device and SMTP credentials. Do not commit this file."""
DEVICE_USERNAME = ''
DEVICE_PASSWORD = ''
EMAIL_SENDER = 'nocwncore@globe.com.ph'
EMAIL_PASSWORD = ''




# SMTP, recipients, polling settings, and device inventory.
SMTP_HOST = 'smtp.gmail.com'
SMTP_PORT = 465
MONITORING_EMAIL_RECIPIENTS = ['nocwncore@globe.com.ph']
ADMIN_EMAIL_RECIPIENTS = ['jctuazon@globe.com.ph','mmrodas@globe.com.ph']
COMMANDS = [f'show pfe statistics traffic fpc {fpc} | match drop' for fpc in (2, 4, 8)]
MAX_WORKERS = 20
CONNECTIVITY_TIMEOUT = 3.0
CONNECTIVITY_PORT = None
DEVICES = [{'device_type': 'juniper_junos', 'host': 'IP_ADDRESS_1'},
           {'device_type': 'juniper_junos', 'host': 'IP_ADDRESS_2'},
           {'device_type': 'juniper_junos', 'host': 'IP_ADDRESS_3'}]
