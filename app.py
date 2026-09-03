import os, secrets, hashlib, smtplib, requests
import psycopg2, psycopg2.extras
import socket

# --- Force IPv4 for outbound connections (fixes "Network is unreachable" on Render) ---
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_getaddrinfo(*args, **kwargs):
    return [r for r in _orig_getaddrinfo(*args, **kwargs) if r[0] == socket.AF_INET]
socket.getaddrinfo = _ipv4_getaddrinfo
# ---------------------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__); CORS(app)

DATABASE_URL = os.getenv('DATABASE_URL', '')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
OTP_MINUTES = int(os.getenv('OTP_MINUTES', '10'))

class PgCursorWrapper:
    def __init__(self, cur): self._cur = cur
    def fetchone(self):
        r = self._cur.fetchone(); return dict(r) if r is not None else None
    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()]

class PgConn:
    def __init__(self):
        self._conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
    def execute(self, query, params=()):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(query.replace('?', '%s'), params)
        return PgCursorWrapper(cur)
    def executescript(self, script):
        cur = self._conn.cursor(); cur.execute(script); cur.close()
    def commit(self): self._conn.commit()
    def close(self): self._conn.close()

def now(): return datetime.now(timezone.utc)
def db(): return PgConn()
def h(v): return hashlib.sha256(v.encode()).hexdigest()
def rows(q,p=()):
    c=db(); r=c.execute(q,p).fetchall(); c.close(); return r

def init():
    if not DATABASE_URL:
        print('WARNING: DATABASE_URL is not set. Set it to your PostgreSQL connection string. '
              'The app will start, but every database operation will fail until this is fixed.')
        return
    try:
        c=db(); c.executescript('''
CREATE TABLE IF NOT EXISTS users(id SERIAL PRIMARY KEY,full_name TEXT NOT NULL,email TEXT NOT NULL UNIQUE,phone TEXT,username TEXT NOT NULL UNIQUE,password_hash TEXT NOT NULL,mt5_account TEXT,email_verified INTEGER DEFAULT 0,status TEXT DEFAULT 'PENDING',membership TEXT DEFAULT 'FREE',membership_expiry TEXT,created_at TEXT NOT NULL,last_login TEXT,wallet_balance REAL DEFAULT 0,license_key TEXT);
CREATE TABLE IF NOT EXISTS otps(id SERIAL PRIMARY KEY,email TEXT NOT NULL,code_hash TEXT NOT NULL,expires_at TEXT NOT NULL,purpose TEXT NOT NULL,used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS payments(id SERIAL PRIMARY KEY,user_id INTEGER NOT NULL,kind TEXT NOT NULL,amount REAL NOT NULL,currency TEXT NOT NULL,method TEXT,reference TEXT,destination TEXT,status TEXT DEFAULT 'PENDING',created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS signals(id SERIAL PRIMARY KEY,symbol TEXT NOT NULL,direction TEXT NOT NULL,entry REAL,sl REAL,tp1 REAL,tp2 REAL,confidence REAL,reason TEXT,created_at TEXT NOT NULL,status TEXT DEFAULT 'ACTIVE');
CREATE TABLE IF NOT EXISTS mt5_calendar(id SERIAL PRIMARY KEY,time TEXT,currency TEXT,country TEXT,event TEXT,importance INTEGER,actual REAL,forecast REAL,previous REAL,received_at TEXT NOT NULL);
ALTER TABLE users ADD COLUMN IF NOT EXISTS license_key TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS signals_viewed_count INTEGER DEFAULT 0;
'''); c.commit(); c.close()
    except Exception as e:
        print('WARNING: could not connect to DATABASE_URL at startup: ' + str(e) +
              '. The app will still start, but database operations will fail until this is fixed. '
              'Check /api/admin/db-check once deployed.')

def send_email(to,subject,body):
    api_key=os.getenv('RESEND_API_KEY','')
    sender=os.getenv('SMTP_FROM','onboarding@resend.dev')
    if not api_key:
        print('EMAIL NOT CONFIGURED',subject,to); return False
    try:
        r=requests.post('https://api.resend.com/emails',
            headers={'Authorization':f'Bearer {api_key}','Content-Type':'application/json'},
            json={'from':sender,'to':[to],'subject':subject,'text':body},
            timeout=15)
        if r.status_code>=400:
            print('EMAIL SEND FAILED:', r.status_code, r.text)
            return False
        return True
    except Exception as e:
        print('EMAIL SEND FAILED:', str(e))
        return False

def admin_email(s,b):
    if os.getenv('ADMIN_EMAIL',''): send_email(os.getenv('ADMIN_EMAIL'),s,b)
def admin_ok(): return request.headers.get('X-Admin-Key','')==os.getenv('ADMIN_API_KEY','CHANGE_ME')
def otp(email,purpose):
    code=f'{secrets.randbelow(1000000):06d}'; c=db(); c.execute('UPDATE otps SET used=1 WHERE email=? AND purpose=? AND used=0',(email,purpose)); c.execute('INSERT INTO otps(email,code_hash,expires_at,purpose) VALUES(?,?,?,?)',(email,h(code),(now()+timedelta(minutes=OTP_MINUTES)).isoformat(),purpose)); c.commit(); c.close(); return code

@app.post('/api/register')
def register():
    d=request.get_json(force=True); req=['full_name','email','username','password','mt5_account']
    if any(not str(d.get(k,'')).strip() for k in req): return jsonify(ok=False,error='Missing required field'),400
    email=d['email'].strip().lower(); c=db(); license_key=secrets.token_hex(16); trial_expiry=(now()+timedelta(days=3)).isoformat()
    try:
        c.execute('INSERT INTO users(full_name,email,phone,username,password_hash,mt5_account,created_at,license_key,membership,membership_expiry) VALUES(?,?,?,?,?,?,?,?,?,?)',(d['full_name'].strip(),email,d.get('phone','').strip(),d['username'].strip(),h(d['password']),str(d['mt5_account']),now().isoformat(),license_key,'FREE',trial_expiry)); c.commit()
    except psycopg2.IntegrityError: c.close(); return jsonify(ok=False,error='Email or username already exists'),409
    c.close(); code=otp(email,'REGISTER'); send_email(email,'GOLD MASTERS verification code',f'Your verification code is: {code}\nExpires in {OTP_MINUTES} minutes.'); admin_email('New GOLD MASTERS registration',f'User: {d["full_name"]}\nEmail: {email}\nMT5: {d["mt5_account"]}\nStatus: PENDING'); return jsonify(ok=True,message='Check email for OTP')

@app.post('/api/verify-email')
def verify():
    d=request.get_json(force=True); email=d.get('email','').strip().lower(); code=d.get('code','').strip(); c=db(); r=c.execute("SELECT * FROM otps WHERE email=? AND purpose='REGISTER' AND used=0 ORDER BY id DESC LIMIT 1",(email,)).fetchone()
    if not r or r['expires_at']<now().isoformat() or r['code_hash']!=h(code): c.close(); return jsonify(ok=False,error='Invalid or expired code'),400
    c.execute('UPDATE otps SET used=1 WHERE id=?',(r['id'],)); c.execute('UPDATE users SET email_verified=1 WHERE email=?',(email,)); c.commit(); c.close(); admin_email('Email verified',f'{email} verified email and awaits admin approval.'); return jsonify(ok=True,status='PENDING')

@app.post('/api/resend-otp')
def resend_otp():
    d=request.get_json(force=True); email=d.get('email','').strip().lower()
    c=db(); u=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
    if not u: c.close(); return jsonify(ok=False,error='User not found'),404
    if u['email_verified']: c.close(); return jsonify(ok=False,error='Already verified'),400
    c.close()
    code=otp(email,'REGISTER')
    sent=send_email(email,'GOLD MASTERS verification code',f'Your verification code is: {code}\nExpires in {OTP_MINUTES} minutes.')
    return jsonify(ok=True,email_sent=sent)

@app.post('/api/login')
def login():
    d=request.get_json(force=True); ident=d.get('username_or_email','').strip(); pw=d.get('password',''); c=db(); u=c.execute('SELECT * FROM users WHERE username=? OR email=?',(ident,ident.lower())).fetchone()
    if not u or u['password_hash']!=h(pw): c.close(); return jsonify(ok=False,error='Invalid login'),401
    if not u['email_verified']: c.close(); return jsonify(ok=False,error='Email not verified'),403
    if u['status']!='APPROVED': c.close(); return jsonify(ok=False,status=u['status'],error='Admin approval required'),403
    c.execute('UPDATE users SET last_login=? WHERE id=?',(now().isoformat(),u['id'])); c.commit(); c.close(); return jsonify(ok=True,user={k:u[k] for k in ['id','full_name','email','membership','membership_expiry','status','mt5_account','wallet_balance','license_key']})

@app.post('/api/payment-request')
def payment():
    d=request.get_json(force=True); email=d.get('email','').strip().lower(); kind=d.get('kind','DEPOSIT').upper(); amount=float(d.get('amount',0))
    if kind not in ('DEPOSIT','WITHDRAWAL') or amount<=0: return jsonify(ok=False,error='Invalid request'),400
    c=db(); u=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
    if not u: c.close(); return jsonify(ok=False,error='User not found'),404
    c.execute('INSERT INTO payments(user_id,kind,amount,currency,method,reference,destination,created_at) VALUES(?,?,?,?,?,?,?,?)',(u['id'],kind,amount,d.get('currency','USD'),d.get('method',''),d.get('reference',''),d.get('destination',''),now().isoformat())); c.commit(); c.close(); send_email(email,f'GOLD MASTERS {kind.title()} Request',f'Amount: {amount} {d.get("currency","USD")}\nStatus: PENDING ADMIN CONFIRMATION'); admin_email(f'New {kind}',f'User: {u["full_name"]}\nEmail: {email}\nAmount: {amount}'); return jsonify(ok=True,status='PENDING')

PRICING={'MONTHLY':50,'SIX_MONTH':200,'YEARLY':400,'LIFETIME':1000}
@app.get('/api/pricing')
def pricing(): return jsonify(ok=True,pricing=PRICING,trial='3 days or 3 signals, whichever comes first',currency='USD')

@app.get('/api/signals')
def signals():
    email=request.args.get('email','').strip().lower()
    all_signals=rows("SELECT * FROM signals WHERE status='ACTIVE' ORDER BY id DESC LIMIT 100")
    if not email:
        return jsonify(ok=True,signals=[],trial=True,error='Login required to view signals. Free trial: 3 days or 3 signals.',pricing=PRICING)
    c=db(); u=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
    if not u: c.close(); return jsonify(ok=False,error='User not found'),404
    paid=u['membership'] in ('MONTHLY','SIX_MONTH','YEARLY','LIFETIME')
    expired=False
    if u['membership_expiry']:
        try:
            if datetime.fromisoformat(u['membership_expiry'])<now(): expired=True
        except ValueError: pass
    if paid and not expired:
        c.close(); return jsonify(ok=True,signals=all_signals,trial=False)
    if paid and expired:
        c.close(); return jsonify(ok=True,signals=[],trial=True,error='Your membership has expired. Please renew to keep receiving signals.',pricing=PRICING)
    if expired or u['signals_viewed_count']>=3:
        c.close(); return jsonify(ok=True,signals=[],trial=True,error='Free trial ended (3 days or 3 signals). Upgrade to keep receiving signals.',pricing=PRICING)
    remaining=3-u['signals_viewed_count']
    c.execute('UPDATE users SET signals_viewed_count=signals_viewed_count+1 WHERE id=?',(u['id'],)); c.commit(); c.close()
    return jsonify(ok=True,signals=all_signals[:1],trial=True,trial_signals_remaining=remaining-1,pricing=PRICING)

@app.get('/api/market/symbols')
def symbols():
    return jsonify(ok=True,provider=os.getenv('MARKET_DATA_PROVIDER','none'),symbols=['XAUUSD','XAGUSD','EURUSD','GBPUSD','USDJPY','USDCHF','USDCAD','AUDUSD','NZDUSD','EURJPY','GBPJPY','BTCUSD','ETHUSD','NAS100','US30','SPX500','GER40'])

def td_symbol(symbol):
    m={
      'XAUUSD':'XAU/USD','XAGUSD':'XAG/USD','EURUSD':'EUR/USD','GBPUSD':'GBP/USD',
      'USDJPY':'USD/JPY','USDCHF':'USD/CHF','USDCAD':'USD/CAD','AUDUSD':'AUD/USD',
      'NZDUSD':'NZD/USD','EURJPY':'EUR/JPY','GBPJPY':'GBP/JPY',
      'BTCUSD':'BTC/USD','ETHUSD':'ETH/USD'
    }
    return m.get(symbol.upper(),symbol.upper())

@app.get('/api/market/quote/<symbol>')
def quote(symbol):
    key=os.getenv('TWELVEDATA_API_KEY','').strip()
    if not key:
        return jsonify(ok=False,error='TWELVEDATA_API_KEY not configured',symbol=symbol.upper(),provider='Twelve Data'),503
    try:
        r=requests.get('https://api.twelvedata.com/quote',params={'symbol':td_symbol(symbol),'apikey':key},timeout=12)
        data=r.json()
        if data.get('status')=='error': return jsonify(ok=False,error=data.get('message','Market provider error'),provider='Twelve Data'),502
        return jsonify(ok=True,provider='Twelve Data',symbol=symbol.upper(),quote={
          'close':data.get('close'),'open':data.get('open'),'high':data.get('high'),'low':data.get('low'),
          'previous_close':data.get('previous_close'),'change':data.get('change'),'percent_change':data.get('percent_change'),
          'datetime':data.get('datetime'),'timestamp':data.get('timestamp')
        })
    except Exception as e:
        return jsonify(ok=False,error='Live quote unavailable: '+str(e),provider='Twelve Data'),502

@app.get('/api/market/history/<symbol>')
def history(symbol):
    key=os.getenv('TWELVEDATA_API_KEY','').strip(); interval=request.args.get('interval','5min')
    if not key: return jsonify(ok=False,error='TWELVEDATA_API_KEY not configured',provider='Twelve Data'),503
    try:
        r=requests.get('https://api.twelvedata.com/time_series',params={'symbol':td_symbol(symbol),'interval':interval,'outputsize':200,'apikey':key},timeout=12)
        data=r.json()
        if data.get('status')=='error': return jsonify(ok=False,error=data.get('message','Market provider error')),502
        return jsonify(ok=True,provider='Twelve Data',symbol=symbol.upper(),interval=interval,values=data.get('values',[]))
    except Exception as e: return jsonify(ok=False,error='Live history unavailable: '+str(e)),502

@app.get('/api/calendar')
def calendar():
    rows_mt5=rows('SELECT time,currency,country,event,importance,actual,forecast,previous,received_at FROM mt5_calendar ORDER BY time ASC LIMIT 200')
    if rows_mt5:
        return jsonify(ok=True,provider='MT5 Built-in Economic Calendar',events=rows_mt5)

    key=os.getenv('TRADING_ECONOMICS_API_KEY','').strip()
    if not key:
        return jsonify(ok=False,error='MT5 calendar has no pushed events yet. Attach/run the GOLD MASTERS EA with PushCalendarToDashboard=true, or configure another calendar provider.',provider='MT5 Built-in Economic Calendar'),503
    country=request.args.get('country','United States')
    try:
        r=requests.get('https://api.tradingeconomics.com/calendar/country/'+requests.utils.quote(country,safe=''),params={'c':'guest:guest' if key=='GUEST' else key,'f':'json'},timeout=15)
        if r.status_code>=400: return jsonify(ok=False,error='Calendar provider returned HTTP '+str(r.status_code),provider='Trading Economics'),502
        data=r.json()
        return jsonify(ok=True,provider='Trading Economics',country=country,events=data[:100])
    except Exception as e:
        return jsonify(ok=False,error='Economic calendar unavailable: '+str(e),provider='Trading Economics'),502

@app.post('/api/calendar/mt5')
def calendar_mt5():
    key=os.getenv('MT5_CALENDAR_PUSH_KEY','CHANGE_ME')
    if request.headers.get('X-Calendar-Key','') != key:
        return jsonify(ok=False,error='Unauthorized'),401
    d=request.get_json(force=True) or {}
    events=d.get('events',[])
    if not isinstance(events,list):
        return jsonify(ok=False,error='events must be a list'),400
    c=db()
    c.execute('DELETE FROM mt5_calendar')
    for e in events[:500]:
        c.execute('INSERT INTO mt5_calendar(time,currency,country,event,importance,actual,forecast,previous,received_at) VALUES(?,?,?,?,?,?,?,?,?)',
                  (str(e.get('time','')),str(e.get('currency','')),str(e.get('country','')),str(e.get('event','')),
                   int(e.get('importance',0) or 0),float(e.get('actual',0) or 0),float(e.get('forecast',0) or 0),
                   float(e.get('previous',0) or 0),now().isoformat()))
    c.commit(); c.close()
    return jsonify(ok=True,provider='MT5 Built-in Economic Calendar',count=min(len(events),500))

@app.get('/api/admin/users')
def admin_users():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    return jsonify(ok=True,users=rows('SELECT id,full_name,email,username,mt5_account,email_verified,status,membership,membership_expiry,created_at,last_login,wallet_balance,license_key FROM users ORDER BY id DESC'))

@app.get('/api/admin/payments')
def admin_payments():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    return jsonify(ok=True,payments=rows('SELECT payments.*,users.full_name,users.email FROM payments JOIN users ON users.id=payments.user_id ORDER BY payments.id DESC'))

@app.post('/api/admin/user-status')
def admin_status():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    d=request.get_json(force=True); status=d.get('status','').upper(); email=d.get('email','').strip().lower()
    if status not in ('APPROVED','REJECTED','SUSPENDED'): return jsonify(ok=False,error='Invalid status'),400
    c=db(); c.execute('UPDATE users SET status=? WHERE email=?',(status,email)); c.commit(); c.close(); send_email(email,'GOLD MASTERS account update',f'Your account status is now: {status}'); return jsonify(ok=True,status=status)

@app.post('/api/admin/payment-status')
def admin_payment():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    d=request.get_json(force=True); pid=int(d['payment_id']); status=d.get('status','').upper(); c=db(); p=c.execute('SELECT payments.*,users.email,users.full_name FROM payments JOIN users ON users.id=payments.user_id WHERE payments.id=?',(pid,)).fetchone()
    if not p: c.close(); return jsonify(ok=False,error='Payment not found'),404
    if status not in ('APPROVED','REJECTED'): c.close(); return jsonify(ok=False,error='Invalid status'),400
    if status=='APPROVED' and p['kind']=='WITHDRAWAL':
        bal=c.execute('SELECT wallet_balance FROM users WHERE id=?',(p['user_id'],)).fetchone()['wallet_balance']
        if bal<p['amount']: c.close(); return jsonify(ok=False,error='Insufficient wallet balance'),400
    c.execute('UPDATE payments SET status=? WHERE id=?',(status,pid))
    if status=='APPROVED': c.execute('UPDATE users SET wallet_balance=wallet_balance+? WHERE id=?',((p['amount'] if p['kind']=='DEPOSIT' else -p['amount']),p['user_id']))
    c.commit(); c.close(); send_email(p['email'],'GOLD MASTERS request update',f'Your {p["kind"].lower()} request #{pid} is {status}.'); return jsonify(ok=True,status=status)

@app.post('/api/admin/signal')
def admin_signal():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    d=request.get_json(force=True); direction=d.get('direction','WAIT').upper()
    if direction not in ('BUY','SELL','WAIT'): return jsonify(ok=False,error='Invalid direction'),400
    c=db(); c.execute('INSERT INTO signals(symbol,direction,entry,sl,tp1,tp2,confidence,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(d.get('symbol','XAUUSD').upper(),direction,d.get('entry'),d.get('sl'),d.get('tp1'),d.get('tp2'),d.get('confidence'),d.get('reason',''),now().isoformat())); c.commit(); c.close(); return jsonify(ok=True)

@app.post('/api/admin/membership')
def admin_membership():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    d=request.get_json(force=True); email=d.get('email','').strip().lower(); membership=d.get('membership','').upper()
    if membership not in ('FREE','MONTHLY','SIX_MONTH','YEARLY','LIFETIME'): return jsonify(ok=False,error='Invalid membership'),400
    expiry=d.get('membership_expiry')
    c=db(); u=c.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone()
    if not u: c.close(); return jsonify(ok=False,error='User not found'),404
    c.execute('UPDATE users SET membership=?, membership_expiry=? WHERE email=?',(membership,expiry,email)); c.commit(); c.close()
    send_email(email,'GOLD MASTERS membership update',f'Your membership is now: {membership}'+(f' (expires {expiry})' if expiry else ''))
    return jsonify(ok=True,membership=membership,membership_expiry=expiry)

@app.get('/api/mt5/verify')
def mt5_verify():
    account=request.args.get('account','').strip(); key=request.args.get('key','').strip()
    if not account or not key: return jsonify(ok=False,error='Missing account or key'),400
    c=db(); u=c.execute('SELECT * FROM users WHERE mt5_account=? AND license_key=?',(account,key)).fetchone(); c.close()
    if not u: return jsonify(ok=False,error='Invalid account or license key'),401
    active=(u['status']=='APPROVED' and u['email_verified']==1)
    expired=False
    if u['membership']!='LIFETIME' and u['membership_expiry']:
        try:
            if datetime.fromisoformat(u['membership_expiry'])<now(): expired=True
        except ValueError: pass
    if expired: active=False
    auto_allowed=active and u['membership'] in ('YEARLY','LIFETIME')
    return jsonify(ok=True,active=active,expired=expired,status=u['status'],membership=u['membership'],
                   membership_expiry=u['membership_expiry'],wallet_balance=u['wallet_balance'],auto_allowed=auto_allowed)

@app.get('/api/health')
def health(): return jsonify(ok=True,service='GOLD MASTERS API',time=now().isoformat())

@app.get('/api/admin/db-check')
def admin_db_check():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    if not DATABASE_URL:
        return jsonify(ok=False,error='DATABASE_URL is not set on this server. Add it in your host\'s environment variables.'),500
    try:
        c = db()
        version = c.execute('SELECT version()').fetchone()['version']
        user_count = c.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n']
        payment_count = c.execute('SELECT COUNT(*) AS n FROM payments').fetchone()['n']
        signal_count = c.execute('SELECT COUNT(*) AS n FROM signals').fetchone()['n']
        c.close()
    except Exception as e:
        return jsonify(ok=False,error='Could not connect/query the database: '+str(e)),500
    return jsonify(ok=True,connected=True,postgres_version=version,
                   users=user_count,payments=payment_count,signals=signal_count)

init()
if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.getenv('PORT','8000')))
