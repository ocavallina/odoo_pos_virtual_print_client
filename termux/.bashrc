# Aliases simples para servicio de impresoras
alias imp-start='cd ~/impresoras_pos && ./start_service.sh start'
alias imp-stop='cd ~/impresoras_pos && ./start_service.sh stop'
alias imp-restart='cd ~/impresoras_pos && ./start_service.sh restart'
alias imp-status='cd ~/impresoras_pos && ./start_service.sh status'
alias imp-logs='cd ~/impresoras_pos && ./start_service.sh logs'
alias imp-monitor='cd ~/impresoras_pos && ./start_service.sh monitor'

# Sesión screen
alias imp-screen='screen -r impresoras'

# Logs con más detalle
alias imp-debug='cd ~/impresoras_pos && LOG_LEVEL=INFO ./start_service.sh restart'
alias imp-quiet='cd ~/impresoras_pos && LOG_LEVEL=WARNING ./start_service.sh restart'

# Ver logs específicos
alias imp-tail='tail -f ~/impresoras_pos/logs/service.log'
alias imp-errors='grep -i "error\|❌" ~/impresoras_pos/logs/service.log | tail -10'

# Dashboard
alias imp-dashboard='cd ~/impresoras_pos && python dashboard.py'

echo "🖨️  Aliases cargados: imp-start, imp-stop, imp-status, imp-logs, imp-screen, imp-dashboard"