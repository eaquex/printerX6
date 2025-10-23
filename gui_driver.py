# gui_driver.py (v1.5 - Final Preview Alignment Fix - COMPLETE ENGLISH)
import sys
import serial
import serial.tools.list_ports
import time
from PIL import Image, ImageOps, UnidentifiedImageError
import platform
import urllib.parse
import os
import logging
import traceback

# --- Setup Logging ---
log_file = 'logs.txt'
try:
    # Set level to INFO for less verbose logs in production, DEBUG for development
    log_level = logging.INFO
    # Uncomment the next line to enable detailed DEBUG logging
    # log_level = logging.DEBUG
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(levelname)s - [%(threadName)s] - %(message)s',
        filename=log_file,
        filemode='w' # Overwrite log file each time
    )
    logging.info("Application started. Logging configured to %s (Level: %s)", log_file, logging.getLevelName(log_level))
except Exception as log_err:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(threadName)s] - %(message)s') # Use INFO for fallback too
    logging.error("File logging to %s failed (%s), falling back to console.", log_file, log_err, exc_info=True)
# --- End Logging Setup ---

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QVBoxLayout, QHBoxLayout,
    QWidget, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QMessageBox, QLabel, QProgressDialog, QRadioButton, QButtonGroup
)
from PySide6.QtGui import QPixmap, QImage, QPainter
from PySide6.QtCore import Qt, QSize, QThread, QObject, Signal, QUrl, QRectF # Added QRectF

# --- Constants ---
PRINTER_WIDTH_PX = 384
PRINTER_HANDSHAKE = b"MHV=H1.0,SV=V1.01,VOLT=8000mv,DPI=384,\n"
PRINTER_EXECUTE = b"LABELAT1\n"
FINAL_FEED_COMMAND = b"\x1B\x64\x06" # ESC d 6
PROBE_PING = b'\x1E\x47\x03'
PROBE_PONG_EXPECTED = b"HV=H1.0"
ACCEPTED_FORMATS_TEXT = "Accepted: PNG, JPG, GIF, WebP"
CHUNK_BYTE_SIZE = 48 * 16 # 768 bytes
CHUNK_DELAY_S = 0.08

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
    QProgressDialog { background-color: #2b2b2b; color: #f0f0f0; border: 1px solid #555; }
    QProgressDialog QLabel { background-color: transparent; color: #f0f0f0; }
    QProgressDialog QProgressBar { border: 1px solid #555; border-radius: 3px; text-align: center; background-color: #3c3f41; color: #f0f0f0; }
    QProgressDialog QProgressBar::chunk { background-color: #5fa8fc; border-radius: 3px; }
    QProgressDialog QPushButton { background-color: #3c3f41; border: 1px solid #555; padding: 5px; border-radius: 3px; min-width: 80px; }
    QProgressDialog QPushButton:hover { background-color: #4f5254; }
    QProgressDialog QPushButton:pressed { background-color: #5f6264; }
    /* Style for Radio Buttons */
    QRadioButton { color: #f0f0f0; background-color: transparent; padding: 2px; }
    QRadioButton::indicator { width: 12px; height: 12px; border: 1px solid #777; border-radius: 6px; background-color: #444; }
    QRadioButton::indicator:checked { background-color: #5fa8fc; border: 1px solid #5fa8fc; }
    QRadioButton::indicator:unchecked:hover { border: 1px solid #aaa; }
    QRadioButton::indicator:checked:hover { border: 1px solid #7fceff; }
"""
logging.debug("Constants and Stylesheet defined.")

# --- PortScannerWorker (Threaded) ---
class PortScannerWorker(QObject):
    finished = Signal(str)
    def run(self):
        logging.info("PortScannerWorker starting run.")
        ports = serial.tools.list_ports.comports()
        found_port = None
        for port in ports:
            logging.debug(f"Probing port {port.device}...")
            try:
                ser = serial.Serial(port.device, baudrate=9600, timeout=0.7, write_timeout=0.7)
                ser.write(PROBE_PING)
                response = ser.read(100)
                ser.close()
                if response and PROBE_PONG_EXPECTED in response:
                    logging.info(f"SUCCESS! Printer found on {port.device}")
                    found_port = port.device
                    break
                else: logging.debug(f"Response from {port.device} (or timeout): {response.hex() if response else 'None'}")
            except (OSError, serial.SerialException) as ser_err: logging.warning(f"Serial Error/Busy on {port.device}: {ser_err}")
            except Exception: logging.exception(f"Unexpected error probing {port.device}")
        logging.info(f"Port scan finished. Found port: {found_port}")
        self.finished.emit(found_port)

# --- PrintWorker (Threaded Worker for Print Job) ---
class PrintWorker(QObject):
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
        logging.debug("PrintWorker initialized.")

    def cancel(self):
        logging.warning("PrintWorker received cancel command.")
        self._is_canceled = True

    def run(self):
        logging.info("PrintWorker starting run.")
        printer_serial = None
        total_bytes_to_send = len(self.all_image_data)
        bytes_sent_total = 0
        logging.info("PrintWorker: Waiting 0.5s before attempting to open port...")
        time.sleep(0.5)

        try:
            logging.info(f"PrintWorker: Connecting to {self.com_port}...")
            printer_serial = serial.Serial(self.com_port, baudrate=9600, timeout=10, write_timeout=10)
            logging.info("PrintWorker: Connected! Sending wake-up command...")
            printer_serial.write(PRINTER_HANDSHAKE); time.sleep(0.1)

            # 1. Send the SINGLE command header
            logging.debug("PrintWorker: Sending image command header...")
            printer_serial.write(self.image_command_header); printer_serial.flush()

            # --- ADD CARRIAGE RETURN ---
            logging.debug("PrintWorker: Sending Carriage Return (CR) to reset horizontal position...")
            printer_serial.write(b'\x0D'); printer_serial.flush(); time.sleep(0.1)
            # ---------------------------

            # 2. Send image DATA in chunks
            logging.debug(f"PrintWorker: Sending image data bytes in chunks of {CHUNK_BYTE_SIZE}...")
            for i in range(0, total_bytes_to_send, CHUNK_BYTE_SIZE):
                if self._is_canceled: logging.warning("PrintWorker: Canceled."); break

                chunk_byte_data = self.all_image_data[i : i + CHUNK_BYTE_SIZE]
                actual_chunk_byte_length = len(chunk_byte_data)
                if actual_chunk_byte_length == 0: continue

                logging.debug(f"Sending data chunk index {i} ({actual_chunk_byte_length} bytes)")
                bytes_sent = printer_serial.write(chunk_byte_data)
                printer_serial.flush()
                bytes_sent_total += bytes_sent if bytes_sent else 0
                logging.debug(f"Chunk sent ({bytes_sent} bytes written). Total sent: {bytes_sent_total}")

                self.progress_update.emit(bytes_sent_total)
                time.sleep(CHUNK_DELAY_S)

            # 3. After Loop: Send Execute and Final Feed
            if not self._is_canceled:
                logging.info("PrintWorker: Sending Execute and Feed commands...")
                printer_serial.write(self.execute_command); time.sleep(0.1)
                printer_serial.write(self.final_feed_command); printer_serial.flush()
                logging.info("PrintWorker: Execute and Feed sent.")
                self.error.emit("Success", "Image sent to printer!")
            elif self._is_canceled:
                self.error.emit("Canceled", "Print job canceled.")

        except serial.SerialTimeoutException as te:
            logging.error(f"Serial Timeout during write: {te}")
            self.error.emit("Print Error", f"Serial timeout while sending data.\n{te}")
        except serial.SerialException as se:
            logging.error(f"Could not open port or general serial failure: {se}")
            self.error.emit("Connection Error", f"Could not connect to printer or serial failure:\n{se}")
        except Exception:
            logging.exception("General print error in PrintWorker")
            self.error.emit("Print Error", "An unexpected error occurred during printing.")
        finally:
            if printer_serial:
                try:
                    if printer_serial.is_open: printer_serial.close(); logging.info("PrintWorker: Serial connection closed.")
                except Exception as close_err: logging.error(f"Error closing serial port: {close_err}")
            logging.info("PrintWorker finished.")
            self.finished.emit()


# --- Custom QGraphicsView (Handles DragDrop, Display, Alignment FIX) ---
class PrintAreaView(QGraphicsView):
    image_dropped = Signal(str)

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setFixedWidth(PRINTER_WIDTH_PX + 4)
        self.setMinimumHeight(200)
        self.setStyleSheet("""
            QGraphicsView { border: 2px dashed #555; background-color: #333; border-radius: 5px; }
        """)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setAcceptDrops(True)
        self.scene = scene
        self.current_pixmap_item = None # Store the item to move it
        self.placeholder_text_item = self.scene.addText(f"Drag image here\n({ACCEPTED_FORMATS_TEXT})")
        self.placeholder_text_item.setDefaultTextColor(Qt.GlobalColor.lightGray)
        # --- FIX: Set initial scene rect to fixed width ---
        self.scene.setSceneRect(0, 0, PRINTER_WIDTH_PX, self.minimumHeight())
        self._center_placeholder() # Center placeholder within fixed width
        # --- END FIX ---
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        logging.debug(f"PrintAreaView initialized, acceptDrops={self.acceptDrops()}")

    def _center_placeholder(self):
        bounds = self.placeholder_text_item.boundingRect()
        scene_w = PRINTER_WIDTH_PX
        # Use sceneRect().height() if valid, otherwise minimumHeight
        scene_h = self.sceneRect().height() if not self.sceneRect().isNull() and self.sceneRect().isValid() else self.minimumHeight()
        center_x = (scene_w / 2) - (bounds.width() / 2)
        center_y = (scene_h / 2) - (bounds.height() / 2)
        self.placeholder_text_item.setPos(max(0, center_x), max(0, center_y))

    def dragEnterEvent(self, event):
        logging.debug("dragEnterEvent triggered!")
        mime_data = event.mimeData()
        if mime_data.hasUrls() and mime_data.hasFormat('text/uri-list'):
             logging.debug(f"MimeData has URLs: {mime_data.urls()}")
             event.acceptProposedAction(); self.setStyleSheet("border: 2px solid #5fa8fc; background-color: #444;")
             logging.debug("dragEnterEvent accepted.")
        else:
            logging.warning(f"dragEnterEvent ignored. Has URLs: {mime_data.hasUrls()}, Has text/uri-list: {mime_data.hasFormat('text/uri-list')}")
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls() and event.mimeData().hasFormat('text/uri-list'): event.acceptProposedAction()
        else: event.ignore()

    def dragLeaveEvent(self, event):
        logging.debug("dragLeaveEvent triggered.")
        self.setStyleSheet(""" QGraphicsView { border: 2px dashed #555; background-color: #333; border-radius: 5px; } """)

    def dropEvent(self, event):
        logging.info("--- Drop Event Triggered ---")
        mime_data = event.mimeData()
        if not mime_data.hasUrls() or not mime_data.hasFormat('text/uri-list'):
            logging.warning("Drop ignored - MimeData lacks URLs or correct format in dropEvent."); event.ignore(); return
        event.acceptProposedAction(); logging.debug("dropEvent accepted.")

        logging.debug(f"Raw URLs in drop: {mime_data.urls()}")
        url = mime_data.urls()[0]
        logging.debug(f"First URL object: {url}, Scheme: {url.scheme()}")
        file_path = None
        try:
            if url.isLocalFile():
                logging.debug("URL is local file. Using toLocalFile()")
                file_path = url.toLocalFile()
                if platform.system() == "Windows" and file_path.startswith('/') and not file_path.startswith('//'): file_path = file_path[1:]
            elif url.scheme() == 'file':
                 logging.debug("URL scheme is 'file'. Using path() and unquote.")
                 file_path = urllib.parse.unquote(url.path())
                 if platform.system() == "Windows" and file_path.startswith('/') and not file_path.startswith('//'): file_path = file_path[1:]
            else: logging.warning(f"Unsupported URL scheme received: {url.scheme()}")

            logging.debug(f"Path before validation: {file_path}")

            if file_path and os.path.exists(file_path):
                logging.info(f"Path confirmed. Emitting signal: {file_path}")
                self.image_dropped.emit(file_path)
            elif file_path:
                 logging.error(f"Path determined but does not exist: {file_path}")
                 QMessageBox.warning(self.parentWidget(), "Error", f"Could not access file path (does not exist):\n{file_path}")
            else:
                logging.error("Could not determine valid file path from dropped item.")
                QMessageBox.warning(self.parentWidget(), "Error", "Could not get file path from dropped item.")
        except Exception as e:
             logging.exception("ERROR processing dropped file URL")
             QMessageBox.critical(self.parentWidget(), "Drop Error", f"Error processing dropped file:\n{e}")

        self.dragLeaveEvent(event)

    def clear_view(self):
        logging.debug("Clearing image view.")
        self.scene.clear()
        self.current_pixmap_item = None
        self.placeholder_text_item = self.scene.addText(f"Drag image here\n({ACCEPTED_FORMATS_TEXT})")
        self.placeholder_text_item.setDefaultTextColor(Qt.GlobalColor.lightGray)
        # --- FIX: Reset scene rect to default fixed width ---
        self.scene.setSceneRect(0, 0, PRINTER_WIDTH_PX, self.minimumHeight())
        self._center_placeholder()
        # --- END FIX ---

    # --- MODIFIED set_image ---
    def set_image(self, pixmap, width, height):
        logging.debug("Setting image in view.")
        self.scene.clear() # Clear previous items

        self.current_pixmap_item = self.scene.addPixmap(pixmap) # Store the item
        if not self.current_pixmap_item: logging.error("FAILED to add pixmap item to scene.")
        else: logging.debug("Pixmap added to scene.")

        # --- FIX: Determine Scene Rect Width based on Image Width ---
        # If image is wider than printer, scene must be wide enough to scroll over
        # If image is narrower, scene stays at printer width for alignment reference
        scene_width = max(width, PRINTER_WIDTH_PX)
        self.scene.setSceneRect(0, 0, scene_width, height)
        logging.debug(f"Scene rect set to 0,0,{scene_width},{height}")
        # --- END FIX ---

        # The alignment method will correctly position the item OR allow panning
        # The main window calls apply_current_alignment right after this

        self.viewport().update() # Force redraw
        logging.debug("Viewport update requested after setting image.")
    # --- END MODIFIED ---

    # --- MODIFIED align_pixmap_item (Corrected Logic) ---
    def align_pixmap_item(self, alignment: Qt.AlignmentFlag):
        """Aligns the pixmap item based on requested alignment, relative to PRINTER_WIDTH_PX."""
        logging.debug(f"Aligning pixmap item within view to: {alignment}")
        if self.current_pixmap_item:
            image_width = self.current_pixmap_item.pixmap().width()
            # Reference width is always the printer width for item positioning calculations
            reference_width = PRINTER_WIDTH_PX

            x_pos = 0 # Default Left (AlignLeft)
            # Calculate offset only if image is narrower than the reference width
            if image_width < reference_width:
                if alignment == Qt.AlignmentFlag.AlignCenter:
                    x_pos = (reference_width - image_width) / 2
                elif alignment == Qt.AlignmentFlag.AlignRight:
                    x_pos = reference_width - image_width
            # If image is wider or equal, x_pos remains 0. Panning is handled by scrollbar.

            # Set the pixmap item's position relative to the scene's top-left (0,0)
            self.current_pixmap_item.setPos(x_pos, 0)
            logging.debug(f"Pixmap item position set to ({x_pos}, 0) relative to scene for alignment {alignment}")
        else:
            logging.debug("No current pixmap item to align.")
    # --- END MODIFIED ---


# --- Main Application Window ---
class PrintAppWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PrinterX6 Utility v1.0") # Official version name
        self.setGeometry(100, 100, 420, 600) # Increased height
        self.printer_com_port = None
        self.current_pil_image = None # Stores the full PIL image
        self.scan_thread = None; self.scan_worker = None
        self.print_thread = None; self.print_worker = None
        self.progress_dialog = None

        # Build Interface
        layout = QVBoxLayout()
        # Port Scan Section
        port_layout = QHBoxLayout()
        self.port_label = QLabel("Searching for printer...")
        self.port_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        port_layout.addWidget(self.port_label)
        self.rescan_button = QPushButton("Re-scan")
        self.rescan_button.clicked.connect(self.start_port_scan)
        port_layout.addWidget(self.rescan_button)
        layout.addLayout(port_layout)
        # Width Info
        self.width_info_label = QLabel(f"Max Print Width: {PRINTER_WIDTH_PX}px")
        self.width_info_label.setObjectName("InfoLabel")
        self.width_info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.width_info_label)
        # Graphics View
        self.scene = QGraphicsScene()
        self.view = PrintAreaView(self.scene)
        self.view.image_dropped.connect(self.load_image)
        layout.addWidget(self.view)
        # Alignment Radio Buttons
        align_layout = QHBoxLayout()
        align_label = QLabel("Alignment / Pan:") # Renamed label
        align_layout.addWidget(align_label)
        self.align_group = QButtonGroup(self)
        self.radio_left = QRadioButton("Left")
        self.radio_center = QRadioButton("Center")
        self.radio_right = QRadioButton("Right")
        self.radio_left.setChecked(True) # Default
        self.align_group.addButton(self.radio_left)
        self.align_group.addButton(self.radio_center)
        self.align_group.addButton(self.radio_right)
        align_layout.addWidget(self.radio_left)
        align_layout.addWidget(self.radio_center)
        align_layout.addWidget(self.radio_right)
        align_layout.addStretch()
        layout.addLayout(align_layout)
        # Connect signals to the unified alignment handler
        self.radio_left.toggled.connect(self.apply_current_alignment)
        self.radio_center.toggled.connect(self.apply_current_alignment)
        self.radio_right.toggled.connect(self.apply_current_alignment)
        # Print Button
        self.print_button = QPushButton("Print")
        self.print_button.clicked.connect(self.start_print_job)
        self.print_button.setEnabled(False)
        layout.addWidget(self.print_button)
        # Final Setup
        central_widget = QWidget(); central_widget.setLayout(layout); self.setCentralWidget(central_widget)
        logging.info("Main window UI created.")
        self.start_port_scan()

    # --- Port Scan Logic ---
    # (No changes)
    def start_port_scan(self):
        if self.scan_thread is not None and self.scan_thread.isRunning(): logging.info("Scan already in progress."); return
        logging.info("Initiating port scan...")
        self.port_label.setText("Scanning ports..."); self.port_label.setStyleSheet("color: #ffa500;")
        self.print_button.setEnabled(False); self.rescan_button.setEnabled(False)
        self.scan_thread = QThread(self); self.scan_worker = PortScannerWorker()
        self.scan_worker.moveToThread(self.scan_thread)
        self.scan_thread.started.connect(self.scan_worker.run)
        self.scan_worker.finished.connect(self.on_scan_finished)
        self.scan_worker.finished.connect(self.scan_thread.quit)
        self.scan_worker.finished.connect(self.scan_worker.deleteLater)
        self.scan_thread.finished.connect(self.scan_thread.deleteLater)
        self.scan_thread.start()

    def on_scan_finished(self, found_port):
        logging.info(f"Port scan finished callback. Found port: {found_port}")
        self.rescan_button.setEnabled(True)
        if found_port:
            self.printer_com_port = found_port; self.port_label.setText(f"Printer found on: {found_port}"); self.port_label.setStyleSheet("color: #90ee90;")
            if self.current_pil_image: self.print_button.setEnabled(True)
        else:
            self.printer_com_port = None; self.port_label.setText("Printer not found."); self.port_label.setStyleSheet("color: #ff7f7f;")
            self.print_button.setEnabled(False)
        self.scan_thread = None; self.scan_worker = None


    # --- Image Loading Logic ---
    def load_image(self, file_path):
        logging.info(f"load_image called with path: {file_path}")
        try:
            Image.init()
            img_pil = Image.open(file_path)
            logging.info(f"PIL opened. Format: {img_pil.format}, Mode: {img_pil.mode}, Size: {img_pil.size}")

            # --- Conversion to RGB (Same as before) ---
            pil_rgb_image = None
            if img_pil.mode in ['RGBA', 'P']:
                 logging.debug("Converting RGBA/P to RGB w/ white bg.")
                 bg = Image.new('RGB', img_pil.size, (255, 255, 255)); mask = None
                 if img_pil.mode == 'RGBA': mask = img_pil.getchannel('A')
                 elif img_pil.mode == 'P':
                      if 'transparency' in img_pil.info: img_pil_rgba = img_pil.convert('RGBA'); mask = img_pil_rgba.getchannel('A'); img_pil = img_pil_rgba
                      elif 'A' in img_pil.getbands(): img_pil_rgba = img_pil.convert('RGBA'); mask = img_pil_rgba.getchannel('A'); img_pil = img_pil_rgba
                 if mask: bg.paste(img_pil, mask=mask)
                 else: bg.paste(img_pil)
                 pil_rgb_image = bg
            else:
                logging.debug("Converting image to RGB directly.")
                pil_rgb_image = img_pil.convert('RGB')
            if not pil_rgb_image: raise ValueError("PIL RGB conversion failed.")
            self.current_pil_image = pil_rgb_image # Store the full RGB PIL image
            logging.debug(f"Stored PIL image. Size: {self.current_pil_image.size}")
            # --- End Conversion ---

            # --- Convert PIL to QPixmap for display ---
            logging.debug("Converting PIL RGB to QImage...")
            bytes_per_line = self.current_pil_image.width * 3
            q_image = QImage(self.current_pil_image.tobytes(), self.current_pil_image.width, self.current_pil_image.height, bytes_per_line, QImage.Format.Format_RGB888)
            if q_image.isNull(): logging.error("QImage is null!"); raise ValueError("QImage conversion failed.")
            pixmap = QPixmap.fromImage(q_image)
            if pixmap.isNull(): logging.error("QPixmap is null!"); raise ValueError("QPixmap conversion failed.")
            logging.debug(f"QPixmap created. Size: {pixmap.size()}")
            # --- End QPixmap Conversion ---

            logging.debug("Calling view.set_image()...")
            self.view.set_image(pixmap, self.current_pil_image.width, self.current_pil_image.height)
            # --- Apply initial alignment/panning ---
            self.apply_current_alignment() # Use the unified handler
            # ------------------------------------
            self.print_button.setEnabled(self.printer_com_port is not None)
            logging.info("Image loaded and displayed successfully.")

        # --- Error Handling (Same as before) ---
        except UnidentifiedImageError:
             logging.error(f"PIL cannot identify format: {file_path}")
             QMessageBox.warning(self, "Image Error", f"Cannot identify image file format.\n{ACCEPTED_FORMATS_TEXT}")
             self.print_button.setEnabled(False); self.view.clear_view()
        except FileNotFoundError:
             logging.error(f"File not found: {file_path}")
             QMessageBox.warning(self, "Image Error", f"File not found:\n{file_path}")
             self.print_button.setEnabled(False); self.view.clear_view()
        except Exception as e:
            logging.exception("General error loading image")
            QMessageBox.warning(self, "Image Error", f"Could not load image:\n{e}")
            self.print_button.setEnabled(False); self.view.clear_view()

    # --- MODIFIED: Unified Alignment Handler ---
    def apply_current_alignment(self):
        """
        Applies alignment/panning based on radio buttons.
        Handles both narrow and wide images correctly now.
        """
        if not self.current_pil_image or not self.view.current_pixmap_item:
            logging.debug("apply_current_alignment called with no image/pixmap loaded.")
            return

        image_width = self.current_pil_image.width
        view_width = PRINTER_WIDTH_PX

        alignment_flag = Qt.AlignmentFlag.AlignLeft # Default for narrow case / logging
        scroll_value = 0 # Default scroll position

        if image_width > view_width:
            # --- WIDE IMAGE: Control scrollbar ---
            max_scroll = image_width - view_width
            logging.debug(f"Wide image (Width: {image_width}). Max scroll: {max_scroll}")
            if self.radio_center.isChecked():
                scroll_value = max_scroll // 2; alignment_flag = Qt.AlignmentFlag.AlignCenter; logging.debug("Applying Center Pan.")
            elif self.radio_right.isChecked():
                scroll_value = max_scroll; alignment_flag = Qt.AlignmentFlag.AlignRight; logging.debug("Applying Right Pan.")
            else: # Left
                scroll_value = 0; logging.debug("Applying Left Pan.")
            # Set the scrollbar value which pans the view
            self.view.horizontalScrollBar().setValue(scroll_value)
            # Ensure pixmap item itself is at 0,0 within its (wider) scene
            # Needed in case user switched from narrow alignment before
            self.view.align_pixmap_item(Qt.AlignmentFlag.AlignLeft)

        else:
            # --- NARROW IMAGE: Control pixmap item position ---
            logging.debug(f"Narrow image (Width: {image_width}). Applying item alignment.")
            if self.radio_center.isChecked(): alignment_flag = Qt.AlignmentFlag.AlignCenter
            elif self.radio_right.isChecked(): alignment_flag = Qt.AlignmentFlag.AlignRight
            # Call the view's method to move the item within the fixed scene width reference
            self.view.align_pixmap_item(alignment_flag)
            # Ensure scrollbar is reset
            self.view.horizontalScrollBar().setValue(0)

        logging.info(f"Alignment/Pan applied: {alignment_flag}, Scrollbar value: {self.view.horizontalScrollBar().value()}")
    # --- END MODIFIED ---

    # --- Print Logic ---
    # (Modified to use selected alignment for narrow images - same logic as v1.2)
    def start_print_job(self):
        if self.current_pil_image is None: QMessageBox.warning(self, "Error", "No image loaded."); return
        if self.printer_com_port is None: QMessageBox.warning(self, "Error", "Printer port not found. Click 'Re-scan'."); return
        if self.print_thread is not None and self.print_thread.isRunning():
             logging.warning("Attempted print job while another running."); QMessageBox.information(self, "Wait", "Print job already in progress."); return

        logging.info("Starting print job pre-processing...")
        self.print_button.setText("Processing..."); self.print_button.setEnabled(False); self.rescan_button.setEnabled(False)
        QApplication.processEvents()

        try:
            # 1. Horizontal Crop (Uses scrollbar value determined by alignment/pan)
            x_offset = self.view.horizontalScrollBar().value()
            right_bound = min(x_offset + PRINTER_WIDTH_PX, self.current_pil_image.width)
            crop_width = right_bound - x_offset
            if crop_width <= 0: raise ValueError("Crop width zero or negative.")
            crop_box_h = (x_offset, 0, right_bound, self.current_pil_image.height)
            logging.debug(f"Cropping horizontally based on scroll offset {x_offset} to: {crop_box_h} (width={crop_width})")
            cropped_image_maybe_narrow = self.current_pil_image.crop(crop_box_h)
            total_height = cropped_image_maybe_narrow.height
            logging.info(f"Cropped image size (pre-padding): {cropped_image_maybe_narrow.size}")

            # 2. Create 384px wide canvas AND ALIGN IF NEEDED based on radio buttons
            image_to_process = None
            paste_position = (0, 0) # Default Left
            if cropped_image_maybe_narrow.width < PRINTER_WIDTH_PX:
                logging.info(f"Image width {cropped_image_maybe_narrow.width}px < {PRINTER_WIDTH_PX}px. Aligning on white canvas.")
                canvas = Image.new('RGB', (PRINTER_WIDTH_PX, total_height), (255, 255, 255)) # White
                space_diff = PRINTER_WIDTH_PX - cropped_image_maybe_narrow.width
                if self.radio_center.isChecked():
                    paste_position = (space_diff // 2, 0); logging.debug("Print Alignment: Center")
                elif self.radio_right.isChecked():
                    paste_position = (space_diff, 0); logging.debug("Print Alignment: Right")
                else: logging.debug("Print Alignment: Left") # Left is default (0, 0)
                logging.debug(f"Pasting image onto canvas at {paste_position}")
                canvas.paste(cropped_image_maybe_narrow, paste_position)
                image_to_process = canvas
            else:
                logging.debug("Image width is sufficient (>= 384px). Processing directly.")
                image_to_process = cropped_image_maybe_narrow

            if image_to_process is None: raise ValueError("Image to process is None.")
            width_bytes = (PRINTER_WIDTH_PX + 7) // 8
            logging.info(f"Image to process final size: {image_to_process.size}")

            # 3. Process the image_to_process (Convert, Invert)
            logging.debug("Processing final image (Convert, Invert)...")
            img_converted = image_to_process.convert('1'); img_final = ImageOps.invert(img_converted)
            logging.debug("Formatting ALL bytes..."); all_image_data = img_final.tobytes()
            logging.info(f"Total image data bytes: {len(all_image_data)}")

            # 4. Construct the SINGLE GS v 0 command header
            logging.debug("Constructing single GS v 0 header...")
            total_width_hex = width_bytes.to_bytes(2, 'big'); total_height_hex = total_height.to_bytes(2, 'big')
            image_command_header = ( b'\x1D\x76\x30' + total_width_hex + total_height_hex )
            logging.debug(f"Header created for {width_bytes*8} px wide, {total_height} px high.")

        except Exception as e:
            logging.exception("Failed during image pre-processing")
            QMessageBox.critical(self, "Image Processing Error", f"Failed to process image: {e}")
            self.print_button.setText("Print"); self.print_button.setEnabled(True); self.rescan_button.setEnabled(True)
            return

        # 5. Prepare and Start Threaded Print Job
        total_bytes_to_send = len(all_image_data)
        self.progress_dialog = QProgressDialog("Sending image data...", "Cancel", 0, total_bytes_to_send, self)
        self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal); self.progress_dialog.setMinimumDuration(0)
        self.progress_dialog.setValue(0); self.progress_dialog.setAutoClose(False); self.progress_dialog.show()
        self.progress_dialog.canceled.connect(self.on_print_canceled)

        self.print_thread = QThread(self)
        self.print_worker = PrintWorker( # Pass necessary data to worker
            self.printer_com_port, all_image_data, image_command_header,
            PRINTER_EXECUTE, FINAL_FEED_COMMAND
        )
        self.print_worker.moveToThread(self.print_thread)
        self.print_thread.started.connect(self.print_worker.run)
        self.print_worker.progress_update.connect(self.progress_dialog.setValue)
        self.print_worker.error.connect(self.on_print_error_or_success)
        self.print_worker.finished.connect(self.on_print_finished)
        self.print_worker.finished.connect(self.print_thread.quit)
        self.print_worker.finished.connect(self.print_worker.deleteLater)
        self.print_thread.finished.connect(self.print_thread.deleteLater)

        logging.info("Starting print thread...")
        self.print_button.setText("Printing...")
        self.print_thread.start()

    # --- Event Handlers (No changes) ---
    def on_print_canceled(self):
        logging.warning("Cancel button pressed.")
        if self.print_worker: self.print_worker.cancel()

    def on_print_error_or_success(self, title, message):
        logging.info(f"Print worker message received: Title='{title}', Message='{message}'")
        if self.progress_dialog: self.progress_dialog.close()
        if title == "Success": QMessageBox.information(self, title, message)
        elif title == "Canceled": QMessageBox.warning(self, title, message)
        else: QMessageBox.critical(self, title, message)

    def on_print_finished(self):
        logging.info("Print thread finished signal received. Cleaning up UI...")
        if self.progress_dialog: self.progress_dialog = None
        self.print_button.setText("Print")
        can_print = self.printer_com_port is not None and self.current_pil_image is not None
        self.print_button.setEnabled(can_print)
        self.rescan_button.setEnabled(True)
        self.print_thread = None; self.print_worker = None
        logging.debug("Print thread references cleared.")


# --- Application Entry Point ---
if __name__ == "__main__":
    try:
        logging.info("Initializing QApplication.")
        app = QApplication(sys.argv)
        app.setStyleSheet(DARK_STYLESHEET)
        logging.info("Creating main window.")
        window = PrintAppWindow()
        window.show()
        logging.info("Application startup complete. Entering event loop.")
        sys.exit(app.exec())
    except Exception as e:
        logging.exception("Unhandled exception during application startup or execution.")
        try: # Try to show error box
            error_box = QMessageBox(); error_box.setIcon(QMessageBox.Icon.Critical)
            error_box.setWindowTitle("Fatal Error"); error_box.setText(f"A critical error occurred:\n{e}\n\nCheck logs.txt for details.")
            error_box.exec()
        except: pass # If GUI fails too
        sys.exit(1)