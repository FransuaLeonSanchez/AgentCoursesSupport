import os
import time
import tkinter as tk
import threading
from dotenv import load_dotenv
from openai import OpenAI
from pypdf import PdfReader
import pyperclip

# --- Configuración ---
PDF_DIRECTORY = "pdfs"
PROMPT_INSTRUCTIONS = """
Tu única tarea es identificar la alternativa correcta para la pregunta de opción múltiple dada, basándote EXCLUSIVAMENTE en el material de estudio adjunto o tu conocimiento general si no está en el material.
RESPONDE CON LA ALTERNATIVA CORRECTA, incluyendo la letra y las primeras palabras de esa alternativa (ej: "a) El proceso de...", "b) Se refiere a..."). Mantenlo muy breve. NO incluyas explicaciones ni texto innecesario. NO uses puntos suspensivos (...) al final, solo el texto inicial.

Material de estudio adjunto:
---
{pdf_context}
---

Pregunta y alternativas del usuario:
---
{user_question}
---

RESPUESTA (letra y primeras palabras, sin puntos suspensivos):
"""
POLL_INTERVAL_SECONDS = 1 # Segundos entre chequeos del portapapeles
WINDOW_WIDTH = 180 # Más ancho
WINDOW_HEIGHT = 40 # Mantenemos la altura

# --- Carga de Clave API ---
load_dotenv()
API_KEY = os.getenv("OPENAI_API_KEY")
if not API_KEY:
    raise ValueError("No se encontró la variable de entorno OPENAI_API_KEY. Asegúrate de que esté en el archivo .env")

# Volver a la inicialización simple de OpenAI
client = OpenAI(api_key=API_KEY)

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

def get_openai_answer(question, context):
    """Obtiene la respuesta de OpenAI."""
    full_prompt = PROMPT_INSTRUCTIONS.format(pdf_context=context, user_question=question)
    try:
        print("Enviando pregunta a OpenAI (gpt-4o)...") # Indicar modelo
        response = client.chat.completions.create(
            model="gpt-4o", # Usar gpt-4o
            messages=[
                {"role": "system", "content": "Eres un asistente experto que responde preguntas de opción múltiple basándose en material de estudio proporcionado."},
                {"role": "user", "content": full_prompt}
            ],
            temperature=0.2, # Baja temperatura para respuestas más directas
            max_tokens=250 # Aumentar sustancialmente los tokens
        )
        answer = response.choices[0].message.content.strip()
        print(f"Respuesta recibida (completa): {answer}") # Loguear la respuesta completa
        return answer
    except Exception as e:
        print(f"Error al llamar a la API de OpenAI: {e}")
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
    y = screen_height - WINDOW_HEIGHT - Y_OFFSET # Usar Y_OFFSET muy pequeño para bajarla al máximo
    
    root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x}+{y}") # Posición ajustada
    root.overrideredirect(True) # Sin bordes ni título
    root.attributes("-topmost", True) # Siempre encima
    # root.attributes("-alpha", 0.6) # Eliminamos alpha

    # Hacer el fondo transparente
    default_bg = root.cget('bg') # Obtener color de fondo por defecto
    root.attributes('-transparentcolor', default_bg)

    # Etiqueta para mostrar la respuesta, con fondo transparente
    answer_label = tk.Label(root, text="Esperando...", font=("Arial", 10),
                            wraplength=WINDOW_WIDTH-10, bg=default_bg, fg="white") # Cambiado a color blanco
    answer_label.pack(expand=True, fill="both", padx=5, pady=5)

    # Hacer la ventana arrastrable
    last_click_x = 0
    last_click_y = 0

    def save_last_click_pos(event):
        nonlocal last_click_x, last_click_y
        last_click_x = event.x
        last_click_y = event.y

    def dragging(event):
        x, y = event.x_root - last_click_x, event.y_root - last_click_y
        root.geometry(f"+{x}+{y}")

    answer_label.bind('<Button-1>', save_last_click_pos)
    answer_label.bind('<B1-Motion>', dragging)

    # Función para actualizar el texto (segura para hilos)
    def update_label(text):
        answer_label.config(text=text)

    root.update_label = update_label # Adjuntar función para acceso externo
    return root

def check_clipboard(pdf_context, root):
    """Verifica el portapapeles y procesa nuevo texto."""
    print("Iniciando monitoreo del portapapeles...")
    recent_value = ""
    try:
        recent_value = pyperclip.paste()
    except pyperclip.PyperclipException as e:
        print(f"No se pudo acceder al portapapeles al inicio: {e}")
        print("Asegúrate de que 'xclip' o 'xsel' estén instalados si usas Linux, o que los permisos sean correctos.")


    while True:
        try:
            # waitForNewPaste puede ser bloqueante, usemos un chequeo manual
            current_value = pyperclip.paste()
            if current_value != recent_value and current_value.strip():
                print("\n--- Nuevo texto detectado ---")
                print(f"Texto copiado: '{current_value[:100]}...'") # Muestra inicio del texto
                recent_value = current_value

                # Mostrar "Procesando..." inmediatamente
                if root and root.winfo_exists():
                     root.after(0, root.update_label, "Procesando...")

                # Obtener respuesta en un hilo separado para no bloquear Tkinter
                def get_and_show_answer():
                    answer = get_openai_answer(recent_value, pdf_context)

                    # Truncar la respuesta para mostrar (15 primeros, 12 últimos)
                    if len(answer) > 27: # 15 + 12 = 27
                        display_text = answer[:16] + "..." + answer[-13:]
                    else:
                        display_text = answer

                    if root and root.winfo_exists():
                        # Usar root.after para actualizar la GUI desde el hilo principal de Tkinter
                        root.after(0, root.update_label, display_text) # Mostrar texto truncado

                threading.Thread(target=get_and_show_answer, daemon=True).start()

        except pyperclip.PyperclipException as e:
            print(f"Error al acceder al portapapeles: {e}")
            # Puedes añadir lógica aquí para reintentar o notificar
        except Exception as e:
            print(f"Error inesperado en el bucle de monitoreo: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


# --- Ejecución Principal ---
if __name__ == "__main__":
    print("Cargando texto de los PDFs...")
    pdf_text_context = extract_text_from_pdfs(PDF_DIRECTORY)

    if not pdf_text_context:
        print("Advertencia: No se pudo cargar texto de los PDFs. El asistente podría no tener contexto de clase.")
        # Considera si quieres continuar sin PDFs o detener el script
        # exit()

    print("Configurando ventana de respuesta...")
    answer_window_root = setup_answer_window()

    # Iniciar el monitoreo del portapapeles en un hilo separado
    clipboard_thread = threading.Thread(target=check_clipboard, args=(pdf_text_context, answer_window_root), daemon=True)
    clipboard_thread.start()

    print("Iniciando interfaz gráfica. Copia texto para obtener respuestas.")
    # Mantener la ventana de Tkinter abierta
    answer_window_root.mainloop()

    print("Script finalizado.") 