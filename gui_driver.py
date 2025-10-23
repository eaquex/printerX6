# gui_driver.py (v2.2 - Semaphore/Serial Open Fix)
import sys
import serial
import serial.tools.list_ports
import time
from PIL import Image, ImageOps, UnidentifiedImageError
import platform
import urllib.parse
import os

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QVBoxLayout, QHBoxLayout,
    QWidget, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QMessageBox, QLabel, QProgressDialog
)
from PySide6.QtGui import QPixmap, QImage, QPainter
from PySide6.QtCore import Qt, QSize, QThread, QObject, Signal, QUrl

# --- Constants ---
PRINTER_WIDTH_PX = 384
PRINTER_HANDSHAKE = b"MHV=H1.0,SV=V1.01,VOLT=8000mv,DPI=384,\n"
PRINTER_EXECUTE = b"LABELAT1\n"
FINAL_FEED_COMMAND = b"\x1B\x64\x06" # ESC d 6 (Feed 6 lines)
PROBE_PING = b'\x1E\x47\x03'
PROBE_PONG_EXPECTED = b"HV=H1.0"
ACCEPTED_FORMATS_TEXT = "Accepted: PNG, JPG, GIF, WebP"
# Chunk size in BYTES (Adjusted back, CR might fix alignment)
CHUNK_BYTE_SIZE = 48 * 16 # 768 bytes
CHUNK_DELAY_S = 0.08 # Delay between data chunks

# --- Dark Mode Stylesheet ---
DARK_STYLESHEET = """
    QWidget {
        background-color: #2b2b2b;
        color: #f0f0f0;
        font-size: 10pt;
    }
    QPushButton {
        background-color: #3c3f41;
        border: 1px solid #555;
        padding: 5px;
        border-radius: 3px;
    }
    QPushButton:hover { background-color: #4f5254; }
    QPushButton:pressed { background-color: #5f6264; }
    QPushButton:disabled { background-color: #454545; color: #777; border-color: #444; }
    QLabel { background-color: transparent; }
    QLabel#InfoLabel {
        font-size: 9pt;
        color: #aaa;
        background-color: transparent;
        padding-bottom: 5px;
    }
    QGraphicsView {
        border: 2px dashed #555;
        background-color: #333;
        border-radius: 5px;
    }
    QScrollBar:vertical { border: none; background: #3c3f41; width: 10px; margin: 0; }
    QScrollBar::handle:vertical { background: #5f6264; min-height: 20px; border-radius: 5px; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
    QScrollBar:horizontal { border: none; background: #3c3f41; height: 10px; margin: 0; }
    QScrollBar::handle:horizontal { background: #5f6264; min-width: 20px; border-radius: 5px; }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
    /* Style for Progress Dialog */
    QProgressDialog {
        background-color: #2b2b2b;
        color: #f0f0f0;
        border: 1px solid #555;
    }
    QProgressDialog QLabel {
         background-color: transparent;
         color: #f0f0f0;
    }
    QProgressDialog QProgressBar {
        border: 1px solid #555;
        border-radius: 3px;
        text-align: center;
        background-color: #3c3f41;
        color: #f0f0f0; /* Text color inside progress bar */
    }
    QProgressDialog QProgressBar::chunk {
        background-color: #5fa8fc; /* Progress bar fill color */
        border-radius: 3px;
    }
    QProgressDialog QPushButton { /* Style Cancel button */
        background-color: #3c3f41;
        border: 1px solid #555;
        padding: 5px;
        border-radius: 3px;
        min-width: 80px; /* Give button some width */
    }
    QProgressDialog QPushButton:hover { background-color: #4f5254; }
    QProgressDialog QPushButton:pressed { background-color: #5f6264; }
"""

# --- PortScannerWorker (Threaded) ---
class PortScannerWorker(QObject):
    finished = Signal(str) # Emits port name (e.g., "COM4") or None

    def run(self):
        # Scans all COM ports looking for the printer
        # print("Starting port scan...")
        ports = serial.tools.list_ports.comports()
        found_port = None
        for port in ports:
            # print(f"Probing port {port.device}...")
            try:
                # IMPORTANT FIX: Reverted timeout to original 0.7s for quick scanning
                ser = serial.Serial(port.device, baudrate=9600, timeout=0.7, write_timeout=0.7)
                ser.write(PROBE_PING)
                response = ser.read(100) # Read enough bytes for the expected response
                ser.close()
                if response and PROBE_PONG_EXPECTED in response:
                    print(f"SUCCESS! Printer found on {port.device}")
                    found_port = port.device
                    break # Stop searching
                # else:
                    # print(f"Response from {port.device} (or timeout): {response}")
            except (OSError, serial.SerialException) as ser_err:
                # This catches the OSError(22) or if the port is busy
                print(f"Serial Error/Busy on {port.device}: {ser_err}")
            except Exception as e:
                print(f"Unexpected error probing {port.device}: {e}")
        self.finished.emit(found_port)

# --- PrintWorker (NEW Threaded Worker for Print Job) ---
class PrintWorker(QObject):
    # Define signals for communication with the main thread
    progress_update = Signal(int)
    finished = Signal()
    error = Signal(str, str) # title, message

    def __init__(self, com_port, image_data, header_command, execute_command, final_feed_command):
        super().__init__()
        self.com_port = com_port
        self.all_image_data = image_data
        self.image_command_header = header_command
        self.execute_command = execute_command
        self.final_feed_command = final_feed_command
        self._is_canceled = False

    def cancel(self):
        """Sets the flag to stop the print loop."""
        print("PrintWorker received cancel command.")
        self._is_canceled = True

    def run(self):
        """The main printing loop, executed in the QThread."""
        printer_serial = None
        total_bytes_to_send = len(self.all_image_data)
        bytes_sent_total = 0

        # IMPORTANT FIX: Add a small pause to ensure the port is released by any previous operation
        print("PrintWorker: Waiting 0.5s before attempting to open port...")
        time.sleep(0.5)

        try:
            print(f"PrintWorker: Connecting to {self.com_port}...")
            # Use appropriate timeout for the data transfer phase
            printer_serial = serial.Serial(self.com_port, baudrate=9600, timeout=10, write_timeout=10)
            print("PrintWorker: Connected! Sending wake-up command...")
            printer_serial.write(PRINTER_HANDSHAKE); time.sleep(0.1)

            # 1. Send the SINGLE command header
            print("PrintWorker: Sending image command header...")
            printer_serial.write(self.image_command_header); printer_serial.flush()

            # --- ADD CARRIAGE RETURN ---
            print("PrintWorker: Sending Carriage Return (CR) to reset horizontal position...")
            printer_serial.write(b'\x0D') # CR character
            printer_serial.flush()
            time.sleep(0.1) # Small pause after CR
            # ---------------------------

            # 2. Send image DATA in chunks
            print(f"PrintWorker: Sending image data bytes in chunks of {CHUNK_BYTE_SIZE}...")
            for i in range(0, total_bytes_to_send, CHUNK_BYTE_SIZE):
                if self._is_canceled: 
                    print("PrintWorker: Print job canceled before completion.")
                    break # Exit the loop and go to finally

                chunk_byte_data = self.all_image_data[i : i + CHUNK_BYTE_SIZE]
                actual_chunk_byte_length = len(chunk_byte_data)
                if actual_chunk_byte_length == 0: continue

                # Send *only the data bytes*
                bytes_sent = printer_serial.write(chunk_byte_data)
                printer_serial.flush()
                bytes_sent_total += bytes_sent if bytes_sent else 0

                self.progress_update.emit(bytes_sent_total)
                time.sleep(CHUNK_DELAY_S) # Delay

            # 3. After Loop: Send Execute and Final Feed
            if not self._is_canceled:
                print("PrintWorker: All data chunks sent. Sending Execute and Feed commands...")
                printer_serial.write(self.execute_command); time.sleep(0.1)
                printer_serial.write(self.final_feed_command)
                printer_serial.flush()
                print("PrintWorker: Execute and Feed sent.")
                # Send success message through error signal with a specific title
                self.error.emit("Success", "Image sent to printer!") 
            elif self._is_canceled:
                self.error.emit("Canceled", "Print job canceled.")

        except serial.SerialTimeoutException as te:
            print(f"ERROR: Serial Timeout during write: {te}")
            self.error.emit("Print Error", f"Serial timeout while sending data.\n{te}")
        # Catch SerialException for issues opening the port (like the Semaphore timeout)
        except serial.SerialException as se:
            print(f"ERROR: Could not open port or general serial failure: {se}")
            self.error.emit("Connection Error", f"Could not connect to printer or serial failure:\n{se}")
        except Exception as e:
            print(f"ERROR: General print error: {e}")
            import traceback; traceback.print_exc()
            self.error.emit("Print Error", f"Failed to send:\n{e}")
        finally:
            # Close Serial Port robustly
            if printer_serial:
                try:
                    if printer_serial.is_open:
                        printer_serial.close()
                        print("PrintWorker: Serial connection closed.")
                except Exception as close_err:
                    print(f"ERROR closing serial port: {close_err}")
            self.finished.emit() # Signal that the thread is done

# --- Custom QGraphicsView (Handles DragDrop and Display) ---
class PrintAreaView(QGraphicsView):
    image_dropped = Signal(str) # Signal to notify the main window

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setFixedWidth(PRINTER_WIDTH_PX + 4) # 384px + border
        self.setMinimumHeight(200) # Initial size
        self.setStyleSheet("""
            QGraphicsView { border: 2px dashed #555; background-color: #333; border-radius: 5px; }
        """) # Dark style for the view
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded) # Allow vertical scroll
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded) # Allow horizontal pan
        self.setAcceptDrops(True) # IMPORTANT: Enable drop events for this widget
        self.scene = scene
        # Add placeholder text indicating accepted formats
        self.placeholder_text_item = self.scene.addText(f"Drag image here\n({ACCEPTED_FORMATS_TEXT})")
        self.placeholder_text_item.setDefaultTextColor(Qt.GlobalColor.lightGray) # Text color for dark mode
        self._center_placeholder() # Center the text initially
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform) # Nicer image scaling

    def _center_placeholder(self):
        # Helper function to center the placeholder text in the view
        bounds = self.placeholder_text_item.boundingRect()
        scene_w = PRINTER_WIDTH_PX # Use fixed width for placeholder
        scene_h = self.minimumHeight()
        center_x = (scene_w / 2) - (bounds.width() / 2)
        center_y = (scene_h / 2) - (bounds.height() / 2)
        self.placeholder_text_item.setPos(max(0, center_x), max(0, center_y)) # Place text

    def dragEnterEvent(self, event):
        mime_data = event.mimeData()
        if mime_data.hasUrls():
             event.acceptProposedAction() 
             self.setStyleSheet("border: 2px solid #5fa8fc; background-color: #444;") # Highlight border
        else:
            event.ignore() 

    def dragMoveEvent(self, event):
        mime_data = event.mimeData()
        if mime_data.hasUrls():
            event.acceptProposedAction() 
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        # Reset the border style
        self.setStyleSheet("""
            QGraphicsView { border: 2px dashed #555; background-color: #333; border-radius: 5px; }
        """)

    def dropEvent(self, event):
        mime_data = event.mimeData()

        if not mime_data.hasUrls():
            event.ignore() 
            return

        event.acceptProposedAction()
        url = mime_data.urls()[0] 
        file_path = None

        try:
            if url.isLocalFile():
                file_path = url.toLocalFile()
                if platform.system() == "Windows" and file_path.startswith('/') and not file_path.startswith('//'):
                    file_path = file_path[1:]
            elif url.scheme() == 'file': 
                 file_path = urllib.parse.unquote(url.path())
                 if platform.system() == "Windows" and file_path.startswith('/') and not file_path.startswith('//'):
                    file_path = file_path[1:]
            
            if file_path and os.path.exists(file_path):
                self.image_dropped.emit(file_path)
            elif file_path:
                 QMessageBox.warning(self.parentWidget(), "Error", f"Could not access file path (does not exist):\n{file_path}")
            else:
                QMessageBox.warning(self.parentWidget(), "Error", "Could not get file path from dropped item.")

        except Exception as e:
             print(f"ERROR processing dropped file URL: {e}")
             QMessageBox.critical(self.parentWidget(), "Drop Error", f"Error processing dropped file:\n{e}")

        self.dragLeaveEvent(event) # Reset style after processing

    def clear_view(self):
        # Resets the view to show the placeholder text
        self.scene.clear()
        self.placeholder_text_item = self.scene.addText(f"Drag image here\n({ACCEPTED_FORMATS_TEXT})")
        self.placeholder_text_item.setDefaultTextColor(Qt.GlobalColor.lightGray)
        self.scene.setSceneRect(0, 0, PRINTER_WIDTH_PX, self.minimumHeight()) # Reset scene size
        self._center_placeholder() # Recenter text

    def set_image(self, pixmap, width, height):
        # Clears the placeholder and displays the loaded image
        self.scene.clear() # Remove placeholder
        item = self.scene.addPixmap(pixmap) # Add the image
        self.scene.setSceneRect(0, 0, width, height) # Set scene size to image size
        self.viewport().update() # Force redraw


# --- Main Application Window ---
class PrintAppWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PrinterX6 Utility v2.2") # Official version name
        self.setGeometry(100, 100, 420, 550) # Adjusted height
        self.printer_com_port = None
        self.current_pil_image = None
        
        # Threads/Workers
        self.scan_thread = None
        self.scan_worker = None
        self.print_thread = None 
        self.print_worker = None
        self.progress_dialog = None # Reference to the progress dialog

        # Build Interface
        layout = QVBoxLayout()
        port_layout = QHBoxLayout()
        self.port_label = QLabel("Searching for printer...")
        self.port_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        port_layout.addWidget(self.port_label)
        self.rescan_button = QPushButton("Re-scan")
        self.rescan_button.clicked.connect(self.start_port_scan)
        port_layout.addWidget(self.rescan_button)
        layout.addLayout(port_layout)

        self.width_info_label = QLabel(f"Max Print Width: {PRINTER_WIDTH_PX}px")
        self.width_info_label.setObjectName("InfoLabel") # ID for specific styling
        self.width_info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.width_info_label)

        self.scene = QGraphicsScene()
        self.view = PrintAreaView(self.scene) # Use our custom view
        self.view.image_dropped.connect(self.load_image) # Connect its signal
        layout.addWidget(self.view)

        self.print_button = QPushButton("Print")
        self.print_button.clicked.connect(self.start_print_job)
        self.print_button.setEnabled(False) # Disabled initially
        layout.addWidget(self.print_button)

        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

        self.start_port_scan() # Initiate port scan when the app starts

    # --- Port Scan Logic ---
    def start_port_scan(self):
        # FIX: Check if the Python reference exists before checking isRunning() 
        if self.scan_thread is not None and self.scan_thread.isRunning(): 
            print("Scan already in progress."); 
            return
            
        self.port_label.setText("Scanning ports..."); 
        self.port_label.setStyleSheet("color: #ffa500;")
        self.print_button.setEnabled(False); 
        self.rescan_button.setEnabled(False)
        
        self.scan_thread = QThread(self); 
        self.scan_worker = PortScannerWorker()
        
        self.scan_worker.moveToThread(self.scan_thread)
        self.scan_thread.started.connect(self.scan_worker.run)
        
        self.scan_worker.finished.connect(self.on_scan_finished)
        
        # Cleanup connections: Quit, then delete Worker, then delete Thread
        self.scan_worker.finished.connect(self.scan_thread.quit)
        self.scan_worker.finished.connect(self.scan_worker.deleteLater)
        self.scan_thread.finished.connect(self.scan_thread.deleteLater)
        
        self.scan_thread.start()

    def on_scan_finished(self, found_port):
        self.rescan_button.setEnabled(True)
        if found_port:
            self.printer_com_port = found_port; 
            self.port_label.setText(f"Printer found on: {found_port}"); 
            self.port_label.setStyleSheet("color: #90ee90;")
            if self.current_pil_image: self.print_button.setEnabled(True)
        else:
            self.printer_com_port = None; 
            self.port_label.setText("Printer not found."); 
            self.port_label.setStyleSheet("color: #ff7f7f;")
            self.print_button.setEnabled(False)
            
        # FIX: Explicitly clear the thread reference immediately after the cleanup connections run
        self.scan_thread = None 


    # --- Image Loading Logic ---
    def load_image(self, file_path):
        try:
            Image.init()
            img_pil = Image.open(file_path)

            pil_rgb_image = None
            if img_pil.mode in ['RGBA', 'P']:
                 bg = Image.new('RGB', img_pil.size, (255, 255, 255))
                 mask = None
                 if img_pil.mode == 'RGBA': mask = img_pil.getchannel('A')
                 elif img_pil.mode == 'P':
                      if 'transparency' in img_pil.info:
                          img_pil_rgba = img_pil.convert('RGBA'); mask = img_pil_rgba.getchannel('A'); img_pil = img_pil_rgba
                      elif 'A' in img_pil.getbands():
                          img_pil_rgba = img_pil.convert('RGBA'); mask = img_pil_rgba.getchannel('A'); img_pil = img_pil_rgba
                 if mask: bg.paste(img_pil, mask=mask)
                 else: bg.paste(img_pil)
                 pil_rgb_image = bg
            else:
                 pil_rgb_image = img_pil.convert('RGB')
            if pil_rgb_image:
                 self.current_pil_image = pil_rgb_image
            else: raise ValueError("PIL RGB conversion failed.")
            
            bytes_per_line = self.current_pil_image.width * 3
            q_image = QImage(self.current_pil_image.tobytes(), self.current_pil_image.width, self.current_pil_image.height, bytes_per_line, QImage.Format.Format_RGB888)
            if q_image.isNull(): 
                raise ValueError("QImage conversion failed.")
            
            pixmap = QPixmap.fromImage(q_image)
            if pixmap.isNull(): 
                raise ValueError("QPixmap conversion failed.")
            
            self.view.set_image(pixmap, self.current_pil_image.width, self.current_pil_image.height)
            self.print_button.setEnabled(self.printer_com_port is not None)
            
        except UnidentifiedImageError:
             QMessageBox.warning(self, "Image Error", f"Cannot identify image file format.\n{ACCEPTED_FORMATS_TEXT}")
             self.print_button.setEnabled(False); self.view.clear_view()
        except FileNotFoundError:
             QMessageBox.warning(self, "Image Error", f"File not found:\n{file_path}")
             self.print_button.setEnabled(False); self.view.clear_view()
        except Exception as e:
            print(f"ERROR: General error loading image: {e}")
            import traceback; traceback.print_exc()
            QMessageBox.warning(self, "Image Error", f"Could not load image:\n{e}")
            self.print_button.setEnabled(False); self.view.clear_view()

    # --- Print Logic (Refactored to start a worker thread) ---
    def start_print_job(self):
        if self.current_pil_image is None: QMessageBox.warning(self, "Error", "No image loaded."); return
        if self.printer_com_port is None: QMessageBox.warning(self, "Error", "Printer port not found. Click 'Re-scan'."); return

        if self.print_thread is not None and self.print_thread.isRunning():
             QMessageBox.information(self, "Wait", "A print job is already in progress.")
             return

        # Pre-process image *before* starting the thread
        print("Starting print job pre-processing...")
        self.print_button.setText("Processing...");
        self.print_button.setEnabled(False); self.rescan_button.setEnabled(False)

        # 1. Horizontal Crop
        try:
            x_offset = self.view.horizontalScrollBar().value()
            crop_box_h = (x_offset, 0, x_offset + PRINTER_WIDTH_PX, self.current_pil_image.height)
            print(f"Cropping horizontally to: {crop_box_h}")
            full_cropped_image = self.current_pil_image.crop(crop_box_h)
            total_height = full_cropped_image.height
            width_bytes = (full_cropped_image.width + 7) // 8
            print(f"Total height to print: {total_height}px")

            # 2. Process the ENTIRE image
            print("Processing full image (Convert, Invert)...")
            img_converted = full_cropped_image.convert('1')
            img_final = ImageOps.invert(img_converted) # 0=White, 1=Black
            print("Formatting ALL bytes...")
            all_image_data = img_final.tobytes()

            # 3. Construct the SINGLE GS v 0 command header
            total_width_hex = width_bytes.to_bytes(2, 'big')
            total_height_hex = total_height.to_bytes(2, 'big')
            image_command_header = (
                b'\x1D\x76\x30' + total_width_hex + total_height_hex
            )
            print(f"Command header created for {width_bytes} bytes wide, {total_height} px high.")

        except Exception as e:
            QMessageBox.critical(self, "Image Processing Error", f"Failed to process image: {e}")
            self.print_button.setText("Print"); self.print_button.setEnabled(True); self.rescan_button.setEnabled(True)
            return

        # 4. Prepare and Start Threaded Print Job
        total_bytes_to_send = len(all_image_data)
        
        # Setup Progress Dialog
        self.progress_dialog = QProgressDialog("Sending image data...", "Cancel", 0, total_bytes_to_send, self)
        self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress_dialog.setMinimumDuration(0)
        self.progress_dialog.setValue(0)
        self.progress_dialog.setAutoClose(False) 
        self.progress_dialog.show()
        # Connect the cancel button action to the worker's cancel method
        self.progress_dialog.canceled.connect(self.on_print_canceled) 
        
        # Setup Worker and Thread
        self.print_thread = QThread(self)
        self.print_worker = PrintWorker(
            self.printer_com_port, 
            all_image_data, 
            image_command_header, 
            PRINTER_EXECUTE, 
            FINAL_FEED_COMMAND
        )
        self.print_worker.moveToThread(self.print_thread)

        # Connect Signals/Slots
        self.print_thread.started.connect(self.print_worker.run)
        self.print_worker.progress_update.connect(self.progress_dialog.setValue)
        self.print_worker.error.connect(self.on_print_error) 
        self.print_worker.finished.connect(self.on_print_finished)
        
        # Cleanup connections
        self.print_worker.finished.connect(self.print_thread.quit)
        self.print_worker.finished.connect(self.print_worker.deleteLater)
        self.print_thread.finished.connect(self.print_thread.deleteLater)

        # Start the thread
        print("Starting print thread...")
        self.print_button.setText("Printing...")
        self.print_thread.start()

    def on_print_canceled(self):
        """Called when the user clicks 'Cancel' on the progress dialog."""
        if self.print_worker:
            self.print_worker.cancel() # Set flag in worker to stop loop

    def on_print_error(self, title, message):
        """Handles messages from the print worker (Success, Error, or Canceled)."""
        if title == "Success":
            QMessageBox.information(self, title, message)
        elif title == "Canceled":
            QMessageBox.warning(self, title, message)
        else: # Print Error or Connection Error
            QMessageBox.critical(self, title, message)
        
    def on_print_finished(self):
        """Final cleanup steps once the PrintWorker thread has finished."""
        print("Print thread finished. Cleaning up UI...")
        
        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None
        
        self.print_button.setText("Print")
        # Only re-enable print button if port is found AND image is loaded
        can_print = self.printer_com_port is not None and self.current_pil_image is not None
        self.print_button.setEnabled(can_print)
        self.rescan_button.setEnabled(True)
        
        # FIX: Explicitly clear the thread reference immediately after the cleanup connections run
        self.print_thread = None
        self.print_worker = None


# --- Application Entry Point ---
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET) # Apply the dark theme
    window = PrintAppWindow() # Create our main window instance
    window.show() # Show the window
    sys.exit(app.exec()) # Start the Qt event loop