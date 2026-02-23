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
# Current Balance = J (index 10 after accounting for empty columns)
# Available Balance = K (index 11)
# Account Limit (Credit Limit) = N (index 14)
# Liability Interest Rate = O (index 15)
# Next Payment Due Date = R (index 18)
# Minimum Payment Amount = T (index 20)
COLUMN_MAPPING = {
    'card_name': 1,        # Column B - Name
    'balance': 10,         # Column J - Current Balance
    'available_balance': 11, # Column K - Available Balance
    'credit_limit': 14,    # Column N - Account Limit
    'apr': 15,             # Column O - Liability Interest Rate
    'due_date': 18,        # Column R - Next Payment Due Date
    'minimum_payment': 20, # Column T - Minimum Payment Amount
}
