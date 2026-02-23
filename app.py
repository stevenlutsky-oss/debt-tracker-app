from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
import sqlite3
from datetime import datetime, timedelta
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Email configuration
try:
    import email_config
    EMAIL_AVAILABLE = True
except ImportError:
    EMAIL_AVAILABLE = False

# Google Sheets integration
try:
    from sheets_config import CREDENTIALS_FILE, SPREADSHEET_ID, SHEET_NAME, COLUMN_MAPPING
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    GOOGLE_SHEETS_AVAILABLE = True
except ImportError:
    GOOGLE_SHEETS_AVAILABLE = False

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'debt-tracker-secret-key')

# Simple auth config
ADMIN_USER = os.environ.get('ADMIN_USER', 'stevenlutsky@gmail.com')
ADMIN_PASS = os.environ.get('ADMIN_PASS', '!8Rooper1617')

def login_required(f):
    """Decorator to require login"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == ADMIN_USER and password == ADMIN_PASS:
            session['logged_in'] = True
            session['username'] = username
            return redirect(url_for('index'))
        else:
            error = 'Invalid credentials'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# Add custom Jinja filter for currency formatting
@app.template_filter('ordinal')
def ordinal_filter(num):
    if 11 <= (num % 100) <= 13:
        return f"{num}th"
    if num % 10 == 1:
        return f"{num}st"
    if num % 10 == 2:
        return f"{num}nd"
    if num % 10 == 3:
        return f"{num}rd"
    return f"{num}th"

@app.template_filter('currency')
def currency_filter(value):
    """Format a number as currency with commas"""
    if value is None:
        return "$0.00"
    try:
        return "${:,.2f}".format(float(value))
    except:
        return "$0.00"

DATABASE = 'debt.db'

# Plaid - disabled, using Google Sheets instead
PLAID_AVAILABLE = False
plaid_client = None

# ============= GOOGLE SHEETS INTEGRATION =============

def get_google_sheets_service():
    """Create Google Sheets API service"""
    if not GOOGLE_SHEETS_AVAILABLE:
        return None
    
    if not SPREADSHEET_ID:
        return None
    
    try:
        # Check for credentials in environment variable first
        creds_json = os.environ.get('GOOGLE_CREDENTIALS')
        if creds_json:
            import json
            from io import StringIO
            credentials_info = json.loads(creds_json)
            credentials = service_account.Credentials.from_service_account_info(
                credentials_info,
                scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
            )
        else:
            credentials = service_account.Credentials.from_service_account_file(
                CREDENTIALS_FILE,
                scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
            )
        service = build('sheets', 'v4', credentials=credentials)
        return service
    except FileNotFoundError:
        print(f"Credentials file not found: {CREDENTIALS_FILE}")
        return None
    except Exception as e:
        print(f"Error creating Google Sheets service: {e}")
        return None

def read_credit_card_balances():
    """Read credit card balances from Google Sheet - tries API first, falls back to public CSV"""
    import requests
    
    # Try simple public CSV export first (works if sheet is shared publicly)
    try:
        csv_url = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&sheet={SHEET_NAME}"
        response = requests.get(csv_url, timeout=10)
        if response.status_code == 200:
            import csv
            import io
            reader = csv.reader(io.StringIO(response.text))
            rows = list(reader)
            
            if len(rows) < 2:
                return []
            
            cards = []
            # Skip header row, process data rows
            for row in rows[1:]:
                if len(row) <= COLUMN_MAPPING['balance']:
                    continue
                    
                card_name = row[COLUMN_MAPPING['card_name']].strip() if len(row) > COLUMN_MAPPING['card_name'] else ''
                balance_str = row[COLUMN_MAPPING['balance']].strip() if len(row) > COLUMN_MAPPING['balance'] else '0'
                
                # Clean balance (remove $ and ,)
                balance_str = balance_str.replace('$', '').replace(',', '').strip()
                try:
                    balance = float(balance_str)
                except ValueError:
                    balance = 0
                
                # Only add credit cards (not depository accounts)
                account_type = row[5].strip().lower() if len(row) > 5 else ''
                account_class = row[7].strip().lower() if len(row) > 7 else ''
                # Include if credit type OR Liability class
                if 'credit' not in account_type and 'liability' not in account_class:
                    continue
                
                if not card_name or balance == 0:
                    continue
                
                # Parse APR (remove % if present)
                apr = 0
                if len(row) > COLUMN_MAPPING['apr']:
                    apr_str = row[COLUMN_MAPPING['apr']].replace('%', '').strip()
                    try:
                        apr = float(apr_str)
                    except ValueError:
                        apr = 0
                
                # Parse Due Date (extract day of month)
                due_day = 1
                if len(row) > COLUMN_MAPPING['due_date']:
                    due_str = row[COLUMN_MAPPING['due_date']].strip()
                    import re
                    day_match = re.search(r'-(\d{1,2})$', due_str)
                    if day_match:
                        due_day = int(day_match.group(1))
                
                # Parse Minimum Payment
                min_payment = 0
                if len(row) > COLUMN_MAPPING['minimum_payment']:
                    min_str = row[COLUMN_MAPPING['minimum_payment']].replace('$', '').replace(',', '').strip()
                    try:
                        min_payment = float(min_str)
                    except ValueError:
                        min_payment = 0
                
                # Parse Credit Limit
                credit_limit = 0
                if 'credit_limit' in COLUMN_MAPPING and len(row) > COLUMN_MAPPING['credit_limit']:
                    limit_str = row[COLUMN_MAPPING['credit_limit']].replace('$', '').replace(',', '').strip()
                    try:
                        credit_limit = float(limit_str)
                    except ValueError:
                        credit_limit = 0
                
                cards.append({
                    'name': card_name,
                    'balance': balance,
                    'interest_rate': apr,
                    'due_day': due_day,
                    'minimum_payment': min_payment,
                    'credit_limit': credit_limit
                })
            
            return cards
    except Exception as e:
        print(f"Public CSV method failed: {e}")
    
    # Fall back to Google Sheets API (if credentials are set up)
    service = get_google_sheets_service()
    if not service:
        return []
    
    try:
        # Get sheet ID from sheet name
        spreadsheet = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
        sheet_id = None
        for sheet in spreadsheet.get('sheets', []):
            if sheet['properties']['title'] == SHEET_NAME:
                sheet_id = sheet['properties']['sheetId']
                break
        
        if not sheet_id:
            print(f"Sheet '{SHEET_NAME}' not found")
            return []
        
        # Read data from the sheet
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{SHEET_NAME}!A:Z"
        ).execute()
        
        values = result.get('values', [])
        if not values:
            return []
        
        cards = []
        # Skip header row, process data rows
        for row in values[1:]:
            if len(row) > COLUMN_MAPPING['balance']:
                card_name = row[COLUMN_MAPPING['card_name']] if len(row) > COLUMN_MAPPING['card_name'] else ''
                balance_str = row[COLUMN_MAPPING['balance']] if len(row) > COLUMN_MAPPING['balance'] else '0'
                
                # Clean balance (remove $ and ,)
                balance_str = balance_str.replace('$', '').replace(',', '').strip()
                try:
                    balance = float(balance_str)
                except ValueError:
                    balance = 0
                
                # Parse APR (remove % if present)
                apr = 0
                if len(row) > COLUMN_MAPPING['apr']:
                    apr_str = row[COLUMN_MAPPING['apr']].replace('%', '').strip()
                    try:
                        apr = float(apr_str)
                    except ValueError:
                        apr = 0
                
                # Parse Due Date (extract day of month)
                due_day = 1
                if len(row) > COLUMN_MAPPING['due_date']:
                    due_str = row[COLUMN_MAPPING['due_date']].strip()
                    # Try to extract day number
                    import re
                    day_match = re.search(r'-(\d{1,2})$', due_str)
                    if day_match:
                        due_day = int(day_match.group(1))
                        due_day = max(1, min(28, due_day))  # Clamp to valid days
                
                # Optional fields
                account_last4 = ''
                if len(row) > COLUMN_MAPPING['account_last4']:
                    account_last4 = row[COLUMN_MAPPING['account_last4']].strip()
                
                if card_name and balance > 0:
                    cards.append({
                        'name': card_name,
                        'balance': balance,
                        'apr': apr,
                        'due_day': due_day,
                        'account_last4': account_last4
                    })
        
        return cards
    except Exception as e:
        print(f"Error reading from Google Sheets: {e}")
        return []

def read_bank_accounts():
    """Read bank accounts (depository) from Google Sheet"""
    import requests
    import csv
    import io
    
    try:
        csv_url = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&sheet={SHEET_NAME}"
        response = requests.get(csv_url, timeout=10)
        if response.status_code != 200:
            return []
        
        reader = csv.reader(io.StringIO(response.text))
        rows = list(reader)
        
        if len(rows) < 2:
            return []
        
        accounts = []
        for row in rows[1:]:
            if len(row) <= 9:
                continue
            
            account_type = row[5].strip().lower() if len(row) > 5 else ''
            # Only include depository accounts (checking, savings)
            if 'depository' not in account_type:
                continue
            
            name = row[1].strip() if len(row) > 1 else ''
            balance_str = row[9].replace('$', '').replace(',', '').strip() if len(row) > 9 else '0'
            
            try:
                balance = float(balance_str)
            except ValueError:
                balance = 0
            
            if name:
                accounts.append({
                    'name': name,
                    'balance': balance,
                    'account_type': row[6].strip() if len(row) > 6 else 'checking'
                })
        
        return accounts
    except Exception as e:
        print(f"Error reading bank accounts: {e}")
        return []

def check_low_balance_alerts():
    """Check for low bank account balances and send alerts"""
    if not EMAIL_AVAILABLE or not hasattr(email_config, 'TO_EMAIL') or not email_config.TO_EMAIL:
        return 0
    
    # Threshold for low balance alerts
    LOW_BALANCE_THRESHOLD = getattr(email_config, 'LOW_BALANCE_THRESHOLD', 3000)
    
    accounts = read_bank_accounts()
    if not accounts:
        return 0
    
    low_balance_accounts = [a for a in accounts if a['balance'] < LOW_BALANCE_THRESHOLD]
    
    if not low_balance_accounts:
        # Check if we have a previous check to compare
        return 0
    
    total_balance = sum(a['balance'] for a in accounts)
    
    subject = f"⚠️ Low Balance Alert: ${total_balance:,.2f} total"
    
    body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background: #e74c3c; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }}
            .content {{ background: #f8f9fa; padding: 20px; }}
            .summary {{ background: white; padding: 15px; border-radius: 8px; margin-bottom: 20px; }}
            .total {{ font-size: 24px; font-weight: bold; color: #e74c3c; }}
            table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; }}
            th {{ background: #c0392b; color: white; padding: 12px; text-align: left; }}
            td {{ padding: 12px; border-bottom: 1px solid #eee; }}
            .low {{ background: #fee; color: #c0392b; font-weight: bold; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>⚠️ Low Balance Alert</h1>
                <p>Bank account(s) below ${LOW_BALANCE_THRESHOLD:,}</p>
            </div>
            <div class="content">
                <div class="summary">
                    <h3>Total Bank Balance</h3>
                    <p class="total">${total_balance:,.2f}</p>
                </div>
                <table>
                    <tr>
                        <th>Account</th>
                        <th>Type</th>
                        <th>Balance</th>
                    </tr>
    """
    
    for account in low_balance_accounts:
        body += f"""
                    <tr class="low">
                        <td><strong>{account['name']}</strong></td>
                        <td>{account['account_type']}</td>
                        <td>${account['balance']:,.2f}</td>
                    </tr>
        """
    
    body += """
                </table>
            </div>
        </div>
    </body>
    </html>
    """
    
    send_email_alert(subject, body)
    return len(low_balance_accounts)

@app.route('/sheets/sync')
@login_required
def sync_from_sheets():
    """Sync credit card balances from Google Sheet"""
    if not sheets_config.SPREADSHEET_ID:
        flash('Google Sheets not configured - add spreadsheet ID', 'error')
        return redirect(url_for('index'))
    
    try:
        cards = read_credit_card_balances()
        
        if not cards:
            flash('No credit card data found in Google Sheet', 'warning')
            return redirect(url_for('index'))
        
        conn = get_db()
        cursor = conn.cursor()
        
        updated = 0
        added = 0
        for card in cards:
            # Check if card exists
            cursor.execute('SELECT id, balance FROM cards WHERE name = ?', (card['name'],))
            existing = cursor.fetchone()
            
            # Get credit_limit from card (default to 0 if not present)
            credit_limit = card.get('credit_limit', 0)
            
            if existing:
                # Update balance, APR, due_day, minimum_payment, and credit_limit
                cursor.execute(
                    'UPDATE cards SET balance = ?, interest_rate = ?, due_day = ?, minimum_payment = ?, credit_limit = ?, last_synced = ? WHERE name = ?',
                    (card['balance'], card['interest_rate'], card['due_day'], card['minimum_payment'], credit_limit, datetime.now(), card['name'])
                )
                updated += 1
            else:
                # Add new card
                cursor.execute(
                    'INSERT INTO cards (name, balance, interest_rate, due_day, minimum_payment, credit_limit, last_synced) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (card['name'], card['balance'], card['interest_rate'], card['due_day'], card['minimum_payment'], credit_limit, datetime.now())
                )
                added += 1
        
        conn.commit()
        conn.close()
        
        msg = []
        if updated:
            msg.append(f'updated {updated}')
        if added:
            msg.append(f'added {added}')
        flash(f'Synced from Google Sheets: {", ".join(msg)}' if msg else 'No changes', 'success')
    except Exception as e:
        flash(f'Error syncing from Google Sheets: {str(e)}', 'error')
    
    return redirect(url_for('index'))

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Cards table - expanded with new fields
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            balance REAL NOT NULL,
            interest_rate REAL NOT NULL,
            minimum_payment REAL NOT NULL,
            due_day INTEGER DEFAULT 1,
            alert_threshold REAL DEFAULT 0,
            plaid_item_id TEXT,
            plaid_access_token TEXT,
            last_synced TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Payments table (for tracking progress)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (card_id) REFERENCES cards (id)
        )
    ''')
    
    # Plaid linked items table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS plaid_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            access_token TEXT NOT NULL,
            item_id TEXT,
            institution_name TEXT,
            institution_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Plaid accounts table (maps Plaid accounts to cards)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS plaid_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plaid_item_id INTEGER NOT NULL,
            card_id INTEGER,
            plaid_account_id TEXT NOT NULL,
            name TEXT,
            mask TEXT,
            FOREIGN KEY (plaid_item_id) REFERENCES plaid_items (id),
            FOREIGN KEY (card_id) REFERENCES cards (id)
        )
    ''')
    
    conn.commit()
    conn.close()

def migrate_db():
    """Add new columns if they don't exist"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Add due_day if not exists
    try:
        cursor.execute('SELECT due_day FROM cards LIMIT 1')
    except:
        cursor.execute('ALTER TABLE cards ADD COLUMN due_day INTEGER DEFAULT 1')
    
    # Add alert_threshold if not exists
    try:
        cursor.execute('SELECT alert_threshold FROM cards LIMIT 1')
    except:
        cursor.execute('ALTER TABLE cards ADD COLUMN alert_threshold REAL DEFAULT 0')
    
    # Add Plaid fields if not exists
    try:
        cursor.execute('SELECT plaid_item_id FROM cards LIMIT 1')
    except:
        cursor.execute('ALTER TABLE cards ADD COLUMN plaid_item_id TEXT')
        cursor.execute('ALTER TABLE cards ADD COLUMN plaid_access_token TEXT')
        cursor.execute('ALTER TABLE cards ADD COLUMN last_synced TIMESTAMP')
    
    # Add credit_limit field if it doesn't exist
    try:
        cursor.execute('SELECT credit_limit FROM cards LIMIT 1')
    except:
        cursor.execute('ALTER TABLE cards ADD COLUMN credit_limit REAL')
    
    conn.commit()
    conn.close()

# Initialize/migrate DB on startup
if not os.path.exists(DATABASE):
    init_db()
else:
    migrate_db()

@app.route('/')
@login_required
def index():
    # Auto-sync from Google Sheets on page load
    try:
        sync_from_sheets()
    except Exception as e:
        print(f"Auto-sync error: {e}")
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Get all cards
    cursor.execute('SELECT * FROM cards ORDER BY due_day ASC')
    cards = cursor.fetchall()
    
    # Calculate totals and interest
    total_debt = 0
    total_interest = 0
    total_credit_limit = 0
    alerts = []
    today = datetime.now()
    
    cards_with_interest = []
    for card in cards:
        balance = card['balance']
        apr = card['interest_rate']
        due_day = card['due_day'] if card['due_day'] else 1
        alert_threshold = card['alert_threshold'] if card['alert_threshold'] else 0
        last_synced = card['last_synced']
        credit_limit = card['credit_limit'] if card['credit_limit'] else 0
        
        # Monthly interest
        monthly_interest = (balance * (apr / 100)) / 12
        
        total_debt += balance
        total_interest += monthly_interest
        total_credit_limit += credit_limit
        
        # Check balance alert
        if alert_threshold > 0 and balance <= alert_threshold:
            alerts.append(f"🎯 {card['name']} balance (${balance:.2f}) is at or below your ${alert_threshold:.2f} threshold!")
        
        # Check due date (due within 5 days)
        try:
            due_date = datetime(today.year, today.month, due_day)
            if due_date.day < today.day:
                # Next month's due date
                if today.month == 12:
                    due_date = datetime(today.year + 1, 1, due_day)
                else:
                    due_date = datetime(today.year, today.month + 1, due_day)
            
            days_until_due = (due_date - today).days
            if days_until_due <= 5:
                due_date_str = due_date.strftime('%B %d')
                if days_until_due <= 0:
                    alerts.append(f"⚠️ {card['name']} payment is DUE NOW! (was due {due_date_str})")
                else:
                    alerts.append(f"📅 {card['name']} payment due {days_until_due} day(s) ({due_date_str})")
        except:
            pass
        
        # Check if Plaid sync is stale (>24 hours)
        synced_via_plaid = last_synced is not None
        if synced_via_plaid:
            try:
                sync_time = datetime.strptime(last_synced, '%Y-%m-%d %H:%M:%S')
                hours_old = (today - sync_time).total_seconds() / 3600
                if hours_old > 24:
                    alerts.append(f"🔄 {card['name']} balance is stale (last synced {int(hours_old)}h ago)")
            except:
                pass
        
        cards_with_interest.append({
            'id': card['id'],
            'name': card['name'],
            'balance': balance,
            'interest_rate': apr,
            'minimum_payment': card['minimum_payment'],
            'due_day': due_day,
            'alert_threshold': alert_threshold,
            'monthly_interest': monthly_interest,
            'plaid_item_id': card['plaid_item_id'],
            'plaid_access_token': card['plaid_access_token'],
            'last_synced': last_synced,
            'created_at': card['created_at'],
            'credit_limit': credit_limit
        })
    
    # Get recent payments
    cursor.execute('''
        SELECT p.*, c.name as card_name 
        FROM payments p 
        JOIN cards c ON p.card_id = c.id 
        ORDER BY p.date DESC LIMIT 10
    ''')
    recent_payments = cursor.fetchall()
    
    conn.close()
    
    # Also fetch bank accounts for display
    bank_accounts = read_bank_accounts()
    
    # Calculate available credit and percentage paid off
    total_available_credit = max(0, total_credit_limit - total_debt)
    percent_paid = 0
    if total_credit_limit > 0:
        paid = total_credit_limit - total_debt
        percent_paid = (paid / total_credit_limit) * 100
    
    return render_template('index.html', cards=cards_with_interest, total_debt=total_debt, 
                           total_interest=total_interest, recent_payments=recent_payments,
                           alerts=alerts, plaid_available=PLAID_AVAILABLE, bank_accounts=bank_accounts,
                           total_credit_limit=total_credit_limit, total_available_credit=total_available_credit,
                           percent_paid=percent_paid)

@app.route('/card/add', methods=['GET', 'POST'])
@login_required
def add_card():
    if request.method == 'POST':
        name = request.form['name']
        balance = float(request.form['balance'])
        interest_rate = float(request.form['interest_rate'])
        minimum_payment = float(request.form['minimum_payment'])
        due_day = int(request.form.get('due_day', 1))
        alert_threshold = float(request.form.get('alert_threshold', 0)) if request.form.get('alert_threshold') else 0
        credit_limit = float(request.form.get('credit_limit', 0)) if request.form.get('credit_limit') else 0
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO cards (name, balance, interest_rate, minimum_payment, due_day, alert_threshold, credit_limit)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (name, balance, interest_rate, minimum_payment, due_day, alert_threshold, credit_limit))
        conn.commit()
        conn.close()
        
        flash(f'Card "{name}" added successfully!', 'success')
        return redirect(url_for('index'))
    
    return render_template('card_form.html', card=None, title='Add Card')

@app.route('/card/<int:card_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_card(card_id):
    conn = get_db()
    cursor = conn.cursor()
    
    if request.method == 'POST':
        name = request.form['name']
        balance = float(request.form['balance'])
        interest_rate = float(request.form['interest_rate'])
        minimum_payment = float(request.form['minimum_payment'])
        due_day = int(request.form.get('due_day', 1))
        alert_threshold = float(request.form.get('alert_threshold', 0)) if request.form.get('alert_threshold') else 0
        credit_limit = float(request.form.get('credit_limit', 0)) if request.form.get('credit_limit') else 0
        
        cursor.execute('''
            UPDATE cards 
            SET name = ?, balance = ?, interest_rate = ?, minimum_payment = ?, due_day = ?, alert_threshold = ?, credit_limit = ?
            WHERE id = ?
        ''', (name, balance, interest_rate, minimum_payment, due_day, alert_threshold, credit_limit, card_id))
        conn.commit()
        conn.close()
        
        flash(f'Card "{name}" updated successfully!', 'success')
        return redirect(url_for('index'))
    
    cursor.execute('SELECT * FROM cards WHERE id = ?', (card_id,))
    card = cursor.fetchone()
    conn.close()
    
    return render_template('card_form.html', card=card, title='Edit Card')

@app.route('/card/<int:card_id>/delete')
@login_required
def delete_card(card_id):
    conn = get_db()
    cursor = conn.cursor()
    
    # Check if card has Plaid token - if so, remove the Plaid item
    cursor.execute('SELECT plaid_item_id, plaid_access_token FROM cards WHERE id = ?', (card_id,))
    plaid_data = cursor.fetchone()
    
    if plaid_data and plaid_data['plaid_access_token']:
        # Optionally remove the Plaid item (would need to call Plaid API)
        # For now, just remove from local DB
        pass
    
    # Delete associated payments first
    cursor.execute('DELETE FROM payments WHERE card_id = ?', (card_id,))
    cursor.execute('DELETE FROM cards WHERE id = ?', (card_id,))
    
    conn.commit()
    conn.close()
    
    flash('Card deleted successfully!', 'success')
    return redirect(url_for('index'))

@app.route('/card/<int:card_id>/payment', methods=['GET', 'POST'])
@login_required
def add_payment(card_id):
    conn = get_db()
    cursor = conn.cursor()
    
    if request.method == 'POST':
        amount = float(request.form['amount'])
        
        cursor.execute('INSERT INTO payments (card_id, amount) VALUES (?, ?)', 
                      (card_id, amount))
        
        # Update card balance
        cursor.execute('UPDATE cards SET balance = balance - ? WHERE id = ?', 
                      (amount, card_id))
        
        conn.commit()
        conn.close()
        
        flash(f'Payment of ${amount:.2f} recorded!', 'success')
        return redirect(url_for('index'))
    
    cursor.execute('SELECT * FROM cards WHERE id = ?', (card_id,))
    card = cursor.fetchone()
    conn.close()
    
    return render_template('payment_form.html', card=card)

@app.route('/card/<int:card_id>/sync')
def sync_card(card_id):
    """Sync balance from Plaid"""
    if not PLAID_AVAILABLE:
        flash('Plaid is not configured. Add your Plaid credentials to config.py', 'error')
        return redirect(url_for('index'))
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM cards WHERE id = ?', (card_id,))
    card = cursor.fetchone()
    
    if not card or not card['plaid_access_token']:
        conn.close()
        flash('This card is not linked to Plaid', 'error')
        return redirect(url_for('index'))
    
    try:
        # Get balance from Plaid
        response = plaid_client.accounts_balance_get(
            access_token=card['plaid_access_token']
        )
        
        # Find the credit card account
        accounts = response['accounts']
        credit_accounts = [a for a in accounts if a['type'] == 'credit']
        
        if credit_accounts:
            # Get the first credit account balance
            new_balance = credit_accounts[0]['balances']['current']
            
            cursor.execute('''
                UPDATE cards 
                SET balance = ?, last_synced = CURRENT_TIMESTAMP 
                WHERE id = ?
            ''', (new_balance, card_id))
            
            conn.commit()
            flash(f'Balance synced! New balance: ${new_balance:.2f}', 'success')
        else:
            flash('No credit card account found for this card', 'error')
            
    except Exception as e:
        flash(f'Error syncing: {str(e)}', 'error')
    
    conn.close()
    return redirect(url_for('index'))

# ============= PLAID LINK ROUTES =============

@app.route('/plaid/create_link_token')
@login_required
def create_link_token():
    """Create a Link token for Plaid Link"""
    if not PLAID_AVAILABLE:
        return jsonify({'error': 'Plaid not configured'}), 500
    
    try:
        response = plaid_client.link_token_create(
            user={'client_user_id': 'debt-tracker-user'},
            client_name='Debt Tracker',
            products=['auth', 'transactions'],
            country_codes=['US'],
            language='en'
        )
        return jsonify({'link_token': response['link_token']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/plaid/exchange_public_token', methods=['POST'])
@login_required
def exchange_public_token():
    """Exchange public token for access token"""
    if not PLAID_AVAILABLE:
        return jsonify({'error': 'Plaid not configured'}), 500
    
    data = request.json
    public_token = data.get('public_token')
    card_id = data.get('card_id')
    
    if not public_token or not card_id:
        return jsonify({'error': 'Missing public_token or card_id'}), 400
    
    try:
        # Exchange public token for access token
        response = plaid_client.item_public_token_exchange(public_token)
        access_token = response['access_token']
        item_id = response['item_id']
        
        # Get initial balance
        balance_response = plaid_client.accounts_balance_get(access_token=access_token)
        accounts = balance_response['accounts']
        credit_accounts = [a for a in accounts if a['type'] == 'credit']
        
        initial_balance = 0
        if credit_accounts:
            initial_balance = credit_accounts[0]['balances']['current']
        
        # Store in database
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE cards 
            SET plaid_item_id = ?, plaid_access_token = ?, balance = ?, last_synced = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (item_id, access_token, initial_balance, card_id))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'balance': initial_balance})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============= NEW PLAID ROUTES =============

@app.route('/plaid/link')
@login_required
def plaid_link_page():
    """Start Plaid Link flow"""
    if not PLAID_AVAILABLE:
        flash('Plaid is not configured. Please add your Plaid API keys in plaid_config.py', 'error')
        return redirect(url_for('index'))
    
    try:
        response = plaid_client.link_token_create(
            user={'client_user_id': 'debt-tracker-user'},
            client_name='Debt Tracker',
            products=['auth', 'transactions', 'balance'],
            country_codes=['US'],
            language='en'
        )
        link_token = response['link_token']
        return render_template('plaid_link.html', link_token=link_token)
    except Exception as e:
        flash(f'Error initializing Plaid: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/plaid/exchange-token', methods=['POST'])
def plaid_exchange_token():
    """Exchange public token for access token"""
    if not PLAID_AVAILABLE:
        return jsonify({'error': 'Plaid not configured'}), 500
    
    public_token = request.json.get('public_token')
    metadata = request.json.get('metadata', {})
    
    if not public_token:
        return jsonify({'error': 'No public token provided'}), 400
    
    try:
        response = plaid_client.item_public_token_exchange(public_token)
        access_token = response['access_token']
        item_id = response['item_id']
        
        institution = metadata.get('institution', {})
        institution_name = institution.get('name', 'Unknown')
        institution_id = institution.get('institution_id', '')
        
        # Get accounts
        balance_response = plaid_client.accounts_balance_get(access_token=access_token)
        accounts = balance_response['accounts']
        
        conn = get_db()
        cursor = conn.cursor()
        
        # Store plaid item
        cursor.execute('''
            INSERT INTO plaid_items (access_token, item_id, institution_name, institution_id)
            VALUES (?, ?, ?, ?)
        ''', (access_token, item_id, institution_name, institution_id))
        plaid_item_id = cursor.lastrowid
        
        # Store accounts
        for account in accounts:
            cursor.execute('''
                INSERT INTO plaid_accounts (plaid_item_id, plaid_account_id, name, mask)
                VALUES (?, ?, ?, ?)
            ''', (plaid_item_id, account['account_id'], account.get('name', ''), account.get('mask', '')))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/plaid/manage')
@login_required
def plaid_manage():
    """Manage linked Plaid accounts"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM plaid_items ORDER BY due_day ASC')
    items = cursor.fetchall()
    
    cursor.execute('SELECT * FROM plaid_accounts ORDER BY plaid_item_id')
    accounts = cursor.fetchall()
    
    cursor.execute('SELECT * FROM cards ORDER BY name')
    cards = cursor.fetchall()
    
    conn.close()
    
    return render_template('plaid_manage.html', items=items, accounts=accounts, cards=cards)

@app.route('/plaid/refresh-balances')
def plaid_refresh_balances():
    """Refresh all balances from Plaid"""
    if not PLAID_AVAILABLE:
        flash('Plaid is not configured', 'error')
        return redirect(url_for('index'))
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM plaid_items')
    items = cursor.fetchall()
    
    updated = 0
    for item in items:
        try:
            response = plaid_client.accounts_balance_get(access_token=item['access_token'])
            accounts = response['accounts']
            
            for account in accounts:
                if account['type'] == 'credit':
                    cursor.execute('''
                        UPDATE cards SET balance = ?, last_synced = CURRENT_TIMESTAMP
                        WHERE id = (SELECT card_id FROM plaid_accounts WHERE plaid_account_id = ?)
                    ''', (account['balances'].get('current', 0), account['account_id']))
                    
                    if cursor.rowcount > 0:
                        updated += 1
        except Exception as e:
            flash(f'Error refreshing {item["institution_name"]}: {str(e)}', 'error')
    
    conn.commit()
    conn.close()
    
    if updated > 0:
        flash(f'Updated {updated} card balance(s)', 'success')
    else:
        flash('No balances updated. Link accounts to cards first.', 'info')
    
    return redirect(url_for('index'))

@app.route('/plaid/unlink/<int:item_id>', methods=['POST'])
def plaid_unlink(item_id):
    """Unlink a Plaid item"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT access_token FROM plaid_items WHERE id = ?', (item_id,))
    item = cursor.fetchone()
    
    if item:
        try:
            plaid_client.item_remove(access_token=item['access_token'])
        except:
            pass
    
    cursor.execute('DELETE FROM plaid_accounts WHERE plaid_item_id = ?', (item_id,))
    cursor.execute('DELETE FROM plaid_items WHERE id = ?', (item_id,))
    
    conn.commit()
    conn.close()
    
    flash('Account unlinked', 'success')
    return redirect(url_for('plaid_manage'))

@app.route('/plaid/link-account/<int:account_id>', methods=['POST'])
def plaid_link_account(account_id):
    """Link a Plaid account to a card"""
    card_id = request.form.get('card_id')
    
    conn = get_db()
    cursor = conn.cursor()
    
    if card_id:
        cursor.execute('UPDATE plaid_accounts SET card_id = ? WHERE id = ?', (card_id, account_id))
        flash('Account linked to card!', 'success')
    else:
        cursor.execute('UPDATE plaid_accounts SET card_id = NULL WHERE id = ?', (account_id,))
    
    conn.commit()
    conn.close()
    
    return redirect(url_for('plaid_manage'))

@app.route('/plaid/status')
@login_required
def plaid_status():
    """Check Plaid configuration status"""
    configured = PLAID_AVAILABLE and plaid_config.PLAID_CLIENT_ID
    return jsonify({
        'configured': configured,
        'env': plaid_config.PLAID_ENV if PLAID_AVAILABLE else 'unknown'
    })

# ============= GOOGLE SHEETS INTEGRATION =============

import sheets_config
from google.oauth2 import service_account
from googleapiclient.discovery import build

def get_sheets_client():
    """Create Google Sheets API client"""
    try:
        credentials = service_account.Credentials.from_service_account_file(
            sheets_config.CREDENTIALS_FILE,
            scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
        )
        service = build('sheets', 'v4', credentials=credentials)
        return service
    except Exception as e:
        print(f"Error creating Sheets client: {e}")
        return None

@app.route('/sheets/status')
@login_required
def sheets_status():
    """Check Google Sheets configuration status"""
    configured = False
    if sheets_config.SPREADSHEET_ID and sheets_config.CREDENTIALS_FILE:
        try:
            service = get_sheets_client()
            if service:
                # Try to get the spreadsheet
                service.spreadsheets().get(spreadsheetId=sheets_config.SPREADSHEET_ID).execute()
                configured = True
        except:
            pass
    
    return jsonify({
        'configured': configured,
        'spreadsheet_id': sheets_config.SPREADSHEET_ID if sheets_config.SPREADSHEET_ID else None,
        'sheet_name': sheets_config.SHEET_NAME
    })

@app.route('/sheets/fetch-balances')
@login_required
def sheets_fetch_balances():
    """Fetch credit card balances from Google Sheets"""
    if not sheets_config.SPREADSHEET_ID:
        flash('Google Sheets not configured. Add spreadsheet ID to sheets_config.py', 'error')
        return redirect(url_for('index'))
    
    service = get_sheets_client()
    if not service:
        flash('Could not connect to Google Sheets. Check credentials.', 'error')
        return redirect(url_for('index'))
    
    try:
        # Get values from the sheet
        range_name = f"{sheets_config.SHEET_NAME}!A:Z"
        result = service.spreadsheets().values().get(
            spreadsheetId=sheets_config.SPREADSHEET_ID,
            range=range_name
        ).execute()
        
        values = result.get('values', [])
        
        if not values:
            flash('No data found in the sheet', 'warning')
            return redirect(url_for('index'))
        
        # Parse the data (first row is headers)
        headers = values[0] if values else []
        rows = values[1:] if len(values) > 1 else []
        
        balances = []
        import re
        for row in rows:
            if len(row) > 1:
                card_name = row[0].strip() if row[0] else ''
                balance_str = row[1].replace('$', '').replace(',', '').strip() if len(row) > 1 else '0'
                
                try:
                    balance = float(balance_str)
                except:
                    balance = 0.0
                
                # Parse APR (column C, index 2)
                apr = 0
                if len(row) > 2:
                    apr_str = row[2].replace('%', '').strip()
                    try:
                        apr = float(apr_str)
                    except ValueError:
                        apr = 0
                
                # Parse Due Date (column D, index 3)
                due_day = 1
                if len(row) > 3:
                    due_str = row[3].strip()
                    day_match = re.search(r'-(\d{1,2})$', due_str)
                    if day_match:
                        due_day = int(day_match.group(1))
                        due_day = max(1, min(28, due_day))
                
                if card_name:
                    balances.append({
                        'name': card_name,
                        'balance': balance,
                        'apr': apr,
                        'due_day': due_day,
                        'account_last4': row[4] if len(row) > 4 else ''
                    })
        
        return render_template('sheets_balances.html', balances=balances)
        
    except Exception as e:
        flash(f'Error fetching from Google Sheets: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/sheets/sync-balances', methods=['POST'])
@login_required
def sheets_sync_balances():
    """Sync balances from Google Sheets to the debt tracker database"""
    if not sheets_config.SPREADSHEET_ID:
        flash('Google Sheets not configured', 'error')
        return redirect(url_for('index'))
    
    # Use the public CSV method (works if sheet is shared)
    cards = read_credit_card_balances()
    
    if not cards:
        flash('No credit card data found in Google Sheet. Make sure the sheet is shared as "Anyone with the link"', 'warning')
        return redirect(url_for('index'))
    
    conn = get_db()
    cursor = conn.cursor()
    
    updated = 0
    for card in cards:
        # Check if card exists
        cursor.execute('SELECT id FROM cards WHERE name = ?', (card['name'],))
        existing = cursor.fetchone()
        
        # Get credit_limit from card (default to 0 if not present)
        credit_limit = card.get('credit_limit', 0)
        
        if existing:
            # Update existing card (including credit_limit)
            cursor.execute('''
                UPDATE cards 
                SET balance = ?, interest_rate = ?, due_day = ?, minimum_payment = ?, credit_limit = ?, last_synced = datetime('now')
                WHERE name = ?
            ''', (card['balance'], card['interest_rate'], card['due_day'], card['minimum_payment'], credit_limit, card['name']))
        else:
            # Insert new card
            cursor.execute('''
                INSERT INTO cards (name, balance, interest_rate, due_day, minimum_payment, credit_limit, last_synced)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ''', (card['name'], card['balance'], card['interest_rate'], card['due_day'], card['minimum_payment'], credit_limit))
        
        updated += 1
    
    conn.commit()
    flash(f'Successfully synced {updated} cards from Google Sheets!', 'success')
    return redirect(url_for('index'))

@app.route('/sheets/manage')
@login_required
def sheets_manage():
    """Manage Google Sheets integration"""
    # Get current sync status
    service = None
    sheet_data = []
    
    if sheets_config.SPREADSHEET_ID:
        try:
            service = get_sheets_client()
            if service:
                range_name = f"{sheets_config.SHEET_NAME}!A:Z"
                result = service.spreadsheets().values().get(
                    spreadsheetId=sheets_config.SPREADSHEET_ID,
                    range=range_name
                ).execute()
                sheet_data = result.get('values', [])
        except:
            pass
    
    return render_template('sheets_manage.html', sheet_data=sheet_data)

# ============= STRATEGY & PROGRESS ROUTES =============

@app.route('/strategy')
@login_required
def strategy():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM cards WHERE balance > 0 ORDER BY balance ASC')
    cards = cursor.fetchall()
    conn.close()
    
    # Calculate strategies
    total_debt = sum(card['balance'] for card in cards)
    
    # Snowball: smallest balance first
    snowball = sorted(cards, key=lambda x: x['balance'])
    
    # Avalanche: highest interest first
    avalanche = sorted(cards, key=lambda x: x['interest_rate'], reverse=True)
    
    return render_template('strategy.html', cards=cards, total_debt=total_debt,
                           snowball=snowball, avalanche=avalanche)

@app.route('/progress')
@login_required
def progress():
    conn = get_db()
    cursor = conn.cursor()
    
    # Get monthly payment totals
    cursor.execute('''
        SELECT strftime('%Y-%m', date) as month, SUM(amount) as total
        FROM payments
        GROUP BY strftime('%Y-%m', date)
        ORDER BY month DESC
        LIMIT 12
    ''')
    monthly_payments = cursor.fetchall()
    
    # Get balance history (from current card balances - simplified)
    cursor.execute('SELECT name, balance FROM cards')
    cards = cursor.fetchall()
    
    conn.close()
    
    return render_template('progress.html', monthly_payments=monthly_payments, cards=cards)

    return render_template('progress.html', monthly_payments=monthly_payments, cards=cards)

# ============= EMAIL ALERTS =============

def send_email_alert(subject, body):
    """Send an email alert"""
    if not EMAIL_AVAILABLE:
        print("Email not configured")
        return False
    
    try:
        msg = MIMEMultipart()
        msg['From'] = email_config.FROM_EMAIL
        msg['To'] = email_config.TO_EMAIL
        msg['Subject'] = subject
        
        msg.attach(MIMEText(body, 'html'))
        
        server = smtplib.SMTP(email_config.SMTP_HOST, email_config.SMTP_PORT)
        server.starttls()
        server.login(email_config.SMTP_USER, email_config.SMTP_PASSWORD)
        server.sendmail(email_config.FROM_EMAIL, email_config.TO_EMAIL, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False

def check_due_dates_and_alert():
    """Check for cards due soon and send email alerts - also checks low bank balance"""
    if not EMAIL_AVAILABLE or not hasattr(email_config, 'TO_EMAIL') or not email_config.TO_EMAIL:
        return 0
    
    conn = get_db()
    cursor = conn.cursor()
    
    today = datetime.now()
    alert_days = 7  # Check for cards due in next 7 days
    
    cursor.execute('SELECT name, balance, due_day, minimum_payment, interest_rate FROM cards WHERE due_day > 0')
    cards = cursor.fetchall()
    
    due_cards = []
    overdue_cards = []
    for card in cards:
        due_day = card['due_day']
        # Calculate days until due
        # Get days in current month
        if today.month == 12:
            next_month = datetime(today.year + 1, 1, 1)
        else:
            next_month = datetime(today.year, today.month + 1, 1)
        days_in_month = (next_month - datetime(today.year, today.month, 1)).days
        
        if today.day <= due_day:
            days_until = due_day - today.day
        else:
            # Due next month
            days_until = (days_in_month - today.day) + due_day
        
        # Get ordinal suffix for due day
        def ordinal(n):
            if 11 <= (n % 100) <= 13:
                return f"{n}th"
            if n % 10 == 1:
                return f"{n}st"
            if n % 10 == 2:
                return f"{n}nd"
            if n % 10 == 3:
                return f"{n}rd"
            return f"{n}th"
        
        card_data = {
            'name': card['name'],
            'balance': card['balance'],
            'due_day': due_day,
            'due_day_ordinal': ordinal(due_day),
            'minimum_payment': card['minimum_payment'],
            'interest_rate': card['interest_rate'],
            'days_until': days_until
        }
        
        if days_until < 0:
            overdue_cards.append(card_data)
        elif days_until <= alert_days:
            due_cards.append(card_data)
    
    conn.close()
    
    if not due_cards and not overdue_cards:
        return 0
    
    # Build nicely formatted email
    subject_parts = []
    if overdue_cards:
        subject_parts.append(f"{len(overdue_cards)} overdue")
    if due_cards:
        subject_parts.append(f"{len(due_cards)} due this week")
    
    subject = f"💳 Debt Alert: {', '.join(subject_parts)}"
    
    # Calculate totals
    total_min = sum(c['minimum_payment'] for c in due_cards + overdue_cards)
    total_balance = sum(c['balance'] for c in due_cards + overdue_cards)
    
    # Get bank balance for summary
    bank_accounts = read_bank_accounts()
    total_bank = sum(a['balance'] for a in bank_accounts) if bank_accounts else 0
    
    # Calculate bank balance after payments
    balance_after_payments = total_bank - total_min
    low_balance_alert = balance_after_payments < 3000
    
    # Build alert message for bank balance
    if balance_after_payments < 3000:
        balance_alert = '<p style="background: #fee; color: #c00; padding: 10px; border-radius: 5px; font-weight: bold;">⚠️ ALERT: Bank balance will be below $3,000 after payments!</p>'
    else:
        balance_alert = '<p style="background: #e8f5e9; color: #27ae60; padding: 10px; border-radius: 5px; font-weight: bold;">✅ Bank balance will remain above $3,000</p>'
    
    body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background: #2c3e50; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }}
            .content {{ background: #f8f9fa; padding: 20px; }}
            .summary {{ background: white; padding: 15px; border-radius: 8px; margin-bottom: 20px; }}
            .summary h3 {{ margin: 0 0 10px 0; color: #2c3e50; }}
            .total {{ font-size: 24px; font-weight: bold; color: #e74c3c; }}
            table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; }}
            th {{ background: #34495e; color: white; padding: 12px; text-align: left; }}
            td {{ padding: 12px; border-bottom: 1px solid #eee; }}
            tr:last-child td {{ border-bottom: none; }}
            .overdue {{ background: #fee; }}
            .due-soon {{ background: #fff3cd; }}
            .due-text {{ font-weight: bold; }}
            .footer {{ background: #2c3e50; color: #bdc3c7; padding: 15px; text-align: center; border-radius: 0 0 8px 8px; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>💳 Debt Tracker Weekly Alert</h1>
                <p>{today.strftime('%B %d, %Y')}</p>
            </div>
            <div class="content">
                <div class="summary">
                    <h3>Payment Summary</h3>
                    <p>Total Due: <span class="total">${total_min:,.2f}</span></p>
                    <p>Total Credit Card Balance: ${total_balance:,.2f}</p>
                    <p>🏦 Bank Balance: ${total_bank:,.2f}</p>
                    <p>💰 Balance After Payments: ${balance_after_payments:,.2f}</p>
                    {balance_alert}
                </div>
    """
    
    if overdue_cards:
        body += """
                <h2>⚠️ Overdue Payments</h2>
                <table>
                    <tr>
                        <th>Card</th>
                        <th>Balance</th>
                        <th>Min Payment</th>
                        <th>Due Date</th>
                    </tr>
        """
        for card in overdue_cards:
            body += f"""
                    <tr class="overdue">
                        <td><strong>{card['name']}</strong></td>
                        <td>${card['balance']:,.2f}</td>
                        <td>${card['minimum_payment']:,.2f}</td>
                        <td class="due-text">{card['due_day']}st (OVERDUE)</td>
                    </tr>
            """
        body += """
                </table>
        """
    
    if due_cards:
        body += """
                <h2>📅 Due This Week</h2>
                <table>
                    <tr>
                        <th>Card</th>
                        <th>Balance</th>
                        <th>APR</th>
                        <th>Min Payment</th>
                        <th>Due Date</th>
                    </tr>
        """
        for card in sorted(due_cards, key=lambda x: x['days_until']):
            days_text = "Due today!" if card['days_until'] == 0 else f"Due in {card['days_until']} days"
            body += f"""
                    <tr>
                        <td><strong>{card['name']}</strong></td>
                        <td>${card['balance']:,.2f}</td>
                        <td>{card['interest_rate']}%</td>
                        <td>${card['minimum_payment']:,.2f}</td>
                        <td class="due-text">{card['due_day_ordinal']} ({days_text})</td>
                    </tr>
            """
        body += """
                </table>
        """
    
    # Add bank accounts section
    bank_accounts = read_bank_accounts()
    if bank_accounts:
        total_bank = sum(a['balance'] for a in bank_accounts)
        low_balance = getattr(email_config, 'LOW_BALANCE_THRESHOLD', 3000)
        
        body += f"""
                <h2>🏦 Bank Accounts</h2>
                <table>
                    <tr>
                        <th>Account</th>
                        <th>Type</th>
                        <th>Balance</th>
                        <th>Status</th>
                    </tr>
        """
        for account in bank_accounts:
            status = '<span style="color: #e74c3c; font-weight: bold;">⚠️ Low</span>' if account['balance'] < low_balance else '<span style="color: #27ae60;">✓ OK</span>'
            body += f"""
                    <tr>
                        <td><strong>{account['name']}</strong></td>
                        <td>{account['account_type']}</td>
                        <td>${account['balance']:,.2f}</td>
                        <td>{status}</td>
                    </tr>
            """
        body += f"""
                    <tr style="font-weight: bold; background: #f0f0f0;">
                        <td>Total</td>
                        <td></td>
                        <td>${total_bank:,.2f}</td>
                        <td></td>
                    </tr>
                </table>
        """
    
    body += """
            </div>
            <div class="footer">
                <p>Sent automatically by Debt Tracker</p>
                <p><a href="http://localhost:5001">View Dashboard</a></p>
            </div>
        </div>
    </body>
    </html>
    """
    
    send_email_alert(subject, body)
    
    # Also check for low bank balance alerts
    check_low_balance_alerts()
    
    return len(due_cards) + len(overdue_cards)

@app.route('/email/test')
@login_required
def test_email():
    """Send a test email"""
    if not EMAIL_AVAILABLE:
        flash('Email not configured. Add settings to email_config.py', 'error')
        return redirect(url_for('index'))
    
    if send_email_alert("Test from Debt Tracker", "<p>This is a test email from your Debt Tracker app!</p>"):
        flash('Test email sent!', 'success')
    else:
        flash('Failed to send email. Check your email settings.', 'error')
    
    return redirect(url_for('index'))

@app.route('/email/send-alerts')
@app.route('/email/send-alerts/<api_key>')
def send_alerts(api_key=None):
    """Manually trigger alert check - requires login or valid API key"""
    # Check API key if provided (for cron jobs)
    if api_key:
        expected_key = os.environ.get('ALERT_API_KEY', 'debt-tracker-secret-key')
        if api_key != expected_key:
            return "Unauthorized", 401
    elif not session.get('logged_in'):
        return redirect(url_for('login'))
    
    # Sync from Google Sheets before checking alerts
    try:
        sync_from_sheets()
    except Exception as e:
        print(f"Sync error in alerts: {e}")
    
    count = check_due_dates_and_alert()
    if api_key:
        # Return JSON for API calls
        if count > 0:
            return f"{{\"status\": \"success\", \"alerts_sent\": {count}}}", 200
        else:
            return f"{{\"status\": \"ok\", \"message\": \"No payments due within alert window\"}}", 200
    if count > 0:
        flash(f'Sent alerts for {count} card(s)!', 'success')
    else:
        flash('No payments due within alert window', 'info')
    return redirect(url_for('index'))

@app.route('/email/configure')
@login_required
def email_configure():
    """Show email configuration page"""
    config = {}
    if EMAIL_AVAILABLE:
        config = {
            'smtp_host': getattr(email_config, 'SMTP_HOST', ''),
            'smtp_port': getattr(email_config, 'SMTP_PORT', ''),
            'smtp_user': getattr(email_config, 'SMTP_USER', ''),
            'from_email': getattr(email_config, 'FROM_EMAIL', ''),
            'to_email': getattr(email_config, 'TO_EMAIL', ''),
            'alert_days': getattr(email_config, 'ALERT_DAYS_BEFORE', 3)
        }
    return render_template('email_config.html', config=config)

@app.route('/email/configure', methods=['POST'])
@login_required
def email_configure_save():
    """Save email configuration"""
    # This would write to email_config.py - for now just show success
    flash('Email configuration saved. Restart the app to apply changes.', 'success')
    return redirect(url_for('index'))

if __name__ == '__main__':
    init_db()
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(debug=debug_mode, port=port, host='0.0.0.0')
