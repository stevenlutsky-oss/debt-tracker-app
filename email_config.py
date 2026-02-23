# Email Configuration for alerts
# Set these environment variables or edit directly

# SMTP Settings
SMTP_HOST = 'smtp.gmail.com'
SMTP_PORT = 587       # 587 for TLS, 465 for SSL
SMTP_USER = 'stevenlutsky@gmail.com'
SMTP_PASSWORD = 'uncj zdod vyqh gprx'

# Email settings
FROM_EMAIL = 'Debt Tracker <stevenlutsky@gmail.com>'
TO_EMAIL = 'stevenlutsky@gmail.com'

# Alert settings
ALERT_DAYS_BEFORE = 3  # Days before due date to send alert
LOW_BALANCE_THRESHOLD = 3000  # Alert if bank balance drops below this
