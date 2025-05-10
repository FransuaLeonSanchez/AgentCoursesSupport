import os
import time
import tkinter as tk
import threading
from dotenv import load_dotenv
from openai import OpenAI
from pypdf import PdfReader
import pyperclip # Descomentado
import httpx
import mss # Para capturas de pantalla
from PIL import Image, ImageDraw # Para procesar la imagen capturada y crear el icono
import io # Para manejar streams de bytes (para la imagen)
import base64 # Para codificar la imagen para la API
# import keyboard # Comentado temporalmente
import signal # Para manejar Ctrl+C
import sys # Para sys.exit
from pynput import mouse # Para escuchar clics del mouse globales
import pystray # Para el icono en la bandeja del sistema

# Bandera global para controlar la ejecución de hilos
app_running = True
# Evento para sincronizar el inicio de Tkinter
tkinter_ready_event = threading.Event()

# Variable global para el estado del color del texto
text_color_is_black = True 
# Variable global para el estado del monitoreo del portapapeles
clipboard_monitoring_active = True
# Variable global para la última respuesta copiada por la app al portapapeles
last_copied_by_app = None 

# --- Variables Globales para Selección de Área ---
selection_coords = []
mouse_listener = None
selecting_area = False # Bandera para indicar si estamos en modo selección

# --- Configuración ---
PDF_DIRECTORY = "pdfs"
# Márgenes globales para el posicionamiento de la ventana
MARGIN_PERCENT_X = 0.01  # 1% de margen desde el borde derecho
MARGIN_PERCENT_Y = 0.01  # 1% de margen desde el borde inferior

PROMPT_INSTRUCTIONS = """
Tu tarea principal es responder la pregunta proporcionada de la manera más precisa y concisa posible.

Considera los siguientes tipos de pregunta:

1. PREGUNTA CON OPCIONES MÚLTIPLES EXPLÍCITAS (ej: con a), b), c)):
   - Identifica la alternativa correcta.
   - RESPONDE ÚNICAMENTE con la letra de la alternativa y el texto completo de esa alternativa (ej: "a) El proceso de transformación digital.", "b) Se refiere a la capacidad de adaptación.").

2. PREGUNTA DIRECTA O DE CONOCIMIENTO (que busca una única respuesta fáctica, sin opciones explícitas en la pregunta):
   - Proporciona la respuesta correcta y concisa.
   - FORMATEA ESTA RESPUESTA COMO SI FUERA LA PRIMERA ALTERNATIVA, utilizando "a)" seguido de la respuesta (ej: si la pregunta es "¿Color del cielo?", responde "a) Azul").

3. PREGUNTA PARA COMPLETAR LA ORACIÓN (ej: "El sol sale por el ____."):
   - Proporciona la palabra o frase corta que completa correctamente la oración.


En todos los casos, DEBES proporcionar una respuesta y seguir ESTRICTAMENTE este orden de prioridad para la información:
1. EXCLUSIVAMENTE el material de estudio adjunto (PDFs).
2. Si se proporciona una imagen, basa tu respuesta PRINCIPALMENTE en la imagen, complementada por el material de estudio.
3. Solo como ÚLTIMO RECURSO, si la información no está en el material ni en la imagen, usa tu conocimiento general.

NO INCLUYAS EXPLICACIONES, saludos, ni ningún otro texto adicional. Solo la respuesta directa según el tipo de pregunta y formato especificado.

Material de estudio adjunto (PDFs):
---
{pdf_context}
---

Pregunta del usuario (y posible imagen adjunta):
---
{user_question}
---

RESPUESTA (según el tipo de pregunta, ver instrucciones arriba):
"""
POLL_INTERVAL_SECONDS = 1 # Segundos entre chequeos del portapapeles
WINDOW_WIDTH = 200 # Ancho de la ventana
WINDOW_HEIGHT = 50 # Alto de la ventana (reducido al quitar el botón)
# SCREENSHOT_HOTKEY = "ctrl+alt+s" # Comentado temporalmente

# --- Carga de Clave API ---
load_dotenv()
API_KEY = os.getenv("OPENAI_API_KEY")
if not API_KEY:
    raise ValueError("No se encontró la variable de entorno OPENAI_API_KEY. Asegúrate de que esté en el archivo .env")

# Inicializar OpenAI con un cliente httpx personalizado
# Esto puede ayudar a evitar problemas con la configuración de proxies del entorno.
try:
    custom_httpx_client = httpx.Client(trust_env=False)
    client = OpenAI(api_key=API_KEY, http_client=custom_httpx_client)
except Exception as e_httpx:
    print(f"Error al inicializar OpenAI con httpx.Client(trust_env=False): {e_httpx}")
    print("Intentando inicialización simple de OpenAI (puede fallar si el problema de proxy persiste)...")
    client = OpenAI(api_key=API_KEY) # Fallback a la original si la nueva falla por otra razón

# --- Funciones ---

def force_window_to_bottom_right_corner(window_obj):
    """Fuerza la ventana a la esquina inferior derecha y la mantiene encima, intentándolo dos veces."""
    if not (window_obj and window_obj.winfo_exists()):
        return

    screen_width = window_obj.winfo_screenwidth()
    screen_height = window_obj.winfo_screenheight()

    margin_x_pixels = int(screen_width * MARGIN_PERCENT_X)
    margin_y_pixels = int(screen_height * MARGIN_PERCENT_Y)

    x = screen_width - WINDOW_WIDTH - margin_x_pixels
    y = screen_height - WINDOW_HEIGHT - margin_y_pixels
    
    new_geometry = f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x}+{y}"
    
    # Primer intento
    window_obj.geometry(new_geometry)
    window_obj.attributes("-topmost", True)
    
    # Forzar procesamiento de eventos pendientes de Tkinter
    window_obj.update_idletasks()
    
    # Segundo intento para asegurar la posición y el estado topmost
    window_obj.geometry(new_geometry) # Reaplicar geometría
    window_obj.attributes("-topmost", True) # Reafirmar topmost
    
    window_obj.last_known_geometry = new_geometry # Actualizar con la posición forzada
    # print(f"Ventana forzada (doble intento) a: {new_geometry}") # Para depuración

def extract_text_from_pdfs(directory):
    """Extrae texto de todos los archivos PDF en el directorio especificado."""
    all_text = []
    print(f"Buscando PDFs en: {os.path.abspath(directory)}")
    if not os.path.isdir(directory):
        print(f"Error: El directorio '{directory}' no existe.")
        return ""
    try:
        for filename in os.listdir(directory):
            if filename.lower().endswith(".pdf"):
                filepath = os.path.join(directory, filename)
                print(f"Procesando: {filename}...")
                try:
                    reader = PdfReader(filepath)
                    for page in reader.pages:
                        page_text = page.extract_text()
                        if page_text:
                            all_text.append(page_text)
                    print(f"Texto extraído de {filename}.")
                except Exception as e:
                    print(f"Error al leer {filename}: {e}")
        print("Extracción de texto de PDFs completada.")
        return "\n\n---\n\n".join(all_text) # Separador entre textos de PDFs
    except Exception as e:
        print(f"Error al listar el directorio '{directory}': {e}")
        return ""

def encode_image_to_base64(image_pil):
    """Codifica un objeto PIL.Image a base64 string."""
    buffered = io.BytesIO()
    image_pil.save(buffered, format="PNG") # Guardar como PNG en memoria
    img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
    return img_str

def take_screenshot():
    """Toma una captura de la pantalla principal y la devuelve como objeto PIL.Image."""
    print("take_screenshot: Iniciando captura...")
    try:
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            print(f"take_screenshot: Capturando monitor {monitor}")
            sct_img = sct.grab(monitor)
            print("take_screenshot: Captura de datos raw completada.")
            # Convertir a PIL Image
            # MSS captura en BGRA. Pillow espera RGB o RGBA. sct_img.rgb da los bytes RGB.
            img = Image.frombytes('RGB', (sct_img.width, sct_img.height), sct_img.rgb, 'raw', 'BGR')
            print("take_screenshot: Conversión a PIL.Image completada.")
            return img
    except mss.exception.ScreenShotError as e_mss:
        print(f"Error específico de MSS al tomar la captura de pantalla: {e_mss}")
        if e_mss.details and "Xlib" in str(e_mss.details): # Ejemplo para Linux Xlib
            print("Esto podría ser un problema con Xlib. Asegúrate de que el entorno gráfico esté accesible.")
        return None
    except Exception as e:
        print(f"Error general al tomar la captura de pantalla: {e}")
        import traceback
        traceback.print_exc() # Imprimir el traceback completo para más detalles
        return None

def get_openai_answer(question, context, image_base64=None): # Modificado para aceptar imagen
    """Obtiene la respuesta de OpenAI."""
    full_prompt_text = PROMPT_INSTRUCTIONS.format(pdf_context=context, user_question=question)
    
    messages_payload = [
        {"role": "system", "content": "Eres un asistente experto que responde preguntas de opción múltiple basándose en material de estudio o imágenes proporcionadas."}
    ]
    
    user_content = [{"type": "text", "text": full_prompt_text}]
    
    if image_base64:
        user_content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{image_base64}"
            }
        })
        print("Enviando pregunta e imagen a OpenAI (gpt-4o)...")
    else:
        print("Enviando pregunta a OpenAI (gpt-4o)...")

    messages_payload.append({"role": "user", "content": user_content})

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages_payload,
            temperature=0.0, # Temperatura bajada para respuestas más deterministas
            max_tokens=250
        )
        answer = response.choices[0].message.content.strip()
        print(f"Respuesta recibida (completa): {answer}")
        return answer
    except Exception as e:
        print(f"Error al llamar a la API de OpenAI: {e}")
        if "safety" in str(e).lower(): # Manejo específico para errores de seguridad de imagen
            return "Error: La imagen fue bloqueada por política de seguridad."
        return "Error API"

def setup_answer_window():
    """Configura la ventana flotante para mostrar la respuesta."""
    root = tk.Tk()
    root.title("Respuesta")
    
    # Posicionamiento inicial forzado a la esquina
    force_window_to_bottom_right_corner(root) 

    root.overrideredirect(True) # Sin bordes ni título
    root.attributes("-topmost", True) # Siempre encima
    # root.attributes("-alpha", 0.6) # Eliminamos alpha

    # Hacer el fondo transparente
    default_bg = root.cget('bg') # Obtener color de fondo por defecto
    root.attributes('-transparentcolor', default_bg)

    # Etiqueta para mostrar la respuesta, con fondo transparente y texto negro
    answer_label = tk.Label(root, text="Esperando...", font=("Arial", 10, "normal"),
                            wraplength=WINDOW_WIDTH-10, bg=default_bg, fg="black") # fg cambiado a "black", peso de fuente especificado
    answer_label.pack(expand=True, fill="both", padx=5, pady=5) # pady ajustado
    root.answer_label = answer_label # Hacer la etiqueta accesible desde root

    # --- Función para cerrar la ventana y la app ---
    # Modificada para integrarse con pystray
    def quit_app_tk_part(): # Solo la parte de Tkinter
        global app_running
        print("Cerrando parte de Tkinter...")
        app_running = False # Indicar a otros hilos que deben detenerse
        
        if root and root.winfo_exists():
            root.quit() # Termina el bucle principal de Tkinter
            root.destroy() # Destruye la ventana
        
    root.protocol("WM_DELETE_WINDOW", lambda: quit_app_combined(None)) # Manejar cierre de ventana
    root.bind("<Escape>", lambda event: quit_app_combined(None)) # Vincular tecla Escape para cerrar

    # Hacer la ventana arrastrable
    last_click_x = 0
    last_click_y = 0

    def save_last_click_pos(event):
        nonlocal last_click_x, last_click_y
        last_click_x = event.x
        last_click_y = event.y

    def dragging(event):
        x_drag, y_drag = event.x_root - last_click_x, event.y_root - last_click_y
        new_geometry = f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x_drag}+{y_drag}"
        root.geometry(new_geometry)
        root.last_known_geometry = new_geometry # Actualizar al arrastrar

    answer_label.bind('<Button-1>', save_last_click_pos)
    answer_label.bind('<B1-Motion>', dragging)

    # Función para actualizar el texto (segura para hilos)
    def update_label(text):
        if root and root.winfo_exists(): # Asegurarse de que la ventana exista
            answer_label.config(text=text)

    root.update_label = update_label # Adjuntar función para acceso externo
    return root

# --- Funciones relacionadas con pystray y manejo de la aplicación ---
tray_icon = None # Variable global para el icono de la bandeja

def toggle_clipboard_monitoring_action():
    """Alterna el estado de monitoreo del portapapeles (activo/pausado)."""
    global clipboard_monitoring_active, tray_icon
    clipboard_monitoring_active = not clipboard_monitoring_active
    status = "activado" if clipboard_monitoring_active else "pausado"
    print(f"Monitoreo del portapapeles {status}.")
    # Si el icono existe y el menú se puede actualizar (pystray lo hace si el texto del item es una función)
    if tray_icon:
        pass # El texto del menú se actualizará automáticamente si es una función lambda

def toggle_text_color_action():
    """Alterna el color del texto de la etiqueta de respuesta entre negro y blanco."""
    global text_color_is_black, global_answer_window_root

    text_color_is_black = not text_color_is_black
    new_color = "black" if text_color_is_black else "white"

    if global_answer_window_root and global_answer_window_root.winfo_exists() and hasattr(global_answer_window_root, 'answer_label'):
        global_answer_window_root.after(0, lambda: global_answer_window_root.answer_label.config(fg=new_color))
        print(f"Color del texto cambiado a: {new_color}")
    else:
        print("No se pudo cambiar el color del texto: la ventana o la etiqueta no están disponibles.")

def create_icon_image():
    """Crea una imagen simple para el icono de la bandeja."""
    width = 64
    height = 64
    # Fondo transparente, color del icono blanco/gris
    color_bg = (0, 0, 0, 0)  # Transparente
    color_icon = (200, 200, 200, 255) # Gris claro para el icono
    image = Image.new('RGBA', (width, height), color_bg)
    dc = ImageDraw.Draw(image)
    # Dibujar una forma simple (un círculo con un punto)
    dc.ellipse([(width*0.1, height*0.1), (width*0.9, height*0.9)], fill=color_icon)
    dc.ellipse([(width*0.4, height*0.4), (width*0.6, height*0.6)], fill=color_bg) # Punto central transparente
    return image

def start_area_selection_mode_thread_safe():
    """Inicia la selección de área de forma segura para hilos (llamada desde pystray)."""
    global selecting_area, selection_coords, mouse_listener, global_answer_window_root
    
    if selecting_area:
        print("Ya se está en modo de selección.")
        if global_answer_window_root and global_answer_window_root.winfo_exists():
            global_answer_window_root.after(0, global_answer_window_root.update_label, "Selección activa")
        return

    print("Modo Selección de Área: Activado. Haz clic para la primera esquina.")
    if global_answer_window_root and global_answer_window_root.winfo_exists():
        global_answer_window_root.after(0, global_answer_window_root.update_label, "Clic 1ª esquina")
    
    selecting_area = True
    selection_coords = []
    
    if global_answer_window_root and global_answer_window_root.winfo_exists():
        global_answer_window_root.after(0, global_answer_window_root.withdraw) # Ocultar ventana

    def on_click(x, y, button, pressed):
        global selection_coords, selecting_area, mouse_listener, global_answer_window_root, global_pdf_text_context
        if pressed and selecting_area and button == mouse.Button.left:
            selection_coords.append((x, y))
            print(f"Clic detectado en: ({x}, {y})")
            
            if len(selection_coords) == 1:
                print("Primera esquina registrada. Haz clic para la segunda esquina.")
            
            elif len(selection_coords) == 2:
                print("Segunda esquina registrada. Procesando área seleccionada...")
                selecting_area = False
                
                if mouse_listener:
                    mouse_listener.stop()
                
                if global_answer_window_root and global_answer_window_root.winfo_exists():
                    # Mostrar la ventana y luego forzar posición y topmost
                    def show_and_force_position():
                        if not (global_answer_window_root and global_answer_window_root.winfo_exists()): return
                        global_answer_window_root.deiconify()
                        # Forzar posición después de un breve retardo para asegurar que deiconify se complete
                        global_answer_window_root.after(20, lambda: force_window_to_bottom_right_corner(global_answer_window_root))
                        global_answer_window_root.update_label("Procesando área...") # Actualizar etiqueta después de deiconify
                    
                    global_answer_window_root.after(0, show_and_force_position)

                x1, y1 = selection_coords[0]
                x2, y2 = selection_coords[1]
                
                region = {
                    "top": min(y1, y2), "left": min(x1, x2),
                    "width": abs(x1 - x2), "height": abs(y1 - y2)
                }
                
                if region["width"] == 0 or region["height"] == 0:
                    print("Error: El área seleccionada tiene ancho o alto cero.")
                    if global_answer_window_root and global_answer_window_root.winfo_exists():
                        global_answer_window_root.after(0, global_answer_window_root.update_label, "Error: Área 0")
                    selection_coords = []
                    return False

                print(f"Región calculada para mss: {region}")
                threading.Thread(target=process_selected_area, args=(region, global_pdf_text_context, global_answer_window_root), daemon=True).start()
                return False # Detener listener
        return True

    mouse_listener = mouse.Listener(on_click=on_click)
    mouse_listener.start()
    print("Listener de mouse iniciado para selección de área.")

def toggle_window_visibility():
    """Muestra u oculta la ventana de respuesta."""
    if global_answer_window_root and global_answer_window_root.winfo_exists():
        if global_answer_window_root.state() == 'withdrawn':
            global_answer_window_root.deiconify() # Mostrar primero
            # Luego, forzar posición y topmost con un pequeño retardo
            global_answer_window_root.after(20, lambda: force_window_to_bottom_right_corner(global_answer_window_root))
            print("Ventana de respuesta mostrada y reposicionada.")
        else:
            global_answer_window_root.withdraw()
            print("Ventana de respuesta oculta.")
    else:
        print("La ventana de respuesta no está disponible para mostrar/ocultar.")


def quit_app_combined(icon_param=None, tk_root_param=None):
    global app_running, tray_icon, global_answer_window_root
    
    if not app_running: # Evitar múltiples llamadas
        return
        
    print("Cerrando aplicación (combinado)...")
    app_running = False

    # Detener el listener de mouse si está activo
    global mouse_listener
    if mouse_listener and mouse_listener.is_alive():
        print("Deteniendo listener de mouse...")
        mouse_listener.stop()

    # Detener el icono de la bandeja
    # El icono que se pasa puede ser el que se usa en el menú o el global
    actual_icon = icon_param if icon_param else tray_icon
    if actual_icon:
        print("Deteniendo icono de la bandeja...")
        actual_icon.stop()
    
    # Detener Tkinter
    actual_tk_root = tk_root_param if tk_root_param else global_answer_window_root
    if actual_tk_root and actual_tk_root.winfo_exists():
        print("Cerrando ventana de Tkinter...")
        # La función quit_app_tk_part ya se encarga de root.quit y root.destroy
        # Pero necesitamos asegurarnos de que se llame desde el hilo correcto
        actual_tk_root.after(0, actual_tk_root.destroy) # Destruir es más directo aquí
        # Esperar un poco a que se procese la destrucción de Tkinter
        # Esto es delicado; idealmente, el hilo de Tkinter se uniría.
        # Como es un hilo demonio, sys.exit() lo terminará.
    
    print("Saliendo del script...")
    # sys.exit(0) no siempre es necesario si pystray.stop() libera el hilo principal
    # y los hilos demonio se cierran. Pero para asegurar...
    # Damos un pequeño tiempo para que los hilos terminen
    # time.sleep(0.5) # Pequeña pausa
    # Forzar la salida si los hilos no terminan limpiamente es la última opción
    # ya que sys.exit() puede ser abrupto para hilos demonio.
    # pystray.Icon.stop() debería permitir que el hilo principal continúe y termine.
    # Si pystray está en el hilo principal, su .stop() debería hacer que .run() retorne.

# --- Fin de funciones pystray ---

def check_clipboard(pdf_context, root):
    """Verifica el portapapeles y procesa nuevo texto."""
    print("Iniciando monitoreo del portapapeles...")
    global app_running, clipboard_monitoring_active, last_copied_by_app
    recent_value = ""
    try:
        # Intentar obtener el valor inicial sin fallar si no está disponible
        recent_value = pyperclip.paste()
    except pyperclip.PyperclipException as e:
        print(f"Advertencia: No se pudo acceder al portapapeles al inicio: {e} (esto puede ser normal si está vacío o en uso).")
        print("Asegúrate de que 'xclip' o 'xsel' estén instalados si usas Linux, o que los permisos sean correctos.")
    except Exception as e_init_paste: # Captura otras posibles excepciones de pyperclip.paste()
        print(f"Error inesperado al intentar leer el portapapeles inicialmente: {e_init_paste}")

    while app_running: # Verificar la bandera global
        try:
            if not clipboard_monitoring_active:
                time.sleep(POLL_INTERVAL_SECONDS) # Ahorrar CPU, pero seguir comprobando app_running
                continue # Saltar el procesamiento del portapapeles si está pausado

            if root and not root.winfo_exists():
                print("Ventana de Tkinter no disponible, deteniendo monitoreo de portapapeles en este hilo.")
                break 

            current_value = pyperclip.paste()
            if current_value != recent_value and current_value.strip():
                if current_value == last_copied_by_app:
                    print("Ignorando el texto del portapapeles ya que fue copiado por la aplicación.")
                    recent_value = current_value # Actualizar recent_value para evitar reprocesar si no hay más cambios
                else:
                    # Es un nuevo texto genuino del usuario/otra app
                    print("\n--- Nuevo texto detectado en portapapeles ---")
                    print(f"Texto copiado: '{current_value[:100]}...'")
                    
                    # Guardar el valor actual como el que se está procesando
                    text_to_process = current_value 
                    # Actualizar recent_value para la próxima comparación
                    recent_value = current_value 
                    # Resetear la bandera, ya que este texto no fue puesto por nuestra app
                    last_copied_by_app = None 

                    if root and root.winfo_exists():
                        root.after(0, root.update_label, "Procesando texto...")

                    # Definir y lanzar el hilo SOLO si es un nuevo texto genuino
                    def process_clipboard_in_thread(text_for_openai):
                        global last_copied_by_app # Necesario para actualizarla desde el hilo
                        
                        answer = get_openai_answer(text_for_openai, pdf_context)
                        display_text = answer[:16] + "..." + answer[-13:] if len(answer) > 27 else answer
                        
                        if root and root.winfo_exists():
                            root.after(0, root.update_label, display_text)
                        
                        try:
                            pyperclip.copy(answer)
                            print(f"Respuesta completa copiada al portapapeles: '{answer[:50]}...'" if len(answer) > 50 else f"Respuesta completa copiada al portapapeles: '{answer}'")
                            last_copied_by_app = answer # Guardar lo que la app copió
                        except pyperclip.PyperclipException as e_copy:
                            print(f"Error al copiar la respuesta al portapapeles: {e_copy}")
                            last_copied_by_app = None # Resetear si falla la copia

                    if app_running: # Asegurarse de que app_running no haya cambiado antes de iniciar nuevo hilo
                        threading.Thread(target=process_clipboard_in_thread, args=(text_to_process,), daemon=True).start()
            
        except pyperclip.PyperclipException:
            pass 
        except Exception as e:
            print(f"Error inesperado en el bucle de monitoreo del portapapeles: {e}")
            # Considerar un pequeño retardo aquí si los errores son muy rápidos y repetitivos
            time.sleep(POLL_INTERVAL_SECONDS) # Esperar antes de reintentar en caso de error grave
        
        time.sleep(POLL_INTERVAL_SECONDS) # Intervalo normal de sondeo
    print("Monitoreo del portapapeles detenido.")

def process_selected_area(region_details, pdf_context_for_area, root_window):
    """Toma captura de una región específica, la procesa y obtiene respuesta de OpenAI."""
    print(f"\n--- Procesando área seleccionada: {region_details} ---")
    
    # Pregunta para la imagen, adaptada para ambos tipos de pregunta
    question_for_image = "Esta imagen contiene una pregunta (puede ser de opción múltiple o para completar). Analiza la imagen y, utilizando también el material de estudio adjunto, proporciona la respuesta correcta y concisa. Si es de opción múltiple, responde con la letra y las primeras palabras de la alternativa. Si es para completar, responde solo con la palabra o frase corta que completa la oración."
    print(f"Usando pregunta para la imagen: '{question_for_image}'")

    if root_window and root_window.winfo_exists():
        root_window.after(0, root_window.update_label, "Capturando área...")

    # Tomar captura de la región
    screenshot_pil = take_screenshot_region(region_details)

    if screenshot_pil:
        if root_window and root_window.winfo_exists():
            root_window.after(0, root_window.update_label, "Procesando imagen...")
        
        print("process_selected_area: Codificando imagen a base64...")
        image_b64 = encode_image_to_base64(screenshot_pil)
        print("process_selected_area: Imagen codificada. Preparando para enviar a OpenAI.")
        
        def get_and_show_answer_area():
            global last_copied_by_app
            answer = get_openai_answer(question_for_image, pdf_context_for_area, image_base64=image_b64)
            display_text = answer[:16] + "..." + answer[-13:] if len(answer) > 27 else answer
            
            try:
                pyperclip.copy(answer)
                print(f"Respuesta completa de área copiada al portapapeles: '{answer[:50]}...'" if len(answer) > 50 else f"Respuesta completa de área copiada al portapapeles: '{answer}'")
                last_copied_by_app = answer # Guardar lo que la app copió
            except pyperclip.PyperclipException as e_copy:
                print(f"Error al copiar la respuesta de área al portapapeles: {e_copy}")
                last_copied_by_app = None # Resetear si falla la copia

            if root_window and root_window.winfo_exists():
                # Función para actualizar etiqueta y luego reaplicar geometría
                def update_and_reapply_geometry():
                    if not root_window.winfo_exists(): return # Comprobación extra
                    root_window.update_label(display_text)
                    
                    # Forzar la posición y topmost después de actualizar la etiqueta (primer intento)
                    force_window_to_bottom_right_corner(root_window)
                
                root_window.after(0, update_and_reapply_geometry)
        
        threading.Thread(target=get_and_show_answer_area, daemon=True).start()
    else:
        print("process_selected_area: Falló la captura de la región (screenshot_pil es None).")
        if root_window and root_window.winfo_exists():
            root_window.after(0, root_window.update_label, "Error área")

# Nueva función para capturar solo una región
def take_screenshot_region(region_dict):
    """Toma una captura de la región especificada y la devuelve como objeto PIL.Image."""
    print(f"take_screenshot_region: Iniciando captura de región {region_dict}...")
    try:
        with mss.mss() as sct:
            # region_dict ya debe tener {"top", "left", "width", "height"}
            sct_img = sct.grab(region_dict)
            print("take_screenshot_region: Captura de datos raw de región completada.")
            img = Image.frombytes('RGB', (sct_img.width, sct_img.height), sct_img.rgb, 'raw', 'BGR')
            print("take_screenshot_region: Conversión a PIL.Image completada.")
            return img
    except mss.exception.ScreenShotError as e_mss:
        print(f"Error específico de MSS al tomar la captura de la región: {e_mss}")
        return None
    except Exception as e:
        print(f"Error general al tomar la captura de la región: {e}")
        import traceback
        traceback.print_exc()
        return None

# Variables globales para pasar a la callback del botón (simplificación temporal)
global_pdf_text_context = None
global_answer_window_root = None
# tray_icon ya está definido arriba

# --- Manejador de Señal para Ctrl+C ---
def signal_handler(sig, frame):
    print('\nCtrl+C detectado! Intentando cerrar la aplicación...')
    quit_app_combined() # Usar la función combinada

# --- Funciones para ejecutar Tkinter en un hilo ---
def run_tkinter_app():
    global global_answer_window_root, global_pdf_text_context
    
    print("Configurando ventana de respuesta en hilo de Tkinter...")
    answer_window = setup_answer_window()
    global_answer_window_root = answer_window
    
    # Adjuntar referencia a quit_app_combined para ser llamada desde Tkinter (e.g. Escape)
    # El lambda necesita acceso al icono de la bandeja, que se crea después.
    # Por ahora, pasamos None y quit_app_combined usará la variable global tray_icon.
    global_answer_window_root.quit_app_ref = lambda: quit_app_combined(None, global_answer_window_root)
    
    # Iniciar monitoreo del portapapeles después de que la ventana esté lista
    # y pasar la referencia correcta de la ventana
    if app_running: # Solo si la app sigue corriendo
        clipboard_thread = threading.Thread(target=check_clipboard, args=(global_pdf_text_context, global_answer_window_root), daemon=True)
        clipboard_thread.start()
        print("Hilo de monitoreo de portapapeles iniciado desde hilo de Tkinter.")

    tkinter_ready_event.set() # Indicar que Tkinter está listo
    print("Iniciando bucle principal de Tkinter (mainloop)...")
    try:
        answer_window.mainloop()
    except Exception as e_mainloop:
        print(f"Error durante mainloop de Tkinter: {e_mainloop}")
    finally:
        print("Bucle principal de Tkinter finalizado.")
        # Asegurarse de que si mainloop termina (p.ej. por error), la app se cierre.
        # quit_app_combined() # Esto podría causar problemas si ya se está cerrando.

# --- Ejecución Principal ---
if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler) # Registrar el manejador para Ctrl+C

    print("Cargando texto de los PDFs...")
    pdf_text_context = extract_text_from_pdfs(PDF_DIRECTORY)
    global_pdf_text_context = pdf_text_context # Asignar a la variable global

    if not pdf_text_context:
        print("Advertencia: No se pudo cargar texto de los PDFs. El asistente podría no tener contexto de clase.")
    
    # Iniciar Tkinter en un hilo separado
    tkinter_thread = threading.Thread(target=run_tkinter_app, daemon=True)
    tkinter_thread.start()
    
    print("Esperando a que la GUI de Tkinter esté lista...")
    tkinter_ready_event.wait() # Esperar a que la ventana de Tkinter esté configurada
    print("GUI de Tkinter lista.")

    if not global_answer_window_root:
        print("Error: La ventana de Tkinter no se inicializó correctamente. Saliendo.")
        sys.exit(1)

    # Configurar y ejecutar el icono de la bandeja del sistema en el hilo principal
    icon_image = create_icon_image()
    
    # Crear los items del menú
    # El primer item con default=True es usualmente la acción para el clic izquierdo.
    menu_items = [
        pystray.MenuItem(
            'Seleccionar Área',
            start_area_selection_mode_thread_safe,
            default=True, # Marcar como acción por defecto
            visible=True # Asegurar que sea visible en el menú de clic derecho también
        ),
        pystray.MenuItem('Mostrar/Ocultar Ventana', toggle_window_visibility),
        pystray.MenuItem(
            # Aceptar un argumento opcional (item) que pystray podría pasar al generar el texto del menú
            lambda item=None: "Pausar Monitoreo Portapapeles" if clipboard_monitoring_active else "Reanudar Monitoreo Portapapeles",
            toggle_clipboard_monitoring_action
        ),
        pystray.MenuItem('Alternar Color Texto', toggle_text_color_action),
        pystray.MenuItem('Salir', lambda: quit_app_combined(tray_icon, global_answer_window_root))
    ]
    menu = pystray.Menu(*menu_items) # Crear una instancia de pystray.Menu

    # La acción principal (seleccionar área) se activa por el MenuItem con default=True.
    # Las otras acciones quedan en el menú de clic derecho.
    tray_icon = pystray.Icon(
        "AsistenteGPT", 
        icon_image, 
        "Asistente GPT - Clic Izq: Sel. Área", # El tooltip puede seguir así
        menu # Pasar la instancia de pystray.Menu
    )

    print("Iniciando icono en la bandeja del sistema. La aplicación está en ejecución.")
    # Mensaje actualizado para reflejar el comportamiento del clic izquierdo y derecho
    print("Haz clic izquierdo en el icono para Seleccionar Área. Clic derecho para más opciones (Mostrar/Ocultar Ventana, Salir). Presiona ESC en la ventana (si está visible) o usa Ctrl+C para salir.")
    
    try:
        tray_icon.run() # Esto es bloqueante y se ejecutará en el hilo principal
    except Exception as e_tray:
        print(f"Error durante la ejecución del icono de la bandeja: {e_tray}")
    finally:
        print("Ejecución del icono de la bandeja finalizada.")
        # Asegurar que todo se cierre si tray_icon.run() termina por alguna razón
        # (aparte de quit_app_combined siendo llamada)
        if app_running: # Si no se cerró por quit_app_combined
            quit_app_combined(tray_icon, global_answer_window_root)
        
        # Esperar a que el hilo de Tkinter termine si es necesario (aunque es demonio)
        if tkinter_thread.is_alive():
            print("Esperando al hilo de Tkinter...")
            # No se puede hacer join a un hilo demonio de esta forma fácilmente si sys.exit() se llama.
            # La lógica de cierre debería haber manejado la GUI.
        
    print("Script finalizado limpiamente.") 