#!/data/data/com.termux/files/usr/bin/bash
# Script simple para gestionar servicio de impresoras
PROJECT_DIR="$HOME/impresoras_pos"
SCRIPT_NAME="async_client.py"
CONFIG_FILE="config.json"
ODOO_URL="https://thepoint.ottavioit.com"
PID_FILE="$PROJECT_DIR/service.pid"

# Función para verificar si está corriendo
is_running() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            return 0
        else
            rm -f "$PID_FILE"
            return 1
        fi
    fi
    return 1
}

# Iniciar servicio
start() {
    if is_running; then
        echo "✅ Servicio ya está corriendo (PID: $(cat $PID_FILE))"
        return 0
    fi

    cd "$PROJECT_DIR" || exit 1

    echo "🚀 Iniciando servicio..."
    termux-wake-lock 2>/dev/null

    # Configurar logging (WARNING por defecto, INFO si se especifica)
    LOG_LEVEL=${LOG_LEVEL:-DEBUG}

    nohup python "$SCRIPT_NAME" \
        --url "$ODOO_URL" \
        --config "$CONFIG_FILE" \
        --log-level "$LOG_LEVEL" \
        --termux \
        > /dev/null 2>&1 &

    PID=$!
    echo $PID > "$PID_FILE"
    sleep 2

    if ps -p "$PID" > /dev/null 2>&1; then
        echo "✅ Servicio iniciado (PID: $PID)"
    else
        echo "❌ Error iniciando servicio"
        rm -f "$PID_FILE"
        return 1
    fi
}

# Detener servicio
stop() {
    if is_running; then
        PID=$(cat "$PID_FILE")
        echo "🛑 Deteniendo servicio (PID: $PID)..."
        kill -TERM "$PID" 2>/dev/null
        sleep 3
        if ps -p "$PID" > /dev/null 2>&1; then
            kill -KILL "$PID" 2>/dev/null
        fi
        rm -f "$PID_FILE"
        termux-wake-unlock 2>/dev/null
        echo "✅ Servicio detenido"
    else
        echo "ℹ️  Servicio no estaba corriendo"
    fi
}

# Ver estado
status() {
    if is_running; then
        PID=$(cat "$PID_FILE")
        echo "✅ Servicio corriendo (PID: $PID)"

        # Mostrar archivos de log recientes
        if [ -d "logs" ]; then
            echo "📁 Logs disponibles:"
            ls -lt logs/*.log 2>/dev/null | head -3
        fi
    else
        echo "❌ Servicio NO está corriendo"
    fi
}

# Ver logs
logs() {
    if [ -f "logs/service.log" ]; then
        echo "📖 Logs del servicio (Ctrl+C para salir):"
        tail -f logs/service.log
    else
        echo "❌ No se encontraron logs"
    fi
}

# Monitor con reinicio automático
monitor() {
    echo "🔍 Monitor iniciado (Ctrl+C para detener)"

    while true; do
        if ! is_running; then
            echo "⚠️  Servicio caído, reiniciando..."
            start
        fi
        sleep 60
    done
}

case "$1" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        stop
        sleep 2
        start
        ;;
    status)
        status
        ;;
    logs)
        logs
        ;;
    monitor)
        monitor
        ;;
    *)
        echo "🖨️  Servicio de Impresoras ESC/POS"
        echo ""
        echo "Uso: $0 {start|stop|restart|status|logs|monitor}"
        echo ""
        echo "Comandos:"
        echo "  start    - Iniciar servicio"
        echo "  stop     - Detener servicio"
        echo "  restart  - Reiniciar servicio"
        echo "  status   - Ver estado"
        echo "  logs     - Ver logs en tiempo real"
        echo "  monitor  - Vigilar y reiniciar automáticamente"
        echo ""
        echo "Variables de entorno:"
        echo "  LOG_LEVEL=INFO ./start_service.sh start   # Para más logs"
        exit 1
        ;;
esac