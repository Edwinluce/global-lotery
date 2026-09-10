import os, sqlite3, hashlib, secrets, random
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "global-lotery-2026-secret-key")
DB = "animalitos.db"
UPLOAD_FOLDER = "static/vouchers"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def db():
    con = sqlite3.connect(DB, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

def ahora_chile():
    return datetime.now(timezone.utc) - timedelta(hours=3)

def get_config():
    con = db(); c = con.cursor()
    c.execute("SELECT pausado, tiempo_min FROM config WHERE id=1")
    r = c.fetchone(); con.close()
    if r: return r[0], r[1]
    return 0, 60

def init_db():
    con = db(); c = con.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE, password TEXT, saldo REAL DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS sorteos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha_hora_cierre TEXT, estado TEXT,
        animal_ganador INTEGER, seed TEXT, hash_verificacion TEXT,
        recaudacion INTEGER DEFAULT 0, fondo_premios INTEGER DEFAULT 0,
        margen_plataforma INTEGER DEFAULT 0, jackpot INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS apuestas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario_id INTEGER, sorteo_id INTEGER,
        animal_id INTEGER, monto INTEGER,
        fecha TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS recargas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario_id INTEGER, monto INTEGER,
        operacion TEXT, nombre TEXT, voucher TEXT,
        estado TEXT, fecha TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS retiros (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario_id INTEGER, monto INTEGER,
        banco_info TEXT, estado TEXT, fecha TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS config (
        id INTEGER PRIMARY KEY, pausado INTEGER DEFAULT 0, tiempo_min INTEGER DEFAULT 60)""")
    c.execute("INSERT OR IGNORE INTO config (id, pausado, tiempo_min) VALUES (1,0,60)")
    c.execute("SELECT id FROM sorteos WHERE estado='ABIERTO'");
    if not c.fetchone():
        proximo = ahora_chile() + timedelta(minutes=60)
        c.execute("INSERT INTO sorteos (fecha_hora_cierre, estado) VALUES (?, 'ABIERTO')", (proximo.isoformat(),))
    con.commit(); con.close()

init_db()

@app.route('/')
def index():
    if 'user' in session: return redirect('/player')
    return render_template('login.html')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='GET': return render_template('login.html')
    u=request.form.get('username','').strip(); p=request.form.get('password','').strip()
    if u=="Globallotery" and p=="Diosmeama.1":
        session['admin']=True; return redirect('/admin')
    con=db(); c=con.cursor()
    c.execute("SELECT id,password FROM usuarios WHERE username=?", (u,)); r=c.fetchone(); con.close()
    if r and r[1]==p:
        session['user']=r[0]; return redirect('/player')
    return "Usuario o clave incorrecta"

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method=='GET': return render_template('register.html')
    u=request.form.get('username','').strip(); p=request.form.get('password','').strip()
    if not u or not p: return "Falta usuario"
    con=db(); c=con.cursor()
    try:
        c.execute("INSERT INTO usuarios (username,password,saldo) VALUES (?,?,0)", (u,p)); con.commit()
        uid=c.lastrowid
    except: con.close(); return "Usuario ya existe"
    con.close(); session['user']=uid; return redirect('/player')

@app.route('/player')
def player():
    if 'user' not in session: return redirect('/login')
    con=db(); c=con.cursor()
    c.execute("SELECT id, fecha_hora_cierre FROM sorteos WHERE estado='ABIERTO' ORDER BY id DESC LIMIT 1")
    s=c.fetchone()
    c.execute("SELECT saldo FROM usuarios WHERE id=?", (session['user'],)); saldo=c.fetchone()[0]
    con.close()
    return render_template('player.html', sorteo=s, saldo=saldo)

@app.route('/logout')
def logout():
    session.clear(); return redirect('/login')

@app.route('/api/saldo')
def api_saldo():
    if 'user' not in session: return jsonify({"saldo":0})
    con=db(); c=con.cursor()
    c.execute("SELECT saldo FROM usuarios WHERE id=?", (session['user'],)); s=c.fetchone()[0]; con.close()
    return jsonify({"saldo":s})

@app.route('/api/apostar-multiple', methods=['POST'])
def api_apostar():
    if 'user' not in session: return jsonify({"ok":False,"msg":"No login"})
    data=request.get_json(); apuestas=data.get('apuestas',{})
    if not apuestas: return jsonify({"ok":False,"msg":"Elige animal"})
    con=db(); c=con.cursor()
    c.execute("SELECT id FROM sorteos WHERE estado='ABIERTO' ORDER BY id DESC LIMIT 1"); s=c.fetchone()
    if not s: con.close(); return jsonify({"ok":False,"msg":"No hay sorteo abierto"})
    sid=s[0]
    total=sum(apuestas.values())
    c.execute("SELECT saldo FROM usuarios WHERE id=?", (session['user'],)); saldo=c.fetchone()[0]
    if saldo<total: con.close(); return jsonify({"ok":False,"msg":"Saldo insuficiente"})
    c.execute("UPDATE usuarios SET saldo=saldo-? WHERE id=?", (total, session['user']))
    for aid, monto in apuestas.items():
        c.execute("INSERT INTO apuestas (usuario_id,sorteo_id,animal_id,monto,fecha) VALUES (?,?,?,?,?)",
                  (session['user'], sid, int(aid), int(monto), ahora_chile().isoformat()))
    con.commit(); con.close()
    return jsonify({"ok":True})

@app.route('/api/mis-apuestas-actuales')
def api_mis_apuestas():
    if 'user' not in session: return jsonify({"apuestas":{}})
    con=db(); c=con.cursor()
    c.execute("SELECT id FROM sorteos WHERE estado='ABIERTO' ORDER BY id DESC LIMIT 1"); s=c.fetchone()
    if not s: con.close(); return jsonify({"apuestas":{}})
    c.execute("SELECT animal_id, SUM(monto) FROM apuestas WHERE usuario_id=? AND sorteo_id=? GROUP BY animal_id", (session['user'], s[0]))
    rows=c.fetchall(); con.close()
    return jsonify({"apuestas":{str(r[0]):r[1] for r in rows}})

@app.route('/api/historial-sorteos')
def api_historial():
    con=db(); c=con.cursor()
    c.execute("SELECT id, animal_ganador, fecha_hora_cierre, recaudacion, fondo_premios FROM sorteos WHERE animal_ganador IS NOT NULL ORDER BY id DESC LIMIT 20")
    rows=c.fetchall(); con.close()
    return jsonify([{"id":r[0],"animal_ganador":r[1],"fecha":r[2],"recaudacion":r[3],"fondo":r[4]} for r in rows])

# CONTINUA EN PARTE 2...@app.route('/api/recarga-bcp', methods=['POST'])
def api_recarga():
    if 'user' not in session: return jsonify({"ok":False,"msg":"No login"})
    monto=request.form.get('monto'); operacion=request.form.get('operacion','').strip()
    nombre=request.form.get('nombre','').strip()
    if not monto or not operacion: return jsonify({"ok":False,"msg":"Falta operacion"})
    file=request.files.get('voucher'); fname=""
    if file:
        fname=secure_filename(f"{session['user']}_{int(datetime.now().timestamp())}_{file.filename}")
        file.save(os.path.join(UPLOAD_FOLDER, fname))
    con=db(); c=con.cursor()
    c.execute("INSERT INTO recargas (usuario_id,monto,operacion,nombre,voucher,estado,fecha) VALUES (?,?,?,?,?,?,?)",
              (session['user'], int(monto), operacion, nombre, fname, "PENDIENTE", ahora_chile().isoformat()))
    con.commit(); con.close()
    return jsonify({"ok":True,"msg":"✅ Voucher enviado. Espera validación en Admin"})

@app.route('/api/solicitar-retiro', methods=['POST'])
def api_retiro():
    if 'user' not in session: return jsonify({"ok":False,"msg":"No login"})
    data=request.get_json(); monto=int(data.get('monto',0)); banco=data.get('banco_info','').strip()
    if monto<10: return jsonify({"ok":False,"msg":"Mínimo S/10"})
    con=db(); c=con.cursor()
    c.execute("SELECT saldo FROM usuarios WHERE id=?", (session['user'],)); saldo=c.fetchone()[0]
    if saldo<monto: con.close(); return jsonify({"ok":False,"msg":"Saldo insuficiente"})
    c.execute("UPDATE usuarios SET saldo=saldo-? WHERE id=?", (monto, session['user']))
    c.execute("INSERT INTO retiros (usuario_id,monto,banco_info,estado,fecha) VALUES (?,?,?,?,?)",
              (session['user'], monto, banco, "PENDIENTE", ahora_chile().isoformat()))
    con.commit(); con.close()
    return jsonify({"ok":True})

@app.route('/api/mis-retiros')
def api_mis_retiros():
    if 'user' not in session: return jsonify([])
    con=db(); c=con.cursor()
    c.execute("SELECT monto, estado, fecha FROM retiros WHERE usuario_id=? ORDER BY id DESC LIMIT 10", (session['user'],))
    rows=c.fetchall(); con.close()
    return jsonify([{"monto":r[0],"estado":r[1],"fecha":r[2][:16]} for r in rows])

# ---------- ADMIN ----------
@app.route('/admin')
def admin():
    if not session.get('admin'): return redirect('/login')
    con=db(); c=con.cursor()
    c.execute("SELECT COALESCE(SUM(saldo),0) FROM usuarios"); saldo_total=c.fetchone()[0]
    c.execute("SELECT COALESCE(SUM(monto),0) FROM apuestas"); apostado=c.fetchone()[0]
    c.execute("SELECT COALESCE(SUM(margen_plataforma),0) FROM sorteos"); margen=c.fetchone()[0]
    c.execute("SELECT * FROM recargas WHERE estado='PENDIENTE' ORDER BY id DESC")
    recargas=c.fetchall()
    c.execute("SELECT r.*, u.username FROM retiros r JOIN usuarios u ON r.usuario_id=u.id WHERE r.estado='PENDIENTE' ORDER BY r.id DESC")
    retiros=c.fetchall()
    c.execute("SELECT id, fecha_hora_cierre, estado, animal_ganador, recaudacion, fondo_premios FROM sorteos ORDER BY id DESC LIMIT 20")
    sorteos=c.fetchall()
    pausado, tiempo_min=get_config()
    con.close()
    return render_template('admin.html', saldo_total=saldo_total, apostado=apostado, margen=margen, recargas=recargas, retiros=retiros, sorteos=sorteos, pausado=pausado, tiempo_min=tiempo_min)

@app.route('/api/admin/aprobar-recarga/<int:rid>', methods=['POST'])
def aprobar_recarga(rid):
    if not session.get('admin'): return jsonify({"ok":False})
    con=db(); c=con.cursor()
    c.execute("SELECT usuario_id, monto, estado FROM recargas WHERE id=?", (rid,)); r=c.fetchone()
    if not r or r[2]!="PENDIENTE": con.close(); return jsonify({"ok":False})
    c.execute("UPDATE usuarios SET saldo=saldo+? WHERE id=?", (r[1], r[0]))
    c.execute("UPDATE recargas SET estado='APROBADO' WHERE id=?", (rid,))
    con.commit(); con.close()
    return jsonify({"ok":True})

@app.route('/api/admin/rechazar-recarga/<int:rid>', methods=['POST'])
def rechazar_recarga(rid):
    if not session.get('admin'): return jsonify({"ok":False})
    con=db(); c=con.cursor()
    c.execute("UPDATE recargas SET estado='RECHAZADO' WHERE id=?", (rid,)); con.commit(); con.close()
    return jsonify({"ok":True})

@app.route('/api/admin/aprobar-retiro/<int:rid>', methods=['POST'])
def aprobar_retiro(rid):
    if not session.get('admin'): return jsonify({"ok":False})
    con=db(); c=con.cursor()
    c.execute("UPDATE retiros SET estado='PAGADO' WHERE id=?", (rid,)); con.commit(); con.close()
    return jsonify({"ok":True})

@app.route('/api/admin/rechazar-retiro/<int:rid>', methods=['POST'])
def rechazar_retiro(rid):
    if not session.get('admin'): return jsonify({"ok":False})
    con=db(); c=con.cursor()
    c.execute("SELECT usuario_id, monto FROM retiros WHERE id=?", (rid,)); r=c.fetchone()
    if r:
        c.execute("UPDATE usuarios SET saldo=saldo+? WHERE id=?", (r[1], r[0]))
        c.execute("UPDATE retiros SET estado='RECHAZADO' WHERE id=?", (rid,))
        con.commit()
    con.close()
    return jsonify({"ok":True})

@app.route('/api/admin/reiniciar', methods=['POST'])
def admin_reiniciar():
    if not session.get('admin'): return jsonify({"ok":False})
    con=db(); c=con.cursor()
    c.execute("DELETE FROM apuestas"); c.execute("DELETE FROM sorteos")
    c.execute("UPDATE usuarios SET saldo=0")
    c.execute("DELETE FROM recargas"); c.execute("DELETE FROM retiros")
    proximo=ahora_chile()+timedelta(minutes=60)
    c.execute("INSERT INTO sorteos (fecha_hora_cierre, estado) VALUES (?, 'ABIERTO')", (proximo.isoformat(),))
    con.commit(); con.close()
    return jsonify({"ok":True})

@app.route('/api/admin/pausar', methods=['POST'])
def admin_pausar():
    if not session.get('admin'): return jsonify({"ok":False})
    con=db(); c=con.cursor()
    c.execute("UPDATE config SET pausado=1 WHERE id=1"); con.commit(); con.close()
    return jsonify({"ok":True})

@app.route('/api/admin/reanudar', methods=['POST'])
def admin_reanudar():
    if not session.get('admin'): return jsonify({"ok":False})
    con=db(); c=con.cursor()
    c.execute("UPDATE config SET pausado=0 WHERE id=1"); con.commit(); con.close()
    return jsonify({"ok":True})

# ---------- SORTEO ARREGLADO 1-25 SIN +1 ----------
def sortear():
    pausado,_ = get_config()
    if pausado: return
    con=db(); c=con.cursor()
    c.execute("SELECT id FROM sorteos WHERE estado='ABIERTO' AND fecha_hora_cierre <=?", (ahora_chile().isoformat(),))
    row=c.fetchone()
    if not row: con.close(); return
    sid=row[0]
    c.execute("UPDATE sorteos SET estado='CERRADO' WHERE id=?", (sid,)); con.commit()
    c.execute("SELECT COUNT(*), COALESCE(SUM(monto),0) FROM apuestas WHERE sorteo_id=?", (sid,))
    tot, recaud=c.fetchone(); recaud=recaud or 0; fondo=int(recaud*0.75); margen=int(recaud*0.25)
    if tot and tot>=1:
        seed_raw=f"{sid}-{ahora_chile().isoformat()}-{secrets.token_hex(16)}"
        h=hashlib.sha256(seed_raw.encode()).hexdigest()
        random.seed(h)
        ganador=random.randint(1,25)
        c.execute("UPDATE sorteos SET animal_ganador=?, seed=?, hash_verificacion=?, recaudacion=?, fondo_premios=?, margen_plataforma=?, estado='RESULTADO_GENERADO' WHERE id=?",
                  (ganador, seed_raw, h, recaud, fondo, margen, sid))
        c.execute("SELECT usuario_id, SUM(monto) FROM apuestas WHERE sorteo_id=? AND animal_id=? GROUP BY usuario_id", (sid, ganador))
        ganadores=c.fetchall()
        if ganadores:
            total_gan=sum([g[1] for g in ganadores]) or 1
            for uid, apostado in ganadores:
                premio=int(fondo*(apostado/total_gan))
                c.execute("UPDATE usuarios SET saldo=saldo+? WHERE id=?", (premio, uid))
            c.execute("UPDATE sorteos SET estado='PAGADO' WHERE id=?", (sid,))
        else:
            c.execute("UPDATE sorteos SET jackpot=?, estado='FINALIZADO' WHERE id=?", (fondo, sid))
    else:
        c.execute("UPDATE sorteos SET recaudacion=?, fondo_premios=?, margen_plataforma=?, jackpot=?, estado='FINALIZADO' WHERE id=?",
                  (recaud,fondo,margen,fondo,sid))
    con.commit()
    _, tiempo_min=get_config()
    proximo=ahora_chile()+timedelta(minutes=tiempo_min)
    c.execute("INSERT INTO sorteos (fecha_hora_cierre, estado) VALUES (?, 'ABIERTO')", (proximo.isoformat(),))
    con.commit(); con.close()

@app.route('/api/admin/forzar-sorteo', methods=['POST'])
def admin_forzar_sorteo():
    if not session.get('admin'): return jsonify({"ok":False})
    con=db(); c=con.cursor()
    c.execute("SELECT id FROM sorteos WHERE estado='ABIERTO' ORDER BY id DESC LIMIT 1"); s=c.fetchone()
    ganador=random.randint(1,25)
    if s:
        c.execute("SELECT COALESCE(SUM(monto),0) FROM apuestas WHERE sorteo_id=?", (s[0],))
        rec=c.fetchone()[0] or 0
        c.execute("UPDATE sorteos SET animal_ganador=?, recaudacion=?, fondo_premios=?, margen_plataforma=?, estado='PAGADO', fecha_hora_cierre=? WHERE id=?",
                  (ganador, rec, int(rec*0.75), int(rec*0.25), ahora_chile().isoformat(), s[0]))
        c.execute("SELECT usuario_id, SUM(monto) FROM apuestas WHERE sorteo_id=? AND animal_id=? GROUP BY usuario_id", (s[0], ganador))
        rows=c.fetchall()
        if rows:
            total=sum([x[1] for x in rows]) or 1
            for uid, apostado in rows:
                premio=int((rec*0.75)*(apostado/total)) if total>0 else 0
                if premio>0: c.execute("UPDATE usuarios SET saldo=saldo+? WHERE id=?", (premio, uid))
    _, tiempo_min=get_config()
    proximo=ahora_chile()+timedelta(minutes=tiempo_min)
    c.execute("INSERT INTO sorteos (fecha_hora_cierre, estado) VALUES (?, 'ABIERTO')", (proximo.isoformat(),))
    con.commit(); con.close()
    return jsonify({"ok":True})

# Loop de sorteo automatico
import threading, time
def loop_sorteo():
    while True:
        try: sortear()
        except: pass
        time.sleep(10)
threading.Thread(target=loop_sorteo, daemon=True).start()

if __name__=='__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))