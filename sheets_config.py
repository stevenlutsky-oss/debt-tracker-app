# Google Sheets Configuration
# Get credentials from Google Cloud Console

import os

# Use environment variables for secrets (set on Render)
# GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI

CREDENTIALS_FILE = 'credentials.json'

# The ID of your spreadsheet (from the Google Sheets URL)
# Example: https://docs.google.com/spreadsheets/d/THIS_IS_THE_ID/edit
SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID', '1wW-wPIQXxZhDwrxXAdzOb4ye3LXPa5aUYlXSGOvr9Yc')

# Sheet name where credit card balances are stored
SHEET_NAME = 'Accounts'

# Column mapping for your Google Sheet (via public CSV export)
# Based on actual header positions:
# Name = B (index 1)
# Current Balance = J (index 9)
# Credit Limit = K (index 10)
# Liability Interest Rate = O (index 14)
# Next Payment Due Date = R (index 17)
# Minimum Payment Amount = T (index 19)
COLUMN_MAPPING = {
    'card_name': 1,        # Column B - Name
    'balance': 9,          # Column J - Current Balance
    'credit_limit': 10,    # Column K - Credit Limit
    'apr': 14,             # Column O - Liability Interest Rate
    'due_date': 17,        # Column R - Next Payment Due Date
    'minimum_payment': 19, # Column T - Minimum Payment Amount
}
