# test_printer.py
import serial
import sys
import time
from PIL import Image, ImageDraw, ImageFont

# --- Configuración ---
PUERTO_COM = "COM4"  # <------ Set your device port
ANCHO_IMPRESORA_PX = 384
# ---------------------

def crear_imagen_para_imprimir():
    print(f"Creando imagen de {ANCHO_IMPRESORA_PX}px de ancho...")
    
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except IOError:
        print("Fuente Arial no encontrada, usando fuente por defecto.")
        font = ImageFont.load_default(size=48)

    alto_imagen = 70

    # Lógica de Impresión Correcta (0=Blanco, 1=Negro)
    img = Image.new('1', (ANCHO_IMPRESORA_PX, alto_imagen), 0) # Fondo 0 (Blanco)
    draw = ImageDraw.Draw(img)
    draw.text((10, 5), "¡HACKEADO!", font=font, fill=1) # Texto 1 (Negro)
    
    print("Imagen creada (Fondo 0, Texto 1).")
    
    img.save("prueba_final_v9.png")
    print("Imagen guardada como 'prueba_final_v9.png'.")
    
    # --- Formatear para la impresora ---
    datos_imagen = img.tobytes()
    ancho_bytes = (img.width + 7) // 8
    alto_pixels = img.height
    
    # --- ¡LA CORRECCIÓN CRÍTICA! ---
    # La impresora espera Big-Endian, no Little-Endian.
    ancho_hex = ancho_bytes.to_bytes(2, 'big') # ¡CAMBIADO A 'big'!
    alto_hex = alto_pixels.to_bytes(2, 'big') # ¡CAMBIADO A 'big'!

    COMANDO_IMPRESION = (
        b'\x1D\x76\x30' +  # "GS v 0"
        ancho_hex +         # Ancho (ej. 00 30)
        alto_hex +          # Alto (ej. 00 46)
        datos_imagen        # Los bytes de la imagen
    )
    
    print(f"Comando de imagen creado: {len(COMANDO_IMPRESION)} bytes.")
    return COMANDO_IMPRESION

def enviar_a_impresora():
    
    datos_para_enviar = crear_imagen_para_imprimir()
    
    # 1. El "despertador"
    HANDSHAKE_WAKE_UP = b"MHV=H1.0,SV=V1.01,VOLT=8000mv,DPI=384,\n"
    
    # 2. El comando de ejecución
    EXECUTE_PRINT = b"LABELAT1\n"
    
    # 3. El avance de papel
    FEED_PAPEL = b"\n\n\n\n"

    print(f"Conectando a {PUERTO_COM}...")
    try:
        printer = serial.Serial(
            PUERTO_COM,
            baudrate=9600,
            timeout=5,
            write_timeout=5
        )
        print("¡Conectado! Enviando 'despertador'...")
        printer.write(HANDSHAKE_WAKE_UP)
        time.sleep(0.1)
        
        print("Enviando comando de imagen (al buffer)...")
        printer.write(datos_para_enviar)
        time.sleep(0.1)

        print("¡Enviando comando de EJECUCIÓN (LABELAT1)!")
        printer.write(EXECUTE_PRINT)
        
        print("Avanzando papel...")
        printer.write(FEED_PAPEL)
        
        printer.close()
        print("¡Datos enviados! Conexión cerrada.")
        print("Revisa la impresora...")

    except serial.SerialException as e:
        print(f"--- ERROR DE PUERTO SERIE ---: {e}")
    except Exception as e:
        print(f"--- ERROR INESPERADO ---: {e}")

if __name__ == "__main__":
    enviar_a_impresora()