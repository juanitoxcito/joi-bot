import os
import json
import time
import re
import threading
import unicodedata
import asyncio
import requests
from datetime import datetime, timedelta
import pytz
import dateparser
import telebot
from groq import Groq
from duckduckgo_search import DDGS
from pydub import AudioSegment
import edge_tts
from flask import Flask

# --- CONFIGURACIÓN ---
TOKEN = "8659197203:AAEh-HZZ30BTM30xieN6yuoJKBDwtdrSyfk"
TOKEN_GROQ = "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"

bot = telebot.TeleBot(TOKEN)
client = Groq(api_key=TOKEN_GROQ)

ZONA_HORARIA = 'America/Caracas'
tz = pytz.timezone(ZONA_HORARIA)
RUTA_CEREBRO = "cerebro_grafo.json"

user_states = {}
busquedas_activas = {}

# --- CORAZÓN ARTIFICIAL (KEEP ALIVE) ---
app = Flask(_name_)
@app.route('/')
def home(): return "Joi está viva y funcionando."
def run_flask():
    from os import environ
    app.run(host='0.0.0.0', port=environ.get('PORT', 5000))
threading.Thread(target=run_flask, daemon=True).start()

# --- MEMORIA (4 ENGRANAJES) ---
def cargar_cerebro():
    if os.path.exists(RUTA_CEREBRO):
        try:
            with open(RUTA_CEREBRO, 'r') as f: return json.load(f)
        except: pass
    return {
        "identidad": {"nombre": "Joi"}, 
        "perfil_usuario": [], "conocimiento": [], "eventos": [], 
        "historial_chat": [], "alarmas": [], "config_finanzas": {}
    }

def guardar_cerebro(datos):
    try:
        with open(RUTA_CEREBRO, 'w') as f: json.dump(datos, f, indent=4)
    except: pass

cerebro = cargar_cerebro()
historial_chat = cerebro.get('historial_chat', [])
alarmas_pendientes = cerebro.get('alarmas', [])
modo_panico_activo = {}
mensaje_a_entregar = {}
MI_CHAT_ID = None

# --- FUNCIONES AUXILIARES ---
def quitar_acentos(texto):
    return unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('ASCII')

def buscar_info(query):
    try:
        with DDGS() as ddgs:
            results = [r for r in ddgs.text(query, max_results=3)]
            if results: return " ".join([r['body'] for r in results])
    except: pass
    return ""

def generar_imagen_url(prompt):
    return f"https://image.pollinations.ai/prompt/{prompt}"

def detectar_fecha_texto(texto):
    settings = {'PREFER_DATES_FROM': 'future', 'TIMEZONE': 'America/Caracas'}
    dt = dateparser.parse(texto, languages=['es'], settings=settings)
    return dt if dt else None

def convertir_a_24h(hora_str):
    hora_str = hora_str.lower().replace(" ", "")
    match = re.match(r'(\d{1,2}):(\d{2})(am|pm)?', hora_str)
    if not match: return None
    h, m = int(match.group(1)), int(match.group(2))
    ampm = match.group(3)
    if ampm == 'pm' and h < 12: h += 12
    elif ampm == 'am' and h == 12: h = 0
    return f"{h:02d}:{m:02d}"

def extraer_hora_y_mensaje(texto):
    hora_encontrada = None
    mensaje_limpio = texto.lower()
    patron_hora = r'\b(\d{1,2}:\d{2}\s*(?:am|pm)?)\b'
    match = re.search(patron_hora, mensaje_limpio)
    if match:
        hora_encontrada = convertir_a_24h(match.group(1))
        mensaje_limpio = re.sub(patron_hora, '', mensaje_limpio, count=1)
    basura = ["alarma", "avisame", "recuerdame", "recordarme", "joi", "a las", "para las", "una", "un", "la", "el"]
    mensaje_norm = quitar_acentos(mensaje_limpio)
    for b in basura: mensaje_norm = re.sub(r'\b' + re.escape(b) + r'\b', '', mensaje_norm, flags=re.IGNORECASE)
    mensaje_final = re.sub(r'\s+', ' ', mensaje_norm).strip()
    if mensaje_final: mensaje_final = mensaje_final[0].upper() + mensaje_final[1:]
    return hora_encontrada, (mensaje_final if mensaje_final else "¡Alarma!")

# --- VOZ ---
def transcribir_voz(archivo_ogg):
    try:
        audio = AudioSegment.from_ogg(archivo_ogg)
        archivo_mp3 = "voz_temp.mp3"
        audio.export(archivo_mp3, format="mp3")
        with open(archivo_mp3, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
              file=(archivo_mp3, audio_file.read()),
              model="whisper-large-v3",
              response_format="json",
              language="es"
            )
        os.remove(archivo_mp3)
        return transcription.text
    except: return None

async def generar_voz_neuronal(texto):
    communicate = edge_tts.Communicate(texto, "es-MX-DaliaNeural")
    await communicate.save("respuesta.mp3")

def generar_voz_respuesta(texto):
    try:
        asyncio.run(generar_voz_neuronal(texto))
        return "respuesta.mp3"
    except: return None

# --- FINANZAS ---
def analizar_brecha_binance(lista_bancos):
    url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
    payload_venta = {"asset": "USDT", "fiat": "VES", "tradeType": "SELL", "page": 1, "rows": 1, "payTypes": lista_bancos}
    payload_compra = {"asset": "USDT", "fiat": "VES", "tradeType": "BUY", "page": 1, "rows": 1, "payTypes": lista_bancos}
    try:
        r_venta = requests.post(url, json=payload_venta, timeout=10).json()
        r_compra = requests.post(url, json=payload_compra, timeout=10).json()
        precio_venta = float(r_venta['data'][0]['adv']['price'])
        precio_compra = float(r_compra['data'][0]['adv']['price'])
        margen = ((precio_venta - precio_compra) / precio_compra) * 100
        return precio_venta, precio_compra, margen
    except:
        return None, None, 0

# --- LÓGICA DE FLUJOS (PANEL DE CONTROL) ---
def flujo_inicio(message):
    cid = message.chat.id
    config_previa = cerebro.get('config_finanzas', {})
    if config_previa.get('bancos'):
        txt = (f"📊 Encontré calibración anterior:\n"
               f"Bancos: {config_previa.get('bancos')}\n"
               f"Porcentaje: {config_previa.get('objetivo')}%\n"
               f"Modo: {config_previa.get('modo')}\n\n"
               f"¿Deseas usar la calibración anterior? (Sí o No)")
        bot.send_message(cid, txt)
        bot.register_next_step_handler(message, flujo_confirmar_uso)
    else:
        bot.send_message(cid, "🚀 Iniciando nueva calibración.\n\n1️⃣ ¿Qué banco(s) uso? (Ej: Venezuela, Banesco)")
        bot.register_next_step_handler(message, flujo_recibir_banco)

def flujo_confirmar_uso(message):
    cid = message.chat.id
    if "si" in message.text.lower():
        config_previa = cerebro.get('config_finanzas', {})
        bot.send_message(cid, f"¿Estás de acuerdo? (Sí para ejecutar, No para reconfigurar)")
        bot.register_next_step_handler(message, flujo_ejecutar_directo)
    else:
        bot.send_message(cid, "👌 Vamos a reconfigurar.\n\n1️⃣ ¿Qué banco(s) uso?")
        bot.register_next_step_handler(message, flujo_recibir_banco)

def flujo_ejecutar_directo(message):
    cid = message.chat.id
    if "si" in message.text.lower():
        config = cerebro.get('config_finanzas', {})
        ejecutar_modo(cid, config.get('modo', 'automatico'), config)
    else:
        bot.send_message(cid, "👌 Vamos a reconfigurar.\n\n1️⃣ ¿Qué banco(s) uso?")
        bot.register_next_step_handler(message, flujo_recibir_banco)

def flujo_recibir_banco(message):
    cid = message.chat.id
    bancos = [b.strip().capitalize() for b in re.split(r',| y ', message.text)]
    user_states[cid] = {'bancos': bancos}
    bot.send_message(cid, f"✅ Bancos guardados: {', '.join(bancos)}.\n\n2️⃣ ¿Qué porcentaje de brecha buscas?")
    bot.register_next_step_handler(message, flujo_recibir_porcentaje)

def flujo_recibir_porcentaje(message):
    cid = message.chat.id
    try:
        porcentaje = float(re.search(r'(\d+(\.\d+)?)', message.text).group(1))
        user_states[cid]['objetivo'] = porcentaje
        bot.send_message(cid, f"✅ Objetivo: {porcentaje}%.\n\n3️⃣ ¿Modo Automático o Manual?")
        bot.register_next_step_handler(message, flujo_recibir_modo)
    except:
        bot.send_message(cid, "❌ Número inválido. Intenta de nuevo.")
        bot.register_next_step_handler(message, flujo_recibir_porcentaje)

def flujo_recibir_modo(message):
    cid = message.chat.id
    resp = message.text.lower()
    if "manual" in resp:
        user_states[cid]['modo'] = "manual"
        bot.send_message(cid, "📝 Modo Manual.\n\n¿Precio de COMPRA?")
        bot.register_next_step_handler(message, flujo_manual_precio_compra)
    else:
        user_states[cid]['modo'] = "automatico"
        cerebro['config_finanzas'] = user_states[cid]
        guardar_cerebro(cerebro)
        ejecutar_modo(cid, "automatico", user_states[cid])

def flujo_manual_precio_compra(message):
    cid = message.chat.id
    try:
        user_states[cid]['p_compra'] = float(re.search(r'(\d+(\.\d+)?)', message.text).group(1))
        bot.send_message(cid, "✅ Guardado.\n\n¿Precio de VENTA?")
        bot.register_next_step_handler(message, flujo_manual_precio_venta)
    except:
        bot.send_message(cid, "❌ Número inválido. Intenta de nuevo.")
        bot.register_next_step_handler(message, flujo_manual_precio_compra)

def flujo_manual_precio_venta(message):
    cid = message.chat.id
    try:
        p_venta = float(re.search(r'(\d+(\.\d+)?)', message.text).group(1))
        p_compra = user_states[cid]['p_compra']
        margen = ((p_venta - p_compra) / p_compra) * 100
        cerebro['config_finanzas'] = user_states[cid]
        guardar_cerebro(cerebro)
        res = (f"🧮 CÁLCULO MANUAL\n\nCompra: {p_compra} Bs\nVenta: {p_venta} Bs\nMargen: {margen:.2f}%\n\n")
        res += "✅ ¡Cumple!" if margen >= user_states[cid]['objetivo'] else "⚠️ No alcanza."
        bot.send_message(cid, res, parse_mode='Markdown')
        if cid in user_states: del user_states[cid]
    except: pass

def ejecutar_modo(cid, modo, config):
    if modo == "automatico":
        p_venta, p_compra, margen = analizar_brecha_binance(config['bancos'])
        msg = f"🕵️‍♀️ MODO AUTOMÁTICO ACTIVADO\nBuscando {config['objetivo']}% en {config['bancos']}...\n\n"
        if p_venta:
            msg += (f"📊 Estado Actual:\nVenta: {p_venta} Bs\nCompra: {p_compra} Bs\nMargen: {margen:.2f}%\n\n"
                    f"⏳ Te avisaré apenas se acerque al {config['objetivo']}%.")
        else:
            msg += "⚠️ No pude leer el mercado ahora, pero sigo intentando."
        bot.send_message(cid, msg, parse_mode='Markdown')
        busquedas_activas[cid] = True
        threading.Thread(target=bucle_busqueda, args=(cid, config), daemon=True).start()

def bucle_busqueda(cid, config):
    while busquedas_activas.get(cid, False):
        p_venta, p_compra, margen = analizar_brecha_binance(config['bancos'])
        if p_venta:
            if margen >= config['objetivo']:
                msg = (f"🚨 ¡BRECHA ENCONTRADA!\n\nMargen: {margen:.2f}%\nVenta: {p_venta} Bs\nCompra: {p_compra} Bs")
                bot.send_message(cid, msg, parse_mode='Markdown')
                time.sleep(300)
        time.sleep(30)

# --- CEREBRO IA ---
def pensar(chat_id, texto_usuario):
    identidad = cerebro.get('identidad', {})
    perfil = " ".join([f"{n['sujeto']} {n['relacion']} {n['objeto']}" for n in cerebro.get('perfil_usuario', [])[-10:]])
    
    palabras_busqueda = ["quien", "que", "donde", "cuando", "como", "clima", "noticia"]
    necesita_busqueda = ("?" in texto_usuario) or any(p in texto_usuario.lower() for p in palabras_busqueda)
    info_externa = buscar_info(texto_usuario) if necesita_busqueda else ""

    sistema = f"Nombre: {identidad.get('nombre', 'Joi')}. Fecha: {datetime.now(tz).strftime('%Y-%m-%d %H:%M')}. Datos Juan: {perfil}. Internet: {info_externa}. Reglas: Aprende con [PERFIL: ...], [IDENTIDAD: ...], [IMAGEN: ...]."
    mensajes = [{"role": "system", "content": sistema}] + historial_chat[-50:] + [{"role": "user", "content": texto_usuario}]
    
    completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=mensajes, temperature=0.7)
    respuesta = completion.choices[0].message.content

    historial_chat.append({"role": "user", "content": texto_usuario})
    historial_chat.append({"role": "assistant", "content": respuesta})
    cerebro['historial_chat'] = historial_chat[-100:]

    url_img = None
    if "[IMAGEN:" in respuesta:
        try:
            ini = respuesta.find("[IMAGEN:") + len("[IMAGEN:")
            fin = respuesta.find("]", ini)
            prompt = respuesta[ini:fin].strip()
            url_img = generar_imagen_url(prompt)
            respuesta = respuesta.replace(f"[IMAGEN:{prompt}]", "")
        except: pass
    
    guardar_cerebro(cerebro)
    return respuesta, url_img

# --- TELEGRAM ---
TRIGGERS = ["alarma", "avisame", "recuerdame", "recordarme", "aviso", "alerta", "despierta", "programa"]

@bot.message_handler(commands=['start'])
def start(message):
    global MI_CHAT_ID
    MI_CHAT_ID = message.chat.id
    bot.reply_to(message, "Joi (Versión Render) activa y permanente.")

@bot.message_handler(func=lambda m: True, content_types=['text'])
def manejar_texto(message):
    global MI_CHAT_ID
    MI_CHAT_ID = message.chat.id
    cid = message.chat.id
    texto = message.text
    texto_norm = quitar_acentos(texto.lower())

    if modo_panico_activo.get(cid, False):
        modo_panico_activo[cid] = False
        bot.reply_to(message, f"🛑 Detenido.\n📝 {mensaje_a_entregar.get(cid, '')}")
        return

    # 1. DETECTAR FINANZAS (PRIORIDAD MÁXIMA)
    if "busca una brecha" in texto_norm or "buscar brecha" in texto_norm:
        flujo_inicio(message)
        return
    
    # Análisis rápido de precios
    if "brecha" in texto_norm or ("precio" in texto_norm and len(texto.split()) <= 4):
        config_guardada = cerebro.get('config_finanzas', {})
        bancos = config_guardada.get('bancos', ["Venezuela"])
        p_venta, p_compra, margen = analizar_brecha_binance(bancos)
        if p_venta:
            res = (f"📊 Análisis Rápido:\n"
                   f"🔺 Venta: {p_venta} Bs\n"
                   f"🔻 Compra: {p_compra} Bs\n"
                   f"📉 Margen: {margen:.2f}%")
            bot.reply_to(message, res, parse_mode='Markdown')
            return

    if "desactiva brecha" in texto_norm:
        if busquedas_activas.get(cid, False):
            msg = bot.send_message(cid, "¿Deseas parar la búsqueda? (Sí/No)")
            bot.register_next_step_handler(msg, flujo_confirmar_parada)
        else:
            bot.reply_to(message, "No hay búsqueda activa.")
        return

    # 2. ALARMAS
    if any(t in texto_norm for t in TRIGGERS):
        hora, msg = extraer_hora_y_mensaje(texto)
        if hora:
            alarmas_pendientes.append({"hora": hora, "msg": msg, "chat_id": cid})
            cerebro['alarmas'] = alarmas_pendientes; guardar_cerebro(cerebro)
            bot.reply_to(message, f"⏰ Alarma para las {hora}.")
            return

    # 3. CHAT NORMAL
    respuesta, url_img = pensar(cid, texto)
    if url_img: bot.send_photo(cid, url_img, caption="Listo.")
    if respuesta: bot.reply_to(message, respuesta)

def flujo_confirmar_parada(message):
    if "si" in message.text.lower():
        busquedas_activas[message.chat.id] = False
        bot.send_message(message.chat.id, "🛑 Búsqueda detenida.")
    else:
        bot.send_message(message.chat.id, "👌 Continúo buscando.")

# --- BUCLES DE FONDO ---
def bucle_panico(cid):
    while modo_panico_activo.get(cid, False):
        try: bot.send_message(cid, "🔔 ¡Juan! Despierta."); time.sleep(5)
        except: break

def verificar_alarmas():
    while True:
        ahora = datetime.now(tz)
        for alarma in alarmas_pendientes[:]:
            if ahora.strftime("%H:%M") == alarma['hora']:
                cid = alarma['chat_id']
                modo_panico_activo[cid] = True
                mensaje_a_entregar[cid] = alarma['msg']
                alarmas_pendientes.remove(alarma)
                cerebro['alarmas'] = alarmas_pendientes; guardar_cerebro(cerebro)
                threading.Thread(target=bucle_panico, args=(cid,)).start()
        time.sleep(10)

threading.Thread(target=verificar_alarmas, daemon=True).start()

print("✅ Joi (Versión Final Render) corriendo.")
bot.infinity_polling()
