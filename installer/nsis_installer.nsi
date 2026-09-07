; NSIS installer for PolyFoamMesh v2.0
; Requires: NSIS 3.x

!define PRODUCT_NAME "PolyFoamMesh"
!define PRODUCT_VERSION "2.0.0"
!define PRODUCT_PUBLISHER "Davide Valoroso"
!define PRODUCT_WEB_SITE "https://polyfoammesh.local"

SetCompressor lzma

Name "${PRODUCT_NAME} ${PRODUCT_VERSION}"
OutFile "PolyFoamMesh-${PRODUCT_VERSION}-Setup.exe"
InstallDir "$PROGRAMFILES64\${PRODUCT_NAME}"
RequestExecutionLevel admin

Section "Install"
    SetOutPath "$INSTDIR"
    
    File /r "..\src\cfmesh_autogui\*.py"
    File "..\pyproject.toml"
    File "..\README.md"
    
    SetOutPath "$INSTDIR\templates"
    File /r "..\templates\*.json"
    
    SetOutPath "$INSTDIR\locale"
    File /r "..\locale\*"
    
    ; Create desktop shortcut
    CreateShortCut "$DESKTOP\${PRODUCT_NAME}.lnk" "$INSTDIR\run_app.bat" "" "$INSTDIR\icon.ico"
    
    ; Create start menu entry
    CreateDirectory "$SMPROGRAMS\${PRODUCT_NAME}"
    CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk" "$INSTDIR\run_app.bat"
    CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall.lnk" "$INSTDIR\uninst.exe"
    
    ; Write uninstaller
    WriteUninstaller "$INSTDIR\uninst.exe"
    
    ; Write registry keys
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}" \
        "DisplayName" "${PRODUCT_NAME} ${PRODUCT_VERSION}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}" \
        "UninstallString" "$INSTDIR\uninst.exe"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}" \
        "Publisher" "${PRODUCT_PUBLISHER}"
SectionEnd

Section "Uninstall"
    Delete "$DESKTOP\${PRODUCT_NAME}.lnk"
    RMDir /r "$SMPROGRAMS\${PRODUCT_NAME}"
    RMDir /r "$INSTDIR"
    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}"
SectionEnd
