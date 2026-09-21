from flask import Flask, render_template, request, jsonify, session, redirect
import sqlite3, hashlib, random, secrets, uuid, os, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler
from werkzeug.utils import secure_filename
try:
    import psycopg2
except:
    psycopg2 = None

app = Flask(__name__)
app.secret_key = 'global-lotery-clave-fija-2024-no-cambiar-nunca'
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

MI_CUENTA_BCP = "19106864219053"
TZ = ZoneInfo("America/Lima")
DATABASE_URL = os.environ.get('DATABASE_URL')

def is_postgres():
    return DATABASE_URL is not None and psycopg2 is not None

def q(query):
    return query.replace('?', '%s') if is_postgres() else query

def db():
    if is_postgres():
        return psycopg2.connect(DATABASE_URL)
    else:
        return sqlite3.connect('animalitos.db')

def get_proximo_cierre_global():
    ahora = datetime.now(TZ)
    for h in [10,12,14,16,18,20]:
        c = ahora.replace(hour=h, minute=0, second=0, microsecond=0)
        if c > ahora:
            return c
    man = ahora + timedelta(days=1)
    return man.replace(hour=10, minute=0, second=0, microsecond=0)

def init_db():
    con=db(); c=con.cursor()
    c.execute(q("CREATE TABLE IF NOT EXISTS usuarios (id SERIAL PRIMARY KEY, email TEXT, password TEXT, saldo FLOAT DEFAULT 0)" if is_postgres() else "CREATE TABLE IF NOT EXISTS usuarios (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, password TEXT, saldo FLOAT DEFAULT 0)"))
    c.execute(q("CREATE TABLE IF NOT EXISTS animales (id SERIAL PRIMARY KEY, numero INTEGER, nombre TEXT)" if is_postgres() else "CREATE TABLE IF NOT EXISTS animales (id INTEGER PRIMARY KEY AUTOINCREMENT, numero INTEGER, nombre TEXT)"))
    c.execute(q("CREATE TABLE IF NOT EXISTS sorteos (id SERIAL PRIMARY KEY, fecha_hora_cierre TEXT, estado TEXT, tiempo_min INTEGER)" if is_postgres() else "CREATE TABLE IF NOT EXISTS sorteos (id INTEGER PRIMARY KEY AUTOINCREMENT, fecha_hora_cierre TEXT, estado TEXT, tiempo_min INTEGER)"))
    c.execute(q("CREATE TABLE IF NOT EXISTS apuestas (id SERIAL PRIMARY KEY, user_id INTEGER, sorteo_id INTEGER, animal_id INTEGER, monto FLOAT, fecha TEXT)" if is_postgres() else "CREATE TABLE IF NOT EXISTS apuestas (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, sorteo_id INTEGER, animal_id INTEGER, monto FLOAT, fecha TEXT)"))
    c.execute(q("CREATE TABLE IF NOT EXISTS recargas_bcp (id SERIAL PRIMARY KEY, user_id INTEGER, monto FLOAT, operacion TEXT, estado TEXT DEFAULT 'pendiente', fecha TEXT, voucher TEXT, nombre TEXT)" if is_postgres() else "CREATE TABLE IF NOT EXISTS recargas_bcp (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, monto FLOAT, operacion TEXT, estado TEXT DEFAULT 'pendiente', fecha TEXT, voucher TEXT, nombre TEXT)"))
    c.execute(q("CREATE TABLE IF NOT EXISTS config (k TEXT PRIMARY KEY, v TEXT)" if is_postgres() else "CREATE TABLE IF NOT EXISTS config (k TEXT PRIMARY KEY, v TEXT)"))
    c.execute("SELECT COUNT(*) FROM animales")
    if c.fetchone()[0]==0:
        noms=["Perro","Gato","Ratón","Conejo","Zorro","Tigre","León","Elefante","Mono","Gallina","Gallo","Cerdo","Vaca","Caballo","Alpaca"]
        for i,n in enumerate(noms):
            try: c.execute(q("INSERT INTO animales VALUES (?,?)"),(i+1,n))
            except: pass
    c.execute(q("SELECT id FROM sorteos WHERE estado='ABIERTO' LIMIT 1"))
    if not c.fetchone():
        proximo = get_proximo_cierre_global()
        c.execute(q("INSERT INTO sorteos (fecha_hora_cierre, estado, tiempo_min) VALUES (?, 'ABIERTO', 60)"),(proximo.isoformat(),))
    c.execute(q("INSERT INTO config (k,v) VALUES ('pausado','0') ON CONFLICT (k) DO NOTHING") if is_postgres() else "INSERT OR IGNORE INTO config (k,v) VALUES ('pausado','0')")
    # --- FIX para recargas_bcp sin columna nombre ---
    try:
        if is_postgres():
            c.execute("ALTER TABLE recargas_bcp ADD COLUMN IF NOT EXISTS nombre TEXT")
            c.execute("ALTER TABLE recargas_bcp ADD COLUMN IF NOT EXISTS voucher TEXT")
            c.execute("ALTER TABLE recargas_bcp ADD COLUMN IF NOT EXISTS fecha TEXT")
            c.execute("ALTER TABLE recargas_bcp ADD COLUMN IF NOT EXISTS estado TEXT DEFAULT 'pendiente'")
        else:
            c.execute("ALTER TABLE recargas_bcp ADD COLUMN nombre TEXT")
    except:
        pass
    con.commit(); con.close()

init_db()
def get_config():
    try:
        con=db(); c=con.cursor()
        c.execute(q("SELECT v FROM config WHERE k='pausado'")); r=c.fetchone()
        pausado = r[0]=='1' if r else False
        con.close(); return pausado, 60
    except: return False, 60

def set_config(k,v):
    con=db(); c=con.cursor()
    c.execute(q("INSERT INTO config (k,v) VALUES (?,?) ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v") if is_postgres() else "INSERT OR REPLACE INTO config (k,v) VALUES (?,?)",(k,str(v)))
    con.commit(); con.close()

def sortear():
    pausado,_ = get_config()
    if pausado: return
    con = db(); c = con.cursor()
    c.execute(q("SELECT id FROM sorteos WHERE estado='ABIERTO' AND fecha_hora_cierre <=?"), (datetime.now().isoformat(),))
    row = c.fetchone()
    if not row: con.close(); return
    sid=row[0]
    c.execute(q("UPDATE sorteos SET estado='CERRADO' WHERE id=?"), (sid,)); con.commit()
    c.execute(q("SELECT COUNT(*), COALESCE(SUM(monto),0) FROM apuestas WHERE sorteo_id=?"), (sid,))
    tot, recaud = c.fetchone(); recaud=recaud or 0; fondo=int(recaud*0.75); margen=int(recaud*0.25)
    seed_raw=f"{sid}-{datetime.now().isoformat()}-{secrets.token_hex(16)}"; h=hashlib.sha256(seed_raw.encode()).hexdigest()
    random.seed(h); ganador=random.randint(1,25)
    if tot and tot>=1:
        c.execute(q("UPDATE sorteos SET animal_ganador=?, seed=?, hash_verificacion=?, recaudacion=?, fondo_premios=?, margen_plataforma=?, estado='RESULTADO_GENERADO' WHERE id=?"), (ganador, seed_raw, h, recaud, fondo, margen, sid))
        c.execute(q("SELECT usuario_id, SUM(monto) FROM apuestas WHERE sorteo_id=? AND animal_id=? GROUP BY usuario_id"), (sid, ganador))
        ganadores=c.fetchall()
        if ganadores:
            total_gan=sum([g[1] for g in ganadores]) or 1
            for uid, apostado in ganadores:
                premio=int(fondo*(apostado/total_gan)); c.execute(q("UPDATE usuarios SET saldo=saldo+? WHERE id=?"), (premio, uid))
            c.execute(q("UPDATE sorteos SET estado='PAGADO' WHERE id=?"), (sid,))
        else: c.execute(q("UPDATE sorteos SET animal_ganador=?, recaudacion=?, fondo_premios=?, margen_plataforma=?, jackpot=?, estado='FINALIZADO' WHERE id=?"), (ganador, recaud,fondo,margen,fondo,sid))
    else:
        c.execute(q("UPDATE sorteos SET animal_ganador=?, seed=?, hash_verificacion=?, recaudacion=?, fondo_premios=?, margen_plataforma=?, jackpot=?, estado='FINALIZADO' WHERE id=?"), (ganador, seed_raw, h, recaud,fondo,margen,fondo,sid))
    con.commit(); proximo=get_proximo_cierre_global()
    c.execute(q("INSERT INTO sorteos (fecha_hora_cierre, estado, tiempo_min) VALUES (?, 'ABIERTO',60)"), (proximo.isoformat(),))
    con.commit(); con.close()

scheduler=BackgroundScheduler()
scheduler.add_job(sortear,'interval', seconds=60)
scheduler.start()

@app.route('/login')
def login_page(): return render_template('login.html')

@app.route('/api/register', methods=['POST'])
def api_register():
    try:
        d=request.json
        email=d['email'].strip().lower()
        pw=hash_pass(d['password'])
        tel=d.get('telefono','')
        con=db(); c=con.cursor()
        c.execute(q("SELECT id FROM usuarios WHERE email=?"), (email,))
        existe = c.fetchone()
        if existe:
            session['user']=existe[0]
            session['email']=email
            con.close()
            return jsonify({"ok":True})
        if is_postgres():
            c.execute(q("INSERT INTO usuarios (email,password,telefono,saldo,fecha_registro) VALUES (?,?,?,?,?) RETURNING id"), (email,pw,tel,0,datetime.now().isoformat()))
            uid=c.fetchone()[0]
        else:
            c.execute(q("INSERT INTO usuarios (email,password,telefono,saldo,fecha_registro) VALUES (?,?,?,?,?)"), (email,pw,tel,0,datetime.now().isoformat()))
            uid=c.lastrowid
        con.commit(); con.close()
        session['user']=uid
        session['email']=email
        return jsonify({"ok":True})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"ok":False,"msg":str(e)})

@app.route('/api/login', methods=['POST'])
def api_login():
    d=request.json; email=d['email'].strip().lower(); pw=hash_pass(d['password'])
    con=db(); c=con.cursor()
    c.execute(q("SELECT id,email,saldo FROM usuarios WHERE email=? AND password=?"), (email,pw))
    row=c.fetchone(); con.close()
    if row:
        session['user']=row[0]; session['email']=row[1]
        return jsonify({"ok":True, "saldo": row[2]})
    return jsonify({"ok":False,"msg":"Credenciales incorrectas"})

@app.route('/logout')
def logout(): session.clear(); return redirect('/login')

@app.route('/')
def player():
    try:
        if 'user' not in session: return redirect('/login')
        con=db(); c=con.cursor()
        c.execute(q("SELECT * FROM sorteos WHERE estado='ABIERTO' ORDER BY id DESC LIMIT 1")); s=c.fetchone()
        if not s:
            proximo = get_proximo_cierre_global()
            c.execute(q("INSERT INTO sorteos (fecha_hora_cierre, estado) VALUES (?, 'ABIERTO')"),(proximo.isoformat(),)); con.commit()
            c.execute(q("SELECT * FROM sorteos WHERE estado='ABIERTO' ORDER BY id DESC LIMIT 1")); s=c.fetchone()
        c.execute(q("SELECT saldo,email FROM usuarios WHERE id=?"), (session['user'],)); u=c.fetchone()
        c.execute(q("SELECT * FROM animales")); anims=c.fetchall()
        con.close()
        return render_template('player.html', sorteo=s, animales=anims, saldo=u[0] if u else 0, email=u[1] if u else '', bcp_cuenta=MI_CUENTA_BCP)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"<h1>FALLO EN: {e}</h1><pre>{traceback.format_exc()}</pre><hr><h3>sorteo={s if 's' in locals() else 'no cargó'}</h3>"

def apostar_multiple():
    if 'user' not in session: return jsonify({"ok":False,"msg":"No logueado"})
    data=request.json['apuestas']; uid=session['user']
    con=db(); c=con.cursor(); c.execute(q("SELECT saldo FROM usuarios WHERE id=?"), (uid,)); saldo_row=c.fetchone()
    saldo = saldo_row[0] if saldo_row else 0
    total=sum([int(v) for v in data.values()])
    if saldo < total: con.close(); return jsonify({"ok":False,"msg":f"Saldo insuficiente S/{saldo}"})
    c.execute(q("SELECT id FROM sorteos WHERE estado='ABIERTO' ORDER BY id DESC LIMIT 1")); sid=c.fetchone()[0]
    for animal_id, monto in data.items():
        c.execute(q("INSERT INTO apuestas (id,sorteo_id,usuario_id,animal_id,monto,fecha) VALUES (?,?,?,?,?,?)"), (str(uuid.uuid4()), sid, uid, int(animal_id), int(monto), datetime.now().isoformat()))
    c.execute(q("UPDATE usuarios SET saldo=saldo-? WHERE id=?"), (total, uid))
    con.commit(); con.close()
    return jsonify({"ok":True})

@app.route('/api/recarga-bcp', methods=['POST'])
def recarga_bcp():
    if 'user' not in session and 'uid' not in session:
        return jsonify({"ok":False, "msg":"No logueado"})
    try:
        user_id = session.get('user') or session.get('uid')
        monto = request.form.get('monto') or '0'
        operacion = request.form.get('operacion','').strip()
        nombre = request.form.get('nombre','').strip() or request.form.get('cardNombre','').strip()
        if not operacion:
            return jsonify({"ok":False, "msg":"Falta N° Operación"})
        voucher_path = ""
        if 'voucher' in request.files:
            f = request.files['voucher']
            if f and f.filename:
                os.makedirs('static/vouchers', exist_ok=True)
                fname = f"{int(time.time())}_{user_id}_{secure_filename(f.filename)}"
                f.save(os.path.join('static/vouchers', fname))
                voucher_path = f"static/vouchers/{fname}"
        con=db(); c=con.cursor()
        c.execute(q("INSERT INTO recargas_bcp (user_id, monto, operacion, estado, fecha, voucher, nombre) VALUES (?,?,?,?,?,?,?)"),
                  (user_id, float(monto), operacion, 'pendiente', datetime.now().strftime("%Y-%m-%d %H:%M:%S"), voucher_path, nombre))
        con.commit(); con.close()
        return jsonify({"ok":True})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"ok":False, "msg":str(e)})

@app.route('/api/admin/aprobar-recarga', methods=['POST'])
def aprobar_recarga():
    if not session.get('admin'): return jsonify({"ok":False})
    d=request.json; rid=d['id']
    con=db(); c=con.cursor()
    try:
        c.execute(q("SELECT user_id, monto FROM recargas_bcp WHERE id=?"), (rid,))
        row=c.fetchone()
        tabla = 'recargas_bcp'
        if not row:
            c.execute(q("SELECT user_id, monto FROM recargas WHERE id=?"), (rid,))
            row=c.fetchone()
            tabla = 'recargas'
        if row:
            c.execute(q("UPDATE usuarios SET saldo=saldo+? WHERE id=?"), (row[1], row[0]))
            c.execute(q(f"UPDATE {tabla} SET estado='aprobado' WHERE id=?"), (rid,))
            try:
                otra = 'recargas' if tabla=='recargas_bcp' else 'recargas_bcp'
                c.execute(q(f"UPDATE {otra} SET estado='aprobado' WHERE id=?"), (rid,))
            except: pass
            con.commit()
    except Exception as e:
        print(e)
    con.close()
    return jsonify({"ok":True})

@app.route('/api/admin/rechazar-recarga', methods=['POST'])
def rechazar_recarga():
    if not session.get('admin'): return jsonify({"ok":False})
    rid=request.json.get('id')
    con=db(); c=con.cursor()
    try: c.execute(q("UPDATE recargas_bcp SET estado='rechazado' WHERE id=?"), (rid,))
    except: pass
    try: c.execute(q("UPDATE recargas SET estado='rechazado' WHERE id=?"), (rid,))
    except: pass
    con.commit(); con.close()
    return jsonify({"ok":True})

@app.route("/api/solicitar-retiro", methods=["POST"])
def solicitar_retiro():
    if 'user' not in session: return jsonify({"ok":False,"msg":"No logueado"}),401
    data=request.get_json(); monto=int(float(data.get("monto",0))); yape=data.get("banco_info","") or data.get("yape","")
    con=db(); c=con.cursor(); c.execute(q("SELECT saldo FROM usuarios WHERE id=?"), (session['user'],)); u=c.fetchone()
    if not u or u[0] < monto: con.close(); return jsonify({"ok":False,"msg":f"Saldo insuficiente S/{u[0] if u else 0}"})
    c.execute(q("UPDATE usuarios SET saldo=saldo-? WHERE id=?"), (monto, session['user']))
    c.execute(q("INSERT INTO retiros (user_id, monto, banco_info, estado, fecha) VALUES (?,?,?,?,?)"), (session['user'], monto, yape, "pendiente", datetime.now().isoformat()))
    con.commit(); con.close()
    return jsonify({"ok":True})

@app.route('/api/saldo')
def api_saldo():
    if 'user' not in session: return jsonify({"saldo":0})
    con=db(); c=con.cursor(); c.execute(q("SELECT saldo FROM usuarios WHERE id=?"),(session['user'],)); row=c.fetchone(); con.close()
    return jsonify({"saldo":row[0] if row else 0})

@app.route('/api/historial-sorteos')
def api_historial_sorteos():
    con=db(); c=con.cursor()
    c.execute(q("SELECT animal_ganador FROM sorteos WHERE estado IN ('PAGADO','FINALIZADO') AND animal_ganador IS NOT NULL ORDER BY id DESC LIMIT 24"))
    rows=c.fetchall(); con.close()
    return jsonify([{"animal_ganador":r[0]} for r in rows])

@app.route('/api/ultimo-resultado')
def api_ultimo_resultado():
    con=db(); c=con.cursor()
    c.execute(q("SELECT animal_ganador FROM sorteos WHERE estado IN ('PAGADO','FINALIZADO') ORDER BY id DESC LIMIT 1"))
    row=c.fetchone(); con.close()
    return jsonify({"ganador": row[0] if row else None})

@app.route('/api/mis-apuestas-actuales')
def api_mis_apuestas_actuales():
    if 'user' not in session: return jsonify({"apuestas":{}})
    con=db(); c=con.cursor()
    c.execute(q("SELECT id FROM sorteos WHERE estado='ABIERTO' ORDER BY id DESC LIMIT 1"))
    sid=c.fetchone()
    if not sid: con.close(); return jsonify({"apuestas":{}})
    c.execute(q("SELECT animal_id, SUM(monto) FROM apuestas WHERE sorteo_id=? AND usuario_id=? GROUP BY animal_id"), (sid[0], session['user']))
    rows=c.fetchall(); con.close()
    return jsonify({"apuestas":{str(r[0]): int(float(r[1])) for r in rows}})

@app.route('/api/mis-retiros')
def api_mis_retiros():
    if 'user' not in session: return jsonify([])
    con=db(); c=con.cursor()
    c.execute(q("SELECT monto, banco_info, estado, fecha FROM retiros WHERE user_id=? ORDER BY id DESC LIMIT 10"), (session['user'],))
    rows=c.fetchall(); con.close()
    return jsonify([{"monto":r[0],"banco":r[1],"estado":r[2],"fecha":(r[3][:16] if r[3] else "")} for r in rows])

@app.route('/api/mis-apuestas')
def api_mis_apuestas():
    if 'user' not in session: return jsonify([])
    con=db(); c=con.cursor()
    c.execute(q("SELECT s.id, s.fecha_hora_cierre, an.nombre, a.monto, s.animal_ganador, s.estado FROM apuestas a JOIN sorteos s ON s.id=a.sorteo_id JOIN animales an ON an.id=a.animal_id WHERE a.usuario_id=? ORDER BY a.fecha DESC LIMIT 50"), (session['user'],))
    rows=c.fetchall(); con.close()
    return jsonify([{"sorteo":r[0],"fecha":r[1][:16] if r[1] else "","mi_animal":r[2],"monto":r[3],"ganador":r[4],"estado":r[5]} for r in rows])

ADMIN_USER="Globallotery"; ADMIN_PASS_HASH=hash_pass("Diosmeama.1")
@app.route('/admin/login')
def admin_login_page(): return render_template('admin_login.html')
@app.route('/api/admin/login', methods=['POST'])
def api_admin_login():
    d=request.json
    if d['user']==ADMIN_USER and hash_pass(d['pass'])==ADMIN_PASS_HASH:
        session['admin']=True; return jsonify({"ok":True})
    return jsonify({"ok":False})
@app.route('/admin/logout')
def admin_logout(): session.pop('admin',None); return redirect('/admin/login')

@app.route('/admin')
def admin_panel():
    try:
        if not session.get('admin'): return redirect('/admin/login')
        pausado, _ = get_config()
        con=db(); c=con.cursor()
        c.execute(q("SELECT * FROM sorteos WHERE estado='ABIERTO' ORDER BY id DESC LIMIT 1")); sorteo_actual=c.fetchone()
        sid = sorteo_actual[0] if sorteo_actual else 0
        c.execute(q("SELECT COALESCE(SUM(monto),0) FROM apuestas WHERE sorteo_id=?"), (sid,))
        recaudado = int(float(c.fetchone()[0] or 0))
        try:
            c.execute(q("SELECT r.id, r.user_id, r.monto, r.operacion, r.estado, r.fecha, r.voucher, u.email FROM recargas_bcp r LEFT JOIN usuarios u ON u.id=r.user_id WHERE r.estado='pendiente' ORDER BY r.id DESC"))
            recargas=c.fetchall()
        except:
            c.execute(q("SELECT r.id, r.user_id, r.monto, r.operacion, r.estado, r.fecha, r.voucher, u.email FROM recargas r LEFT JOIN usuarios u ON u.id=r.user_id WHERE r.estado='pendiente' ORDER BY r.id DESC"))
            recargas=c.fetchall()
        c.execute(q("SELECT COUNT(*) FROM usuarios")); num_usuarios=c.fetchone()[0] or 0
        con.close()
        return render_template('admin.html', sorteo_actual=sorteo_actual, recargas_pendientes=recargas, recargas=recargas, bcp_cuentas=[], recaudado=recaudado, num_usuarios=num_usuarios, pausado=pausado, bcp_cuenta=None)
    except Exception as e:
        import traceback; traceback.print_exc()
        return f"<h1>Error en admin: {e}</h1><pre>{traceback.format_exc()}</pre>"

@app.route('/admin/usuarios')
def admin_usuarios():
    if not session.get('admin'): return redirect('/admin/login')
    con=db(); c=con.cursor()
    try:
        c.execute(q("SELECT id, email, telefono, saldo, fecha_registro FROM usuarios ORDER BY id DESC"))
        usuarios = c.fetchall()
    except:
        usuarios = []
    con.close()
    return render_template('admin_usuarios.html', usuarios=usuarios)

@app.route('/api/admin/control', methods=['POST'])
def api_admin_control():
    if not session.get('admin'): return jsonify({"ok":False})
    d=request.json; accion=d.get('accion')
    if accion=='pausar': set_config('pausado','1')
    if accion=='activar': set_config('pausado','0')
    return jsonify({"ok":True})

if __name__=='__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',5000)))
