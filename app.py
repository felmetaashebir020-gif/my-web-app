import os, secrets, hashlib, smtplib, requests
import psycopg2, psycopg2.extras
import socket, time, json, re

# --- Force IPv4 for outbound connections (fixes "Network is unreachable" on Render) ---
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_getaddrinfo(*args, **kwargs):
    return [r for r in _orig_getaddrinfo(*args, **kwargs) if r[0] == socket.AF_INET]
socket.getaddrinfo = _ipv4_getaddrinfo
# ---------------------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

app = Flask(__name__); CORS(app)
app.config['MAX_CONTENT_LENGTH']=10*1024*1024   # 10 MB (EA upload limit)

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
    @property
    def rowcount(self): return self._cur.rowcount

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
PW_ITER=200000
def hash_pw(pw):
    salt=secrets.token_hex(16)
    dk=hashlib.pbkdf2_hmac('sha256',pw.encode(),bytes.fromhex(salt),PW_ITER).hex()
    return f'pbkdf2${PW_ITER}${salt}${dk}'
def check_pw(pw,stored):
    if not stored: return False
    if stored.startswith('pbkdf2$'):
        try:
            _,it,salt,dk=stored.split('$')
            calc=hashlib.pbkdf2_hmac('sha256',pw.encode(),bytes.fromhex(salt),int(it)).hex()
        except Exception: return False
        return secrets.compare_digest(calc.encode(),dk.encode())
    return secrets.compare_digest(h(pw).encode(),stored.encode())   # legacy unsalted hash
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
CREATE TABLE IF NOT EXISTS bot_status(mt5_account TEXT PRIMARY KEY,state TEXT,broker TEXT,server TEXT,symbol TEXT,balance REAL,equity REAL,free_margin REAL,spread_points REAL,positions INTEGER,daily_stop INTEGER DEFAULT 0,daily_profit_stop INTEGER DEFAULT 0,drawdown_stop INTEGER DEFAULT 0,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bot_trades(id SERIAL PRIMARY KEY,mt5_account TEXT,event_type TEXT,side TEXT,ticket BIGINT,symbol TEXT,price REAL,lot REAL,sl REAL,tp REAL,profit REAL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bot_control(mt5_account TEXT PRIMARY KEY,enabled INTEGER DEFAULT 1,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bot_mode(mt5_account TEXT PRIMARY KEY,mode TEXT NOT NULL DEFAULT 'MANUAL',seq INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL);
ALTER TABLE users ADD COLUMN IF NOT EXISTS license_key TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS signals_viewed_count INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_chat_id TEXT;
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL,created_at TEXT NOT NULL,expires_at TEXT NOT NULL);
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS version TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS tp3 REAL;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS timeframe TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS dedupe_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_dedupe ON signals(dedupe_key);
CREATE TABLE IF NOT EXISTS ea_files(id SERIAL PRIMARY KEY,version TEXT NOT NULL,filename TEXT NOT NULL,data BYTEA NOT NULL,size INTEGER NOT NULL,uploaded_at TEXT NOT NULL);
ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by INTEGER;
ALTER TABLE users ADD COLUMN IF NOT EXISTS commission_credit REAL DEFAULT 0;
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_refcode ON users(referral_code);
CREATE TABLE IF NOT EXISTS orders(id SERIAL PRIMARY KEY,user_id INTEGER NOT NULL,plan TEXT NOT NULL,price REAL NOT NULL,wallet_amount REAL NOT NULL DEFAULT 0,commission_used REAL NOT NULL DEFAULT 0,crypto_amount REAL NOT NULL DEFAULT 0,network TEXT,txid TEXT,status TEXT NOT NULL DEFAULT 'PENDING',created_at TEXT NOT NULL,decided_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_txid ON orders(txid) WHERE txid IS NOT NULL;
CREATE TABLE IF NOT EXISTS commissions(id SERIAL PRIMARY KEY,order_id INTEGER NOT NULL UNIQUE,referrer_id INTEGER NOT NULL,referred_id INTEGER NOT NULL,plan TEXT NOT NULL,basis REAL NOT NULL,rate REAL NOT NULL,amount REAL NOT NULL,status TEXT NOT NULL DEFAULT 'PENDING',created_at TEXT NOT NULL,decided_at TEXT);
CREATE TABLE IF NOT EXISTS wallet_ledger(id SERIAL PRIMARY KEY,user_id INTEGER NOT NULL,kind TEXT NOT NULL,amount REAL NOT NULL,balance_after REAL NOT NULL,ref TEXT,note TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS admin_audit(id SERIAL PRIMARY KEY,action TEXT NOT NULL,detail TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ea_downloads(id SERIAL PRIMARY KEY,user_id INTEGER NOT NULL,ea_id INTEGER NOT NULL,version TEXT,ip TEXT,downloaded_at TEXT NOT NULL);
'''); c.commit(); c.close()
    except Exception as e:
        print('WARNING: could not connect to DATABASE_URL at startup: ' + str(e) +
              '. The app will still start, but database operations will fail until this is fixed. '
              'Check /api/admin/db-check once deployed.')

EMAIL_ERR=['']
def email_error():
    return EMAIL_ERR[0] or 'Could not send the verification email.'

def send_email(to,subject,body):
    EMAIL_ERR[0]=''
    api_key=os.getenv('RESEND_API_KEY','').strip()
    sender=os.getenv('SMTP_FROM','onboarding@resend.dev').strip()
    if not api_key:
        print('EMAIL NOT CONFIGURED',subject,to)
        EMAIL_ERR[0]='Email service is not configured on the server (RESEND_API_KEY missing).'
        return False
    try:
        r=requests.post('https://api.resend.com/emails',
            headers={'Authorization':f'Bearer {api_key}','Content-Type':'application/json'},
            json={'from':sender,'to':[to],'subject':subject,'text':body},
            timeout=15)
        if r.status_code>=400:
            print('EMAIL SEND FAILED:', r.status_code, r.text)
            try: msg=str(r.json().get('message',''))
            except Exception: msg=''
            low=msg.lower()
            if 'verify a domain' in low or 'testing emails' in low or 'domain is not verified' in low:
                EMAIL_ERR[0]='Server problem: the sending email domain is not verified yet (Resend). Please contact support.'
            elif r.status_code in (401,403) and 'api key' in low:
                EMAIL_ERR[0]='Server problem: the email API key is wrong (Resend).'
            else:
                EMAIL_ERR[0]='Email error '+str(r.status_code)+': '+msg[:150]
            return False
        return True
    except Exception as e:
        print('EMAIL SEND FAILED:', str(e))
        EMAIL_ERR[0]='Could not reach the email service: '+str(e)[:120]
        return False

TG_ERR=['']
def tg_error():
    return TG_ERR[0] or 'Could not send the Telegram code. Please check your Telegram Chat ID.'

def send_telegram(chat_id,text):
    TG_ERR[0]=''
    token=os.getenv('TELEGRAM_BOT_TOKEN','').strip()
    if not token:
        TG_ERR[0]='Server problem: TELEGRAM_BOT_TOKEN is missing on Render (Environment settings).'
        print('TELEGRAM SEND FAILED: TELEGRAM_BOT_TOKEN missing'); return False
    if not chat_id:
        TG_ERR[0]='Telegram Chat ID is empty.'; return False
    try:
        r=requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id':str(chat_id).strip(),'text':text}, timeout=15)
        if r.status_code>=400:
            print('TELEGRAM SEND FAILED:', r.status_code, r.text)
            try: desc=r.json().get('description','')
            except Exception: desc=''
            if r.status_code==401:
                TG_ERR[0]='Server problem: the bot token on Render is wrong (Unauthorized). Update TELEGRAM_BOT_TOKEN.'
            elif r.status_code==403:
                TG_ERR[0]='Telegram blocked it ('+desc+'). Open the bot in Telegram and press Start, then try again.'
            elif 'chat not found' in desc.lower():
                TG_ERR[0]='Telegram says: chat not found. Check your Chat ID (numbers only, from @userinfobot) and press Start on the bot.'
            else:
                TG_ERR[0]='Telegram error '+str(r.status_code)+': '+desc
            return False
        return True
    except Exception as e:
        print('TELEGRAM SEND FAILED:', str(e))
        TG_ERR[0]='Could not reach Telegram from the server: '+str(e)
        return False

def otp_mode():
    # telegram (default, current behaviour) | email | both
    m=os.getenv('OTP_MODE','telegram').strip().lower()
    # none = no OTP at all: the account is created verified and waits for admin approval
    return m if m in ('telegram','email','both','none') else 'telegram'

RATE={}
def rate_ok(key,limit,window):
    t=time.time()
    xs=[x for x in RATE.get(key,[]) if t-x<window]
    if len(xs)>=limit:
        RATE[key]=xs
        return False
    xs.append(t); RATE[key]=xs
    if len(RATE)>5000: RATE.clear()
    return True

def client_ip():
    return (request.headers.get('X-Forwarded-For','').split(',')[0].strip() or request.remote_addr or '?')


def send_otp_message(to,subject,body,telegram_chat_id=None):
    mode=otp_mode()
    if mode=='email':
        return send_email(to,subject,body)
    if mode=='both':
        e=send_email(to,subject,body)
        t=send_telegram(telegram_chat_id,subject+'\n\n'+body) if telegram_chat_id else False
        return e or t
    if not telegram_chat_id:
        TG_ERR[0]='Telegram Chat ID is required.'
        return False
    return send_telegram(telegram_chat_id, subject+'\n\n'+body)

def otp_error():
    return email_error() if otp_mode() in ('email','both') else tg_error()

def otp_sent_message():
    return {'email':'Check your email (also the spam folder) for the verification code',
            'both':'Check your email and Telegram for the verification code'}.get(otp_mode(),'Check your Telegram for the verification code')

@app.get('/api/otp-mode')
def api_otp_mode():
    return jsonify(ok=True,mode=otp_mode())

def admin_email(s,b):
    # Notifies the admin on Telegram (ADMIN_TELEGRAM_CHAT_ID) and/or email (ADMIN_EMAIL), whichever is set.
    tg=os.getenv('ADMIN_TELEGRAM_CHAT_ID','').strip()
    if tg:
        try: send_telegram(tg,s+'\n\n'+b)
        except Exception as e: print('admin telegram notify failed:',e)
    if os.getenv('ADMIN_EMAIL',''): send_email(os.getenv('ADMIN_EMAIL'),s,b)
def key_ok(provided,env_name):
    k=os.getenv(env_name,'').strip()
    if not k or k=='CHANGE_ME': return False
    return secrets.compare_digest((provided or '').encode(),k.encode())
def admin_ok(): return key_ok(request.headers.get('X-Admin-Key',''),'ADMIN_API_KEY')

# --- MT5 EA integration auth helpers ---
def website_api_ok():
    auth=request.headers.get('Authorization','')
    token=auth.replace('Bearer ','').strip() if auth.startswith('Bearer ') else auth.strip()
    return token!='' and key_ok(token,'WEBSITE_API_KEY')
def bot_control_ok():
    return key_ok(request.headers.get('X-Bot-Control-Key',''),'BOT_CONTROL_KEY')

def otp(email,purpose):
    code=f'{secrets.randbelow(1000000):06d}'; c=db(); c.execute('UPDATE otps SET used=1 WHERE email=? AND purpose=? AND used=0',(email,purpose)); c.execute('INSERT INTO otps(email,code_hash,expires_at,purpose) VALUES(?,?,?,?)',(email,h(code),(now()+timedelta(minutes=OTP_MINUTES)).isoformat(),purpose)); c.commit(); c.close(); return code

@app.post('/api/register')
def register():
    if not rate_ok('reg:'+client_ip(),20,3600): return jsonify(ok=False,error='Too many attempts. Please try again later.'),429
    d=request.get_json(force=True); req=['full_name','username','password','mt5_account','telegram_chat_id']+(['email'] if otp_mode() in ('email','both') else [])
    if any(not str(d.get(k,'')).strip() for k in req): return jsonify(ok=False,error='Missing required field (Full name, Username, Password, MT5 account and Telegram Chat ID are required)'),400
    for k_ in ('full_name','username','phone','email','ref'):
        if re.search(r'[<>"`\\\']',str(d.get(k_,'')).replace("'",'') if k_=='full_name' else str(d.get(k_,''))): return jsonify(ok=False,error='Invalid characters in '+k_),400
    if not re.fullmatch(r'[A-Za-z0-9_.@-]{3,40}',str(d.get('username','')).strip()): return jsonify(ok=False,error='Username: 3-40 letters, numbers, . _ - only'),400
    telegram_chat_id=str(d.get('telegram_chat_id','')).strip()
    if not telegram_chat_id.lstrip('-').isdigit(): return jsonify(ok=False,error='Telegram Chat ID must be a number (get it from @userinfobot)'),400
    email=str(d.get('email','')).strip().lower() or ('tg'+telegram_chat_id+'@telegram.user'); c=db(); license_key=secrets.token_hex(16); trial_expiry=(now()+timedelta(days=3)).isoformat(); telegram_chat_id=str(d.get('telegram_chat_id','')).strip()
    try:
        c.execute('INSERT INTO users(full_name,email,phone,username,password_hash,mt5_account,created_at,license_key,membership,membership_expiry,telegram_chat_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(d['full_name'].strip(),email,d.get('phone','').strip(),d['username'].strip(),hash_pw(d['password']),str(d['mt5_account']),now().isoformat(),license_key,'FREE',trial_expiry,telegram_chat_id)); c.commit()
    except psycopg2.IntegrityError:
        c.close(); c=db()
        ex=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
        if not ex or ex.get('email_verified') or ex.get('username')!=d['username'].strip():
            c.close(); return jsonify(ok=False,error='Email or username already exists'),409
        # Earlier attempt failed before the code was delivered: let the user retry with updated details.
        c.execute('UPDATE users SET full_name=?,phone=?,password_hash=?,mt5_account=?,telegram_chat_id=? WHERE email=?',(d['full_name'].strip(),d.get('phone','').strip(),hash_pw(d['password']),str(d['mt5_account']),telegram_chat_id,email)); c.commit()
    refc=str(d.get('ref','')).strip().upper()[:20]
    if refc:
        rr=c.execute('SELECT id,telegram_chat_id,mt5_account FROM users WHERE referral_code=?',(refc,)).fetchone()
        if rr and str(rr['telegram_chat_id'] or '')!=telegram_chat_id and str(rr['mt5_account'] or '')!=str(d['mt5_account']):   # no self-referral
            c.execute('UPDATE users SET referred_by=? WHERE email=? AND referred_by IS NULL',(rr['id'],email)); c.commit()
    if otp_mode()=='none':
        c.execute('UPDATE users SET email_verified=1 WHERE email=?',(email,)); c.commit(); c.close()
        admin_email('New GOLD MASTERS registration (awaiting your approval)',f'User: {d["full_name"]}\nEmail: {email}\nMT5: {d["mt5_account"]}\nStatus: PENDING')
        return jsonify(ok=True,email=email,message='Registered. Your account is waiting for admin approval. You can log in after it is approved.')
    c.close(); code=otp(email,'REGISTER'); sent=send_otp_message(email,'GOLD MASTERS verification code',f'Your verification code is: {code}\nExpires in {OTP_MINUTES} minutes.',telegram_chat_id); admin_email('New GOLD MASTERS registration',f'User: {d["full_name"]}\nEmail: {email}\nMT5: {d["mt5_account"]}\nStatus: PENDING')
    if not sent: return jsonify(ok=False,error=otp_error()),502
    return jsonify(ok=True,email=email,message=otp_sent_message())

@app.post('/api/verify-email')
def verify():
    if otp_mode()=='none': return jsonify(ok=False,error='Verification is not required. Wait for admin approval.'),400
    d=request.get_json(force=True); email=d.get('email','').strip().lower(); code=d.get('code','').strip(); c=db(); r=c.execute("SELECT * FROM otps WHERE email=? AND purpose='REGISTER' AND used=0 ORDER BY id DESC LIMIT 1",(email,)).fetchone()
    if not r or r['expires_at']<now().isoformat() or r['code_hash']!=h(code): c.close(); return jsonify(ok=False,error='Invalid or expired code'),400
    c.execute('UPDATE otps SET used=1 WHERE id=?',(r['id'],)); c.execute('UPDATE users SET email_verified=1 WHERE email=?',(email,)); c.commit(); c.close(); admin_email('Email verified',f'{email} verified email and awaits admin approval.'); return jsonify(ok=True,status='PENDING')

@app.post('/api/resend-otp')
def resend_otp():
    if otp_mode()=='none': return jsonify(ok=False,error='Verification is not required.'),400
    d=request.get_json(force=True); email=d.get('email','').strip().lower()
    if not rate_ok('rs:'+email,5,3600) or not rate_ok('rsip:'+client_ip(),30,3600): return jsonify(ok=False,error='Too many attempts. Please try again later.'),429
    c=db(); u=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
    if not u: c.close(); return jsonify(ok=False,error='User not found'),404
    if u['email_verified']: c.close(); return jsonify(ok=False,error='Already verified'),400
    c.close()
    code=otp(email,'REGISTER')
    sent=send_otp_message(email,'GOLD MASTERS verification code',f'Your verification code is: {code}\nExpires in {OTP_MINUTES} minutes.',u.get('telegram_chat_id'))
    if not sent: return jsonify(ok=False,error=otp_error()),502
    return jsonify(ok=True,email_sent=sent)

@app.post('/api/login')
def login():
    if not rate_ok('login:'+client_ip(),60,600): return jsonify(ok=False,error='Too many attempts. Please try again later.'),429
    d=request.get_json(force=True); ident=d.get('username_or_email','').strip(); pw=d.get('password',''); c=db(); u=c.execute('SELECT * FROM users WHERE username=? OR email=?',(ident,ident.lower())).fetchone()
    if not u or not check_pw(pw,u['password_hash']): c.close(); return jsonify(ok=False,error='Invalid login'),401
    if not u['email_verified']: c.close(); return jsonify(ok=False,error='Email not verified'),403
    if u['status']!='APPROVED': c.close(); return jsonify(ok=False,status=u['status'],error='Admin approval required'),403
    c.execute('UPDATE users SET last_login=? WHERE id=?',(now().isoformat(),u['id']))
    if not u['password_hash'].startswith('pbkdf2$'): c.execute('UPDATE users SET password_hash=? WHERE id=?',(hash_pw(pw),u['id']))
    tok=secrets.token_urlsafe(32); ts=now()
    c.execute('DELETE FROM sessions WHERE expires_at<?',(ts.isoformat(),))
    c.execute('INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)',(h(tok),u['id'],ts.isoformat(),(ts+timedelta(days=30)).isoformat()))
    c.commit(); c.close(); return jsonify(ok=True,token=tok,user={k:u[k] for k in ['id','full_name','email','membership','membership_expiry','status','mt5_account','wallet_balance','license_key']})

def bearer_token():
    a=request.headers.get('Authorization','')
    return a[7:].strip() if a.startswith('Bearer ') else ''

def auth_user():
    tok=bearer_token()
    if not tok: return None
    c=db(); r=c.execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?',(h(tok),now().isoformat())).fetchone(); c.close()
    return r

@app.post('/api/logout')
def logout():
    tok=bearer_token()
    if tok:
        c=db(); c.execute('DELETE FROM sessions WHERE token_hash=?',(h(tok),)); c.commit(); c.close()
    return jsonify(ok=True)

@app.post('/api/payment-request')
def payment():
    u=auth_user()
    if not u: return jsonify(ok=False,error='Please log in again'),401
    d=request.get_json(force=True); email=u['email']; kind=d.get('kind','DEPOSIT').upper(); amount=float(d.get('amount',0))
    if kind not in ('DEPOSIT','WITHDRAWAL') or amount<=0: return jsonify(ok=False,error='Invalid request'),400
    c=db(); ref_=str(d.get('reference','')).strip()
    if kind=='DEPOSIT' and ref_ and (c.execute('SELECT 1 AS x FROM payments WHERE LOWER(reference)=? LIMIT 1',(ref_.lower(),)).fetchone() or c.execute('SELECT 1 AS x FROM orders WHERE LOWER(txid)=? LIMIT 1',(ref_.lower(),)).fetchone()):
        c.close(); return jsonify(ok=False,error='This transaction ID was already submitted'),409
    c.execute('INSERT INTO payments(user_id,kind,amount,currency,method,reference,destination,created_at) VALUES(?,?,?,?,?,?,?,?)',(u['id'],kind,amount,d.get('currency','USD'),d.get('method',''),d.get('reference',''),d.get('destination',''),now().isoformat())); c.commit(); c.close(); send_email(email,f'GOLD MASTERS {kind.title()} Request',f'Amount: {amount} {d.get("currency","USD")}\nStatus: PENDING ADMIN CONFIRMATION'); admin_email(f'New {kind}',f'User: {u["full_name"]}\nEmail: {email}\nAmount: {amount}'); return jsonify(ok=True,status='PENDING')

# Membership levels (days=None means lifetime). Old plan names stay valid so existing members keep working.
PLAN_INFO=[
 ('BRONZE','Bronze',50,30,['Daily gold signals','Entry price, SL & TP','Basic gold market analysis']),
 ('SILVER','Silver',200,90,['All Bronze benefits','Advanced market analysis','Signal history','Weekly market reports']),
 ('GOLD','Gold',350,180,['All Silver benefits','Sniper entry signals','Quick entry signals','Multi-timeframe analysis','Trading education']),
 ('PLATINUM','Platinum',500,365,['All Gold benefits','VIP GOLD group access','Priority support','Premium market reports','Referral partner eligibility','Live trading education']),
 ('DIAMOND','Diamond',1500,None,['All Platinum benefits','Lifetime membership','VIP GOLD & VIP DIAMOND access','Lifetime educational content','Referral partner eligibility','Exclusive member benefits'])]
PRICING={c:pr for c,_,pr,_,_ in PLAN_INFO}
PLAN_DAYS={c:d for c,_,_,d,_ in PLAN_INFO}
PLAN_DAYS.update({'MONTHLY':30,'SIX_MONTH':180,'YEARLY':365,'LIFETIME':None})
PAID_PLANS=tuple(PLAN_DAYS)
LIFETIME_PLANS=tuple(k for k,v in PLAN_DAYS.items() if v is None)
# Which plans may use AUTO trading. Change on Render with the AUTO_PLANS variable, no code change needed.
AUTO_PLANS=tuple(x.strip().upper() for x in os.getenv('AUTO_PLANS','GOLD,PLATINUM,DIAMOND,YEARLY,LIFETIME').split(',') if x.strip())
def auto_plan_text():
    n=[nm for c,nm,_,_,_ in PLAN_INFO if c in AUTO_PLANS]
    return ', '.join(n) if n else 'a higher plan'
@app.get('/api/pricing')
def pricing():
    plans=[{'code':c,'name':nm,'price':pr,'duration':('Lifetime' if d is None else str(d)+' days'),'features':f,'auto':c in AUTO_PLANS} for c,nm,pr,d,f in PLAN_INFO]
    return jsonify(ok=True,pricing=PRICING,plans=plans,trial='3 days or 3 signals, whichever comes first',currency='USD')

@app.get('/api/signals')
def signals():
    all_signals=rows("SELECT * FROM signals WHERE status='ACTIVE' ORDER BY id DESC LIMIT 100")
    u=auth_user()
    if not u:
        return jsonify(ok=True,signals=[],trial=True,error='Login required to view signals. Free trial: 3 days or 3 signals.',pricing=PRICING)
    c=db()
    paid=u['membership'] in PAID_PLANS
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
        r=requests.get('https://api.tradingeconomics.com/calendar/country/'+requests.utils.quote(country,safe=''),params={'c':'guest:guest' if key=='GUEST' else key,'f':'json'},timeout=8)
        if r.status_code>=400: return jsonify(ok=False,error='Calendar provider returned HTTP '+str(r.status_code),provider='Trading Economics'),502
        data=r.json()
        return jsonify(ok=True,provider='Trading Economics',country=country,events=data[:100])
    except Exception as e:
        return jsonify(ok=False,error='Economic calendar unavailable: '+str(e),provider='Trading Economics'),502

@app.post('/api/calendar/mt5')
def calendar_mt5():
    if not key_ok(request.headers.get('X-Calendar-Key',''),'MT5_CALENDAR_PUSH_KEY'):
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

# ============================================================
# MT5 EA INTEGRATION: heartbeat, trade log, remote bot control
# These are the endpoints ROBOT_AI_AUTO_TRADING_BOT-14.mq5 calls
# when UseWebsiteAPI / UseRemoteBotControl are enabled in its inputs.
# ============================================================

@app.post('/api/mt5/heartbeat')
def mt5_heartbeat():
    if not website_api_ok(): return jsonify(ok=False,error='Unauthorized'),401
    d=request.get_json(force=True) or {}
    account=str(d.get('mt5_account','')).strip()
    if not account: return jsonify(ok=False,error='Missing mt5_account'),400
    c=db()
    u=c.execute('SELECT * FROM users WHERE mt5_account=?',(account,)).fetchone()
    c.execute('''INSERT INTO bot_status(mt5_account,state,broker,server,symbol,balance,equity,free_margin,spread_points,positions,daily_stop,daily_profit_stop,drawdown_stop,updated_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(mt5_account) DO UPDATE SET state=EXCLUDED.state,broker=EXCLUDED.broker,server=EXCLUDED.server,symbol=EXCLUDED.symbol,
                   balance=EXCLUDED.balance,equity=EXCLUDED.equity,free_margin=EXCLUDED.free_margin,spread_points=EXCLUDED.spread_points,
                   positions=EXCLUDED.positions,daily_stop=EXCLUDED.daily_stop,daily_profit_stop=EXCLUDED.daily_profit_stop,
                   drawdown_stop=EXCLUDED.drawdown_stop,updated_at=EXCLUDED.updated_at''',
              (account,d.get('state',''),d.get('broker',''),d.get('server',''),d.get('symbol',''),
               float(d.get('balance',0) or 0),float(d.get('equity',0) or 0),float(d.get('free_margin',0) or 0),
               float(d.get('spread_points',0) or 0),int(d.get('positions',0) or 0),
               1 if d.get('daily_stop') else 0,1 if d.get('daily_profit_stop') else 0,1 if d.get('drawdown_stop') else 0,
               now().isoformat()))
    c.commit(); c.close()
    # license flag the EA checks inside the heartbeat response ("license":true/false)
    license_ok=True
    if u:
        license_ok = (u['status']=='APPROVED' and u['email_verified']==1)
        if u['membership'] not in LIFETIME_PLANS and u['membership_expiry']:
            try:
                if datetime.fromisoformat(u['membership_expiry'])<now(): license_ok=False
            except ValueError: pass
    return jsonify(ok=True,license=license_ok)

@app.post('/api/mt5/trade')
def mt5_trade():
    if not website_api_ok(): return jsonify(ok=False,error='Unauthorized'),401
    d=request.get_json(force=True) or {}
    c=db()
    c.execute('INSERT INTO bot_trades(mt5_account,event_type,side,ticket,symbol,price,lot,sl,tp,profit,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
              (str(d.get('mt5_account','')).strip(),str(d.get('type','')),str(d.get('side','')),
               int(d.get('ticket',0) or 0),str(d.get('symbol','')),float(d.get('price',0) or 0),
               float(d.get('lot',0) or 0),float(d.get('sl',0) or 0),float(d.get('tp',0) or 0),
               float(d.get('profit',0) or 0),now().isoformat()))
    c.commit(); c.close()
    return jsonify(ok=True)

@app.get('/api/mt5/bot-control')
def mt5_bot_control():
    if not bot_control_ok(): return jsonify(ok=False,error='Unauthorized'),401
    account=request.args.get('account','').strip()
    c=db()
    row=c.execute('SELECT enabled FROM bot_control WHERE mt5_account=?',(account,)).fetchone() if account else None
    c.close()
    enabled = bool(row['enabled']) if row else True  # default ON if no explicit record
    return jsonify(ok=True,enabled=enabled)

@app.get('/api/admin/mt5-status')
def admin_mt5_status():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    return jsonify(ok=True,bots=rows('SELECT * FROM bot_status ORDER BY updated_at DESC'))

@app.get('/api/admin/mt5-trades')
def admin_mt5_trades():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    return jsonify(ok=True,trades=rows('SELECT * FROM bot_trades ORDER BY id DESC LIMIT 200'))

@app.post('/api/admin/bot-control')
def admin_bot_control():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    d=request.get_json(force=True); account=str(d.get('mt5_account','')).strip(); enabled=1 if d.get('enabled',True) else 0
    if not account: return jsonify(ok=False,error='Missing mt5_account'),400
    c=db()
    c.execute('''INSERT INTO bot_control(mt5_account,enabled,updated_at) VALUES(?,?,?)
                 ON CONFLICT(mt5_account) DO UPDATE SET enabled=EXCLUDED.enabled,updated_at=EXCLUDED.updated_at''',
              (account,enabled,now().isoformat()))
    c.commit(); c.close()
    return jsonify(ok=True,mt5_account=account,enabled=bool(enabled))

@app.get('/api/admin/users')
def admin_users():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    return jsonify(ok=True,users=rows('SELECT u.id,u.full_name,u.email,u.phone,u.telegram_chat_id,u.username,u.mt5_account,u.email_verified,u.status,u.membership,u.membership_expiry,u.created_at,u.last_login,u.wallet_balance,u.license_key,s.state AS bot_state,s.equity AS bot_equity,s.balance AS bot_balance,s.positions AS bot_positions,s.updated_at AS bot_seen,s.version AS bot_version,COALESCE(c.enabled,1) AS bot_enabled FROM users u LEFT JOIN bot_status s ON s.mt5_account=u.mt5_account LEFT JOIN bot_control c ON c.mt5_account=u.mt5_account ORDER BY u.id DESC'))

@app.get('/api/admin/payments')
def admin_payments():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    return jsonify(ok=True,payments=rows('SELECT payments.*,users.full_name,users.email,users.username,users.phone,users.telegram_chat_id FROM payments JOIN users ON users.id=payments.user_id ORDER BY payments.id DESC'))

@app.post('/api/admin/user-status')
def admin_status():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    d=request.get_json(force=True); status=d.get('status','').upper(); email=d.get('email','').strip().lower()
    if status not in ('APPROVED','REJECTED','SUSPENDED'): return jsonify(ok=False,error='Invalid status'),400
    c=db(); c.execute('UPDATE users SET status=? WHERE email=?',(status,email)); c.commit(); c.close(); send_email(email,'GOLD MASTERS account update',f'Your account status is now: {status}'); return jsonify(ok=True,status=status)

@app.post('/api/admin/user-mt5')
def admin_user_mt5():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    d=request.get_json(force=True,silent=True) or {}
    email=str(d.get('email','')).strip().lower(); acc=str(d.get('mt5_account','')).strip()
    if not acc.isdigit() or not (4<=len(acc)<=12): return jsonify(ok=False,error='MT5 account must be 4 to 12 digits (numbers only)'),400
    c=db()
    u=c.execute('SELECT id,mt5_account FROM users WHERE email=?',(email,)).fetchone()
    if not u: c.close(); return jsonify(ok=False,error='User not found'),404
    other=c.execute('SELECT email FROM users WHERE mt5_account=? AND email<>?',(acc,email)).fetchone()
    if other: c.close(); return jsonify(ok=False,error='This MT5 account number is already used by another customer'),409
    c.execute('UPDATE users SET mt5_account=? WHERE email=?',(acc,email)); c.commit(); c.close()
    return jsonify(ok=True,old=u['mt5_account'],mt5_account=acc)

@app.post('/api/admin/payment-status')
def admin_payment():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    d=request.get_json(force=True); pid=int(d['payment_id']); status=d.get('status','').upper(); c=db(); p=c.execute('SELECT payments.*,users.email,users.full_name FROM payments JOIN users ON users.id=payments.user_id WHERE payments.id=?',(pid,)).fetchone()
    if not p: c.close(); return jsonify(ok=False,error='Payment not found'),404
    if status not in ('APPROVED','REJECTED'): c.close(); return jsonify(ok=False,error='Invalid status'),400
    if p['status']!='PENDING': c.close(); return jsonify(ok=False,error='This request was already processed'),400
    if status=='APPROVED' and p['kind']=='WITHDRAWAL':
        ub=c.execute('SELECT wallet_balance,commission_credit FROM users WHERE id=?',(p['user_id'],)).fetchone()
        bal=float(ub['wallet_balance'] or 0); free=bal-float(ub['commission_credit'] or 0)
        if bal<p['amount']: c.close(); return jsonify(ok=False,error='Insufficient wallet balance'),400
        if free<p['amount']: c.close(); return jsonify(ok=False,error='Commission credits cannot be withdrawn. Withdrawable balance: '+str(round(max(free,0),2))),400
    c.execute('UPDATE payments SET status=? WHERE id=?',(status,pid))
    if status=='APPROVED':
        delta=p['amount'] if p['kind']=='DEPOSIT' else -p['amount']
        c.execute('UPDATE users SET wallet_balance=COALESCE(wallet_balance,0)+? WHERE id=?',(delta,p['user_id']))
        ledger(c,p['user_id'],p['kind'],delta,'payment:'+str(pid),p.get('reference') or '')
    audit(c,'PAYMENT_'+status,'payment #'+str(pid)+' '+p['kind']+' '+str(p['amount']))
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
    if membership!='FREE' and membership not in PLAN_DAYS: return jsonify(ok=False,error='Invalid membership'),400
    expiry=d.get('membership_expiry') or None
    if not expiry and membership!='FREE' and PLAN_DAYS.get(membership): expiry=(now()+timedelta(days=PLAN_DAYS[membership])).isoformat()   # blank = automatic from the plan
    if membership=='FREE' or membership in LIFETIME_PLANS: expiry=None if not d.get('membership_expiry') else expiry
    c=db(); u=c.execute('SELECT id,email,telegram_chat_id FROM users WHERE LOWER(email)=? OR LOWER(username)=? OR telegram_chat_id=?',(email,email,email)).fetchone()
    if not u: c.close(); return jsonify(ok=False,error='User not found (use username, Telegram ID or email)'),404
    email=u['email']
    c.execute('UPDATE users SET membership=?, membership_expiry=? WHERE email=?',(membership,expiry,email)); c.commit(); c.close()
    msg_=f'Your membership is now: {membership}'+(f' (expires {expiry[:10]})' if expiry else (' (lifetime)' if membership in LIFETIME_PLANS else ''))
    if u.get('telegram_chat_id'):
        try: send_telegram(u['telegram_chat_id'],'GOLD MASTERS membership update\n'+msg_+('\nAUTO mode is included in your plan.' if membership in AUTO_PLANS else ''))
        except Exception: pass
    if not email.endswith('@telegram.user'): send_email(email,'GOLD MASTERS membership update',msg_)
    return jsonify(ok=True,membership=membership,membership_expiry=expiry)

@app.post('/api/admin/mt5-account')
def admin_mt5_account():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    d=request.get_json(force=True); email=d.get('email','').strip().lower(); mt5_account=str(d.get('mt5_account','')).strip()
    if not mt5_account: return jsonify(ok=False,error='Missing mt5_account'),400
    c=db(); u=c.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone()
    if not u: c.close(); return jsonify(ok=False,error='User not found'),404
    c.execute('UPDATE users SET mt5_account=? WHERE email=?',(mt5_account,email)); c.commit(); c.close()
    return jsonify(ok=True,email=email,mt5_account=mt5_account)

@app.get('/api/mt5/verify')
def mt5_verify():
    account=request.args.get('account','').strip(); key=request.args.get('key','').strip()
    if not account or not key: return jsonify(ok=False,error='Missing account or key'),400
    c=db(); u=c.execute('SELECT * FROM users WHERE mt5_account=? AND license_key=?',(account,key)).fetchone()
    ctl=c.execute('SELECT enabled FROM bot_control WHERE mt5_account=?',(account,)).fetchone() if u else None
    bm=c.execute('SELECT mode,seq FROM bot_mode WHERE mt5_account=?',(account,)).fetchone() if u else None
    c.close()
    if not u: return jsonify(ok=False,error='Invalid account or license key'),401
    enabled=bool(ctl['enabled']) if ctl else True   # admin can pause one customer's EA; default ON
    active=(u['status']=='APPROVED' and u['email_verified']==1)
    expired=False
    if u['membership'] not in LIFETIME_PLANS and u['membership_expiry']:
        try:
            if datetime.fromisoformat(u['membership_expiry'])<now(): expired=True
        except ValueError: pass
    if expired: active=False
    auto_allowed=active and u['membership'] in AUTO_PLANS
    return jsonify(ok=True,active=active,expired=expired,status=u['status'],membership=u['membership'],
                   membership_expiry=u['membership_expiry'],wallet_balance=u['wallet_balance'],auto_allowed=auto_allowed,enabled=enabled,mode=(bm['mode'] if bm else 'MANUAL'),mode_seq=(bm['seq'] if bm else 0),
                   latest_version=os.getenv('EA_LATEST_VERSION','').strip(),download_url=os.getenv('EA_DOWNLOAD_URL','').strip())


# ---- EA <-> website link: every call is authenticated with the customer's own MT5 account + license key ----
def ea_auth(d):
    account=str(d.get('account','')).strip(); key=str(d.get('key','')).strip()
    if not account or not key: return None
    c=db(); u=c.execute('SELECT * FROM users WHERE mt5_account=? AND license_key=?',(account,key)).fetchone(); c.close()
    return u

@app.post('/api/ea/report')
def ea_report():
    d=request.get_json(force=True,silent=True) or {}
    u=ea_auth(d)
    if not u: return jsonify(ok=False,error='Invalid account or license key'),401
    account=str(d.get('account')).strip()
    def num(k): 
        try: return float(d.get(k,0) or 0)
        except (TypeError,ValueError): return 0.0
    c=db()
    c.execute('''INSERT INTO bot_status(mt5_account,state,broker,server,symbol,balance,equity,free_margin,spread_points,positions,daily_stop,daily_profit_stop,drawdown_stop,updated_at,version)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(mt5_account) DO UPDATE SET state=EXCLUDED.state,broker=EXCLUDED.broker,server=EXCLUDED.server,symbol=EXCLUDED.symbol,
                   balance=EXCLUDED.balance,equity=EXCLUDED.equity,free_margin=EXCLUDED.free_margin,spread_points=EXCLUDED.spread_points,
                   positions=EXCLUDED.positions,daily_stop=EXCLUDED.daily_stop,daily_profit_stop=EXCLUDED.daily_profit_stop,
                   drawdown_stop=EXCLUDED.drawdown_stop,updated_at=EXCLUDED.updated_at,version=EXCLUDED.version''',
              (account,str(d.get('state',''))[:80],str(d.get('broker',''))[:80],str(d.get('server',''))[:80],str(d.get('symbol',''))[:30],
               num('balance'),num('equity'),num('free_margin'),num('spread_points'),int(num('positions')),
               1 if d.get('daily_stop') else 0,0,0,now().isoformat(),str(d.get('version',''))[:20]))
    ctl=c.execute('SELECT enabled FROM bot_control WHERE mt5_account=?',(account,)).fetchone()
    c.commit(); c.close()
    return jsonify(ok=True,enabled=(bool(ctl['enabled']) if ctl else True))

@app.post('/api/ea/trade')
def ea_trade():
    d=request.get_json(force=True,silent=True) or {}
    u=ea_auth(d)
    if not u: return jsonify(ok=False,error='Invalid account or license key'),401
    def num(k):
        try: return float(d.get(k,0) or 0)
        except (TypeError,ValueError): return 0.0
    c=db()
    c.execute('INSERT INTO bot_trades(mt5_account,event_type,side,ticket,symbol,price,lot,sl,tp,profit,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
              (str(d.get('account')).strip(),str(d.get('type',''))[:30],str(d.get('side',''))[:10],int(num('ticket')),
               str(d.get('symbol',''))[:30],num('price'),num('lot'),num('sl'),num('tp'),num('profit'),now().isoformat()))
    c.commit(); c.close()
    return jsonify(ok=True)


# ---- GOLD TITAN EA -> website Signals page (owner's EA only; protected by a secret key in Render) ----
def signal_key_ok():
    k=(os.getenv('SIGNAL_API_KEY','') or os.getenv('WEBSITE_API_KEY','')).strip()
    if not k or k=='CHANGE_ME': return False
    auth=request.headers.get('Authorization','')
    token=auth.replace('Bearer ','').strip() if auth.startswith('Bearer ') else auth.strip()
    return secrets.compare_digest(token,k)

@app.post('/api/ea/signal')
def ea_signal():
    if not signal_key_ok(): return jsonify(ok=False,error='Unauthorized'),401
    d=request.get_json(force=True,silent=True) or {}
    direction=str(d.get('direction','')).upper()
    if direction not in ('BUY','SELL'): return jsonify(ok=False,error='Invalid direction'),400
    try:
        entry,sl,tp1,tp2,tp3=[float(d.get(k)) for k in ('entry','sl','tp1','tp2','tp3')]
        conf=float(d.get('confidence',0) or 0)
    except (TypeError,ValueError): return jsonify(ok=False,error='Invalid numbers'),400
    ok=(sl<entry<tp1<tp2<tp3) if direction=='BUY' else (tp3<tp2<tp1<entry<sl)
    if not ok: return jsonify(ok=False,error='Price levels are not consistent'),400
    symbol=''.join(ch for ch in str(d.get('symbol','XAUUSD')).upper() if ch.isalnum() or ch in '._#')[:20] or 'XAUUSD'
    tf=str(d.get('timeframe',''))[:6]; bar=str(d.get('bar_time',''))[:20]; reason=str(d.get('reason',''))[:200]
    key=symbol+'|'+tf+'|'+bar+'|'+direction
    c=db()
    if bar and c.execute('SELECT id FROM signals WHERE dedupe_key=?',(key,)).fetchone():
        c.close(); return jsonify(ok=True,duplicate=True)
    # EA signals stop being "ACTIVE" after 24 hours
    c.execute("UPDATE signals SET status='EXPIRED' WHERE status='ACTIVE' AND dedupe_key IS NOT NULL AND created_at<?",((now()-timedelta(hours=24)).isoformat(),))
    c.execute('INSERT INTO signals(symbol,direction,entry,sl,tp1,tp2,tp3,confidence,reason,created_at,timeframe,dedupe_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
              (symbol,direction,entry,sl,tp1,tp2,tp3,conf,reason,now().isoformat(),tf,(key if bar else None)))
    c.commit(); c.close()
    return jsonify(ok=True)


# ---------------- Customer EA download (stored in PostgreSQL, never in GitHub) ----------------
def latest_ea(with_data=False):
    cols='id,version,filename,size,uploaded_at'+(',data' if with_data else '')
    c=db(); r=c.execute('SELECT '+cols+' FROM ea_files ORDER BY id DESC LIMIT 1').fetchone(); c.close()
    return r

def ea_user():
    """Logged-in, email-verified and admin-APPROVED users only (payment NOT required)."""
    u=auth_user()
    if not u: return None,(jsonify(ok=False,error='Please log in again'),401)
    if not u['email_verified'] or u['status']!='APPROVED':
        return None,(jsonify(ok=False,error='Your account must be approved by the admin before you can download the EA'),403)
    return u,None

@app.get('/api/ea/info')
def ea_info():
    u,err=ea_user()
    if err: return err
    e=latest_ea()
    if not e: return jsonify(ok=True,available=False)
    c=db(); b=c.execute('SELECT version FROM bot_status WHERE mt5_account=?',(str(u['mt5_account']),)).fetchone(); c.close()
    mine=(b['version'] if b and b.get('version') else '') or ''
    def vf(x):
        try: return float(str(x).lower().lstrip('v').strip())
        except Exception: return None
    a,l=vf(mine),vf(e['version'])
    upd=bool(mine) and ((l>a) if (a is not None and l is not None) else mine.strip()!=str(e['version']).strip())
    return jsonify(ok=True,available=True,version=e['version'],filename=e['filename'],size=e['size'],uploaded_at=e['uploaded_at'],your_version=mine,update_available=upd)

@app.get('/api/ea/download')
def ea_download():
    if not rate_ok('eadl:'+client_ip(),30,3600): return jsonify(ok=False,error='Too many downloads. Please try again later.'),429
    u,err=ea_user()
    if err: return err
    e=latest_ea(True)
    if not e: return jsonify(ok=False,error='The EA file has not been uploaded yet. Please contact support.'),404
    c=db(); c.execute('INSERT INTO ea_downloads(user_id,ea_id,version,ip,downloaded_at) VALUES(?,?,?,?,?)',(u['id'],e['id'],e['version'],client_ip(),now().isoformat())); c.commit(); c.close()
    return Response(bytes(e['data']),mimetype='application/octet-stream',headers={'Content-Disposition':'attachment; filename="'+e['filename']+'"','Cache-Control':'no-store'})

@app.post('/api/admin/ea-upload')
def admin_ea_upload():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    f=request.files.get('file'); version=(request.form.get('version') or '').strip()[:20]
    if not f or not f.filename: return jsonify(ok=False,error='Choose the compiled .ex5 file'),400
    if not version: return jsonify(ok=False,error='Enter a version number, e.g. 2.4'),400
    name=os.path.basename(f.filename).replace('"','').replace(' ','_')
    if not name.lower().endswith('.ex5'):
        return jsonify(ok=False,error='Only the compiled .ex5 file is allowed. Never upload the .mq5 source file.'),400
    data=f.read()
    if len(data)<1024: return jsonify(ok=False,error='File looks empty or invalid'),400
    c=db(); c.execute('INSERT INTO ea_files(version,filename,data,size,uploaded_at) VALUES(?,?,?,?,?)',(version,name,psycopg2.Binary(data),len(data),now().isoformat())); c.commit(); c.close()
    return jsonify(ok=True,version=version,filename=name,size=len(data))

@app.get('/api/admin/ea-info')
def admin_ea_info():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    e=latest_ea()
    c=db(); n=c.execute('SELECT COUNT(*) AS n FROM ea_downloads').fetchone()['n']; c.close()
    return jsonify(ok=True,available=bool(e),ea=e,downloads=n)

@app.errorhandler(413)
def too_big(e): return jsonify(ok=False,error='File too large (max 10 MB)'),413


# ---------------- In-app high-impact news alerts ----------------
NEWS_CACHE={'t':0,'ev':[]}
def parse_event_time(s):
    s=str(s or '').strip()
    if not s: return None
    if s.isdigit():
        try: return datetime.fromtimestamp(int(s),timezone.utc)
        except Exception: return None
    for fmt in ('%Y.%m.%d %H:%M:%S','%Y.%m.%d %H:%M','%Y-%m-%d %H:%M:%S','%Y-%m-%d %H:%M'):
        try: return datetime.strptime(s,fmt).replace(tzinfo=timezone.utc)
        except Exception: pass
    try:
        d=datetime.fromisoformat(s.replace('Z','+00:00'))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception: return None

def news_events():
    """High-impact USD events from the MT5 calendar table, else Trading Economics (cached 5 min)."""
    if time.time()-NEWS_CACHE['t']<300: return NEWS_CACHE['ev']
    out=[]
    try:
        for r in rows("SELECT time,currency,event,importance,actual,forecast,previous FROM mt5_calendar WHERE importance>=3 AND (currency='USD' OR currency='')"):
            t=parse_event_time(r['time'])
            if t: out.append({'event':r['event'],'time':t,'actual':r['actual'],'forecast':r['forecast'],'previous':r['previous']})
        if not out:
            key=os.getenv('TRADING_ECONOMICS_API_KEY','').strip()
            if key:
                r=requests.get('https://api.tradingeconomics.com/calendar/country/United%20States',params={'c':'guest:guest' if key=='GUEST' else key,'f':'json'},timeout=15)
                if r.status_code<400:
                    for x in r.json():
                        if int(x.get('Importance') or 0)>=3:
                            t=parse_event_time(x.get('Date'))
                            if t: out.append({'event':x.get('Event',''),'time':t,'actual':x.get('Actual'),'forecast':x.get('Forecast'),'previous':x.get('Previous')})
    except Exception as e:
        print('news_events error:',e)
    NEWS_CACHE['t']=time.time(); NEWS_CACHE['ev']=out
    return out

@app.get('/api/news/alerts')
def news_alerts():
    n=now(); res=[]
    for e in news_events():
        mins=round((e['time']-n).total_seconds()/60)
        if -15<=mins<=60:
            res.append({'event':e['event'],'time':e['time'].isoformat(),'minutes':mins,'status':'upcoming' if mins>0 else 'released','actual':e['actual'],'forecast':e['forecast'],'previous':e['previous']})
    res.sort(key=lambda x:x['minutes'])
    return jsonify(ok=True,alerts=res[:5])


# ---------------- Contact form -> Telegram (admin) ----------------
@app.post('/api/contact')
def contact():
    if not rate_ok('contact:'+client_ip(),5,3600): return jsonify(ok=False,error='Too many messages. Please try again later.'),429
    d=request.get_json(force=True) or {}
    name=str(d.get('name','')).strip()[:80]; tg=str(d.get('telegram','')).strip()[:60]
    subj=str(d.get('subject','')).strip()[:100]; msg_=str(d.get('message','')).strip()[:1500]
    if not name or not tg or not msg_: return jsonify(ok=False,error='Please enter your name, Telegram username and message'),400
    chat=os.getenv('ADMIN_TELEGRAM_CHAT_ID','').strip()
    if not chat: return jsonify(ok=False,error='The form is not available right now. Please use the Telegram support button.'),503
    ok=send_telegram(chat,'New support message\nName: '+name+'\nTelegram: '+tg+'\nSubject: '+(subj or '-')+'\n\n'+msg_)
    if not ok: return jsonify(ok=False,error='Could not deliver the message. Please use the Telegram support button.'),502
    return jsonify(ok=True,message='Message sent. We will reply to you on Telegram.')


# ---------------- Forgot password: OTP sent to the user's Telegram ----------------
def find_user_by_ident(ident):
    ident=(ident or '').strip().lower()
    if not ident: return None
    c=db(); u=c.execute('SELECT * FROM users WHERE LOWER(username)=? OR LOWER(email)=? OR telegram_chat_id=?',(ident,ident,ident)).fetchone(); c.close()
    return u

@app.post('/api/forgot-password')
def forgot_password():
    d=request.get_json(force=True,silent=True) or {}; ident=str(d.get('identifier','')).strip().lower()
    if not ident: return jsonify(ok=False,error='Enter your username or Telegram ID'),400
    if not rate_ok('fp:'+client_ip(),10,3600) or not rate_ok('fpid:'+ident,3,3600):
        return jsonify(ok=False,error='Too many attempts. Please try again in an hour.'),429
    u=find_user_by_ident(ident)
    generic=jsonify(ok=True,message='If this account exists, a 6-digit code was sent to its Telegram. Make sure you pressed Start on @goldmasters_otp_bot.')
    if not u or not u.get('telegram_chat_id'): return generic
    code=otp(u['email'],'RESET')
    sent=send_telegram(u['telegram_chat_id'],'GOLD MASTERS password reset\n\nYour code is: '+code+'\nExpires in '+str(OTP_MINUTES)+' minutes.\nIf you did not ask for this, ignore this message and do not share the code.')
    if not sent: return jsonify(ok=False,error=tg_error()),502
    return generic

@app.post('/api/reset-password')
def reset_password():
    d=request.get_json(force=True,silent=True) or {}
    ident=str(d.get('identifier','')).strip().lower(); code=str(d.get('code','')).strip(); pw=str(d.get('new_password',''))
    if not ident or not code or not pw: return jsonify(ok=False,error='Enter your username, the code and a new password'),400
    if len(pw)<8: return jsonify(ok=False,error='Password must be at least 8 characters'),400
    if not rate_ok('rp:'+client_ip(),30,3600) or not rate_ok('rpid:'+ident,6,900):
        return jsonify(ok=False,error='Too many attempts. Please request a new code later.'),429
    u=find_user_by_ident(ident)
    bad=jsonify(ok=False,error='Invalid or expired code'),400
    if not u: return bad
    c=db(); r=c.execute("SELECT * FROM otps WHERE email=? AND purpose='RESET' AND used=0 ORDER BY id DESC LIMIT 1",(u['email'],)).fetchone()
    if not r or r['expires_at']<now().isoformat() or not secrets.compare_digest(r['code_hash'],h(code)): c.close(); return bad
    c.execute('UPDATE otps SET used=1 WHERE id=?',(r['id'],))
    c.execute('UPDATE users SET password_hash=? WHERE id=?',(hash_pw(pw),u['id']))
    c.execute('DELETE FROM sessions WHERE user_id=?',(u['id'],))   # log out every old session
    c.commit(); c.close()
    if u.get('telegram_chat_id'): send_telegram(u['telegram_chat_id'],'GOLD MASTERS: your password was changed. If this was not you, contact support right away.')
    return jsonify(ok=True,message='Password changed. You can log in now.')


# ---------------- AI market brief (mobile AI dashboard) ----------------
AI_CACHE={}
def _ema(v,n):
    k=2/(n+1); e=sum(v[:n])/n
    for x in v[n:]: e=x*k+e*(1-k)
    return e
def _rsi(v,n=14):
    g=l=0.0
    for i in range(1,n+1):
        d=v[i]-v[i-1]; g+=max(d,0); l+=max(-d,0)
    g/=n; l/=n
    for i in range(n+1,len(v)):
        d=v[i]-v[i-1]; g=(g*(n-1)+max(d,0))/n; l=(l*(n-1)+max(-d,0))/n
    return 100.0 if l==0 else 100-100/(1+g/l)
def _atr(c,n=14):
    trs=[]
    for i in range(1,len(c)):
        h,l,_=c[i]; pc=c[i-1][2]; trs.append(max(h-l,abs(h-pc),abs(l-pc)))
    a=sum(trs[:n])/n
    for t in trs[n:]: a=(a*(n-1)+t)/n
    return a

def ai_facts():
    """Real numbers only. The AI (or the rule-based fallback) may use nothing else."""
    f={'symbol':'XAUUSD','timeframe':'1h','as_of':now().isoformat()}
    key=os.getenv('TWELVEDATA_API_KEY','').strip()
    if key:
        try:
            r=requests.get('https://api.twelvedata.com/time_series',params={'symbol':'XAU/USD','interval':'1h','outputsize':120,'apikey':key},timeout=7).json()
            vals=list(reversed(r.get('values') or []))
            if len(vals)>=60:
                cl=[float(x['close']) for x in vals]; hi=[float(x['high']) for x in vals]; lo=[float(x['low']) for x in vals]
                e20,e50=_ema(cl,20),_ema(cl,50)
                f.update(price=round(cl[-1],2),ema20=round(e20,2),ema50=round(e50,2),rsi14=round(_rsi(cl),1),atr14=round(_atr(list(zip(hi,lo,cl))),2),
                         high_24h=round(max(hi[-24:]),2),low_24h=round(min(lo[-24:]),2),change_24h_pct=round((cl[-1]/cl[-25]-1)*100,2),
                         trend='up' if (e20>e50 and cl[-1]>e50) else 'down' if (e20<e50 and cl[-1]<e50) else 'mixed')
        except Exception as e:
            print('ai_facts market error:',e)
    try:
        f['signals']=[{'direction':s['direction'],'entry':s['entry'],'sl':s['sl'],'tp1':s['tp1'],'tp2':s['tp2'],'confidence':s['confidence'],'created_at':s['created_at']}
                      for s in rows("SELECT direction,entry,sl,tp1,tp2,confidence,created_at FROM signals WHERE status='ACTIVE' ORDER BY id DESC LIMIT 3")]
    except Exception as e: f['signals']=[]
    n=now(); nw=[]
    for e in news_events():
        mins=round((e['time']-n).total_seconds()/60)
        if -60<=mins<=1440: nw.append({'event':e['event'],'in_minutes':mins})
    f['news']=sorted(nw,key=lambda x:x['in_minutes'])[:5]
    return f

def ai_rules_text(f):
    if not f.get('price') and not f.get('signals') and not f.get('news'):
        return 'Market data is not available right now, so no analysis can be produced. Please try again later.'
    L=[]
    if f.get('price'):
        t={'up':'bullish (EMA20 above EMA50, price above both)','down':'bearish (EMA20 below EMA50, price below both)','mixed':'mixed / range-bound'}[f['trend']]
        rs=f['rsi14']; rt='overbought' if rs>=70 else 'oversold' if rs<=30 else 'neutral'
        L.append('Bias: '+t+'. RSI(14) is '+str(rs)+' ('+rt+'); 24h change '+str(f['change_24h_pct'])+'%.')
        L.append('Key levels: price '+str(f['price'])+', 24h high '+str(f['high_24h'])+', 24h low '+str(f['low_24h'])+'. Typical hourly range (ATR) is about '+str(f['atr14'])+'.')
    if f.get('signals'):
        s=f['signals'][0]; L.append('Latest signal: '+str(s['direction'])+' near '+str(s['entry'])+', SL '+str(s['sl'])+', TP1 '+str(s['tp1'])+' (confidence '+str(s['confidence'])+'%).')
    if f.get('news'):
        L.append('News risk: '+'; '.join((x['event']+(' in '+str(x['in_minutes'])+' min' if x['in_minutes']>0 else ' (just released)')) for x in f['news'])+'. Expect fast moves and wider spreads.')
    else: L.append('News risk: no high-impact USD news in the next 24 hours.')
    L.append('Risk reminder: use a stop loss on every trade and risk only what you can afford to lose. This is information, not a guarantee.')
    return '\n'.join(L)

def ai_llm_text(f,lang):
    key=os.getenv('ANTHROPIC_API_KEY','').strip()
    if not key: return None
    system=('You are the market analyst inside the GOLD MASTERS gold (XAUUSD) trading dashboard. Use ONLY the facts JSON the user sends. '
            'Never invent prices, levels, news or numbers; if something is missing, say it is unavailable. '
            'Never promise profit and never give a direct order to buy or sell: describe the bias, scenarios and levels to watch. '
            'Write exactly four short labeled lines: Bias, Key levels, News risk, Risk reminder. Maximum 170 words in total. Plain text, no markdown.')
    if lang=='am': system+=' Write the whole answer in Amharic (አማርኛ, Ethiopic script). Keep numbers in digits.'
    try:
        r=requests.post('https://api.anthropic.com/v1/messages',headers={'x-api-key':key,'anthropic-version':'2023-06-01','content-type':'application/json'},
            json={'model':os.getenv('AI_MODEL','claude-sonnet-5-5'),'max_tokens':700,'system':system,'messages':[{'role':'user','content':'Facts:\n'+json.dumps(f)}]},timeout=17)
        if r.status_code>=400:
            print('ai_llm error',r.status_code,r.text[:200]); return None
        t=''.join(b.get('text','') for b in r.json().get('content',[]) if b.get('type')=='text').strip()
        return t or None
    except Exception as e:
        print('ai_llm exception:',e); return None

@app.get('/api/ai/brief')
def ai_brief():
    u,err=ea_user()
    if err: return err
    if not rate_ok('ai:'+str(u['id']),20,3600): return jsonify(ok=False,error='Too many requests. Please try again later.'),429
    lang='am' if request.args.get('lang')=='am' else 'en'
    ent=AI_CACHE.get(lang)
    if ent and time.time()-ent['t']<600: return jsonify(ok=True,cached=True,**ent['data'])
    f=ai_facts(); text=ai_llm_text(f,lang) if (f.get('price') or f.get('signals') or f.get('news')) else None
    source='ai' if text else 'rules'
    if not text: text=ai_rules_text(f)
    data={'text':text,'source':source,'facts':f,'generated_at':now().isoformat(),'lang':lang}
    AI_CACHE[lang]={'t':time.time(),'data':data}
    return jsonify(ok=True,cached=False,**data)


# ---------------- Remote AUTO / MANUAL mode switch (website -> EA) ----------------
def member_auto_allowed(u):
    if u['status']!='APPROVED' or u['email_verified']!=1: return False
    if u['membership'] not in AUTO_PLANS: return False
    if u['membership'] not in LIFETIME_PLANS and u['membership_expiry']:
        try:
            if datetime.fromisoformat(u['membership_expiry'])<now(): return False
        except ValueError: pass
    return True

@app.get('/api/bot/mode')
def bot_mode_get():
    u,err=ea_user()
    if err: return err
    acc=str(u['mt5_account'] or '')
    c=db()
    bm=c.execute('SELECT mode,seq,updated_at FROM bot_mode WHERE mt5_account=?',(acc,)).fetchone()
    st=c.execute('SELECT state,version,balance,equity,positions,updated_at FROM bot_status WHERE mt5_account=?',(acc,)).fetchone()
    ctl=c.execute('SELECT enabled FROM bot_control WHERE mt5_account=?',(acc,)).fetchone()
    c.close()
    online=False; state=''; ver=''; seen=''
    if st:
        state=st['state'] or ''; ver=st['version'] or ''; seen=st['updated_at'] or ''
        try:
            t=datetime.fromisoformat(seen); t=t if t.tzinfo else t.replace(tzinfo=timezone.utc)
            online=(now()-t).total_seconds()<180
        except Exception: pass
    return jsonify(ok=True,requested=(bm['mode'] if bm else 'MANUAL'),requested_at=(bm['updated_at'] if bm else ''),auto_allowed=member_auto_allowed(u),auto_plans=auto_plan_text(),
                   paused=bool(ctl) and not bool(ctl['enabled']),online=online,state=state,version=ver,last_seen=seen,
                   balance=(st['balance'] if st else None),equity=(st['equity'] if st else None),positions=(st['positions'] if st else None))

@app.post('/api/bot/mode')
def bot_mode_set():
    u,err=ea_user()
    if err: return err
    if not rate_ok('botmode:'+str(u['id']),20,600): return jsonify(ok=False,error='Too many changes. Please wait a few minutes.'),429
    d=request.get_json(force=True,silent=True) or {}; mode=str(d.get('mode','')).upper()
    if mode not in ('AUTO','MANUAL','OFF'): return jsonify(ok=False,error='Invalid mode'),400
    if not u['mt5_account']: return jsonify(ok=False,error='No MT5 account on your profile'),400
    if mode=='AUTO' and not member_auto_allowed(u):
        return jsonify(ok=False,error='AUTO mode is included in these plans: '+auto_plan_text()+'. Please upgrade.'),403
    acc=str(u['mt5_account'])
    c=db()
    c.execute("INSERT INTO bot_mode(mt5_account,mode,seq,updated_at) VALUES(?,?,1,?) ON CONFLICT(mt5_account) DO UPDATE SET mode=EXCLUDED.mode,seq=bot_mode.seq+1,updated_at=EXCLUDED.updated_at",(acc,mode,now().isoformat()))
    c.commit(); c.close()
    if u.get('telegram_chat_id'):
        try: send_telegram(u['telegram_chat_id'],'GOLD MASTERS: bot mode set to '+mode+' from the website. The EA applies it within about a minute. If this was not you, change your password and contact support.')
        except Exception: pass
    return jsonify(ok=True,requested=mode,message='Mode request saved. The EA applies it within about a minute.')


# ================= Wallet ledger, membership orders and 25% referral commissions =================
REF_RATE=0.25
SITE_URL=os.getenv('SITE_URL','https://goldmasterbot.pages.dev').rstrip('/')
PARTNER_PLANS=('PLATINUM','DIAMOND','YEARLY','LIFETIME')      # active members of these plans are Referral Partners
PLAN_RANK={'MONTHLY':1,'BRONZE':1,'SILVER':2,'SIX_MONTH':2,'GOLD':3,'YEARLY':4,'PLATINUM':4,'DIAMOND':5,'LIFETIME':5}
def audit(c,action,detail=''): c.execute('INSERT INTO admin_audit(action,detail,created_at) VALUES(?,?,?)',(action,str(detail)[:500],now().isoformat()))
def ledger(c,uid,kind,amount,ref='',note=''):
    bal=c.execute('SELECT wallet_balance FROM users WHERE id=?',(uid,)).fetchone()['wallet_balance'] or 0
    c.execute('INSERT INTO wallet_ledger(user_id,kind,amount,balance_after,ref,note,created_at) VALUES(?,?,?,?,?,?,?)',(uid,kind,round(float(amount),2),round(float(bal),2),ref,str(note)[:200],now().isoformat()))
def plan_active(u):
    if u['membership'] not in PAID_PLANS: return False
    if u['membership'] in LIFETIME_PLANS or not u['membership_expiry']: return True
    try: return datetime.fromisoformat(u['membership_expiry'])>=now()
    except ValueError: return True
def is_partner(u): return u['status']=='APPROVED' and u['membership'] in PARTNER_PLANS and plan_active(u)
def ensure_ref_code(u):
    if u.get('referral_code'): return u['referral_code']
    for _ in range(8):
        code=''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(8))
        c=db()
        try:
            c.execute('UPDATE users SET referral_code=? WHERE id=? AND referral_code IS NULL',(code,u['id'])); c.commit(); c.close()
            r=rows('SELECT referral_code FROM users WHERE id=?',(u['id'],)); return (r[0]['referral_code'] if r else '') or ''
        except psycopg2.IntegrityError:
            c.close()
    return ''
def tg_user(uid,text):
    try:
        r=rows('SELECT telegram_chat_id FROM users WHERE id=?',(uid,))
        if r and r[0]['telegram_chat_id']: send_telegram(r[0]['telegram_chat_id'],text)
    except Exception as e: print('tg_user failed:',e)

def credit_commission(c,cid):
    cm=c.execute("SELECT * FROM commissions WHERE id=? AND status='PENDING'",(cid,)).fetchone()
    if not cm: return None
    c.execute("UPDATE commissions SET status='CREDITED',decided_at=? WHERE id=?",(now().isoformat(),cid))
    c.execute('UPDATE users SET wallet_balance=COALESCE(wallet_balance,0)+?,commission_credit=COALESCE(commission_credit,0)+? WHERE id=?',(cm['amount'],cm['amount'],cm['referrer_id']))
    ledger(c,cm['referrer_id'],'COMMISSION',cm['amount'],'commission:'+str(cid),cm['plan']+' referral 25%')
    return cm

def create_commission(c,o,buyer):
    if not buyer['referred_by'] or o['plan'] not in PRICING: return
    ref=c.execute('SELECT * FROM users WHERE id=?',(buyer['referred_by'],)).fetchone()
    if not ref or ref['id']==buyer['id']: return
    if (ref['telegram_chat_id'] and ref['telegram_chat_id']==buyer['telegram_chat_id']) or (ref['mt5_account'] and ref['mt5_account']==buyer['mt5_account']): return
    if not is_partner(ref): return
    if os.getenv('COMMISSION_FIRST_ONLY','0')=='1' and c.execute('SELECT 1 AS x FROM commissions WHERE referred_id=? LIMIT 1',(buyer['id'],)).fetchone(): return
    basis=round(float(o['price'])-float(o['commission_used']),2)      # the part paid with commission credits earns no commission
    if basis<=0: return
    amt=round(basis*REF_RATE,2)
    c.execute('INSERT INTO commissions(order_id,referrer_id,referred_id,plan,basis,rate,amount,created_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(order_id) DO NOTHING',(o['id'],ref['id'],buyer['id'],o['plan'],basis,REF_RATE,amt,now().isoformat()))
    if os.getenv('COMMISSION_AUTO','0')=='1':
        cm=c.execute('SELECT id FROM commissions WHERE order_id=?',(o['id'],)).fetchone()
        if cm: credit_commission(c,cm['id'])

def approve_order(c,oid):
    o=c.execute("SELECT * FROM orders WHERE id=? AND status='PENDING'",(oid,)).fetchone()
    if not o: return None
    u=c.execute('SELECT * FROM users WHERE id=?',(o['user_id'],)).fetchone()
    days=PLAN_DAYS.get(o['plan']); base=now()
    if u['membership']==o['plan'] and u['membership_expiry'] and o['plan'] not in LIFETIME_PLANS:
        try: base=max(base,datetime.fromisoformat(u['membership_expiry']))
        except ValueError: pass
    expiry=None if days is None else (base+timedelta(days=days)).isoformat()
    c.execute('UPDATE users SET membership=?,membership_expiry=? WHERE id=?',(o['plan'],expiry,u['id']))
    c.execute("UPDATE orders SET status='APPROVED',decided_at=? WHERE id=?",(now().isoformat(),oid))
    create_commission(c,o,u)
    return expiry or 'LIFETIME'

def reject_order(c,oid):
    o=c.execute("SELECT * FROM orders WHERE id=? AND status='PENDING'",(oid,)).fetchone()
    if not o: return False
    c.execute("UPDATE orders SET status='REJECTED',decided_at=? WHERE id=?",(now().isoformat(),oid))
    if o['wallet_amount']>0:
        c.execute('UPDATE users SET wallet_balance=COALESCE(wallet_balance,0)+?,commission_credit=COALESCE(commission_credit,0)+? WHERE id=?',(o['wallet_amount'],o['commission_used'],o['user_id']))
        ledger(c,o['user_id'],'PURCHASE_REFUND',o['wallet_amount'],'order:'+str(oid),o['plan'])
    return True

@app.post('/api/order')
def order_create():
    u,err=ea_user()
    if err: return err
    if not rate_ok('order:'+str(u['id']),10,3600): return jsonify(ok=False,error='Too many attempts. Please try again later.'),429
    d=request.get_json(force=True,silent=True) or {}; plan=str(d.get('plan','')).upper()
    if plan not in PRICING: return jsonify(ok=False,error='Choose a membership plan'),400
    price=float(PRICING[plan])
    cur=u['membership']
    if plan_active(u) and PLAN_RANK.get(cur,0)>PLAN_RANK.get(plan,0): return jsonify(ok=False,error='You already have a higher plan ('+cur+')'),400
    if cur in LIFETIME_PLANS and plan_active(u): return jsonify(ok=False,error='You already have a lifetime plan'),400
    try: use=round(max(0.0,min(float(d.get('wallet_amount') or 0),price)),2)
    except (TypeError,ValueError): return jsonify(ok=False,error='Invalid wallet amount'),400
    crypto=round(price-use,2); net=str(d.get('network','')).upper(); txid=str(d.get('txid','')).strip()
    if crypto>0:
        if net not in ('BEP20','TRC20'): return jsonify(ok=False,error='Choose the payment network (BEP20 or TRC20)'),400
        if not re.fullmatch(r'(0x)?[0-9a-fA-F]{64}',txid): return jsonify(ok=False,error='Enter a valid transaction hash (TXID)'),400
    else: net=''; txid=''
    c=db()
    try:
        if c.execute("SELECT 1 AS x FROM orders WHERE user_id=? AND status='PENDING' LIMIT 1",(u['id'],)).fetchone(): c.close(); return jsonify(ok=False,error='You already have an order waiting for approval'),409
        if txid and (c.execute('SELECT 1 AS x FROM payments WHERE LOWER(reference)=? LIMIT 1',(txid.lower(),)).fetchone() or c.execute('SELECT 1 AS x FROM orders WHERE LOWER(txid)=? LIMIT 1',(txid.lower(),)).fetchone()):
            c.close(); return jsonify(ok=False,error='This transaction ID was already submitted'),409
        cu=0.0
        if use>0:
            fresh=c.execute('SELECT COALESCE(commission_credit,0) AS cc FROM users WHERE id=?',(u['id'],)).fetchone()
            cu=round(min(float(fresh['cc']),use),2)
            got=c.execute('UPDATE users SET wallet_balance=wallet_balance-?,commission_credit=COALESCE(commission_credit,0)-? WHERE id=? AND COALESCE(wallet_balance,0)>=?',(use,cu,u['id'],use))
            if got.rowcount!=1: c.close(); return jsonify(ok=False,error='Not enough wallet balance'),400
        oid=c.execute('INSERT INTO orders(user_id,plan,price,wallet_amount,commission_used,crypto_amount,network,txid,created_at) VALUES(?,?,?,?,?,?,?,?,?) RETURNING id',(u['id'],plan,price,use,cu,crypto,net or None,txid or None,now().isoformat())).fetchone()['id']
        if use>0: ledger(c,u['id'],'PURCHASE_HOLD',-use,'order:'+str(oid),plan)
        done=None
        if crypto==0: done=approve_order(c,oid); audit(c,'ORDER_AUTO_APPROVED','order #'+str(oid)+' '+plan+' wallet-only')
        c.commit(); c.close()
    except psycopg2.IntegrityError:
        try: c.close()
        except Exception: pass
        return jsonify(ok=False,error='This transaction ID was already submitted'),409
    if done:
        tg_user(u['id'],'GOLD MASTERS: your '+plan+' membership is active ('+('lifetime' if done=='LIFETIME' else 'until '+done[:10])+').')
        return jsonify(ok=True,status='APPROVED',message='Membership activated.')
    admin_email('New membership order #'+str(oid),'User: '+u['full_name']+' (@'+u['username']+')\nPlan: '+plan+' $'+str(price)+'\nFrom wallet: $'+str(use)+'\nCrypto: $'+str(crypto)+' '+net+'\nTXID: '+txid)
    return jsonify(ok=True,status='PENDING',message='Order sent. The admin will verify your payment and activate your membership.')

@app.get('/api/wallet')
def wallet_get():
    u=auth_user()
    if not u: return jsonify(ok=False,error='Please log in again'),401
    code=ensure_ref_code(u)
    c=db()
    one=lambda q,pp=(): c.execute(q,pp).fetchone()
    bal=float(u['wallet_balance'] or 0); cc=float(u['commission_credit'] or 0)
    pend=one("SELECT COALESCE(SUM(amount),0) AS s FROM commissions WHERE referrer_id=? AND status='PENDING'",(u['id'],))['s']
    earned=one("SELECT COALESCE(SUM(amount),0) AS s FROM commissions WHERE referrer_id=? AND status='CREDITED'",(u['id'],))['s']
    spent=one("SELECT COALESCE(SUM(wallet_amount),0) AS s FROM orders WHERE user_id=? AND status='APPROVED'",(u['id'],))['s']
    held=one("SELECT COALESCE(SUM(wallet_amount),0) AS s FROM orders WHERE user_id=? AND status='PENDING'",(u['id'],))['s']
    partner=is_partner(u)
    refs=c.execute("SELECT full_name,status,membership,created_at FROM users WHERE referred_by=? ORDER BY id DESC LIMIT 50",(u['id'],)).fetchall()
    for r in refs:
        n=(r['full_name'] or '').split(); r['full_name']=(n[0]+' '+n[1][0]+'.') if len(n)>1 else (n[0] if n else '')
    out=dict(ok=True,available=round(bal,2),withdrawable=round(max(bal-cc,0),2),pending_commission=round(float(pend),2),total_earned=round(float(earned),2),total_spent=round(float(spent),2),on_hold=round(float(held),2),
        is_partner=partner,referral_code=code,referral_link=(SITE_URL+'/?ref='+code if partner and code else ''),rate=int(REF_RATE*100),
        referrals=refs,
        commissions=c.execute('SELECT plan,basis,amount,status,created_at FROM commissions WHERE referrer_id=? ORDER BY id DESC LIMIT 50',(u['id'],)).fetchall(),
        ledger=c.execute('SELECT kind,amount,balance_after,note,created_at FROM wallet_ledger WHERE user_id=? ORDER BY id DESC LIMIT 50',(u['id'],)).fetchall(),
        orders=c.execute('SELECT id,plan,price,wallet_amount,crypto_amount,status,created_at FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10',(u['id'],)).fetchall(),
        membership=u['membership'],membership_expiry=u['membership_expiry'])
    c.close(); return jsonify(out)

# ---------------- admin: orders, commissions, wallet adjustments, audit log ----------------
@app.get('/api/admin/orders')
def admin_orders():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    return jsonify(ok=True,orders=rows('SELECT o.*,u.full_name,u.username,u.phone,u.telegram_chat_id FROM orders o JOIN users u ON u.id=o.user_id ORDER BY o.id DESC LIMIT 200'))

@app.post('/api/admin/order-status')
def admin_order_status():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    d=request.get_json(force=True,silent=True) or {}; status=str(d.get('status','')).upper()
    try: oid=int(d.get('order_id'))
    except (TypeError,ValueError): return jsonify(ok=False,error='Invalid order'),400
    if status not in ('APPROVED','REJECTED'): return jsonify(ok=False,error='Invalid status'),400
    c=db(); o=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
    if not o: c.close(); return jsonify(ok=False,error='Order not found'),404
    if o['status']!='PENDING': c.close(); return jsonify(ok=False,error='This order was already processed'),400
    if status=='APPROVED':
        exp=approve_order(c,oid); audit(c,'ORDER_APPROVED','order #'+str(oid)+' '+o['plan']); c.commit(); c.close()
        tg_user(o['user_id'],'GOLD MASTERS: payment approved. Your '+o['plan']+' membership is active ('+('lifetime' if exp=='LIFETIME' else 'until '+str(exp)[:10])+').')
        return jsonify(ok=True,status='APPROVED',membership_expiry=exp)
    reject_order(c,oid); audit(c,'ORDER_REJECTED','order #'+str(oid)+' '+o['plan']); c.commit(); c.close()
    tg_user(o['user_id'],'GOLD MASTERS: your order #'+str(oid)+' was rejected. Any wallet amount was returned. Contact support if you think this is a mistake.')
    return jsonify(ok=True,status='REJECTED')

@app.get('/api/admin/commissions')
def admin_commissions():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    cms=rows('SELECT m.*,r.full_name AS referrer,r.username AS referrer_user,b.full_name AS buyer FROM commissions m JOIN users r ON r.id=m.referrer_id JOIN users b ON b.id=m.referred_id ORDER BY m.id DESC LIMIT 200')
    partners=rows("SELECT u.id,u.full_name,u.username,u.telegram_chat_id,u.membership,u.membership_expiry,u.referral_code,(SELECT COUNT(*) FROM users x WHERE x.referred_by=u.id) AS referred FROM users u WHERE u.status='APPROVED' AND u.membership IN ('PLATINUM','DIAMOND','YEARLY','LIFETIME') ORDER BY u.id DESC LIMIT 200")
    return jsonify(ok=True,commissions=cms,partners=partners)

@app.post('/api/admin/commission-status')
def admin_commission_status():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    d=request.get_json(force=True,silent=True) or {}; status=str(d.get('status','')).upper()
    try: cid=int(d.get('id'))
    except (TypeError,ValueError): return jsonify(ok=False,error='Invalid commission'),400
    if status not in ('APPROVED','REJECTED'): return jsonify(ok=False,error='Invalid status'),400
    c=db()
    if status=='APPROVED':
        cm=credit_commission(c,cid)
        if not cm: c.close(); return jsonify(ok=False,error='Commission not found or already processed'),400
        audit(c,'COMMISSION_CREDITED','commission #'+str(cid)+' $'+str(cm['amount'])); c.commit(); c.close()
        tg_user(cm['referrer_id'],'GOLD MASTERS: your referral commission of $'+str(cm['amount'])+' was added to your wallet.')
    else:
        got=c.execute("UPDATE commissions SET status='REJECTED',decided_at=? WHERE id=? AND status='PENDING'",(now().isoformat(),cid))
        if got.rowcount!=1: c.close(); return jsonify(ok=False,error='Commission not found or already processed'),400
        audit(c,'COMMISSION_REJECTED','commission #'+str(cid)); c.commit(); c.close()
    return jsonify(ok=True,status=status)

@app.post('/api/admin/wallet-adjust')
def admin_wallet_adjust():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    d=request.get_json(force=True,silent=True) or {}
    u=find_user_by_ident(str(d.get('user','')))
    note=str(d.get('note','')).strip()
    try: amt=round(float(d.get('amount')),2)
    except (TypeError,ValueError): return jsonify(ok=False,error='Enter an amount'),400
    if not u: return jsonify(ok=False,error='User not found'),404
    if not note: return jsonify(ok=False,error='A note (reason) is required'),400
    if amt==0 or abs(amt)>100000: return jsonify(ok=False,error='Invalid amount'),400
    c=db()
    got=c.execute('UPDATE users SET wallet_balance=COALESCE(wallet_balance,0)+? WHERE id=? AND COALESCE(wallet_balance,0)+?>=0',(amt,u['id'],amt))
    if got.rowcount!=1: c.close(); return jsonify(ok=False,error='Balance cannot go below zero'),400
    c.execute('UPDATE users SET commission_credit=LEAST(COALESCE(commission_credit,0),wallet_balance) WHERE id=?',(u['id'],))
    ledger(c,u['id'],'ADJUSTMENT',amt,'admin',note); audit(c,'WALLET_ADJUST','user '+u['username']+' '+str(amt)+' | '+note); c.commit(); c.close()
    return jsonify(ok=True)

@app.get('/api/admin/audit')
def admin_audit_log():
    if not admin_ok(): return jsonify(ok=False,error='Unauthorized'),401
    return jsonify(ok=True,log=rows('SELECT action,detail,created_at FROM admin_audit ORDER BY id DESC LIMIT 100'))

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
