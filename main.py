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
from PIL import Image # Para procesar la imagen capturada
import io # Para manejar streams de bytes (para la imagen)
import base64 # Para codificar la imagen para la API
# import keyboard # Comentado temporalmente
import signal # Para manejar Ctrl+C
import sys # Para sys.exit
from pynput import mouse # Para escuchar clics del mouse globales

# Bandera global para controlar la ejecución de hilos
app_running = True

# --- Variables Globales para Selección de Área ---
selection_coords = []
mouse_listener = None
selecting_area = False # Bandera para indicar si estamos en modo selección

# --- Configuración ---
PDF_DIRECTORY = "pdfs"
PROMPT_INSTRUCTIONS = """
Tu única tarea es identificar la alternativa correcta para la pregunta de opción múltiple dada, basándote EXCLUSIVAMENTE en el material de estudio adjunto, tu conocimiento general si no está en el material, o la imagen adjunta si se proporciona.
Si se adjunta una imagen, tu respuesta DEBE basarse principalmente en la imagen.
RESPONDE CON LA ALTERNATIVA CORRECTA, incluyendo la letra y las primeras palabras de esa alternativa (ej: "a) El proceso de...", "b) Se refiere a..."). Mantenlo muy breve. NO incluyas explicaciones ni texto innecesario. NO uses puntos suspensivos (...) al final, solo el texto inicial.

Material de estudio adjunto (si aplica):
---
{pdf_context}
---

Pregunta y alternativas del usuario (y posible imagen adjunta):
---
{user_question}
---

RESPUESTA (letra y primeras palabras, sin puntos suspensivos):
"""
POLL_INTERVAL_SECONDS = 1 # Segundos entre chequeos del portapapeles
WINDOW_WIDTH = 200 # Un poco más ancho para el botón
WINDOW_HEIGHT = 60 # Un poco más alto para el botón
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
            temperature=0.2,
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
    
    # Calcular posición inferior derecha, muy cerca del borde inferior
    X_OFFSET = 20 # Píxeles de margen desde el borde derecho
    Y_OFFSET = 10 # Píxeles de margen desde el borde inferior (MUY PEQUEÑO = muy abajo)
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    x = screen_width - WINDOW_WIDTH - X_OFFSET
    y = screen_height - WINDOW_HEIGHT - Y_OFFSET
    
    initial_geometry = f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x}+{y}"
    root.geometry(initial_geometry) # Posición ajustada
    root.last_known_geometry = initial_geometry # Guardar la geometría

    root.overrideredirect(True) # Sin bordes ni título
    root.attributes("-topmost", True) # Siempre encima
    # root.attributes("-alpha", 0.6) # Eliminamos alpha

    # Hacer el fondo transparente
    default_bg = root.cget('bg') # Obtener color de fondo por defecto
    root.attributes('-transparentcolor', default_bg)

    # Etiqueta para mostrar la respuesta, con fondo transparente
    answer_label = tk.Label(root, text="Esperando...", font=("Arial", 10),
                            wraplength=WINDOW_WIDTH-10, bg=default_bg, fg="white")
    answer_label.pack(expand=True, fill="both", padx=5, pady=2) # Menos pady para botón

    # --- Botón para iniciar selección de área ---
    def start_area_selection_mode():
        global selecting_area, selection_coords, mouse_listener, global_answer_window_root
        
        if selecting_area:
            print("Ya se está en modo de selección.")
            return

        print("Modo Selección de Área: Activado. Haz clic para la primera esquina.")
        global_answer_window_root.update_label("Clic 1ª esquina") # Actualizar GUI
        
        selecting_area = True
        selection_coords = []
        
        # Ocultar ventana principal para no interferir con los clics de selección
        if global_answer_window_root:
            global_answer_window_root.withdraw()

        # Iniciar listener de mouse
        # Usaremos un listener que se detiene después de 2 clics.
        def on_click(x, y, button, pressed):
            global selection_coords, selecting_area, mouse_listener, global_answer_window_root, global_pdf_text_context
            if pressed and selecting_area and button == mouse.Button.left:
                selection_coords.append((x, y))
                print(f"Clic detectado en: ({x}, {y})")
                
                if len(selection_coords) == 1:
                    print("Primera esquina registrada. Haz clic para la segunda esquina.")
                    # No podemos actualizar la GUI directamente aquí porque está oculta y esto es un hilo diferente
                    # El feedback será por consola.
                    # Si tuviéramos una pequeña ventana de feedback separada, la actualizaríamos.
                
                elif len(selection_coords) == 2:
                    print("Segunda esquina registrada. Procesando área seleccionada...")
                    selecting_area = False # Salir del modo selección
                    
                    # Detener el listener
                    if mouse_listener:
                        mouse_listener.stop()
                    
                    # Mostrar ventana principal de nuevo y restaurar geometría
                    if global_answer_window_root:
                        global_answer_window_root.deiconify()
                        if hasattr(global_answer_window_root, 'last_known_geometry'):
                            global_answer_window_root.geometry(global_answer_window_root.last_known_geometry)
                        global_answer_window_root.update_label("Procesando área...")

                    # Calcular coordenadas para mss (left, top, width, height)
                    x1, y1 = selection_coords[0]
                    x2, y2 = selection_coords[1]
                    
                    region = {
                        "top": min(y1, y2),
                        "left": min(x1, x2),
                        "width": abs(x1 - x2),
                        "height": abs(y1 - y2)
                    }
                    
                    # Asegurarse que width y height no sean cero
                    if region["width"] == 0 or region["height"] == 0:
                        print("Error: El área seleccionada tiene ancho o alto cero. Inténtalo de nuevo.")
                        if global_answer_window_root and global_answer_window_root.winfo_exists():
                            global_answer_window_root.update_label("Error: Área 0")
                        selection_coords = [] # Resetear para nuevo intento
                        # No reiniciamos selecting_area aquí, el usuario debe presionar el botón de nuevo.
                        return False # Detener procesamiento del listener para este clic.

                    print(f"Región calculada para mss: {region}")
                    # Lanzar el procesamiento de esta región en un hilo
                    threading.Thread(target=process_selected_area, args=(region, global_pdf_text_context, global_answer_window_root), daemon=True).start()
                    return False # Detener el listener después del segundo clic válido
            return True # Continuar escuchando si no es un clic relevante o no estamos listos

        # Crear y empezar el listener en el hilo actual (que es el hilo de Tkinter para el botón)
        # pynput.mouse.Listener se ejecuta en su propio hilo demonio por defecto.
        mouse_listener = mouse.Listener(on_click=on_click)
        mouse_listener.start()
        print("Listener de mouse iniciado.")

    capture_button = tk.Button(root, text="Seleccionar Área", command=start_area_selection_mode, font=("Arial", 8))
    capture_button.pack(pady=(0,5))

    # --- Función para cerrar la ventana y la app ---
    def quit_app(event=None): # event=None para poder llamarla desde Ctrl+C y Escape
        global app_running
        print("Cerrando aplicación...")
        app_running = False # Indicar a otros hilos que deben detenerse si es posible
        try:
            # Detener el listener de keyboard si estuviera activo (no en esta versión simplificada)
            # if keyboard_listener_active: # Necesitaríamos una bandera para esto
            #     keyboard.unhook_all() # O el método específico de la librería si es diferente
            #     print("Listeners de teclado detenidos.")
            pass # No hay listener de teclado activo en esta versión simplificada
        except Exception as e_kb_stop:
            print(f"Error al intentar detener listeners de teclado: {e_kb_stop}")
        
        root.quit() # Termina el bucle principal de Tkinter
        root.destroy() # Destruye la ventana
        # sys.exit(0) # Forzar salida, aunque root.quit() debería ser suficiente si no hay más hilos no demonio

    root.bind("<Escape>", quit_app) # Vincular tecla Escape para cerrar

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
        answer_label.config(text=text)

    root.update_label = update_label # Adjuntar función para acceso externo
    return root

def check_clipboard(pdf_context, root): # DESCOMENTADA COMPLETAMENTE
    """Verifica el portapapeles y procesa nuevo texto."""
    print("Iniciando monitoreo del portapapeles...")
    global app_running # Usar la bandera global
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
            current_value = pyperclip.paste()
            if current_value != recent_value and current_value.strip():
                print("\n--- Nuevo texto detectado en portapapeles ---")
                print(f"Texto copiado: '{current_value[:100]}...'")
                recent_value = current_value

                if root and root.winfo_exists():
                    root.after(0, root.update_label, "Procesando texto...")

                def get_and_show_answer_clipboard():
                    answer = get_openai_answer(recent_value, pdf_context) # Sin imagen para el portapapeles
                    display_text = answer[:16] + "..." + answer[-13:] if len(answer) > 27 else answer
                    if root and root.winfo_exists():
                        root.after(0, root.update_label, display_text)

                threading.Thread(target=get_and_show_answer_clipboard, daemon=True).start()

        except pyperclip.PyperclipException:
            # Errores de acceso al portapapeles (ej. "could not open clipboard") pueden ser frecuentes
            # si otra aplicación lo está usando intensivamente. Silenciarlos para no llenar la consola.
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
    
    question_for_image = "Analiza esta imagen y describe su contenido."
    print(f"Usando pregunta fija para la imagen: '{question_for_image}'")

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
            answer = get_openai_answer(question_for_image, pdf_context_for_area, image_base64=image_b64)
            display_text = answer[:16] + "..." + answer[-13:] if len(answer) > 27 else answer
            if root_window and root_window.winfo_exists():
                # Función para actualizar etiqueta y luego reaplicar geometría
                def update_and_reapply_geometry():
                    if not root_window.winfo_exists(): return # Comprobación extra
                    root_window.update_label(display_text)
                    if hasattr(root_window, 'last_known_geometry'):
                        # print(f"Reaplicando geometría después de respuesta: {root_window.last_known_geometry}") # Para depurar
                        root_window.geometry(root_window.last_known_geometry)
                    else:
                        print("Advertencia: last_known_geometry no encontrada al intentar reaplicar después de respuesta.")
                
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

# --- Manejador de Señal para Ctrl+C ---
def signal_handler(sig, frame):
    print('\nCtrl+C detectado! Intentando cerrar la aplicación...')
    global app_running
    app_running = False # Indicar a otros hilos/procesos que se detengan

    if global_answer_window_root and hasattr(global_answer_window_root, 'quit_app_ref'):
        print("Signal handler: Intentando llamar a la función de cierre de la GUI (quit_app_ref)...")
        try:
            # Llamar a la función que contiene root.quit() y root.destroy()
            # Esto es potencialmente inseguro si se llama desde un hilo que no es el principal de Tkinter,
            # pero dado que after_idle no funcionó, es un intento más directo.
            global_answer_window_root.quit_app_ref()
            # Damos una pequeña oportunidad para que el evento de quit se procese
            # Esto es heurístico y no una solución garantizada para problemas de hilos.
            print("Signal handler: quit_app_ref llamada. Esperando brevemente antes de forzar salida si es necesario.")
            # No se puede hacer un time.sleep() largo aquí porque estamos en un manejador de señales.
            # El objetivo es que root.quit() termine el mainloop.
        except Exception as e:
            print(f"Signal handler: Error al llamar a quit_app_ref: {e}")
            print("Signal handler: Recurriendo a sys.exit(1) debido a error.")
            sys.exit(1) # Salir con código de error si la llamada a quit_app_ref falla
    else:
        print("Signal handler: No hay referencia a la GUI (global_answer_window_root) o a quit_app_ref.")

    # Si root.quit() fue llamado por quit_app_ref, mainloop debería terminar.
    # Si el programa sigue aquí, o si no se pudo llamar a quit_app_ref, forzamos la salida.
    print("Signal handler: Forzando salida con sys.exit(0) como último recurso.")
    sys.exit(0)

# --- Ejecución Principal ---
if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler) # Registrar el manejador para Ctrl+C

    print("Cargando texto de los PDFs...")
    pdf_text_context = extract_text_from_pdfs(PDF_DIRECTORY)
    global_pdf_text_context = pdf_text_context # Asignar a la variable global

    if not pdf_text_context:
        print("Advertencia: No se pudo cargar texto de los PDFs. El asistente podría no tener contexto de clase.")
    
    print("Configurando ventana de respuesta...")
    answer_window_root = setup_answer_window()
    global_answer_window_root = answer_window_root # Asignar a la variable global
    
    answer_window_root.quit_app_ref = lambda: quit_app(None)

    # Iniciar el monitoreo del portapapeles en un hilo separado (DESCOMENTADO)
    clipboard_thread = threading.Thread(target=check_clipboard, args=(pdf_text_context, answer_window_root), daemon=True)
    clipboard_thread.start()

    # Configurar el atajo de teclado para la captura de pantalla (SIGUE COMENTADO)
    # try:
    #     def hotkey_callback_wrapper():
    #         print("hotkey_callback_wrapper: Atajo presionado, iniciando procesamiento...")
    #         try:
    #             process_screenshot_request(pdf_text_context, answer_window_root)
    #         except Exception as e_wrapper:
    #             print(f"Error catastrófico en hotkey_callback_wrapper: {e_wrapper}")
    #             import traceback
    #             traceback.print_exc()
    #             if answer_window_root and answer_window_root.winfo_exists():
    #                 try:
    #                     answer_window_root.after(0, answer_window_root.update_label, "Error atajo fatal")
    #                 except Exception as e_gui_fatal:
    #                     print(f"Error al intentar mostrar error fatal en GUI: {e_gui_fatal}")
    #         print("hotkey_callback_wrapper: Procesamiento del atajo finalizado.")

    #     keyboard.add_hotkey(SCREENSHOT_HOTKEY, hotkey_callback_wrapper)
    #     print(f"Atajo de teclado '{SCREENSHOT_HOTKEY}' registrado para capturar pantalla.")
    #     print("NOTA: La detección de atajos puede requerir que la consola tenga foco o ejecutar como administrador.")
    # except Exception as e:
    #     print(f"Error al registrar el atajo de teclado '{SCREENSHOT_HOTKEY}': {e}")
    #     print("Asegúrate de que la librería 'keyboard' esté instalada y, en Windows, prueba ejecutar como administrador.")

    # print("Iniciando interfaz gráfica. Copia texto o usa el atajo para capturas.") # Mensaje modificado
    print("Iniciando interfaz gráfica. Usa el botón para capturar pantalla. Presiona ESC para salir.")
    answer_window_root.mainloop()

    # Después de que mainloop termina (por root.quit())
    print("Script finalizado limpiamente.") 