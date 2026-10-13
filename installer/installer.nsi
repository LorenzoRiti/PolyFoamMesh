; PolyFoamMesh NSIS Installer
; Enterprise-grade Windows installer with:
;   - Silent mode for GPO deployment
;   - Custom installation directory
;   - Start Menu shortcuts
;   - Desktop shortcut (optional)
;   - File association (.step, .stp, .stl)
;   - WSL2 + OpenFOAM dependency check
;   - Uninstaller with registry cleanup

!define PRODUCT_NAME "PolyFoamMesh"
!define PRODUCT_VERSION "2.0.1"
!define PRODUCT_PUBLISHER "PolyFoamMesh Project"
!define PRODUCT_WEB_SITE "https://github.com/polyfoammesh"
!define PRODUCT_DIR_REGKEY "Software\Microsoft\Windows\CurrentVersion\App Paths\PolyFoamMesh.exe"
!define PRODUCT_UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}"

SetCompressor lzma
RequestExecutionLevel admin

; Modern UI
!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"

; MUI Settings
!define MUI_ABORTWARNING
!define MUI_ICON "..\build\icon.ico"
!define MUI_UNICON "..\build\icon.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP "..\build\installer_banner.bmp"

; Pages
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
Page custom PageWslCheck PageWslCheckLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; Languages
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Italian"
!insertmacro MUI_LANGUAGE "German"
!insertmacro MUI_LANGUAGE "French"
!insertmacro MUI_LANGUAGE "Spanish"

; =============================================================
; Installer sections
; =============================================================

Section "PolyFoamMesh Core" SEC_MAIN
    SectionIn RO
    SetOutPath "$INSTDIR"

    ; Main executable and libraries
    File /r "..\dist\PolyFoamMesh\*.*"

    ; Templates
    SetOutPath "$INSTDIR\templates"
    File /r "..\templates\*.*"

    ; Plugins
    SetOutPath "$INSTDIR\plugins"
    File /r "..\plugins\*.*"

    ; Locales
    SetOutPath "$INSTDIR\locale"
    File /r "..\locale\*.*"

    ; Documentation
    SetOutPath "$INSTDIR\docs"
    File "..\README.md"
    File "..\LICENSE"

    ; Create shortcuts
    CreateDirectory "$SMPROGRAMS\${PRODUCT_NAME}"
    CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\PolyFoamMesh.lnk" "$INSTDIR\PolyFoamMesh.exe" "" "$INSTDIR\PolyFoamMesh.exe" 0
    CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall.lnk" "$INSTDIR\Uninstall.exe" "" "$INSTDIR\Uninstall.exe" 0

    ; Register application paths
    WriteRegStr HKLM "${PRODUCT_DIR_REGKEY}" "" "$INSTDIR\PolyFoamMesh.exe"
    WriteRegStr HKLM "${PRODUCT_DIR_REGKEY}" "Path" "$INSTDIR"

    ; File associations
    WriteRegStr HKCR ".step" "" "PolyFoamMesh.STEP"
    WriteRegStr HKCR "PolyFoamMesh.STEP" "" "STEP Model File"
    WriteRegStr HKCR "PolyFoamMesh.STEP\DefaultIcon" "" "$INSTDIR\PolyFoamMesh.exe,0"
    WriteRegStr HKCR "PolyFoamMesh.STEP\shell\open\command" "" '"$INSTDIR\PolyFoamMesh.exe" "%1"'

    WriteRegStr HKCR ".stp" "" "PolyFoamMesh.STP"
    WriteRegStr HKCR "PolyFoamMesh.STP" "" "STEP Model File"
    WriteRegStr HKCR "PolyFoamMesh.STP\DefaultIcon" "" "$INSTDIR\PolyFoamMesh.exe,0"
    WriteRegStr HKCR "PolyFoamMesh.STP\shell\open\command" "" '"$INSTDIR\PolyFoamMesh.exe" "%1"'

    WriteRegStr HKCR ".stl" "" "PolyFoamMesh.STL"
    WriteRegStr HKCR "PolyFoamMesh.STL" "" "STL Mesh File"
    WriteRegStr HKCR "PolyFoamMesh.STL\DefaultIcon" "" "$INSTDIR\PolyFoamMesh.exe,0"
    WriteRegStr HKCR "PolyFoamMesh.STL\shell\open\command" "" '"$INSTDIR\PolyFoamMesh.exe" "%1"'

    ; Uninstaller registry
    WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayName" "${PRODUCT_NAME}"
    WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayVersion" "${PRODUCT_VERSION}"
    WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
    WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "URLInfoAbout" "${PRODUCT_WEB_SITE}"
    WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayIcon" "$INSTDIR\PolyFoamMesh.exe"
    WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "UninstallString" "$INSTDIR\Uninstall.exe"
    WriteRegDWORD HKLM "${PRODUCT_UNINST_KEY}" "NoModify" 1
    WriteRegDWORD HKLM "${PRODUCT_UNINST_KEY}" "NoRepair" 1

    ; Estimate size
    ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
    IntFmt $0 "0x%08X" $0
    WriteRegDWORD HKLM "${PRODUCT_UNINST_KEY}" "EstimatedSize" "$0"

    ; Write uninstaller
    WriteUninstaller "$INSTDIR\Uninstall.exe"
SectionEnd

Section "Desktop Shortcut" SEC_DESKTOP
    CreateShortCut "$DESKTOP\PolyFoamMesh.lnk" "$INSTDIR\PolyFoamMesh.exe" "" "$INSTDIR\PolyFoamMesh.exe" 0
SectionEnd

Section "OpenFOAM Tutorial Cases" SEC_TUTORIALS
    SetOutPath "$INSTDIR\tutorials"
    File /r "..\sample_cad\*.*"
SectionEnd

; =============================================================
; WSL2 / OpenFOAM dependency check (custom page)
; =============================================================

Var WSL_CHECK_RESULT

Function PageWslCheck
    !insertmacro MUI_HEADER_TEXT "Dependency Check" "Checking WSL2 and OpenFOAM availability"
    nsExec::ExecToStack 'wsl.exe -d Ubuntu -- bash -lc "source /usr/lib/openfoam/openfoam2512/etc/bashrc 2>/dev/null && which cartesianMesh"'
    Pop $0
    Pop $1
    StrCmp $0 "0" wsl_ok wsl_missing

wsl_ok:
    StrCpy $WSL_CHECK_RESULT "✓ WSL2 + OpenFOAM v2512 detected"
    Goto wsl_done

wsl_missing:
    StrCpy $WSL_CHECK_RESULT "⚠ WSL2 or OpenFOAM not found. Install WSL2 + OpenFOAM v2512 manually."

wsl_done:
    !insertmacro MUI_INSTALLOPTIONS_WRITE "ioSpecial.ini" "Field 1" "Text" $WSL_CHECK_RESULT
FunctionEnd

Function PageWslCheckLeave
    ; Allow installation to proceed regardless of WSL status
FunctionEnd

; =============================================================
; Uninstaller
; =============================================================

Section "Uninstall"
    ; Remove shortcuts
    RmDir /r "$SMPROGRAMS\${PRODUCT_NAME}"
    Delete "$DESKTOP\PolyFoamMesh.lnk"

    ; Remove application files
    RmDir /r "$INSTDIR"

    ; Remove file associations
    DeleteRegKey HKCR ".step"
    DeleteRegKey HKCR ".stp"
    DeleteRegKey HKCR ".stl"
    DeleteRegKey HKCR "PolyFoamMesh.STEP"
    DeleteRegKey HKCR "PolyFoamMesh.STP"
    DeleteRegKey HKCR "PolyFoamMesh.STL"

    ; Remove registry keys
    DeleteRegKey HKLM "${PRODUCT_DIR_REGKEY}"
    DeleteRegKey HKLM "${PRODUCT_UNINST_KEY}"

    ; Remove user settings (optional)
    ; RmDir /r "$APPDATA\cfmesh-autogui"
SectionEnd
