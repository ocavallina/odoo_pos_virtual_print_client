#!/data/data/com.termux/files/usr/bin/python
"""
Cliente Asíncrono Optimizado para Termux/Android - VERSIÓN CON CONTROL DE LOGS
============================================================================
Versión con control de verbosidad y rotación automática de logs
"""

import json
import time
import socket
import logging
import argparse
import asyncio
import aiohttp
import subprocess
import signal
import sys
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler, RotatingFileHandler

# Imports para impresoras
try:
    from escpos.printer import Network
    from escpos.exceptions import Error as EscposError
    ESCPOS_AVAILABLE = True
except ImportError:
    ESCPOS_AVAILABLE = False
    print("⚠️  Instalar: pip install python-escpos")
    sys.exit(1)

# Activar wakelock automáticamente
def setup_wakelock(enable_termux=False):
    """Configura wakelock para mantener dispositivo activo"""
    
    if not enable_termux:
        print("🔋 Wakelock deshabilitado (no es Termux)")
        return False
    
    try:
        subprocess.run(['termux-wake-lock'], check=False, capture_output=True)
        print("🔋 Wakelock activado")
        return True
    except:
        print("⚠️  No se pudo activar wakelock")
        return False

def cleanup_wakelock(enable_termux=False):
    """Libera wakelock al terminar"""
    
    if not enable_termux:
        return

    try:
        subprocess.run(['termux-wake-unlock'], check=False, capture_output=True)
        print("🔋 Wakelock liberado")
    except:
        pass

# Manejador de señales para cierre elegante
class GracefulKiller:
    kill_now = False
    def __init__(self):
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        print(f'\n📢 Señal {signum} recibida, cerrando elegantemente...')
        self.kill_now = True

@dataclass
class PrinterConfig:
    """Configuración mejorada de impresora"""
    name: str
    ip: str
    port: int
    token: str
    active: bool = True
    max_retries: int = 3
    retry_delay: int = 2
    connection_timeout: int = 8
    keep_alive_interval: int = 300
    max_idle_time: int = 600

@dataclass
class PrinterStatus:
    """Estado detallado de impresora"""
    name: str
    ip: str
    token: str
    last_successful_connection: datetime = field(default_factory=datetime.now)
    last_keep_alive: datetime = field(default_factory=datetime.now)
    last_job_printed: datetime = field(default_factory=datetime.now)
    consecutive_failures: int = 0
    is_healthy: bool = True
    last_error: str = ""
    total_jobs_printed: int = 0
    total_keep_alives_sent: int = 0
    total_keep_alives_failed: int = 0
    average_response_time: float = 0.0
    last_response_time: float = 0.0

    def to_dict(self):
        """Convierte a diccionario para JSON"""
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        return data

@dataclass
class FailedJob:
    """Trabajo fallido para reintento"""
    job_id: int
    printer_token: str
    job_data: Dict
    attempts: int = 0
    last_attempt: datetime = field(default_factory=datetime.now)
    next_retry: datetime = field(default_factory=datetime.now)

class StatusExporter:
    """Exporta estado del sistema para dashboard"""
    
    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.status_file = self.base_dir / "status" / "system_status.json"
        self.status_file.parent.mkdir(exist_ok=True)
    
    def export_status(self, client_instance):
        """Exporta estado actual del sistema"""
        now = datetime.now()
        
        system_status = {
            "timestamp": now.isoformat(),
            "uptime_seconds": (now - client_instance.stats['start_time']).total_seconds(),
            "service_healthy": client_instance.running and not client_instance.killer.kill_now,
            "stats": client_instance.stats.copy(),
            "failed_jobs_count": len(client_instance.failed_jobs),
            "active_printers_count": len([p for p in client_instance.printer_status.values() if p.is_healthy]),
            "total_printers_count": len(client_instance.printer_status)
        }
        
        if 'start_time' in system_status['stats']:
            system_status['stats']['start_time'] = system_status['stats']['start_time'].isoformat()
        if 'last_activity' in system_status['stats']:
            system_status['stats']['last_activity'] = system_status['stats']['last_activity'].isoformat()
        
        printers_status = {}
        for token, status in client_instance.printer_status.items():
            printers_status[token] = status.to_dict()
        
        failed_jobs = []
        for job_id, failed_job in client_instance.failed_jobs.items():
            failed_jobs.append({
                "job_id": job_id,
                "printer_token": failed_job.printer_token,
                "attempts": failed_job.attempts,
                "last_attempt": failed_job.last_attempt.isoformat(),
                "next_retry": failed_job.next_retry.isoformat()
            })
        
        full_status = {
            "system": system_status,
            "printers": printers_status,
            "failed_jobs": failed_jobs,
            "last_update": now.isoformat()
        }
        
        try:
            with open(self.status_file, 'w', encoding='utf-8') as f:
                json.dump(full_status, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error exportando estado: {e}")

class AsyncClient:
    """Cliente asíncrono mejorado con control de logging avanzado"""
    
    def __init__(self, odoo_url: str, check_interval: int = 3, log_level: str = 'INFO', 
                 log_rotation_days: int = 7, log_max_size_mb: int = 10, enable_termux: bool = False):
        self.odoo_url = odoo_url.rstrip('/')
        self.check_interval = check_interval
        self.log_level = log_level.upper()
        self.log_rotation_days = log_rotation_days
        self.log_max_size_mb = log_max_size_mb
        
        self.printers: Dict[str, PrinterConfig] = {}
        self.printer_status: Dict[str, PrinterStatus] = {}

        self.enable_termux = enable_termux
        
        # Sistema de reintentos
        self.failed_jobs: Dict[int, FailedJob] = {}
        self.max_failed_jobs = 50
        self.retry_intervals = [30, 60, 120, 300, 600]
        
        # Pool de threads
        self.print_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="printer")
        
        # Control de ejecución
        self.running = False
        self.killer = GracefulKiller()
        
        # Estadísticas
        self.stats = {
            'jobs_processed': 0,
            'jobs_failed': 0,
            'jobs_retried': 0,
            'keep_alives_sent': 0,
            'keep_alives_failed': 0,
            'connections_restored': 0,
            'start_time': datetime.now(),
            'last_activity': datetime.now(),
            'total_errors': 0,
            'total_warnings': 0
        }
        
        # Control de keep-alive
        self.last_keep_alive_check = datetime.now()
        self.keep_alive_interval = 60
        
        # Exportador de estado
        self.status_exporter = StatusExporter(Path.cwd() )
        
        # Setup logging con configuraciones
        self.setup_logging()
        
        # Logs de inicio según nivel
        if self.logger.isEnabledFor(logging.INFO):
            self.logger.info("🚀 Cliente ESC/POS iniciado")
            self.logger.info(f"🌐 Odoo URL: {odoo_url}")
            self.logger.info(f"⏱️  Intervalo: {check_interval}s")
            self.logger.info(f"📝 Log level: {log_level}")
            self.logger.info(f"🔄 Rotación: {log_rotation_days} días")
        
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(f"💓 Keep-alive: {self.keep_alive_interval}s")
            self.logger.debug(f"📊 Dashboard export: Activado")
            self.logger.debug(f"📱 Entorno: Termux Android")
        
        print(f"🚀 Cliente ESC/POS iniciado - Log: {log_level}")
        print(f"🌐 Odoo: {odoo_url}")
        print(f"⏱️  Intervalo: {check_interval}s")
    
    def setup_logging(self):
        """Configura logging con rotación y niveles personalizables"""
        log_dir = Path.cwd() / "logs"
        log_dir.mkdir(exist_ok=True)
        
        # Configurar nivel numérico
        numeric_level = getattr(logging, self.log_level, logging.INFO)
        
        # Crear logger personalizado
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(numeric_level)
        
        # Limpiar handlers existentes
        self.logger.handlers.clear()
        
        # Crear handlers con rotación
        handlers = []
        
        # Handler principal con rotación por tiempo (diaria)
        main_log = log_dir / "service.log"
        main_handler = TimedRotatingFileHandler(
            main_log,
            when='midnight',
            interval=1,
            backupCount=self.log_rotation_days,
            encoding='utf-8'
        )
        main_handler.setLevel(numeric_level)
        main_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        handlers.append(main_handler)
        
        # Handler detallado con rotación por tamaño (solo si DEBUG)
        if numeric_level <= logging.DEBUG:
            detailed_log = log_dir / "detailed.log"
            detailed_handler = RotatingFileHandler(
                detailed_log,
                maxBytes=self.log_max_size_mb * 1024 * 1024,
                backupCount=3,
                encoding='utf-8'
            )
            detailed_handler.setLevel(logging.DEBUG)
            detailed_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
            handlers.append(detailed_handler)
        
        # Handler de consola
        console_handler = logging.StreamHandler(sys.stdout)
        console_level = logging.WARNING if numeric_level > logging.INFO else logging.INFO
        console_handler.setLevel(console_level)
        console_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        handlers.append(console_handler)
        
        # Agregar handlers al logger
        for handler in handlers:
            self.logger.addHandler(handler)
        
        # Evitar propagación al root logger
        self.logger.propagate = False
        
        # Log de configuración
        if self.logger.isEnabledFor(logging.INFO):
            self.logger.info("📝 Sistema de logging configurado:")
            self.logger.info(f"   📊 Nivel: {self.log_level}")
            self.logger.info(f"   🔄 Rotación: {self.log_rotation_days} días")
            self.logger.info(f"   📁 Directorio: {log_dir}")
            
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(f"   📄 Log principal: {main_log}")
            if numeric_level <= logging.DEBUG:
                self.logger.debug(f"   📋 Log detallado: {detailed_log} ({self.log_max_size_mb}MB)")
            self.logger.debug(f"   🖥️  Consola: {console_level}")
    
    def load_config(self, config_file: str) -> bool:
        """Carga configuración con logging adaptativo"""
        if self.logger.isEnabledFor(logging.INFO):
            self.logger.info(f"📂 Cargando configuración: {config_file}")
        
        try:
            config_path = Path(config_file)
            if not config_path.exists():
                self.logger.error(f"❌ Config no encontrado: {config_file}")
                return False
            
            if self.logger.isEnabledFor(logging.DEBUG):
                self.logger.debug(f"Archivo encontrado, tamaño: {config_path.stat().st_size} bytes")
            
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            
            if self.logger.isEnabledFor(logging.DEBUG):
                self.logger.debug(f"JSON cargado, claves: {list(config_data.keys())}")
            
            if 'printers' not in config_data:
                self.logger.error("❌ Config inválido: falta 'printers'")
                return False
            
            printer_count = len(config_data['printers'])
            if self.logger.isEnabledFor(logging.INFO):
                self.logger.info(f"📋 Procesando {printer_count} impresoras")
            
            for i, printer_data in enumerate(config_data['printers']):
                try:
                    if self.logger.isEnabledFor(logging.DEBUG):
                        self.logger.debug(f"Procesando impresora {i+1}: {printer_data.get('name', 'SIN_NOMBRE')}")
                    
                    printer = PrinterConfig(
                        name=printer_data['name'],
                        ip=printer_data['ip'],
                        port=printer_data.get('port', 9100),
                        token=printer_data['token'],
                        active=printer_data.get('active', True),
                        max_retries=printer_data.get('max_retries', 3),
                        retry_delay=printer_data.get('retry_delay', 2),
                        connection_timeout=printer_data.get('connection_timeout', 8),
                        keep_alive_interval=printer_data.get('keep_alive_interval', 300),
                        max_idle_time=printer_data.get('max_idle_time', 600)
                    )
                    
                    if printer.active:
                        self.printers[printer.token] = printer
                        self.printer_status[printer.token] = PrinterStatus(
                            name=printer.name,
                            ip=printer.ip,
                            token=printer.token
                        )
                        
                        if self.logger.isEnabledFor(logging.INFO):
                            self.logger.info(f"📎 {printer.name}: {printer.ip}:{printer.port}")
                        
                        if self.logger.isEnabledFor(logging.DEBUG):
                            self.logger.debug(f"   Token: {printer.token[:8]}...")
                            self.logger.debug(f"   Keep-alive: {printer.keep_alive_interval}s")
                            self.logger.debug(f"   Max reintentos: {printer.max_retries}")
                    else:
                        if self.logger.isEnabledFor(logging.WARNING):
                            self.logger.warning(f"⚠️  {printer.name} configurada pero INACTIVA")
                    
                except KeyError as e:
                    self.logger.error(f"❌ Impresora {i+1} inválida, falta: {e}")
                    self.stats['total_errors'] += 1
                    continue
                except Exception as e:
                    self.logger.error(f"❌ Error procesando impresora {i+1}: {e}")
                    self.stats['total_errors'] += 1
                    continue
            
            if not self.printers:
                self.logger.error("❌ No se cargaron impresoras válidas")
                return False
            
            if self.logger.isEnabledFor(logging.INFO):
                self.logger.info(f"✅ Configuración cargada: {len(self.printers)} impresoras activas")
            
            return True
            
        except json.JSONDecodeError as e:
            self.logger.error(f"❌ Error JSON en {config_file}: línea {e.lineno}")
            self.stats['total_errors'] += 1
            return False
        except Exception as e:
            self.logger.error(f"❌ Error cargando configuración: {e}")
            self.stats['total_errors'] += 1
            return False
    
    def create_printer_connection(self, printer_config: PrinterConfig, for_keep_alive: bool = False) -> Optional[Network]:
        """Crea conexión con logging adaptativo"""
        operation = "keep-alive" if for_keep_alive else "trabajo"
        
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(f"🔌 Conectando para {operation} - {printer_config.name}")
        
        try:
            timeout = 3 if for_keep_alive else printer_config.connection_timeout
            start_time = time.time()
            
            printer = Network(
                printer_config.ip, 
                port=printer_config.port,
                timeout=timeout
            )
            
            connection_time = time.time() - start_time
            
            if self.logger.isEnabledFor(logging.DEBUG):
                self.logger.debug(f"✅ Conexión creada en {connection_time:.2f}s")
            
            # Actualizar tiempo de respuesta
            if printer_config.token in self.printer_status:
                status = self.printer_status[printer_config.token]
                status.last_response_time = connection_time
                if status.average_response_time == 0:
                    status.average_response_time = connection_time
                else:
                    status.average_response_time = (status.average_response_time + connection_time) / 2
            
            return printer
            
        except socket.timeout:
            if not for_keep_alive or self.logger.isEnabledFor(logging.DEBUG):
                self.logger.warning(f"⏱️  Timeout conectando a {printer_config.name}")
            return None
        except ConnectionRefusedError:
            if not for_keep_alive or self.logger.isEnabledFor(logging.DEBUG):
                self.logger.warning(f"🚫 Conexión rechazada: {printer_config.name}")
            return None
        except Exception as e:
            if not for_keep_alive:
                self.logger.error(f"❌ Error conectando a {printer_config.name}: {e}")
            return None
    
    def test_printer_with_keep_alive(self, printer_config: PrinterConfig) -> Tuple[bool, str]:
        """Test con logging mínimo para keep-alive"""
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(f"💓 Keep-alive test: {printer_config.name}")
        
        try:
            start_time = time.time()
            printer = self.create_printer_connection(printer_config, for_keep_alive=True)
            if not printer:
                return False, "Sin conexión"
            
            # Comando keep-alive
            printer._raw(b'\x1B\x40')
            
            try:
                printer.close()
            except:
                pass
            
            total_time = time.time() - start_time
            return True, f"OK ({total_time:.2f}s)"
            
        except Exception as e:
            return False, str(e)
    
    def update_printer_status(self, token: str, success: bool, error_msg: str = ""):
        """Actualiza estado con logging adaptativo"""
        if token not in self.printer_status:
            if self.logger.isEnabledFor(logging.WARNING):
                self.logger.warning(f"⚠️  Token inexistente: {token}")
            return
        
        status = self.printer_status[token]
        now = datetime.now()
        previous_health = status.is_healthy
        
        if success:
            status.last_successful_connection = now
            if status.consecutive_failures > 0 and self.logger.isEnabledFor(logging.INFO):
                self.logger.info(f"🔄 {status.name} recuperada tras {status.consecutive_failures} fallos")
                self.stats['connections_restored'] += 1
            status.consecutive_failures = 0
            status.is_healthy = True
            status.last_error = ""
        else:
            status.consecutive_failures += 1
            status.last_error = error_msg
            
            if self.logger.isEnabledFor(logging.WARNING):
                self.logger.warning(f"⚠️  Fallo #{status.consecutive_failures} - {status.name}: {error_msg}")
            
            if status.consecutive_failures >= 3 and status.is_healthy:
                status.is_healthy = False
                self.logger.error(f"🚨 {status.name} marcada como NO SALUDABLE")
                self.stats['total_errors'] += 1
        
        # Log cambio de estado
        if previous_health != status.is_healthy and self.logger.isEnabledFor(logging.INFO):
            health_change = "SALUDABLE" if status.is_healthy else "NO SALUDABLE"
            self.logger.info(f"🔄 {status.name} cambió a: {health_change}")
    
    async def perform_keep_alive(self):
        """Keep-alive con logging eficiente"""
        now = datetime.now()
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(f"💓 Verificación keep-alive: {now.strftime('%H:%M:%S')}")
        
        keep_alives_sent = 0
        keep_alives_successful = 0
        
        for token, printer_config in self.printers.items():
            status = self.printer_status[token]
            
            time_since_last_activity = now - max(status.last_keep_alive, status.last_job_printed)
            seconds_since_activity = time_since_last_activity.total_seconds()
            
            if seconds_since_activity > printer_config.keep_alive_interval:
                keep_alives_sent += 1
                
                if self.logger.isEnabledFor(logging.INFO):
                    self.logger.info(f"💓 Keep-alive a {printer_config.name} ({seconds_since_activity:.0f}s inactiva)")
                
                loop = asyncio.get_event_loop()
                success, error = await loop.run_in_executor(
                    self.print_executor,
                    self.test_printer_with_keep_alive,
                    printer_config
                )
                
                if success:
                    status.last_keep_alive = now
                    status.total_keep_alives_sent += 1
                    keep_alives_successful += 1
                    self.stats['keep_alives_sent'] += 1
                    
                    if self.logger.isEnabledFor(logging.INFO):
                        self.logger.info(f"💓 Keep-alive OK: {printer_config.name}")
                    self.update_printer_status(token, True)
                else:
                    status.total_keep_alives_failed += 1
                    self.stats['keep_alives_failed'] += 1
                    
                    if self.logger.isEnabledFor(logging.WARNING):
                        self.logger.warning(f"💓 Keep-alive FALLO: {printer_config.name} - {error}")
                    self.update_printer_status(token, False, f"Keep-alive: {error}")
        
        if keep_alives_sent > 0 and self.logger.isEnabledFor(logging.INFO):
            self.logger.info(f"💓 Keep-alive completado: {keep_alives_successful}/{keep_alives_sent} exitosos")
    
    async def get_jobs_from_odoo(self, session: aiohttp.ClientSession, token: str) -> List[Dict]:
        """Obtiene trabajos con logging eficiente"""
        printer_name = next((p.name for p in self.printers.values() if p.token == token), token[:8])
        
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(f"🌐 Consultando trabajos: {printer_name}")
        
        try:
            url = f"{self.odoo_url}/api/pos_virtual_print/jobs"
            
            async with session.post(
                url, 
                json={"token": token},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                
                if self.logger.isEnabledFor(logging.DEBUG):
                    request_time = 0  # Simplificado para eficiencia
                    self.logger.debug(f"🌐 HTTP {response.status} para {printer_name}")
                
                if response.status == 200:
                    data = await response.json()
                    jobs = data.get('jobs', []) if data.get('success') else []
                    
                    if jobs and self.logger.isEnabledFor(logging.INFO):
                        self.logger.info(f"📋 {len(jobs)} trabajos para {printer_name}")
                        if self.logger.isEnabledFor(logging.DEBUG):
                            for i, job in enumerate(jobs):
                                job_id = job.get('id', 'SIN_ID')
                                ref = job.get('tracking_number', job.get('order_name', 'SIN_REF'))
                                self.logger.debug(f"   📄 {i+1}: #{job_id} - {ref}")
                    
                    return jobs
                elif response.status == 401:
                    if self.logger.isEnabledFor(logging.WARNING):
                        self.logger.warning(f"🔑 Token inválido: {printer_name}")
                    return []
                else:
                    if self.logger.isEnabledFor(logging.WARNING):
                        self.logger.warning(f"🌐 HTTP {response.status}: {printer_name}")
                    return []
                    
        except asyncio.TimeoutError:
            if self.logger.isEnabledFor(logging.WARNING):
                self.logger.warning(f"⏱️  Timeout Odoo: {printer_name}")
            return []
        except Exception as e:
            self.logger.error(f"❌ Error obteniendo trabajos {printer_name}: {e}")
            self.stats['total_errors'] += 1
            return []
    
    async def update_job_status(self, session: aiohttp.ClientSession, job_id: int, token: str, status: str) -> bool:
        """Actualiza estado con logging mínimo"""
        if self.logger.isEnabledFor(logging.DEBUG):
            printer_name = next((p.name for p in self.printers.values() if p.token == token), token[:8])
            self.logger.debug(f"📤 Actualizando #{job_id} a '{status}' - {printer_name}")
        
        try:
            url = f"{self.odoo_url}/api/pos_virtual_print/update_job"
            payload = {"job_id": job_id, "status": status, "token": token}
            
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as response:
                success = response.status == 200
                
                if not success and self.logger.isEnabledFor(logging.WARNING):
                    self.logger.warning(f"⚠️  HTTP {response.status} actualizando #{job_id}")
                
                return success
                
        except Exception as e:
            if self.logger.isEnabledFor(logging.ERROR):
                self.logger.error(f"❌ Error actualizando #{job_id}: {e}")
            self.stats['total_errors'] += 1
            return False
    
    def print_job_sync(self, printer_config: PrinterConfig, job_data: Dict) -> bool:
        """Imprime con logging eficiente"""
        job_id = job_data.get('id', 0)
        tracking_number = job_data.get('tracking_number', job_data.get('order_name', f"#{job_id}"))
        max_attempts = 3
        
        if self.logger.isEnabledFor(logging.INFO):
            self.logger.info(f"🖨️  Imprimiendo #{job_id} - {tracking_number} en {printer_config.name}")
        
        for attempt in range(max_attempts):
            if self.logger.isEnabledFor(logging.DEBUG):
                self.logger.debug(f"🔄 Intento {attempt + 1}/{max_attempts}")
            
            try:
                connection_start = time.time()
                printer = self.create_printer_connection(printer_config)
                
                if not printer:
                    if attempt < max_attempts - 1:
                        if self.logger.isEnabledFor(logging.WARNING):
                            self.logger.warning(f"⚠️  Intento {attempt + 1} falló, reintentando...")
                        time.sleep(printer_config.retry_delay)
                        continue
                    else:
                        self.logger.error(f"❌ Sin conexión tras {max_attempts} intentos")
                        self.stats['jobs_failed'] += 1
                        return False
                
                # Procesar contenido
                content = job_data.get('content', {})
                if isinstance(content, str):
                    content = json.loads(content)
                
                job_type = content.get('job_type', 'preparation')
                
                if self.logger.isEnabledFor(logging.DEBUG):
                    self.logger.debug(f"📋 Tipo: {job_type}")
                
                # Imprimir según tipo
                if job_type == 'receipt':
                    success = self._print_receipt(printer, content, job_id, printer_config)
                else:
                    success = self._print_preparation(printer, content, job_id, printer_config)
                
                total_time = time.time() - connection_start
                
                # Cerrar conexión
                try:
                    printer.close()
                except:
                    pass
                
                if success:
                    # Actualizar estadísticas
                    status = self.printer_status[printer_config.token]
                    status.last_job_printed = datetime.now()
                    status.total_jobs_printed += 1
                    self.update_printer_status(printer_config.token, True)
                    
                    if self.logger.isEnabledFor(logging.INFO):
                        self.logger.info(f"✅ IMPRESO: #{job_id} - {tracking_number} ({total_time:.2f}s)")
                    
                    return True
                else:
                    if attempt < max_attempts - 1:
                        if self.logger.isEnabledFor(logging.WARNING):
                            self.logger.warning(f"⚠️  Impresión falló, reintentando...")
                        time.sleep(printer_config.retry_delay)
                        continue
                    else:
                        self.logger.error(f"❌ FALLO FINAL imprimiendo #{job_id}")
                        self.update_printer_status(printer_config.token, False, "Error impresión")
                        self.stats['jobs_failed'] += 1
                        return False
                
            except Exception as e:
                if attempt < max_attempts - 1:
                    if self.logger.isEnabledFor(logging.WARNING):
                        self.logger.warning(f"⚠️  Error intento {attempt + 1}: {e}")
                    time.sleep(printer_config.retry_delay)
                    continue
                else:
                    self.logger.error(f"❌ ERROR FINAL #{job_id}: {e}")
                    self.update_printer_status(printer_config.token, False, str(e))
                    self.stats['jobs_failed'] += 1
                    return False
        
        return False

    def _print_preparation(self, printer: Network, content: Dict, job_id: int, printer_config: PrinterConfig) -> bool:
        """Imprime comanda con logging eficiente"""
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(f"📄 Imprimiendo comanda #{job_id}")
        
        try:
            # Encabezado
            printer.set(align='center', width=2, height=2, bold=True)
            printer.text("=== COMANDA ===\n")
            
            # Información básica
            printer.set(align='center', width=1, height=1, bold=True)
            tracking_number = content.get('tracking_number', content.get('order_name', 'N/A'))
            printer.text(f"ORDEN: {tracking_number} |  CONTROL#{job_id}\n")
            
            printer.set(align='left', bold=False)
            printer.text("=" * 48 + "\n")
            
            # Detalles de orden
            order_name = content.get('order_name', 'N/A')
            table = content.get('table', 'N/A')
            floor = content.get('floor', '')
            server = content.get('server', 'N/A')
            customer = content.get('customer', '')
            
            printer.text(f"Ref POS: {order_name:<20} Mesa: {table}\n")
            if floor:
                printer.text(f"Piso: {floor:<25} Mesero: {server}\n")
            else:
                printer.text(f"Mesero: {server}\n")
            
            if customer and customer != 'Cliente General':
                printer.text(f"Cliente: {customer[:40]}\n")
            
            printer.text(f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
            
            # Nota general del pedido
            order_note = content.get('note', content.get('order_note', content.get('special_instructions', '')))
            if order_note:
                printer.text("-" * 48 + "\n")
                printer.set(bold=True)
                printer.text("NOTA ESPECIAL:\n")
                printer.set(bold=False)
                
                # Dividir nota en líneas de máximo 46 caracteres
                note_lines = []
                words = order_note.split()
                current_line = ""
                
                for word in words:
                    if len(current_line + " " + word) <= 46:
                        current_line += (" " if current_line else "") + word
                    else:
                        if current_line:
                            note_lines.append(current_line)
                        current_line = word
                
                if current_line:
                    note_lines.append(current_line)
                
                # Imprimir máximo 3 líneas de nota
                for i, line in enumerate(note_lines[:3]):
                    printer.text(f"{line}\n")
                
                if len(note_lines) > 3:
                    printer.text("...(continúa)\n")
            
            printer.text("-" * 48 + "\n")
            
            # Verificar tipo de orden
            is_cancellation = content.get('is_cancellation', False)
            has_modifications = any(line.get('is_modified', False) for line in content.get('lines', []))
            
            if is_cancellation:
                printer.set(align='center', bold=True)
                printer.text("*** ORDEN CANCELADA ***\n")
                printer.set(align='left', bold=False)
                printer.text("-" * 48 + "\n")
            elif has_modifications:
                printer.set(align='center', bold=True)
                printer.text("*** MODIFICACIONES ***\n")
                printer.set(align='left', bold=False)
                printer.text("-" * 48 + "\n")
            
            # Productos
            lines = content.get('lines', [])
            if self.logger.isEnabledFor(logging.DEBUG):
                self.logger.debug(f"📋 Imprimiendo {len(lines)} líneas")
            
            for line in lines:
                qty = line.get('qty', 1)
                name = line.get('name', 'Producto')
                note = line.get('note', '')  # Nota específica del producto
                status = line.get('status', 'NUEVO')
                category = line.get('category_name', '')
                is_modified = line.get('is_modified', False)
                is_cancelled = line.get('is_cancelled', False)
                
                # Indicador de estado
                status_indicator = ""
                if is_cancelled:
                    status_indicator = "[CANCELADO] "
                elif is_modified:
                    status_indicator = "[MODIFICADO] "
                elif status == 'NUEVO':
                    status_indicator = "[NUEVO] "
                
                # Producto principal
                printer.set(bold=True)
                full_name = f"{status_indicator}{name}"
                if len(full_name) > 40:
                    printer.text(f"{full_name[:40]}\n")
                    if len(full_name) > 40:
                        printer.text(f"  {full_name[40:]}\n")
                else:
                    printer.text(f"{full_name}\n")
                
                # Cantidad y categoría
                printer.set(bold=False)
                qty_text = f"Cantidad: {qty}"
                if category:
                    category_text = f"Cat: {category}"
                    spacing = 48 - len(qty_text) - len(category_text)
                    printer.text(f"{qty_text}{' ' * max(1, spacing)}{category_text}\n")
                else:
                    printer.text(f"{qty_text}\n")
                
                # Nota específica del producto (cocina)
                if note:
                    printer.text(f"Nota: {note[:42]}\n")
                    if len(note) > 42:
                        printer.text(f"      {note[42:84]}\n")
                
                printer.text("\n")
            
            # Pie
            printer.text("-" * 48 + "\n")
            printer.set(align='center')
            printer.text(f"Impreso: {datetime.now().strftime('%H:%M:%S')}\n")
            printer.text(f"Estacion: {printer_config.name[:30]}\n")
            
            # Cortar papel
            try:
                printer.cut(mode='FULL')
            except:
                try:
                    printer.cut(mode='PART')
                except:
                    try:
                        printer.cut()
                    except:
                        printer.text("\n\n\n\n")
            
            time.sleep(0.3)
            if self.logger.isEnabledFor(logging.DEBUG):
                self.logger.debug(f"✅ Comanda #{job_id} completada")
            return True
        
        except Exception as e:
            if self.logger.isEnabledFor(logging.ERROR):
                self.logger.error(f"❌ Error comanda #{job_id}: {e}")
            return False
        
    def _print_receipt(self, printer: Network, content: Dict, job_id: int, printer_config: PrinterConfig) -> bool:
        """Imprime recibo mejorado con mejor formato y alineación"""
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(f"🧾 Imprimiendo recibo #{job_id}")
        
        try:
            company_name = content.get('company_name', 'EMPRESA')
            tracking_number = content.get('tracking_number', content.get('order_name', 'N/A'))
            
            # Encabezado empresa
            printer.set(align='center', width=2, height=2, bold=True)
            printer.text(f"{company_name}\n")
            
            printer.set(align='center', width=1, height=1, bold=False)
            company_info = content.get('company_info', {})
            if isinstance(company_info, dict):
                if company_info.get('address'):
                    printer.text(f"{company_info['address']}\n")
                if company_info.get('phone'):
                    printer.text(f"Tel: {company_info['phone']}\n")
                if company_info.get('email'):
                    printer.text(f"{company_info['email']}\n")
                if company_info.get('vat') or company_info.get('rfc'):
                    vat = company_info.get('vat', company_info.get('rfc', ''))
                    printer.text(f"RFC: {vat}\n")
            
            printer.text("=" * 48 + "\n")
            
            # Información de la orden
            printer.set(align='left', bold=True)
            printer.text(f"RECIBO: {tracking_number}\n")
            printer.set(bold=False)
            
            # Fecha y hora
            order_date = content.get('order_date', content.get('date_order', ''))
            if not order_date:
                order_date = datetime.now().strftime('%d/%m/%Y %H:%M')
            printer.text(f"Fecha: {order_date}\n")
            
            # Información adicional - Mejor formato
            table = content.get('table', content.get('table_name', 'N/A'))
            server = content.get('server', content.get('user_name', content.get('cashier', 'N/A')))
            customer = content.get('customer', content.get('partner_name', content.get('client_name', '')))
            
            # Mesa y mesero en líneas separadas si es muy largo
            mesa_mesero = f"Mesa: {table} | Mesero: {server}"
            if len(mesa_mesero) > 48:
                printer.text(f"Mesa: {table}\n")
                printer.text(f"Mesero: {server}\n")
            else:
                printer.text(f"{mesa_mesero}\n")
            
            if customer and customer not in ['Cliente General', '']:
                # Truncar cliente si es muy largo
                customer_line = f"Cliente: {customer[:40]}"
                printer.text(f"{customer_line}\n")
            
            printer.text("-" * 48 + "\n")
            
            # Encabezado de productos con mejor alineación
            printer.set(bold=True)
            printer.text("CANT DESCRIPCION           PRECIO     SUBTOTAL\n")
            printer.set(bold=False)
            printer.text("-" * 48 + "\n")
            
            # Productos con formato mejorado
            lines = content.get('lines', content.get('order_lines', content.get('products', [])))
            total = 0
            line_count = 0
            
            for line in lines:
                try:
                    name = (line.get('name') or 
                        line.get('product_name') or 
                        line.get('description') or 
                        line.get('display_name') or 
                        'Producto Sin Nombre')
                    
                    qty = float(line.get('qty', line.get('quantity', 1)))
                    price = float(line.get('price', line.get('price_unit', 0)))
                    subtotal = qty * price
                    total += subtotal
                    line_count += 1
                    
                    # Formato: CANT DESCRIPCION PRECIO SUBTOTAL
                    # Espacios: 4 + 20 + 10 + 14 = 48
                    qty_str = f"{qty:>3.0f}" if qty == int(qty) else f"{qty:>3.1f}"
                    
                    # Truncar nombre a 20 caracteres
                    name_str = name[:20].ljust(20)
                    
                    price_str = f"Bs.{price:>6.2f}"
                    subtotal_str = f"Bs.{subtotal:>8.2f}"
                    
                    # Línea principal del producto
                    printer.text(f"{qty_str} {name_str} {price_str} {subtotal_str}\n")
                    
                    # Si el nombre era muy largo, mostrar resto en línea siguiente
                    if len(name) > 20:
                        remaining_name = name[20:60]  # Máximo 40 chars adicionales
                        printer.text(f"    {remaining_name}\n")
                    
                    # Nota si existe
                    note = line.get('note', line.get('description', ''))
                    if note and note != name:
                        note_lines = [note[i:i+44] for i in range(0, len(note), 44)]
                        for note_line in note_lines[:2]:  # Máximo 2 líneas de nota
                            printer.text(f"    Nota: {note_line}\n")
                    
                except (ValueError, TypeError) as e:
                    if self.logger.isEnabledFor(logging.WARNING):
                        self.logger.warning(f"⚠️  Error procesando línea: {e}")
                    continue
            
            # Separador antes de totales
            printer.text("-" * 48 + "\n")
            
            # Cálculo de impuestos
            tax_included = content.get('tax_included', True)
            tax_rate = float(content.get('tax_rate', 0.16))
            
            if tax_included and total > 0:
                subtotal_before_tax = total / (1 + tax_rate)
                tax_amount = total - subtotal_before_tax
            else:
                subtotal_before_tax = total
                tax_amount = total * tax_rate
                total = subtotal_before_tax + tax_amount
            
            # Mostrar subtotales con mejor alineación
            if tax_amount > 0:
                printer.text(f"{'Subtotal:':<32} Bs.{subtotal_before_tax:>11.2f}\n")
                tax_label = f"IVA ({int(tax_rate*100)}%):"
                printer.text(f"{tax_label:<32} Bs.{tax_amount:>11.2f}\n")
            
            # Descuentos
            discount = float(content.get('discount', content.get('discount_amount', 0)))
            if discount > 0:
                printer.text(f"{'Descuento:':<32} Bs.{discount:>11.2f}\n")
                total -= discount
            
            # Total final
            printer.text("=" * 48 + "\n")
            printer.set(bold=True, width=1, height=1)
            printer.text(f"{'TOTAL:':<32} Bs.{total:>11.2f}\n")
            printer.set(bold=False, width=1, height=1)
            printer.text("=" * 48 + "\n")
            
            # Información de pago mejorada
            payment_methods = []
            
            # Buscar métodos de pago en diferentes campos
            payment_method = (content.get('payment_method') or 
                            content.get('payment_mode') or 
                            content.get('payment_journal') or
                            content.get('journal_name'))
            
            if payment_method:
                payment_methods.append(payment_method)
            
            # Buscar en payments si existe
            payments = content.get('payments', content.get('payment_lines', []))
            if payments:
                for payment in payments:
                    method = (payment.get('journal_name') or 
                            payment.get('payment_method') or
                            payment.get('name'))
                    amount = payment.get('amount', 0)
                    if method and amount > 0:
                        payment_methods.append(f"{method}: Bs.{amount:.2f}")
            
            # Si no hay métodos específicos, usar efectivo por defecto
            if not payment_methods:
                payment_methods = ['Efectivo']
            
            # Mostrar métodos de pago
            printer.text("Metodo(s) de pago:\n")
            for method in payment_methods:
                printer.text(f"  {method}\n")
            
            # Cálculo de cambio solo para efectivo
            if any('efectivo' in method.lower() or 'cash' in method.lower() for method in payment_methods):
                amount_paid = float(content.get('amount_paid', content.get('received_amount', total)))
                if amount_paid > total:
                    change = amount_paid - total
                    printer.text(f"Pago recibido: Bs.{amount_paid:.2f}\n")
                    printer.text(f"Cambio: Bs.{change:.2f}\n")
            
            printer.text("\n")
            
            # Información adicional
            internal_ref = content.get('internal_reference', content.get('pos_reference', ''))
            if internal_ref and internal_ref != tracking_number:
                printer.text(f"         Ref. interna: {internal_ref}\n")
            
            # Pie del recibo
            printer.set(align='center')
            printer.text("¡GRACIAS POR SU COMPRA!\n")
            
            # Información del terminal
            cashier = content.get('cashier', content.get('user_name', content.get('server', 'Sistema')))
            printer.text(f"Cajero: {cashier}\n")
            printer.text(f"Terminal: {printer_config.name[:30]}\n")
            printer.text(f"Impreso: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
            
            # Información fiscal
            fiscal_info = content.get('fiscal_info', {})
            if isinstance(fiscal_info, dict) and fiscal_info:
                printer.text("\n")
                if fiscal_info.get('folio'):
                    printer.text(f"Folio: {fiscal_info['folio']}\n")
                if fiscal_info.get('serie'):
                    printer.text(f"Serie: {fiscal_info['serie']}\n")
            
            # Cortar papel
            printer.text("\n")
            try:
                printer.cut(mode='FULL')
            except:
                try:
                    printer.cut(mode='PART')
                except:
                    try:
                        printer.cut()
                    except:
                        printer.text("\n\n\n\n")
            
            time.sleep(0.3)
            
            if self.logger.isEnabledFor(logging.INFO):
                self.logger.info(f"✅ Recibo #{job_id} - {line_count} productos, Total: Bs.{total:.2f}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Error recibo #{job_id}: {e}")
            return False

    async def process_printer_jobs(self, session: aiohttp.ClientSession, token: str) -> int:
        """Procesa trabajos de una impresora"""
        jobs = await self.get_jobs_from_odoo(session, token)
        if not jobs:
            return 0
        
        printer_config = self.printers[token]
        processed = 0
        
        for job in jobs:
            if self.killer.kill_now:
                break
            
            job_id = job.get('id')
            if not job_id:
                continue
            
            # Actualizar estado a "processing"
            #await self.update_job_status(session, job_id, token, 'processing')
            
            # Imprimir en thread pool
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                self.print_executor,
                self.print_job_sync,
                printer_config,
                job
            )
            
            if success:
                await self.update_job_status(session, job_id, token, 'done')
                self.stats['jobs_processed'] += 1
                processed += 1
                
                # Remover de cola de fallos si existía
                if job_id in self.failed_jobs:
                    del self.failed_jobs[job_id]
                    if self.logger.isEnabledFor(logging.INFO):
                        self.logger.info(f"✅ Trabajo #{job_id} recuperado de cola de fallos")
            else:
                await self.update_job_status(session, job_id, token, 'error')
                self.add_to_retry_queue(job_id, token, job)
            
            self.stats['last_activity'] = datetime.now()
        
        return processed
    
    def add_to_retry_queue(self, job_id: int, token: str, job_data: Dict):
        """Añade trabajo a cola de reintentos"""
        if len(self.failed_jobs) >= self.max_failed_jobs:
            # Remover el más antiguo
            oldest_job_id = min(self.failed_jobs.keys(), 
                              key=lambda x: self.failed_jobs[x].last_attempt)
            del self.failed_jobs[oldest_job_id]
            if self.logger.isEnabledFor(logging.WARNING):
                self.logger.warning(f"⚠️  Cola llena, removido trabajo #{oldest_job_id}")
        
        if job_id in self.failed_jobs:
            failed_job = self.failed_jobs[job_id]
            failed_job.attempts += 1
        else:
            failed_job = FailedJob(job_id, token, job_data)
        
        # Calcular próximo intento
        delay_index = min(failed_job.attempts - 1, len(self.retry_intervals) - 1)
        delay_seconds = self.retry_intervals[delay_index]
        failed_job.next_retry = datetime.now() + timedelta(seconds=delay_seconds)
        failed_job.last_attempt = datetime.now()
        
        self.failed_jobs[job_id] = failed_job
        
        if self.logger.isEnabledFor(logging.WARNING):
            self.logger.warning(f"⚠️  Trabajo #{job_id} en cola reintento #{failed_job.attempts} "
                              f"(próximo en {delay_seconds}s)")
    
    async def process_retry_queue(self, session: aiohttp.ClientSession) -> int:
        """Procesa cola de reintentos"""
        now = datetime.now()
        ready_jobs = [job for job in self.failed_jobs.values() if job.next_retry <= now]
        
        if not ready_jobs:
            return 0
        
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(f"🔄 Procesando {len(ready_jobs)} reintentos")
        
        processed = 0
        for failed_job in ready_jobs:
            if self.killer.kill_now:
                break
            
            if failed_job.printer_token not in self.printers:
                del self.failed_jobs[failed_job.job_id]
                continue
            
            printer_config = self.printers[failed_job.printer_token]
            
            if self.logger.isEnabledFor(logging.INFO):
                self.logger.info(f"🔄 Reintentando trabajo #{failed_job.job_id} "
                               f"(intento {failed_job.attempts})")
            
            # Intentar imprimir
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                self.print_executor,
                self.print_job_sync,
                printer_config,
                failed_job.job_data
            )
            
            if success:
                await self.update_job_status(session, failed_job.job_id, 
                                           failed_job.printer_token, 'done')
                del self.failed_jobs[failed_job.job_id]
                self.stats['jobs_processed'] += 1
                self.stats['jobs_retried'] += 1
                processed += 1
                
                if self.logger.isEnabledFor(logging.INFO):
                    self.logger.info(f"✅ Reintento exitoso: #{failed_job.job_id}")
            else:
                # Si falla mucho, remover
                if failed_job.attempts >= 5:
                    del self.failed_jobs[failed_job.job_id]
                    await self.update_job_status(session, failed_job.job_id,
                                               failed_job.printer_token, 'error')
                    if self.logger.isEnabledFor(logging.ERROR):
                        self.logger.error(f"❌ Trabajo #{failed_job.job_id} "
                                        f"descartado tras {failed_job.attempts} intentos")
                else:
                    # Reagendar
                    self.add_to_retry_queue(failed_job.job_id, failed_job.printer_token, 
                                          failed_job.job_data)
        
        return processed
    
    def heartbeat(self):
        """Heartbeat con logging controlado"""
        uptime = datetime.now() - self.stats['start_time']
        healthy_printers = len([p for p in self.printer_status.values() if p.is_healthy])
        
        if self.logger.isEnabledFor(logging.INFO):
            self.logger.info(f"💗 Heartbeat - Uptime: {uptime}, "
                           f"Impresoras OK: {healthy_printers}/{len(self.printers)}, "
                           f"Trabajos: {self.stats['jobs_processed']}, "
                           f"Pendientes: {len(self.failed_jobs)}")
    
    def print_status_summary(self):
        """Resumen de estado con logging controlado"""
        if not self.logger.isEnabledFor(logging.INFO):
            return
        
        uptime = datetime.now() - self.stats['start_time']
        self.logger.info("=" * 60)
        self.logger.info("📊 RESUMEN DE ESTADO")
        self.logger.info(f"   ⏱️  Uptime: {uptime}")
        self.logger.info(f"   ✅ Trabajos procesados: {self.stats['jobs_processed']}")
        self.logger.info(f"   💓 Keep-alives: {self.stats['keep_alives_sent']}")
        self.logger.info(f"   🔄 Reintentos: {self.stats['jobs_retried']}")
        self.logger.info(f"   ❌ Errores: {self.stats['total_errors']}")
        self.logger.info(f"   📋 Trabajos pendientes: {len(self.failed_jobs)}")
        
        healthy_count = 0
        for token, status in self.printer_status.items():
            printer_name = status.name
            health = "✅" if status.is_healthy else "❌"
            self.logger.info(f"   🖨️  {printer_name}: {health} "
                           f"({status.total_jobs_printed} trabajos)")
            if status.is_healthy:
                healthy_count += 1
        
        self.logger.info(f"   📊 Impresoras saludables: {healthy_count}/{len(self.printers)}")
        self.logger.info("=" * 60)

    async def main_loop(self):
        """Loop principal con logging optimizado"""
        if self.logger.isEnabledFor(logging.INFO):
            self.logger.info("🔄 INICIANDO LOOP PRINCIPAL")
            self.logger.info(f"   🌐 Odoo: {self.odoo_url}")
            self.logger.info(f"   ⏱️  Intervalo: {self.check_interval}s")
            self.logger.info(f"   💓 Keep-alive: {self.keep_alive_interval}s")
        
        connector = aiohttp.TCPConnector(limit=20, limit_per_host=5)
        timeout = aiohttp.ClientTimeout(total=15)
        
        heartbeat_counter = 0
        status_summary_counter = 0
        status_export_counter = 0
        
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            while self.running and not self.killer.kill_now:
                try:
                    loop_start = time.time()
                    
                    # Keep-alive periódico
                    now = datetime.now()
                    if (now - self.last_keep_alive_check).total_seconds() >= self.keep_alive_interval:
                        await self.perform_keep_alive()
                        self.last_keep_alive_check = now
                    
                    # Exportar estado cada 10 ciclos
                    status_export_counter += 1
                    if status_export_counter >= 10:
                        self.status_exporter.export_status(self)
                        status_export_counter = 0
                        if self.logger.isEnabledFor(logging.DEBUG):
                            self.logger.debug("📊 Estado exportado para dashboard")
                    
                    # Heartbeat cada 20 ciclos
                    heartbeat_counter += 1
                    if heartbeat_counter >= 20:
                        self.heartbeat()
                        heartbeat_counter = 0
                    
                    # Resumen cada 100 ciclos
                    status_summary_counter += 1
                    if status_summary_counter >= 100:
                        self.print_status_summary()
                        status_summary_counter = 0
                    
                    # Procesar trabajos
                    tasks = []
                    for token in self.printers.keys():
                        tasks.append(self.process_printer_jobs(session, token))
                    tasks.append(self.process_retry_queue(session))
                    
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    total_processed = sum(r for r in results[:-1] if isinstance(r, int))
                    
                    loop_time = time.time() - loop_start
                    
                    if total_processed > 0:
                        if self.logger.isEnabledFor(logging.INFO):
                            failed_count = len(self.failed_jobs)
                            self.logger.info(f"✅ Ciclo: {total_processed} trabajos, "
                                           f"{failed_count} pendientes ({loop_time:.2f}s)")
                    else:
                        if self.logger.isEnabledFor(logging.DEBUG):
                            self.logger.debug(f"😴 Ciclo sin trabajos ({loop_time:.2f}s)")
                    
                    await asyncio.sleep(self.check_interval)
                    
                except Exception as e:
                    self.logger.error(f"💥 Error en loop principal: {e}")
                    self.stats['total_errors'] += 1
                    await asyncio.sleep(5)
        
        if self.logger.isEnabledFor(logging.INFO):
            self.logger.info("🔚 Loop principal terminado")
    
    async def run(self):
        """Ejecuta cliente con logging configurable"""
        setup_wakelock(self.enable_termux)
        self.running = True
        
        if self.logger.isEnabledFor(logging.INFO):
            self.logger.info("=" * 80)
            self.logger.info("🚀 INICIANDO SERVICIO ESC/POS")
            self.logger.info("=" * 80)
            self.logger.info(f"📊 Impresoras: {len(self.printers)}")
            self.logger.info(f"📝 Log level: {self.log_level}")
            self.logger.info(f"🔄 Rotación: {self.log_rotation_days} días")
            self.logger.info("   Presiona Ctrl+C para detener")
            self.logger.info("=" * 80)
        
        try:
            await self.main_loop()
        except KeyboardInterrupt:
            if self.logger.isEnabledFor(logging.INFO):
                self.logger.info("👋 SERVICIO DETENIDO POR USUARIO")
        except Exception as e:
            self.logger.error(f"💥 ERROR INESPERADO: {e}")
            if self.logger.isEnabledFor(logging.DEBUG):
                self.logger.exception("Stack trace completo:")
        finally:
            self.running = False
            self.print_executor.shutdown(wait=True)
            cleanup_wakelock(self.enable_termux)
            
            if self.logger.isEnabledFor(logging.INFO):
                self.logger.info("=" * 80)
                self.logger.info("🔚 SERVICIO CERRADO")
                self.logger.info(f"📊 Estadísticas finales:")
                self.logger.info(f"   ✅ Trabajos: {self.stats['jobs_processed']}")
                self.logger.info(f"   💓 Keep-alives: {self.stats['keep_alives_sent']}")
                self.logger.info(f"   🔄 Reintentos: {self.stats['jobs_retried']}")
                self.logger.info(f"   ❌ Errores: {self.stats['total_errors']}")
                self.logger.info("=" * 80)

def main():
    parser = argparse.ArgumentParser(description='Cliente ESC/POS con Control de Logging')
    
    parser.add_argument('--url', required=True,
                       help='URL de Odoo (ej: http://thepoint.ottavioit.com:8090)')
    
    parser.add_argument('--config', default='config.json',
                       help='Archivo de configuración JSON')
    
    parser.add_argument('--interval', type=int, default=3,
                       help='Segundos entre consultas (default: 3)')
    
    # Nuevos argumentos para logging
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], 
                       default='INFO',
                       help='Nivel de logging (default: INFO)')
    
    parser.add_argument('--log-rotation-days', type=int, default=7,
                       help='Días de rotación de logs (default: 7)')
    
    parser.add_argument('--log-max-size-mb', type=int, default=10,
                       help='Tamaño máximo logs detallados en MB (default: 10)')
    
    parser.add_argument('--termux', action='store_true',
                       help='Activar características específicas de Termux (wakelock)')

    args = parser.parse_args()
    
    if not ESCPOS_AVAILABLE:
        print("❌ python-escpos no disponible")
        return 1
    
    # Crear cliente con configuraciones de logging
    client = AsyncClient(
        args.url, 
        args.interval,
        args.log_level,
        args.log_rotation_days,
        args.log_max_size_mb,
        args.termux
    )
    
    if not client.load_config(args.config):
        return 1
    
    try:
        asyncio.run(client.run())
        return 0
    except KeyboardInterrupt:
        print("\n👋 Terminado por usuario")
        return 0
    except Exception as e:
        print(f"\n💥 Error fatal: {e}")
        return 1

if __name__ == '__main__':
    sys.exit(main())