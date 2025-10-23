import serial
import sys
import time
from PIL import Image, ImageDraw, ImageFont

# --- Configuration ---
COM_PORT = "COM4"   # <------ Set your device port
PRINTER_WIDTH_PX = 384
# ---------------------

def create_image_to_print():
    """
    Creates a simple black-on-white image for testing,
    formats it, and returns the binary command.
    """
    print(f"Creating image with {PRINTER_WIDTH_PX}px width...")
    
    # Try to load Arial, fall back to default font if not found
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except IOError:
        print("Arial font not found, using default font.")
        # Size parameter for load_default is often ignored, but kept for clarity
        font = ImageFont.load_default() 

    image_height = 70

    # Correct Printing Logic (0=White, 1=Black)
    # Background 0 (White), Text 1 (Black)
    img = Image.new('1', (PRINTER_WIDTH_PX, image_height), 0) 
    draw = ImageDraw.Draw(img)
    # The text fill must be 1 (Black)
    draw.text((10, 5), "TEST PRINT!", font=font, fill=1) 
    
    print("Image created (Background 0, Text 1).")
    
    # Save the image locally for verification
    img.save("test.png")
    print("Image saved as 'test.png'.")
    
    # --- Format for the Printer (GS v 0) ---
    image_data = img.tobytes()
    # Width in bytes (rounded up to the next byte boundary)
    width_bytes = (img.width + 7) // 8
    height_pixels = img.height
    
    # --- THE CRITICAL CORRECTION! ---
    # The printer expects Big-Endian for width and height.
    # We use 'big' endian for MSB (Most Significant Byte) first.
    width_hex = width_bytes.to_bytes(2, 'big') # CHANGED TO 'big'!
    height_hex = height_pixels.to_bytes(2, 'big') # CHANGED TO 'big'!

    PRINT_COMMAND = (
        b'\x1D\x76\x30' +      # "GS v 0" command
        width_hex +            # Width (e.g., 00 30)
        height_hex +           # Height (e.g., 00 46)
        image_data             # The actual image bytes
    )
    
    print(f"Image command created: {len(PRINT_COMMAND)} bytes.")
    return PRINT_COMMAND

def send_to_printer():
    """
    Handles the entire process of creating the image data and sending
    it, along with necessary handshake/control commands, to the printer.
    """
    
    data_to_send = create_image_to_print()
    
    # 1. The "Wake-up" handshake command
    HANDSHAKE_WAKE_UP = b"MHV=H1.0,SV=V1.01,VOLT=8000mv,DPI=384,\n"
    
    # 2. The execution command (specific to some thermal printers)
    EXECUTE_PRINT = b"LABELAT1\n"
    
    # 3. Paper feed commands
    PAPER_FEED = b"\n\n\n\n"

    print(f"Connecting to {COM_PORT}...")
    try:
        printer = serial.Serial(
            COM_PORT,
            baudrate=9600,
            timeout=5,
            write_timeout=5
        )
        print("Connected! Sending 'wake-up' handshake...")
        printer.write(HANDSHAKE_WAKE_UP)
        time.sleep(0.1)
        
        print("Sending image command (to the buffer)...")
        printer.write(data_to_send)
        time.sleep(0.1)

        print("Sending EXECUTION command (LABELAT1)!")
        printer.write(EXECUTE_PRINT)
        
        print("Advancing paper...")
        printer.write(PAPER_FEED)
        
        printer.close()
        print("Data sent! Connection closed.")
        print("Check the printer for the output.")

    except serial.SerialException as e:
        print(f"--- SERIAL PORT ERROR ---: {e}")
        print("Please ensure the printer is connected to the correct COM port and turned on.")
    except Exception as e:
        print(f"--- UNEXPECTED ERROR ---: {e}")

if __name__ == "__main__":
    send_to_printer()