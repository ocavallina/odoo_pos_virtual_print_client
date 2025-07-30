# Manual de Implementación - Sistema ESC/POS Termux

## 1. Requisitos Previos

### Hardware
- Dispositivo Android 7+ con 2GB RAM mínimo
- Impresoras ESC/POS conectadas a red (puerto 9100)
- Red WiFi estable

### Software
- Termux (desde F-Droid)
- Termux:Boot (desde F-Droid/Play Store)
- Servidor Odoo con módulo pos_virtual_print

## 2. Instalación Automática

### Script de instalación rápida
```bash
#!/bin/bash
# install.sh
set -e

echo "🚀 Iniciando instalación..."

# Actualizar sistema
pkg update && pkg upgrade -y

# Instalar dependencias
pkg install python git openssh screen -y

# Instalar librerías Python
pip install aiohttp python-escpos

echo "✅ Dependencias instaladas"

# Configurar proyecto
mkdir -p ~/impresoras_pos/logs
cd ~/impresoras_pos

# Hacer scripts ejecutables
chmod +x start_service.sh

# Configurar autoboot
mkdir -p ~/.termux/boot
cp termux/boot/start_impresoras ~/.termux/boot/
chmod +x ~/.termux/boot/start_impresoras

# Configurar aliases
cat termux/.bashrc >> ~/.bashrc

echo "✅ Instalación completada"
echo "📝 Siguiente: Configurar config.json"
```

## 3. Configuración

### 3.1 Configurar Impresoras (config.json)
```json
{
  "printers": [
    {
      "name": "COCINA",
      "ip": "192.168.1.100",
      "port": 9100,
      "token": "uuid-desde-odoo",
      "active": true,
      "keep_alive_interval": 120
    }
  ]
}
```

### 3.2 Variables de Entorno
```bash
# En start_service.sh modificar:
ODOO_URL="https://tu-servidor.com"
```

### 3.3 Configurar Autoboot
1. Instalar app "Termux:Boot"
2. Abrir Termux:Boot y activar
3. Script ya está en `~/.termux/boot/start_impresoras`

## 4. Comandos de Gestión

### Scripts principales
```bash
# Iniciar servicio
./start_service.sh start

# Detener servicio  
./start_service.sh stop

# Reiniciar servicio
./start_service.sh restart

# Ver estado
./start_service.sh status

# Ver logs en tiempo real
./start_service.sh logs

# Monitor con reinicio automático
./start_service.sh monitor
```

### Aliases (después de source ~/.bashrc)
```bash
imp-start     # Iniciar
imp-stop      # Detener  
imp-restart   # Reiniciar
imp-status    # Estado
imp-logs      # Ver logs
imp-screen    # Conectar a sesión screen
```

### Gestión con Screen
```bash
# Conectar a sesión activa
screen -r impresoras

# Desconectar sin cerrar (Ctrl+A, D)
# Crear nueva sesión
screen -S impresoras -d -m ./start_service.sh monitor
```

## 5. Niveles de Logging

### Control de verbosidad
```bash
# Mínimo (solo errores críticos)
LOG_LEVEL=ERROR ./start_service.sh restart

# Normal (recomendado para producción)
LOG_LEVEL=WARNING ./start_service.sh restart

# Informativo (para monitoreo)
LOG_LEVEL=INFO ./start_service.sh restart

# Debug completo (para desarrollo)
LOG_LEVEL=DEBUG ./start_service.sh restart
```

### Archivos de log
- `logs/service.log` - Log principal con rotación diaria
- `logs/detailed.log` - Log detallado (solo en DEBUG)
- `logs/autostart.log` - Log de autoboot

## 6. Verificación de Funcionamiento

### Test básico
```bash
cd ~/impresoras_pos

# 1. Verificar dependencias
python -c "import aiohttp, escpos; print('✅ OK')"

# 2. Test de configuración
./start_service.sh start
./start_service.sh status

# 3. Verificar logs
tail -f logs/service.log
```

### Test de conectividad
```bash
# Test ping a impresora
ping 192.168.1.100

# Test puerto impresora
nc -zv 192.168.1.100 9100

# Test conexión Odoo
curl -I https://tu-servidor.com
```

### Test autoboot
1. Cerrar Termux completamente
2. Reabrir Termux
3. Ejecutar: `screen -r impresoras`
4. Debe mostrar servicio activo

## 7. Estructura de Archivos

```
~/impresoras_pos/
├── async_client.py          # Cliente principal
├── config.json              # Configuración impresoras
├── start_service.sh         # Script gestión
├── install.sh               # Script instalación
├── service.pid              # PID servicio (auto)
├── logs/                    # Logs (auto)
├── status/                  # Estado sistema (auto)
└── termux/
    ├── .bashrc              # Aliases
    └── boot/
        └── start_impresoras # Script autoboot

~/.termux/boot/
└── start_impresoras         # Script autoboot (copia)
```

## 8. Troubleshooting

### Servicio no inicia
```bash
# Verificar permisos
ls -la start_service.sh

# Ver error específico
LOG_LEVEL=DEBUG ./start_service.sh restart
tail logs/service.log
```

### Autoboot no funciona
```bash
# Verificar app Termux:Boot instalada
# Verificar script existe
ls -la ~/.termux/boot/start_impresoras

# Test manual
~/.termux/boot/start_impresoras
```

### Impresora no responde
```bash
# Test básico
ping IP_IMPRESORA
nc -zv IP_IMPRESORA 9100

# Verificar en logs
grep -i "error\|timeout" logs/service.log
```

### Sin conexión Odoo
```bash
# Test HTTPS
curl -I https://tu-servidor.com

# Verificar token en config.json
# Ver logs conexión
grep -i "http\|odoo" logs/service.log
```

## 9. Mantenimiento

### Rotación de logs
- Automática cada 7 días
- Máximo 10MB por archivo detallado
- Configurable en async_client.py

### Limpieza manual
```bash
# Limpiar logs antiguos
rm logs/*.log.*

# Limpiar PID huérfano
rm service.pid

# Reinicio completo
./start_service.sh stop
sleep 5
./start_service.sh start
```

### Actualización
```bash
# Detener servicio
./start_service.sh stop

# Actualizar archivos
# (copiar nuevos async_client.py, etc.)

# Reiniciar
./start_service.sh start
```

## 10. Configuración Avanzada

### Optimización RAM limitada
```bash
# En config.json reducir:
"keep_alive_interval": 300
"connection_timeout": 5

# Usar logging mínimo
LOG_LEVEL=ERROR ./start_service.sh restart
```

### Múltiples instancias
```bash
# Crear directorios separados
mkdir ~/impresoras_pos_local1
mkdir ~/impresoras_pos_local2

# Usar configs diferentes
# Modificar nombres de PID files
```

### Monitoreo externo
```bash
# Estado JSON disponible en:
cat status/system_status.json

# Para dashboard externo via SSH
ssh user@device "cat ~/impresoras_pos/status/system_status.json"
```

## 11. Checklist de Implementación

### Pre-instalación
- [ ] Termux instalado desde F-Droid
- [ ] Termux:Boot instalado y activado
- [ ] Red WiFi configurada
- [ ] IPs de impresoras conocidas

### Instalación
- [ ] Ejecutar install.sh
- [ ] Configurar config.json con tokens Odoo
- [ ] Modificar URL Odoo en start_service.sh
- [ ] Test básico: `./start_service.sh start`

### Post-instalación
- [ ] Verificar autoboot funciona
- [ ] Configurar aliases: `source ~/.bashrc`
- [ ] Test impresión desde Odoo
- [ ] Configurar nivel logging producción

### Producción
- [ ] Monitor automático: `./start_service.sh monitor`
- [ ] Verificar logs periódicamente
- [ ] Backup de config.json
- [ ] Documentar tokens y configuración específica
