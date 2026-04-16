# PrinterX6_Utility.spec

# --- Add imports needed to find PySide6 plugins ---
import os
import PySide6
# --- End added imports ---

# --- Define version info DIRECTLY in the spec file ---
from PyInstaller.utils.win32.versioninfo import VSVersionInfo, StringFileInfo, StringTable, StringStruct, FixedFileInfo, VarFileInfo, VarStruct

filevers = (1, 0, 0, 0)
prodvers = (1, 0, 0, 0)
company_name = 'Edgar Quex'
product_name = 'PrinterX6 Utility'
file_description = 'Print Utility for X6'
copyright_info = 'Edgar Quex (eaquex@gmail.com)'

version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=filevers,
        prodvers=prodvers,
        mask=0x3f, # VS_FFI_FILEFLAGSMASK [cite: 1]
        flags=0x0, # VS_FFI_FILEFLAGS [cite: 1]
        OS=0x40004, # VOS_NT_WINDOWS32 [cite: 1]
        fileType=0x1, # VFT_APP [cite: 1]
        subtype=0x0, # VFT2_UNKNOWN [cite: 2]
        date=(0, 0) # [cite: 2]
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    '040904B0', # Lang: US English, Charset: Unicode [cite: 2]
                    [
                        StringStruct('CompanyName', company_name), # [cite: 3]
                        StringStruct('FileDescription', file_description), # [cite: 3]
                        StringStruct('FileVersion', '1.0.0.0'), # [cite: 3]
                        StringStruct('InternalName', 'PrinterX6_Utility'), # [cite: 4]
                        StringStruct('LegalCopyright', copyright_info), # [cite: 4]
                        StringStruct('OriginalFilename', 'PrinterX6_Utility.exe'), # [cite: 4]
                        StringStruct('ProductName', product_name), # [cite: 4]
                        StringStruct('ProductVersion', '1.0.0.0') # [cite: 5]
                    ]
                )
            ]
        ),
        VarFileInfo(
            [
                VarStruct(
                    'Translation', # [cite: 6]
                    [0x0409, 0x04B0] # 0409 = US English, 04B0 = Unicode [cite: 6]
                )
            ]
        )
    ]
)
# --- End of version info definition ---


# --- Standard PyInstaller setup ---
block_cipher = None # 

# --- Find PySide6 directory dynamically ---
pyside6_dir = os.path.dirname(PySide6.__file__)
# --- End PySide6 directory find ---

a = Analysis(
    ['gui_driver.py'], # Points to your main script 
    pathex=['.'], # Keep this, just in case 
    binaries=[], # 
    # --- MODIFIED DATAS SECTION TO INCLUDE PLUGINS ---
    datas=[
        # Include the 'platforms' plugin directory (essential for Windows integration)
        (os.path.join(pyside6_dir, 'plugins', 'platforms'), os.path.join('PySide6', 'plugins', 'platforms')),
        # Include 'styles' plugins (might be needed for consistent theming)
        (os.path.join(pyside6_dir, 'plugins', 'styles'), os.path.join('PySide6', 'plugins', 'styles')),
        # Include 'imageformats' plugins (ensures Qt can handle internal image operations)
        (os.path.join(pyside6_dir, 'plugins', 'imageformats'), os.path.join('PySide6', 'plugins', 'imageformats')),
    ],
    # --------------------------------------------------
    hiddenimports=['serial', 'PIL', 'PySide6'], # 
    hookspath=[], # 
    hooksconfig={}, # 
    runtime_hooks=[], # 
    excludes=[], # 
    win_no_prefer_redirects=False, # 
    win_private_assemblies=False, # 
    cipher=block_cipher, # 
    noarchive=False, # 
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher) # 

exe = EXE(
    pyz, # [cite: 8]
    a.scripts, # [cite: 8]
    a.binaries, # [cite: 8]
    a.zipfiles, # [cite: 8]
    a.datas, # [cite: 8]
    [], # [cite: 8]
    name='PrinterX6_Utility', # [cite: 8]
    debug=False, # [cite: 8]
    bootloader_ignore_signals=False, # [cite: 8]
    strip=False, # [cite: 8]
    upx=True, # [cite: 8]
    upx_exclude=[], # [cite: 8]
    runtime_tmpdir=None, # [cite: 8]
    console=False, # This is --noconsole [cite: 8]
    disable_windowed_traceback=False, # [cite: 8]
    argv_emulation=False, # [cite: 8]
    target_arch=None, # [cite: 8]
    codesign_identity=None, # [cite: 8]
    entitlements_file=None, # [cite: 8]
    version=version_info, # This now refers to the object defined above [cite: 8]
)